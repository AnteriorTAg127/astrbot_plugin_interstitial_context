import re
import random
from datetime import datetime

from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star, register
from astrbot.api import logger
from astrbot.api.provider import ProviderRequest, LLMResponse
from astrbot.core.agent.message import TextPart
from astrbot.api import AstrBotConfig
from quart import request, jsonify

from .db import AffectionDB

PLUGIN_NAME = "astrbot_plugin_interstitial_context"


@register(PLUGIN_NAME, "AnteriorTAg127", "轻量上下文注入插件", "1.3.0")
class InterstitialContextPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config
        # 内存缓存
        self._inject_snapshot = {}  # {cache_key: {affection_range_key, time_segment, user_id}}
        self._freeze_state = {}  # {cache_key: freeze_start_datetime}
        self._rate_limit = {}  # {group_id: {count, window_start}}
        self._last_cache_cleanup = datetime.now()  # 上次缓存清理时间

        # 数据库
        self.db = AffectionDB()

        # 注册 Web API（bridge 只支持 GET/POST，update 和 delete 用独立路径）
        self.context.register_web_api(
            f"/{PLUGIN_NAME}/affections", self._api_affections, ["GET"], "查询好感度"
        )
        self.context.register_web_api(
            f"/{PLUGIN_NAME}/affections",
            self._api_affections_create,
            ["POST"],
            "添加好感度",
        )
        self.context.register_web_api(
            f"/{PLUGIN_NAME}/affections/update",
            self._api_affections_update,
            ["POST"],
            "修改好感度",
        )
        self.context.register_web_api(
            f"/{PLUGIN_NAME}/affections/delete",
            self._api_affections_delete,
            ["GET"],
            "删除好感度",
        )
        self.context.register_web_api(
            f"/{PLUGIN_NAME}/sessions",
            self._api_sessions,
            ["GET"],
            "获取会话列表",
        )
        self.context.register_web_api(
            f"/{PLUGIN_NAME}/users",
            self._api_users,
            ["GET"],
            "获取用户列表",
        )

    @filter.on_astrbot_loaded()
    async def on_loaded(self):
        """插件加载完成后初始化数据库并迁移旧数据"""
        await self.db.init_db()
        await self.db.migrate_from_kv(self.get_kv_data, self.delete_kv_data)

    # ==================== 辅助方法 ====================

    def _cleanup_caches(self):
        """定期清理内存缓存，移除过期条目"""
        now = datetime.now()
        # 每小时最多清理一次
        if (now - self._last_cache_cleanup).total_seconds() < 3600:
            return
        self._last_cache_cleanup = now

        # 清理过期的速率限制窗口
        window = self.config.get("view_rate_limit_window", 5)
        expired_groups = [
            g
            for g, info in self._rate_limit.items()
            if (now - info["window_start"]).total_seconds() / 60 > window
        ]
        for g in expired_groups:
            del self._rate_limit[g]

        # 清理冻结状态中已完全恢复的条目（防御性检查）
        freeze_duration = self.config.get("freeze_duration", 24.0)
        recovery_rate = self.config.get("recovery_rate", 5.0)
        recovered_keys = []
        for key, freeze_start in self._freeze_state.items():
            elapsed = (now - freeze_start).total_seconds() / 3600
            if elapsed > freeze_duration:
                recovery_hours = elapsed - freeze_duration
                recovered_prob = (recovery_rate / 100.0) * recovery_hours
                if recovered_prob >= 1.0:
                    recovered_keys.append(key)
        for key in recovered_keys:
            del self._freeze_state[key]

    @staticmethod
    def _get_session_id(event: AstrMessageEvent) -> str:
        """获取会话ID，群聊用群ID，私聊用 private:user_id"""
        group_id = event.get_group_id()
        if group_id:
            return group_id
        return f"private:{event.get_sender_id()}"

    def _get_cache_key(self, event: AstrMessageEvent) -> str:
        """获取缓存key，群聊用群ID+用户ID，私聊用用户ID（仅用于内存缓存）"""
        group_id = event.get_group_id()
        user_id = event.get_sender_id()
        if group_id:
            return f"{group_id}:{user_id}"
        return f"private:{user_id}"

    async def _get_group_name(
        self, event: AstrMessageEvent, group_id: str
    ) -> str | None:
        """通过协议端 API 获取群名称，失败返回 None"""
        try:
            if event.get_platform_name() == "aiocqhttp":
                from astrbot.core.platform.sources.aiocqhttp.aiocqhttp_message_event import (
                    AiocqhttpMessageEvent,
                )

                assert isinstance(event, AiocqhttpMessageEvent)
                ret = await event.bot.api.call_action(
                    "get_group_info", group_id=int(group_id)
                )
                return ret.get("group_name", "")
        except Exception as e:
            logger.debug(f"[InterstitialContext] 获取群名失败: {e}")
        return None

    # ==================== 好感规则匹配 ====================

    def _match_affection_rule(self, affection: int) -> str:
        """根据好感度匹配范围段，返回显示文字"""
        rules = self.config.get("affection_rules", [])
        for rule in rules:
            range_min = rule.get("range_min", -100)
            range_max = rule.get("range_max", 100)
            display_text = rule.get("display_text", "")
            if range_min <= affection <= range_max:
                return display_text
        return str(affection)

    def _get_affection_range_key(self, affection: int) -> str:
        """获取好感范围段的唯一标识（用于变更判定）"""
        rules = self.config.get("affection_rules", [])
        for rule in rules:
            if rule.get("range_min", -100) <= affection <= rule.get("range_max", 100):
                return f"{rule.get('range_min')}-{rule.get('range_max')}"
        return f"unmatched:{affection}"

    def _render_affection_display(self, affection: int) -> str:
        """渲染好感度显示文本"""
        mode = self.config.get("affection_display_mode", "text")
        label = self._match_affection_rule(affection)
        if mode == "number":
            return str(affection)
        elif mode == "text":
            return label
        else:  # both
            return f"{affection}（{label}）"

    # ==================== 时间区间计算 ====================

    def _get_time_segment(self) -> tuple:
        """计算当前时间区间，返回 (区间标识, 格式化文本)"""
        now = datetime.now()
        granularity = self.config.get("time_granularity", 30)
        total_minutes = now.hour * 60 + now.minute
        segment_index = total_minutes // granularity
        start_minutes = segment_index * granularity
        end_minutes = min(start_minutes + granularity, 24 * 60)

        start_h, start_m = divmod(start_minutes, 60)
        end_h, end_m = divmod(end_minutes, 60)

        # 时段描述
        period_map = [
            (0, "凌晨"),
            (6, "上午"),
            (12, "中午"),
            (14, "下午"),
            (18, "晚上"),
        ]
        time_period = "深夜"
        for threshold, label in period_map:
            if now.hour >= threshold:
                time_period = label

        template = self.config.get(
            "time_format_template", "{time_period}{start_h}:{start_m}-{end_h}:{end_m}"
        )
        formatted = template.format(
            time_period=time_period,
            start_h=f"{start_h:02d}",
            start_m=f"{start_m:02d}",
            end_h=f"{end_h:02d}",
            end_m=f"{end_m:02d}",
        )

        return (f"{segment_index}", formatted)

    # ==================== 变更注入（on_llm_request 钩子） ====================

    @filter.on_llm_request()
    async def on_llm_request(self, event: AstrMessageEvent, req: ProviderRequest):
        """LLM 请求前注入上下文"""
        self._cleanup_caches()
        cache_key = self._get_cache_key(event)
        user_id = event.get_sender_id()
        nickname = event.get_sender_name()
        group_id = event.get_group_id()
        session_id = self._get_session_id(event)
        no_save = self.config.get("inject_no_save", True)

        # 更新昵称
        if nickname:
            affection_initial = self.config.get("affection_initial", 0)
            await self.db.upsert_nickname(
                user_id, session_id, nickname, affection_initial
            )

        # 1. 计算好感度衰减
        affection = await self._calculate_decay(user_id, session_id)

        # 2. 更新最后活跃时间（无论是否回复都更新，避免衰减持续累积）
        await self._update_last_active(user_id, session_id)

        # 3. 回复概率 + 冷淡提示（共享激活阈值）
        activation_threshold = self.config.get("activation_threshold", 0)
        if affection < activation_threshold:
            # 低于激活阈值：概率判定 + 冷淡提示
            should_reply = self._check_reply_probability(affection, cache_key)
            if not should_reply:
                logger.info(
                    f"[InterstitialContext] {cache_key} 好感度 {affection} 低于激活阈值 {activation_threshold}，回复概率判定不回复"
                )
                event.stop_event()
                return
            # 注入冷淡提示（动态，注入用户消息）
            cold_hint = self.config.get("cold_hint_template", "")
            if cold_hint:
                part = TextPart(text=cold_hint)
                if no_save:
                    part.mark_as_temp()
                req.extra_user_content_parts.append(part)
                logger.debug(f"[InterstitialContext] 注入冷淡提示: {cold_hint}")

        # 4. 静态信息注入 system_prompt
        if group_id:
            group_name = await self._get_group_name(event, group_id)
            if group_name:
                static_info = f"[当前对话:群聊{group_name}({group_id})]"
                await self.db.upsert_session_name(group_id, group_name)
            else:
                static_info = f"[当前对话:群聊{group_id}]"
        else:
            static_info = f"[当前对话:私聊 用户{nickname}({user_id})]"
        hint = self.config.get("affection_change_hint", "")
        if hint:
            max_change = self.config.get("max_affection_change", 5)
            static_info += "\n" + hint.format(max_change=max_change)
        if self.config.get("inject_system_prompt_position", "before") == "before":
            req.system_prompt = static_info + "\n" + req.system_prompt
        else:
            req.system_prompt += "\n" + static_info

        # 5. 动态信息注入用户消息（好感度、时间区间、变化提示）
        affection_range_key = self._get_affection_range_key(affection)
        time_segment_key, time_segment_text = self._get_time_segment()

        snapshot = self._inject_snapshot.get(cache_key, {})
        changed = (
            snapshot.get("affection_range_key") != affection_range_key
            or snapshot.get("time_segment") != time_segment_key
            or snapshot.get("user_id") != user_id
        )

        if changed or not snapshot or no_save:
            affection_display = self._match_affection_rule(affection)
            inject_template = self.config.get(
                "inject_template",
                "<{nickname}好感{affection_display}> <{time_segment}>",
            )
            inject_text = inject_template.format(
                user_id=user_id,
                nickname=nickname,
                affection_display=affection_display,
                time_segment=time_segment_text,
            )
            part = TextPart(text=inject_text)
            if no_save:
                part.mark_as_temp()
            req.extra_user_content_parts.append(part)
            logger.info(f"[InterstitialContext] 注入上下文: {inject_text}")

            # 更新快照
            self._inject_snapshot[cache_key] = {
                "affection_range_key": affection_range_key,
                "time_segment": time_segment_key,
                "user_id": user_id,
            }
        else:
            logger.debug(f"[InterstitialContext] {cache_key} 无变更，跳过注入")

    # ==================== 好感度变化解析（on_llm_response 钩子） ====================

    @filter.on_llm_response()
    async def on_llm_response(self, event: AstrMessageEvent, response: LLMResponse):
        """LLM 回复后解析好感度变化"""
        resp_text = response.completion_text or ""
        pattern = r"<affection>([+-]?\d+)</affection>"
        matches = re.findall(pattern, resp_text)

        if matches:
            max_change = self.config.get("max_affection_change", 5)
            deltas = []
            for m in matches:
                d = int(m)
                if max_change > 0:
                    d = max(-max_change, min(max_change, d))
                deltas.append(d)
            total_delta = sum(deltas)
            if max_change > 0:
                total_delta = max(-max_change, min(max_change, total_delta))
            min_val = self.config.get("affection_min", -100)
            max_val = self.config.get("affection_max", 100)
            user_id = event.get_sender_id()
            session_id = self._get_session_id(event)
            nickname = event.get_sender_name()
            current = await self.db.get_affection(user_id, session_id)
            new_affection = max(min_val, min(max_val, current + total_delta))
            await self.db.set_affection(user_id, session_id, new_affection, nickname)
            logger.debug(f"好感度变化: {total_delta:+d}, 新值: {new_affection}")

            # 从回复中移除 XML 标记（通过 completion_text setter 修改）
            cleaned = re.sub(pattern, "", resp_text).strip()
            response.completion_text = cleaned

    # ==================== 回复概率计算 ====================

    def _check_reply_probability(self, affection: int, cache_key: str) -> bool:
        """检查是否应该回复，返回 True 表示回复。仅在 affection < activation_threshold 时调用。"""
        activation_threshold = self.config.get("activation_threshold", 0)
        min_affection = self.config.get("affection_min", -100)
        min_prob = self.config.get("min_reply_probability", 0.0)

        # 线性插值：activation_threshold 处概率 1.0，min_affection 处概率 min_prob
        if affection <= min_affection:
            base_prob = min_prob
        elif affection >= activation_threshold:
            base_prob = 1.0
        else:
            ratio = (affection - min_affection) / (activation_threshold - min_affection)
            base_prob = min_prob + ratio * (1.0 - min_prob)

        # 冻结恢复计算
        actual_prob = self._calculate_freeze_recovery(base_prob, cache_key)

        # 概率判定
        result = random.random() < actual_prob

        # 如果基础概率为0且未在冻结中，记录冻结开始时间
        if base_prob <= 0 and cache_key not in self._freeze_state:
            self._freeze_state[cache_key] = datetime.now()

        return result

    def _calculate_freeze_recovery(self, base_prob: float, cache_key: str) -> float:
        """计算冻结恢复后的实际回复概率"""
        freeze_info = self._freeze_state.get(cache_key)
        if not freeze_info:
            return base_prob

        freeze_start = freeze_info  # datetime
        now = datetime.now()
        freeze_duration = self.config.get("freeze_duration", 24.0)  # 小时
        recovery_rate = self.config.get("recovery_rate", 5.0)  # %/小时

        elapsed_hours = (now - freeze_start).total_seconds() / 3600

        if elapsed_hours < freeze_duration:
            # 仍在冻结期
            return 0.0

        # 冻结期结束，线性恢复
        recovery_hours = elapsed_hours - freeze_duration
        recovered_prob = (recovery_rate / 100.0) * recovery_hours

        # 不超过基础概率
        actual_prob = min(recovered_prob, base_prob)

        # 如果已恢复到基础概率，清除冻结状态
        if actual_prob >= base_prob:
            del self._freeze_state[cache_key]

        return actual_prob

    # ==================== 好感度衰减 ====================

    async def _calculate_decay(self, user_id: str, session_id: str) -> int:
        """计算好感度衰减，返回衰减后的好感度"""
        if not self.config.get("enable_decay", True):
            return await self.db.get_affection(user_id, session_id)

        last_active_str = await self.db.get_last_active(user_id, session_id)

        if not last_active_str:
            return await self.db.get_affection(user_id, session_id)

        try:
            last_active = datetime.fromisoformat(last_active_str)
        except (ValueError, TypeError):
            return await self.db.get_affection(user_id, session_id)

        now = datetime.now()
        elapsed_hours = (now - last_active).total_seconds() / 3600
        decay_timeout = self.config.get("decay_timeout", 48.0)

        if elapsed_hours <= decay_timeout:
            return await self.db.get_affection(user_id, session_id)

        # 超时，计算衰减
        decay_rate = self.config.get("decay_rate", 1.0)
        decay_floor = self.config.get("decay_floor", -100)
        overtime = elapsed_hours - decay_timeout
        decay_amount = overtime * decay_rate

        current = await self.db.get_affection(user_id, session_id)
        new_value = max(current - decay_amount, decay_floor)
        # clamp
        min_val = self.config.get("affection_min", -100)
        max_val = self.config.get("affection_max", 100)
        new_value = max(min_val, min(max_val, int(new_value)))
        await self.db.set_affection(user_id, session_id, new_value)

        logger.debug(f"好感度衰减: {current} -> {new_value} (超时{overtime:.1f}小时)")
        return new_value

    async def _update_last_active(self, user_id: str, session_id: str):
        """更新最后活跃时间"""
        affection_initial = self.config.get("affection_initial", 0)
        await self.db.update_last_active(
            user_id, session_id, datetime.now().isoformat(), affection_initial
        )

    # ==================== 管理指令 ====================

    @filter.command_group("好感度")
    def affection_cmd(self):
        """好感度管理指令组"""

    @affection_cmd.command("查看")
    async def view_affection(self, event: AstrMessageEvent):
        """查看好感度"""
        # 速率限制检查
        group_id = event.get_group_id() or "private"
        if not self._check_rate_limit(group_id):
            yield event.plain_result("查询过于频繁，请稍后再试")
            return

        user_id = event.get_sender_id()
        session_id = self._get_session_id(event)
        affection = await self.db.get_affection(user_id, session_id)
        display = self._render_affection_display(affection)
        yield event.plain_result(f"你的好感度：{display}")

    @affection_cmd.command("设置")
    @filter.permission_type(filter.PermissionType.ADMIN)
    async def set_affection(self, event: AstrMessageEvent):
        """设置好感度（管理员）"""
        message_str = event.message_str.strip()
        parts = message_str.split()
        try:
            value = int(parts[-1])
            user_id = event.get_sender_id()
            session_id = self._get_session_id(event)
            nickname = event.get_sender_name()
            new_value = await self.db.set_affection(
                user_id, session_id, value, nickname
            )
            yield event.plain_result(f"好感度已设置为 {new_value}")
        except (ValueError, IndexError):
            yield event.plain_result("格式：/好感度 设置 <数值>")

    @affection_cmd.command("增加")
    @filter.permission_type(filter.PermissionType.ADMIN)
    async def add_affection(self, event: AstrMessageEvent):
        """增加好感度（管理员）"""
        message_str = event.message_str.strip()
        parts = message_str.split()
        try:
            delta = int(parts[-1])
            user_id = event.get_sender_id()
            session_id = self._get_session_id(event)
            nickname = event.get_sender_name()
            new_value = await self.db.adjust_affection(
                user_id, session_id, delta, nickname
            )
            yield event.plain_result(f"好感度 +{delta}，当前 {new_value}")
        except (ValueError, IndexError):
            yield event.plain_result("格式：/好感度 增加 <数值>")

    @affection_cmd.command("减少")
    @filter.permission_type(filter.PermissionType.ADMIN)
    async def sub_affection(self, event: AstrMessageEvent):
        """减少好感度（管理员）"""
        message_str = event.message_str.strip()
        parts = message_str.split()
        try:
            delta = int(parts[-1])
            user_id = event.get_sender_id()
            session_id = self._get_session_id(event)
            nickname = event.get_sender_name()
            new_value = await self.db.adjust_affection(
                user_id, session_id, -delta, nickname
            )
            yield event.plain_result(f"好感度 -{delta}，当前 {new_value}")
        except (ValueError, IndexError):
            yield event.plain_result("格式：/好感度 减少 <数值>")

    @affection_cmd.command("重置")
    @filter.permission_type(filter.PermissionType.ADMIN)
    async def reset_affection(self, event: AstrMessageEvent):
        """重置好感度（管理员）"""
        initial = self.config.get("affection_initial", 0)
        user_id = event.get_sender_id()
        session_id = self._get_session_id(event)
        nickname = event.get_sender_name()
        new_value = await self.db.set_affection(user_id, session_id, initial, nickname)
        yield event.plain_result(f"好感度已重置为 {new_value}")

    @affection_cmd.command("排行")
    @filter.event_message_type(filter.EventMessageType.GROUP_MESSAGE)
    async def rank_affection(self, event: AstrMessageEvent, count: int = 0):
        """好感度排行（群聊）"""
        group_id = event.get_group_id()
        if not group_id:
            yield event.plain_result("此指令仅在群聊中可用")
            return

        default_count = self.config.get("rank_default_count", 10)
        max_count = self.config.get("rank_max_count", 20)
        n = count if count > 0 else default_count
        n = min(n, max_count)

        ranking = await self.db.get_ranking(group_id, n)
        if not ranking:
            yield event.plain_result("暂无好感度数据")
            return

        # 获取头像 URL
        avatar_map = {}  # user_id -> avatar_url
        if event.get_platform_name() == "aiocqhttp":
            for entry in ranking:
                uid = entry["user_id"]
                try:
                    avatar_map[uid] = f"https://q1.qlogo.cn/g?b=qq&nk={uid}&s=640"
                except Exception:
                    avatar_map[uid] = ""

        # 构建排行数据
        rank_data = []
        for i, entry in enumerate(ranking):
            affection = entry["affection"]
            level = self._match_affection_rule(affection)
            rank_data.append(
                {
                    "rank": i + 1,
                    "user_id": entry["user_id"],
                    "nickname": entry["nickname"] or entry["user_id"],
                    "affection": affection,
                    "level": level,
                    "avatar": avatar_map.get(entry["user_id"], ""),
                }
            )

        # 获取群名
        group_name = await self._get_group_name(event, group_id) or group_id

        # HTML 模板渲染排行图片
        tmpl = """<!DOCTYPE html>
<html>
<head>
  <meta charset="UTF-8">
  <style>
    * { margin: 0; padding: 0; box-sizing: border-box; }
    body { width: fit-content; background: transparent; }
  </style>
</head>
<body>
<div style="width: 700px; font-family: 'Microsoft YaHei', sans-serif; background: linear-gradient(135deg, #1a1a2e 0%, #16213e 50%, #0f3460 100%); border-radius: 20px; padding: 40px; color: #e0e0e0;">
  <div style="text-align: center; margin-bottom: 28px;">
    <h2 style="margin: 0; font-size: 30px; color: #e94560;">{{ group_name }} 好感度排行</h2>
    <p style="margin: 8px 0 0; font-size: 15px; color: #888;">TOP {{ rank_data|length }}</p>
  </div>
  {% for item in rank_data %}
  <div style="display: flex; align-items: center; padding: 14px 20px; margin-bottom: 10px; background: rgba(255,255,255,0.06); border-radius: 14px; {% if item.rank <= 3 %}border: 1px solid rgba(233,69,96,0.3);{% endif %}">
    <div style="width: 40px; text-align: center; font-size: 25px; font-weight: bold; {% if item.rank == 1 %}color: #ffd700;{% elif item.rank == 2 %}color: #c0c0c0;{% elif item.rank == 3 %}color: #cd7f32;{% else %}color: #888;{% endif %}">
      {{ item.rank }}
    </div>
    {% if item.avatar %}
    <img src="{{ item.avatar }}" style="width: 48px; height: 48px; border-radius: 50%; margin: 0 16px; object-fit: cover;" />
    {% else %}
    <div style="width: 48px; height: 48px; border-radius: 50%; margin: 0 16px; background: #333; display: flex; align-items: center; justify-content: center; font-size: 20px; color: #666;">{{ item.nickname[0] }}</div>
    {% endif %}
    <div style="flex: 1; min-width: 0;">
      <div style="font-size: 16px; font-weight: 500; white-space: nowrap; overflow: hidden; text-overflow: ellipsis;">{{ item.nickname }}</div>
      <div style="font-size: 13px; color: #888; margin-top: 3px;">ID: {{ item.user_id }}</div>
    </div>
    <div style="text-align: right; margin-left: 16px;">
      <div style="font-size: 20px; font-weight: bold; {% if item.affection >= 0 %}color: #4ecca3;{% else %}color: #e94560;{% endif %}">{{ item.affection }}</div>
      <div style="font-size: 13px; color: #aaa; margin-top: 3px;">{{ item.level }}</div>
    </div>
  </div>
  {% endfor %}
</div>
</body>
</html>"""
        # viewport_width 匹配卡片 700px，viewport_height=1 配合 full_page 按内容高度裁剪
        # type=png 避免 JPEG quality=40 压缩造成文字模糊
        url = await self.html_render(
            tmpl,
            {"rank_data": rank_data, "group_name": group_name},
            options={"viewport_width": 700, "viewport_height": 1, "type": "png"},
        )
        yield event.image_result(url)

    def _check_rate_limit(self, group_id: str) -> bool:
        """检查查看指令速率限制"""
        window = self.config.get("view_rate_limit_window", 5)
        max_count = self.config.get("view_rate_limit_max", 3)

        now = datetime.now()
        info = self._rate_limit.get(group_id)

        if not info:
            self._rate_limit[group_id] = {"count": 1, "window_start": now}
            return True

        elapsed = (now - info["window_start"]).total_seconds() / 60
        if elapsed > window:
            self._rate_limit[group_id] = {"count": 1, "window_start": now}
            return True

        if info["count"] >= max_count:
            return False

        info["count"] += 1
        return True

    # ==================== Web API ====================

    async def _api_affections(self):
        """GET 查询好感度"""
        user_id = request.args.get("user_id", "")
        session_id = request.args.get("session_id", "")
        try:
            if user_id and session_id:
                affection = await self.db.get_affection(user_id, session_id)
                return jsonify(
                    {
                        "ok": True,
                        "data": {
                            "user_id": user_id,
                            "session_id": session_id,
                            "affection": affection,
                        },
                    }
                )
            elif user_id:
                records = await self.db.list_by_user(user_id)
            elif session_id:
                records = await self.db.list_by_session(session_id)
            else:
                return jsonify(
                    {"ok": False, "error": "需要提供 user_id 或 session_id"}
                ), 400
            # 为每条记录附加级别
            for r in records:
                r["level"] = self._match_affection_rule(r["affection"])
            return jsonify({"ok": True, "data": records})
        except Exception as e:
            logger.error(f"[InterstitialContext] API 查询失败: {e}")
            return jsonify({"ok": False, "error": str(e)}), 500

    async def _api_affections_create(self):
        """POST 添加好感度记录"""
        data = await request.get_json()
        user_id = data.get("user_id", "")
        session_id = data.get("session_id", "")
        affection = data.get("affection", 0)
        nickname = data.get("nickname", "")
        if not user_id or not session_id:
            return jsonify(
                {"ok": False, "error": "user_id 和 session_id 不能为空"}
            ), 400
        try:
            value = await self.db.set_affection(
                user_id, session_id, int(affection), nickname
            )
            return jsonify({"ok": True, "data": {"affection": value}})
        except Exception as e:
            logger.error(f"[InterstitialContext] API 添加失败: {e}")
            return jsonify({"ok": False, "error": str(e)}), 500

    async def _api_affections_update(self):
        """PUT 修改好感度"""
        data = await request.get_json()
        user_id = data.get("user_id", "")
        session_id = data.get("session_id", "")
        affection = data.get("affection")
        nickname = data.get("nickname", "")
        if not user_id or not session_id or affection is None:
            return jsonify(
                {"ok": False, "error": "user_id、session_id 和 affection 不能为空"}
            ), 400
        try:
            value = await self.db.set_affection(
                user_id, session_id, int(affection), nickname
            )
            return jsonify({"ok": True, "data": {"affection": value}})
        except Exception as e:
            logger.error(f"[InterstitialContext] API 修改失败: {e}")
            return jsonify({"ok": False, "error": str(e)}), 500

    async def _api_affections_delete(self):
        """DELETE 删除好感度记录"""
        user_id = request.args.get("user_id", "")
        session_id = request.args.get("session_id", "")
        if not user_id or not session_id:
            return jsonify(
                {"ok": False, "error": "user_id 和 session_id 不能为空"}
            ), 400
        try:
            ok = await self.db.delete_affection(user_id, session_id)
            return jsonify({"ok": ok})
        except Exception as e:
            logger.error(f"[InterstitialContext] API 删除失败: {e}")
            return jsonify({"ok": False, "error": str(e)}), 500

    async def _api_sessions(self):
        """GET 获取所有会话ID列表"""
        try:
            sessions = await self.db.list_sessions()
            return jsonify({"ok": True, "data": sessions})
        except Exception as e:
            logger.error(f"[InterstitialContext] API 获取会话列表失败: {e}")
            return jsonify({"ok": False, "error": str(e)}), 500

    async def _api_users(self):
        """GET 获取所有用户列表"""
        try:
            users = await self.db.list_users()
            return jsonify({"ok": True, "data": users})
        except Exception as e:
            logger.error(f"[InterstitialContext] API 获取用户列表失败: {e}")
            return jsonify({"ok": False, "error": str(e)}), 500

    # ==================== terminate ====================

    async def terminate(self):
        """插件卸载时调用"""
        await self.db.close()
