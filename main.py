import re
import random
from datetime import datetime

from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star, register
from astrbot.api import logger
from astrbot.api.provider import ProviderRequest, LLMResponse
from astrbot.core.agent.message import TextPart
from astrbot.api import AstrBotConfig


@register(
    "astrbot_plugin_interstitial_context", "AnteriorTAg127", "轻量上下文注入插件", "1.1.0"
)
class InterstitialContextPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config
        # 内存缓存
        self._inject_snapshot = {}  # {cache_key: {affection_range_key, time_segment, user_id}}
        self._freeze_state = {}  # {cache_key: freeze_start_datetime}
        self._rate_limit = {}  # {group_id: {count, window_start}}

    # ==================== 好感度管理 ====================

    def _get_cache_key(self, event: AstrMessageEvent) -> str:
        """获取缓存key，群聊用群ID+用户ID，私聊用用户ID"""
        group_id = event.get_group_id()
        user_id = event.get_sender_id()
        if group_id:
            return f"{group_id}:{user_id}"
        return f"private:{user_id}"

    async def _get_affection(self, event: AstrMessageEvent) -> int:
        """获取用户好感度"""
        key = f"affection:{self._get_cache_key(event)}"
        val = await self.get_kv_data(key, self.config.get("affection_initial", 0))
        return int(val)

    async def _set_affection(self, event: AstrMessageEvent, value: int) -> int:
        """设置用户好感度，返回 clamp 后的实际值"""
        min_val = self.config.get("affection_min", -100)
        max_val = self.config.get("affection_max", 100)
        value = max(min_val, min(max_val, value))
        key = f"affection:{self._get_cache_key(event)}"
        await self.put_kv_data(key, value)
        return value

    async def _adjust_affection(self, event: AstrMessageEvent, delta: int) -> int:
        """调整好感度，返回新值"""
        current = await self._get_affection(event)
        return await self._set_affection(event, current + delta)

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

        template = self.config.get(
            "time_format_template", "{start_h}:{start_m}-{end_h}:{end_m}"
        )
        formatted = template.format(
            start_h=start_h, start_m=f"{start_m:02d}", end_h=end_h, end_m=f"{end_m:02d}"
        )

        return (f"{segment_index}", formatted)

    # ==================== 变更注入（on_llm_request 钩子） ====================

    @filter.on_llm_request()
    async def on_llm_request(self, event: AstrMessageEvent, req: ProviderRequest):
        """LLM 请求前注入上下文"""
        cache_key = self._get_cache_key(event)
        user_id = event.get_sender_id()
        nickname = event.get_sender_name()

        # 1. 计算好感度衰减
        affection = await self._calculate_decay(event)

        # 2. 更新最后活跃时间（无论是否回复都更新，避免衰减持续累积）
        await self._update_last_active(event)

        # 3. 回复概率 + 冷淡提示（共享激活阈值）
        activation_threshold = self.config.get("activation_threshold", 0)
        if affection < activation_threshold:
            # 低于激活阈值：概率判定 + 冷淡提示
            should_reply = self._check_reply_probability(affection, cache_key)
            if not should_reply:
                logger.info(f"[InterstitialContext] {cache_key} 好感度 {affection} 低于激活阈值 {activation_threshold}，回复概率判定不回复")
                event.stop_event()
                return
            # 注入冷淡提示
            cold_hint = self.config.get("cold_hint_template", "")
            if cold_hint:
                req.extra_user_content_parts.append(
                    TextPart(text=cold_hint).mark_as_temp()
                )
                logger.debug(f"[InterstitialContext] 注入冷淡提示: {cold_hint}")

        # 4. 变更判定
        affection_range_key = self._get_affection_range_key(affection)
        time_segment_key, time_segment_text = self._get_time_segment()

        snapshot = self._inject_snapshot.get(cache_key, {})
        changed = (
            snapshot.get("affection_range_key") != affection_range_key
            or snapshot.get("time_segment") != time_segment_key
            or snapshot.get("user_id") != user_id
        )

        # 4. 注入上下文（变更时）
        if changed or not snapshot:
            affection_display = self._render_affection_display(affection)
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
            req.extra_user_content_parts.append(
                TextPart(text=inject_text).mark_as_temp()
            )
            logger.info(f"[InterstitialContext] 注入上下文: {inject_text}")

            # 更新快照
            self._inject_snapshot[cache_key] = {
                "affection_range_key": affection_range_key,
                "time_segment": time_segment_key,
                "user_id": user_id,
            }
        else:
            logger.debug(f"[InterstitialContext] {cache_key} 无变更，跳过注入")

        # 5. 注入好感度变化提示
        hint = self.config.get("affection_change_hint", "")
        if hint:
            max_change = self.config.get("max_affection_change", 5)
            hint = hint.format(max_change=max_change)
            req.extra_user_content_parts.append(TextPart(text=hint).mark_as_temp())

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
            new_affection = await self._adjust_affection(event, total_delta)
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

    async def _calculate_decay(self, event: AstrMessageEvent) -> int:
        """计算好感度衰减，返回衰减后的好感度"""
        if not self.config.get("enable_decay", True):
            return await self._get_affection(event)

        cache_key = self._get_cache_key(event)
        last_active_key = f"last_active:{cache_key}"
        last_active_str = await self.get_kv_data(last_active_key, None)

        if not last_active_str:
            return await self._get_affection(event)

        try:
            last_active = datetime.fromisoformat(last_active_str)
        except (ValueError, TypeError):
            return await self._get_affection(event)

        now = datetime.now()
        elapsed_hours = (now - last_active).total_seconds() / 3600
        decay_timeout = self.config.get("decay_timeout", 48.0)

        if elapsed_hours <= decay_timeout:
            return await self._get_affection(event)

        # 超时，计算衰减
        decay_rate = self.config.get("decay_rate", 1.0)
        decay_floor = self.config.get("decay_floor", -100)
        overtime = elapsed_hours - decay_timeout
        decay_amount = overtime * decay_rate

        current = await self._get_affection(event)
        new_value = max(current - decay_amount, decay_floor)
        new_value = await self._set_affection(event, int(new_value))

        logger.debug(f"好感度衰减: {current} -> {new_value} (超时{overtime:.1f}小时)")
        return new_value

    async def _update_last_active(self, event: AstrMessageEvent):
        """更新最后活跃时间"""
        cache_key = self._get_cache_key(event)
        last_active_key = f"last_active:{cache_key}"
        await self.put_kv_data(last_active_key, datetime.now().isoformat())

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

        affection = await self._get_affection(event)
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
            new_value = await self._set_affection(event, value)
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
            new_value = await self._adjust_affection(event, delta)
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
            new_value = await self._adjust_affection(event, -delta)
            yield event.plain_result(f"好感度 -{delta}，当前 {new_value}")
        except (ValueError, IndexError):
            yield event.plain_result("格式：/好感度 减少 <数值>")

    @affection_cmd.command("重置")
    @filter.permission_type(filter.PermissionType.ADMIN)
    async def reset_affection(self, event: AstrMessageEvent):
        """重置好感度（管理员）"""
        initial = self.config.get("affection_initial", 0)
        new_value = await self._set_affection(event, initial)
        yield event.plain_result(f"好感度已重置为 {new_value}")

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

    # ==================== terminate ====================

    async def terminate(self):
        """插件卸载时调用"""
        pass
