import random
import re
import ssl
from datetime import datetime

import aiohttp
import certifi
from quart import jsonify, request

import astrbot.api.message_components as Comp
from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.provider import LLMResponse, ProviderRequest
from astrbot.api.star import Context, Star, register
from astrbot.core.agent.message import TextPart

from .db import AffectionDB

PLUGIN_NAME = "astrbot_plugin_interstitial_context"


@register(PLUGIN_NAME, "AnteriorTAg127", "轻量上下文注入插件", "1.4.2")
class InterstitialContextPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config
        # 内存缓存
        self._inject_snapshot = {}  # {cache_key: {affection_range_key, time_segment, user_id}}
        self._freeze_state = {}  # {cache_key: freeze_start_datetime}
        self._rate_limit = {}  # {group_id: {count, window_start}}
        # 群聊维度：上一位「关系绑定」发话人，用于下一位非该用户发话时一次性注入去歧义提示
        # {group_id: {"user_id","nickname","relation_type","relation_desc"}}
        self._last_relation_speaker = {}
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
        self.context.register_web_api(
            f"/{PLUGIN_NAME}/freeze_list",
            self._api_freeze_list,
            ["GET"],
            "查询屏蔽列表",
        )
        self.context.register_web_api(
            f"/{PLUGIN_NAME}/freeze_list/add",
            self._api_freeze_list_add,
            ["POST"],
            "添加屏蔽",
        )
        self.context.register_web_api(
            f"/{PLUGIN_NAME}/freeze_list/remove",
            self._api_freeze_list_remove,
            ["POST"],
            "解除屏蔽",
        )
        self.context.register_web_api(
            f"/{PLUGIN_NAME}/relationships",
            self._api_relationships,
            ["GET"],
            "查询关系列表",
        )
        self.context.register_web_api(
            f"/{PLUGIN_NAME}/relationships/add",
            self._api_relationships_add,
            ["POST"],
            "添加关系",
        )
        self.context.register_web_api(
            f"/{PLUGIN_NAME}/relationships/unbind",
            self._api_relationships_unbind,
            ["POST"],
            "解绑关系",
        )
        self.context.register_web_api(
            f"/{PLUGIN_NAME}/relationship-types",
            self._api_relationship_types,
            ["GET"],
            "获取预设关系类型",
        )

    @filter.on_astrbot_loaded()
    async def on_loaded(self):
        """插件加载完成后初始化数据库并迁移旧数据"""
        await self.db.init_db()
        await self.db.migrate_from_kv(self.get_kv_data, self.delete_kv_data)
        # 根据开关动态卸载 LLM 工具，使关闭后对 LLM 完全不可见
        try:
            tool_mgr = self.context.get_llm_tool_manager()
            if not self.config.get("enable_mute", True):
                tool_mgr.remove_func("mute_user")
                logger.info("[InterstitialContext] enable_mute=false，已卸载 mute_user 工具")
            if not self.config.get("enable_relationship", True):
                tool_mgr.remove_func("bind_relationship")
                logger.info(
                    "[InterstitialContext] enable_relationship=false，已卸载 bind_relationship 工具"
                )
        except Exception as e:
            logger.warning(f"[InterstitialContext] 动态卸载 LLM 工具失败: {e}")

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

    def _format_relationship(
        self, template: str, user_id: str, nickname: str, rel: dict
    ) -> str:
        """格式化关系注入模板，格式化失败时回退原文本"""
        if not template:
            return ""
        try:
            return template.format(
                user_id=user_id,
                nickname=nickname or "",
                relation_type=rel.get("relation_type", ""),
                relation_desc=rel.get("relation_desc", ""),
            )
        except (KeyError, IndexError) as e:
            logger.warning(f"[InterstitialContext] 关系模板格式化失败: {e}，使用原文本")
            return template

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

    def _get_level_change_hint(self, affection: int) -> str:
        """获取当前好感度等级的语言变化模板"""
        rules = self.config.get("affection_rules", [])
        for rule in rules:
            range_min = rule.get("range_min", -100)
            range_max = rule.get("range_max", 100)
            if range_min <= affection <= range_max:
                template = rule.get("change_hint_template", "")
                if template:
                    display_text = rule.get("display_text", "")
                    return template.format(
                        affection=affection,
                        display_text=display_text,
                    )
                break
        return ""

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

        # 0. 屏蔽检查（最早执行，跳过所有后续处理）
        if self.config.get("enable_mute", True):
            mute_info = await self.db.check_muted(user_id, session_id)
            if mute_info:
                logger.info(
                    f"[InterstitialContext] {cache_key} 被屏蔽，原因: {mute_info.get('mute_reason', '')}"
                )
                event.stop_event()
                return

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

        # 3. 回复概率判定（低于激活阈值时按概率不回复；语气冷淡感由等级模板表达）
        activation_threshold = self.config.get("activation_threshold", 0)
        if affection < activation_threshold:
            should_reply = self._check_reply_probability(affection, cache_key)
            if not should_reply:
                logger.info(
                    f"[InterstitialContext] {cache_key} 好感度 {affection} 低于激活阈值 {activation_threshold}，回复概率判定不回复"
                )
                event.stop_event()
                return

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

        # 4a. 好感度变化提示
        hint = self.config.get("affection_change_hint", "")
        if hint:
            max_change = self.config.get("max_affection_change", 5)
            static_info += "\n" + hint.format(max_change=max_change)

        # 4b. 好感度等级语言模板注入（在 affection_change_hint 之后，沿用变更检测机制）
        # 仅当好感度等级变化时注入一次，同等级不重复注入；no_save 模式下每次都注入。
        injected_sections = []  # 用于本轮注入日志汇总
        affection_range_key_for_hint = self._get_affection_range_key(affection)
        snapshot_for_hint = self._inject_snapshot.get(cache_key, {})
        level_changed = (
            snapshot_for_hint.get("affection_range_key") != affection_range_key_for_hint
            or snapshot_for_hint.get("user_id") != user_id
        )
        if self.config.get("enable_affection_change_hint", True) and (level_changed or no_save):
            rule_hint = self._get_level_change_hint(affection)
            if rule_hint:
                static_info += "\n" + rule_hint
                injected_sections.append(("等级语言模板", rule_hint))

        # 4c. 关系描述注入（独立开关控制）
        # 私聊用 relationship_inject_template_private 注入到 system prompt
        # 群聊则在 step 5 跟随 inject_template 注入用户消息
        rel = None
        if self.config.get("enable_relationship", True):
            rel = await self.db.get_relationship(user_id)
            if rel and not group_id:
                priv_tpl = self.config.get(
                    "relationship_inject_template_private",
                    "[关系设定] 你与对方的关系为「{relation_type}」。{relation_desc}",
                )
                if priv_tpl:
                    rel_text = self._format_relationship(priv_tpl, user_id, nickname, rel)
                    if rel_text:
                        static_info += "\n" + rel_text
                        injected_sections.append(("关系设定/私聊", rel_text))
        if self.config.get("inject_system_prompt_position", "before") == "before":
            req.system_prompt = static_info + "\n" + req.system_prompt
        else:
            req.system_prompt += "\n" + static_info

        # 5. 动态信息注入用户消息（好感度、时间区间、群聊关系）
        affection_range_key = self._get_affection_range_key(affection)
        time_segment_key, time_segment_text = self._get_time_segment()
        # 群聊关系类型也纳入变更检测：关系变化时强制重新注入
        relation_key = rel.get("relation_type", "") if (rel and group_id) else ""

        snapshot = self._inject_snapshot.get(cache_key, {})
        changed = (
            snapshot.get("affection_range_key") != affection_range_key
            or snapshot.get("time_segment") != time_segment_key
            or snapshot.get("user_id") != user_id
            or snapshot.get("relation_key") != relation_key
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

            # 群聊关系：拼接到 inject_text 之后
            if rel and group_id:
                group_tpl = self.config.get(
                    "relationship_inject_template_group",
                    "<{nickname}与你的关系:{relation_type}>",
                )
                if group_tpl:
                    rel_text = self._format_relationship(group_tpl, user_id, nickname, rel)
                    if rel_text:
                        inject_text = inject_text + " " + rel_text

            # 关系切换去歧义：群聊里上一关系发话人 ≠ 当前发话人 → 注入一次提示，注入后立即清除
            # 该状态在 step 5 末尾（当前发话人本身是关系对象时）刷新
            if (
                group_id
                and self.config.get("enable_relationship", True)
                and self._last_relation_speaker.get(group_id)
                and self._last_relation_speaker[group_id].get("user_id") != user_id
            ):
                prev = self._last_relation_speaker[group_id]
                disambig_tpl = self.config.get("relationship_disambiguation_template", "")
                if disambig_tpl:
                    try:
                        disambig_text = disambig_tpl.format(
                            user_id=user_id,
                            nickname=nickname,
                            prev_user_id=prev.get("user_id", ""),
                            prev_nickname=prev.get("nickname", ""),
                            prev_relation_type=prev.get("relation_type", ""),
                            prev_relation_desc=prev.get("relation_desc", ""),
                        )
                    except (KeyError, IndexError) as e:
                        logger.warning(
                            f"[InterstitialContext] 关系去歧义模板格式化失败: {e}，使用原文本"
                        )
                        disambig_text = ""
                    if disambig_text:
                        inject_text = inject_text + " " + disambig_text
                        injected_sections.append(("关系去歧义", disambig_text))
                # 无论模板是否有效都清除，保证"只注入一次"
                self._last_relation_speaker.pop(group_id, None)

            part = TextPart(text=inject_text)
            if no_save:
                part.mark_as_temp()
            req.extra_user_content_parts.append(part)
            injected_sections.append(("上下文", inject_text))

            # 更新快照
            self._inject_snapshot[cache_key] = {
                "affection_range_key": affection_range_key,
                "time_segment": time_segment_key,
                "user_id": user_id,
                "relation_key": relation_key,
            }
        else:
            logger.debug(f"[InterstitialContext] {cache_key} 无变更，跳过注入")

        # 刷新"上一位关系发话人"（仅群聊、当前发话人本身是关系对象时）
        # 注意：此处放在 changed 分支外——A 多次连发时也持续刷新，避免昵称/描述滞后
        if group_id and rel and self.config.get("enable_relationship", True):
            self._last_relation_speaker[group_id] = {
                "user_id": user_id,
                "nickname": nickname,
                "relation_type": rel.get("relation_type", ""),
                "relation_desc": rel.get("relation_desc", ""),
            }

        # 汇总打印本轮注入内容（一条日志，便于排查）
        if injected_sections:
            parts_str = " | ".join(f"[{name}]{text}" for name, text in injected_sections)
            logger.info(f"[InterstitialContext] {cache_key} 注入 → {parts_str}")

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
        event.stop_event()
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
        event.stop_event()
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
        event.stop_event()
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
        event.stop_event()
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
        event.stop_event()
        initial = self.config.get("affection_initial", 0)
        user_id = event.get_sender_id()
        session_id = self._get_session_id(event)
        nickname = event.get_sender_name()
        new_value = await self.db.set_affection(user_id, session_id, initial, nickname)
        yield event.plain_result(f"好感度已重置为 {new_value}")

    @affection_cmd.command("解绑关系")
    async def unbind_relationship_cmd(self, event: AstrMessageEvent):
        """解绑关系（本人或管理员@他人）"""
        event.stop_event()
        # 受 enable_relationship 开关控制
        if not self.config.get("enable_relationship", True):
            yield event.plain_result("关系绑定功能已禁用")
            return

        sender_id = event.get_sender_id()
        # 检查是否有 @ 提及
        message_chain = event.get_messages()
        target_user_id = None
        for comp in message_chain:
            if isinstance(comp, Comp.At):
                target_user_id = str(comp.qq)
                break

        if target_user_id:
            # 带 @，需要管理员权限
            if not event.is_admin():
                yield event.plain_result("解绑他人的关系需要管理员权限")
                return
        else:
            # 不带 @，只能解绑自己
            target_user_id = sender_id

        ok = await self.db.unbind_relationship(target_user_id)
        if ok:
            if target_user_id == sender_id:
                yield event.plain_result("你的关系已解除")
            else:
                yield event.plain_result(f"用户 {target_user_id} 的关系已解除")
        else:
            yield event.plain_result(f"用户 {target_user_id} 没有绑定的关系")

    @affection_cmd.command("排行")
    @filter.event_message_type(filter.EventMessageType.GROUP_MESSAGE)
    async def rank_affection(self, event: AstrMessageEvent, count: int = 0):
        """好感度排行（群聊）"""
        event.stop_event()
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
        # 绕过 AstrBot 默认 quality=40，直接调 T2I API
        url = await self._t2i_rank_render(
            tmpl,
            {"rank_data": rank_data, "group_name": group_name},
        )
        yield event.image_result(url)

    async def _t2i_rank_render(self, tmpl: str, data: dict) -> str:
        """绕过 AstrBot render_custom_template，直接调 T2I API"""
        t2i_base = self.config.get(
            "t2i_endpoint", "http://192.168.0.47:8999/text2img"
        ).rstrip("/")
        payload = {
            "tmpl": tmpl,
            "json": True,
            "tmpldata": data,
            "options": {
                "full_page": True,
                "type": "png",
                "viewport_width": 700,
                "viewport_height": 1,
            },
        }
        ssl_context = ssl.create_default_context(cafile=certifi.where())
        connector = aiohttp.TCPConnector(ssl=ssl_context)
        async with aiohttp.ClientSession(trust_env=True, connector=connector) as session:
            async with session.post(
                f"{t2i_base}/generate", json=payload
            ) as resp:
                if resp.status != 200:
                    raise RuntimeError(f"T2I render failed: HTTP {resp.status}")
                ret = await resp.json()
                return f"{t2i_base}/{ret['data']['id']}"

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

    # ==================== Web API（屏蔽 & 关系） ====================

    async def _api_freeze_list(self):
        """GET 查询屏蔽列表（不传 session_id 时返回所有有效屏蔽，供管理面板使用）"""
        session_id = request.args.get("session_id", "")
        try:
            if session_id:
                records = await self.db.get_active_mutes_by_session(session_id)
            else:
                records = await self.db.list_all_active_mutes()
            return jsonify({"ok": True, "data": records})
        except Exception as e:
            logger.error(f"[InterstitialContext] API 查询屏蔽列表失败: {e}")
            return jsonify({"ok": False, "error": str(e)}), 500

    async def _api_freeze_list_remove(self):
        """POST 解除屏蔽"""
        data = await request.get_json()
        mute_id = data.get("id")
        if not mute_id:
            return jsonify({"ok": False, "error": "需要提供 id"}), 400
        try:
            ok = await self.db.remove_mute(int(mute_id))
            return jsonify({"ok": ok})
        except Exception as e:
            logger.error(f"[InterstitialContext] API 解除屏蔽失败: {e}")
            return jsonify({"ok": False, "error": str(e)}), 500

    async def _api_freeze_list_add(self):
        """POST 添加屏蔽"""
        data = await request.get_json()
        user_id = data.get("user_id", "")
        session_id = data.get("session_id", "")
        duration_minutes = data.get("duration_minutes", 60)
        reason = data.get("reason", "")
        muted_by = data.get("muted_by", "admin")
        if not user_id or not session_id:
            return jsonify({"ok": False, "error": "user_id 和 session_id 不能为空"}), 400
        try:
            mute_id = await self.db.add_mute(
                user_id=user_id,
                session_id=session_id,
                muted_by=muted_by,
                mute_reason=reason,
                duration_minutes=int(duration_minutes),
            )
            return jsonify({"ok": True, "data": {"id": mute_id}})
        except Exception as e:
            logger.error(f"[InterstitialContext] API 添加屏蔽失败: {e}")
            return jsonify({"ok": False, "error": str(e)}), 500

    async def _api_relationships(self):
        """GET 查询关系列表"""
        try:
            records = await self.db.list_relationships()
            return jsonify({"ok": True, "data": records})
        except Exception as e:
            logger.error(f"[InterstitialContext] API 查询关系列表失败: {e}")
            return jsonify({"ok": False, "error": str(e)}), 500

    async def _api_relationships_add(self):
        """POST 添加/编辑关系"""
        data = await request.get_json()
        user_id = data.get("user_id", "")
        relation_type = data.get("relation_type", "")
        relation_desc = data.get("relation_desc", "")
        bound_by = data.get("bound_by", "admin")
        if not user_id or not relation_type:
            return jsonify({"ok": False, "error": "user_id 和 relation_type 不能为空"}), 400
        try:
            ok = await self.db.bind_relationship(
                user_id=user_id,
                relation_type=relation_type,
                relation_desc=relation_desc,
                bound_by=bound_by,
            )
            return jsonify({"ok": ok})
        except Exception as e:
            logger.error(f"[InterstitialContext] API 添加关系失败: {e}")
            return jsonify({"ok": False, "error": str(e)}), 500

    async def _api_relationships_unbind(self):
        """POST 管理员解绑关系"""
        data = await request.get_json()
        user_id = data.get("user_id", "")
        if not user_id:
            return jsonify({"ok": False, "error": "需要提供 user_id"}), 400
        try:
            ok = await self.db.unbind_relationship(user_id)
            return jsonify({"ok": ok})
        except Exception as e:
            logger.error(f"[InterstitialContext] API 解绑关系失败: {e}")
            return jsonify({"ok": False, "error": str(e)}), 500

    async def _api_relationship_types(self):
        """GET 获取预设关系类型（从配置 relationship_type_templates 读取）"""
        try:
            templates = self.config.get("relationship_type_templates", []) or []
            data = []
            for t in templates:
                # 模板中可能含 __template_key 等元字段，仅提取 type/description
                if not isinstance(t, dict):
                    continue
                data.append(
                    {
                        "type": t.get("type", ""),
                        "description": t.get("description", ""),
                    }
                )
            return jsonify({"ok": True, "data": data})
        except Exception as e:
            logger.error(f"[InterstitialContext] API 获取关系类型失败: {e}")
            return jsonify({"ok": False, "error": str(e)}), 500

    # ==================== LLM 工具 ====================

    @filter.llm_tool(name="mute_user")
    async def mute_user_tool(
        self,
        event: AstrMessageEvent,
        user_id: str,
        duration_minutes: int,
        reason: str = "",
    ):
        """暂时屏蔽指定用户，在指定时间内该用户不会收到 bot 的回复。在哪个群聊调用就在哪个群屏蔽，私聊调用则在私聊屏蔽。

        Args:
            user_id(string): 被屏蔽用户的 ID
            duration_minutes(int): 屏蔽时长，单位分钟（最大 10080 = 7 天）
            reason(string): 屏蔽原因（可选）
        """
        if not self.config.get("enable_mute", True):
            return "屏蔽功能已禁用"

        session_id = self._get_session_id(event)

        # 检查被屏蔽用户好感度：必须低于阈值（说明 bot 已对该用户态度冷淡）
        target_affection = await self.db.get_affection(user_id, session_id)
        threshold = self.config.get("mute_affection_threshold", -50)
        if target_affection > threshold:
            return (
                f"无法屏蔽：用户 {user_id} 当前好感度({target_affection})高于屏蔽阈值({threshold})，"
                f"好感度必须降至{threshold}以下才能屏蔽。"
            )

        # 参数校验
        if duration_minutes <= 0:
            return "屏蔽时长必须大于 0 分钟"
        if duration_minutes > 10080:
            return "屏蔽时长不能超过 7 天（10080 分钟）"

        sender_id = event.get_sender_id()
        _ = await self.db.add_mute(
            user_id=user_id,
            session_id=session_id,
            muted_by=sender_id,
            mute_reason=reason,
            duration_minutes=duration_minutes,
        )
        logger.info(
            f"[InterstitialContext] {sender_id} 屏蔽用户 {user_id}({session_id}) "
            f"{duration_minutes}分钟，原因: {reason}"
        )
        return f"已屏蔽用户 {user_id}，时长 {duration_minutes} 分钟，原因：{reason or '无'}"

    @filter.llm_tool(name="bind_relationship")
    async def bind_relationship_tool(
        self,
        event: AstrMessageEvent,
        user_id: str,
        relation_type: str,
        relation_desc: str = "",
    ):
        """给指定用户绑定关系，绑定后该用户与 bot 的对话会体现关系设定。全局唯一，一个用户只能绑定一个关系。

        Args:
            user_id(string): 被绑定用户的 ID（全局唯一）
            relation_type(string): 关系类型名称（简短，如：师徒、主从、搭档、朋友、爱人），建议不超过 6 字，超过 12 字会被拒绝
            relation_desc(string): 关系描述文本（可选，不填则使用预设模板或默认描述），建议不超过 20 字，超过 35 字会被拒绝
        """
        if not self.config.get("enable_relationship", True):
            return "关系绑定功能已禁用"

        # 字数校验（仅校验 LLM 实际传入的内容；relation_desc 为空被预设模板回填的不参与校验）
        relation_type = (relation_type or "").strip()
        relation_desc_input = (relation_desc or "").strip()
        if not relation_type:
            return "无法绑定关系：relation_type 不能为空"

        type_hard = self.config.get("relationship_type_max_length_hard", 12)
        type_soft = self.config.get("relationship_type_max_length_soft", 6)
        desc_hard = self.config.get("relationship_desc_max_length_hard", 35)
        desc_soft = self.config.get("relationship_desc_max_length_soft", 20)

        # 硬限制：直接拒绝
        if type_hard > 0 and len(relation_type) > type_hard:
            return (
                f"无法绑定关系：relation_type 长度({len(relation_type)})超过硬上限({type_hard})，"
                f"关系类型应简短（如：师徒、朋友、亲密爱人），请勿将描述塞入类型字段"
            )
        if desc_hard > 0 and relation_desc_input and len(relation_desc_input) > desc_hard:
            return (
                f"无法绑定关系：relation_desc 长度({len(relation_desc_input)})超过硬上限({desc_hard})，"
                f"请精简描述后重试"
            )

        # 软限制：截断 + 在返回值中告知
        truncated_notes = []
        if type_soft > 0 and len(relation_type) > type_soft:
            original_len = len(relation_type)
            relation_type = relation_type[:type_soft]
            truncated_notes.append(
                f"relation_type 已从 {original_len} 字截断至 {type_soft} 字"
            )
        if desc_soft > 0 and relation_desc_input and len(relation_desc_input) > desc_soft:
            original_len = len(relation_desc_input)
            relation_desc_input = relation_desc_input[:desc_soft]
            truncated_notes.append(
                f"relation_desc 已从 {original_len} 字截断至 {desc_soft} 字"
            )
        # 把（可能被截断后的）输入回写，供后续 desc 回填逻辑使用
        relation_desc = relation_desc_input

        # 检查被绑定用户好感度：取该用户在当前会话的好感度，查不到则回退全局最高
        session_id = self._get_session_id(event)
        target_affection = await self.db.get_affection(user_id, session_id)
        if target_affection == 0:
            try:
                user_records = await self.db.list_by_user(user_id)
            except Exception:
                user_records = []
            if user_records:
                target_affection = max(
                    (r.get("affection", 0) for r in user_records), default=0
                )
        min_affection = self.config.get("relationship_min_affection", 30)
        if target_affection < min_affection:
            return (
                f"无法绑定关系：用户 {user_id} 当前好感度({target_affection})未达到绑定要求({min_affection})"
            )

        # 检查是否已有关系（一对一约束）
        existing = await self.db.get_relationship(user_id)
        if existing:
            return (
                f"用户 {user_id} 已有关系 [{existing['relation_type']}]，如需更换请先解绑"
            )

        # 未提供描述时，优先匹配预设模板，最后回退默认描述
        if not relation_desc:
            templates = self.config.get("relationship_type_templates", []) or []
            for t in templates:
                if isinstance(t, dict) and t.get("type") == relation_type:
                    relation_desc = t.get("description", "")
                    break
        if not relation_desc:
            relation_desc = f"你是用户的{relation_type}，请以{relation_type}的身份与对方交流。"

        bound_by = event.get_sender_id()
        ok = await self.db.bind_relationship(
            user_id=user_id,
            relation_type=relation_type,
            relation_desc=relation_desc,
            bound_by=bound_by,
        )
        if ok:
            msg = f"已为用户 {user_id} 绑定关系：[{relation_type}]"
            if truncated_notes:
                msg += "；注意：" + "；".join(truncated_notes)
            return msg
        return "绑定关系失败，请稍后重试"

    # ==================== terminate ====================

    async def terminate(self):
        """插件卸载时调用"""
        await self.db.close()
