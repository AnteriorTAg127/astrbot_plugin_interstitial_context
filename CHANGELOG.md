# Changelog

## [1.4.4] - 2026-07-01
### Fixed
- **群聊关系去歧义后语境未回切**：群聊中关系对象 A（如「主人」）→ 非关系对象 B 发言触发一次去歧义提示后，A 再次发言时因 `_inject_snapshot` 未过期（好感段/时间段/关系类型均未变）导致不重新注入，LLM 上下文里最后一条只剩「不是主人」的歧义提示，容易延续错误称呼语境。现触发去歧义时同时清除关系对象的 `_inject_snapshot`，其下次发话强制完整重注入身份/关系。仅影响 `inject_no_save=False`（持久化）配置。

### Changed
- **注入日志加字数统计**：`[InterstitialContext] {cache_key} 注入 → ...` 改为 `注入(N字) → ...`，`N` 为本轮所有注入段文本总字符数（按码点计数，一个汉字算 1），方便评估上下文预算占用。

## [1.4.3] - 2026-07-01
### Fixed
- **好感度等级语言模板错注入到 system_prompt**：等级语言模板（如「刚刚认识，有点害羞」）原先在静态信息阶段拼入 `system_prompt`，与其「随好感度等级变化」的性质不符。现统一改为随用户消息注入（`extra_user_content_parts`，沿用 `inject_no_save` 临时标记），私聊/群聊一致，不再出现在系统提示词中。`enable_affection_change_hint` 开关与「等级变化才注入一次 / no_save 每次注入」的行为保持不变。

## [1.4.2] - 2026-06-27
### Fixed
- **群聊关系称呼串扰**：群聊中前一位发话人是关系绑定对象（如「老婆」）时，下一位非关系对象发言不再被 LLM 误用同一称呼。新增「关系切换去歧义」机制，在群聊维度记录上一位关系发话人，当下一位非该用户的关系对象发言时一次性注入提示「当前发言人是 X，不是你的{关系}「Y」」，注入后立即重置，直到关系对象再次发言才会再触发。

### Added
- 配置项 `relationship_disambiguation_template`：去歧义提示模板，可用变量 `{user_id} {nickname} {prev_user_id} {prev_nickname} {prev_relation_type} {prev_relation_desc}`。默认值：`<注意：当前发言人是{nickname}({user_id})，不是你的{prev_relation_type}「{prev_nickname}」，请勿混淆称呼>`。留空即关闭该功能。复用 `enable_relationship` 总开关。
- **bind_relationship 字数约束（软+硬双层）**：
  - `relation_type` 软限默认 6 字、硬限默认 12 字
  - `relation_desc` 软限默认 20 字、硬限默认 35 字
  - 超软限：自动截断后照常写入，返回值告知 LLM
  - 超硬限：直接拒绝，不写入数据库
  - 仅校验 LLM 实际传入的内容，`relation_desc` 为空被预设模板回填的情况不参与校验
  - 新增配置项 `relationship_type_max_length_soft` / `relationship_type_max_length_hard` / `relationship_desc_max_length_soft` / `relationship_desc_max_length_hard`，置 0 关闭对应校验
  - 工具 docstring 同步告知 LLM 长度建议

## [1.4.1] - 2026-06-25
### Fixed
- **命令穿透 LLM**：所有好感度指令（查看/设置/增加/减少/重置/排行/解绑关系）添加 `event.stop_event()`，防止消息流到 LLM 阶段造成"插件回复 + LLM 回复"双份输出，也避免图片渲染与 LLM 文本并发发送导致的 QQ 内部超时（retcode=1200）
- **LLM 工具返回值泄漏到群聊**：`mute_user` / `bind_relationship` 从 `yield event.plain_result()` 改为 `return` 字符串，确保工具返回值仅对 LLM 可见
- **mute_user 好感度检查对象错误**：检查目标从 `sender_id`（调用者）修正为 `user_id`（被屏蔽用户）
- **mute_user 参数暴露**：移除不必要的 `session_id` 参数，改由 `event` 内部推导

## [1.4.0] - 2026-06-25
### Added
- **用户临时屏蔽（Mute）**：新增 LLM 工具 `mute_user`，调用者好感度低于 `mute_affection_threshold`（默认 -50）时可屏蔽指定用户，到期自动失效；被屏蔽用户的消息在 `on_llm_request` 早期即被 `stop_event()` 丢弃
- **好感度等级语言变化模板**：`affection_rules` 每条规则新增 `change_hint_template` 字段，等级变化时注入 system prompt（沿用变更检测，同等级不重复注入），支持 `{affection}` / `{display_text}` 占位符；预设五个等级均带默认模板
- **关系绑定**：新增 LLM 工具 `bind_relationship`，被绑定用户好感度达 `relationship_min_affection`（默认 30）才能绑定，关系基于 user_id 全局唯一（一对一）
- **关系注入模板**：新增 `relationship_inject_template` 配置项（支持 `{user_id}` / `{relation_type}` / `{relation_desc}`），默认 `[关系设定] 你与对方的关系为「{relation_type}」。{relation_desc}`
- **解绑指令**：`/好感度 解绑关系` — 本人可解除自己；管理员 `@某人` 可解除指定用户
- **预设关系类型配置**：新增 `relationship_type_templates`（默认含师徒/主从/朋友/搭档四种），LLM 未指定描述时按预设回填
- **独立功能开关**：`enable_mute` / `enable_relationship` / `enable_affection_change_hint`，关闭时对应 LLM 工具会被动态卸载，对 LLM 完全不可见
- **后台屏蔽管理标签页**：列出所有活跃屏蔽、显示剩余时间，支持手动添加 / 解除
- **后台关系管理标签页**：列出所有关系绑定，支持解绑；附带"预设关系类型"参考表
- **Web API**：`GET /freeze_list`（不传 session_id 时返回全部）、`POST /freeze_list/add` `/freeze_list/remove`、`GET /relationships` `/relationship-types`、`POST /relationships/add` `/relationships/unbind`

### Changed
- T2I 渲染端点默认值改为公共服务 `https://t2i.soulter.top/text2img`
- 后台前端按钮事件改用 `data-action` 单一分派，避免多个 listener 累积
- 关系/屏蔽时间字段在前端用 `toLocaleString()` 本地化显示
- 所有注入点（等级语言模板 / 关系设定 / 上下文）统一升级为 info 级日志，便于排查

### Removed (BREAKING)
- **移除 `cold_hint_template` 配置项**：低好感冷淡语气改由 `affection_rules.change_hint_template` 表达。已为预设等级提供默认模板，升级后无需手动配置即可保持原有冷淡风格。低于 `activation_threshold` 时的"概率不回复"行为保持不变。

### Database
- 新增表 `freeze_list`（id / user_id / session_id / muted_by / mute_reason / mute_start / mute_duration_minutes / is_active），含 session_id 与 user_id 两条索引
- 新增表 `relationship`（user_id PK / relation_type / relation_desc / bound_by / bound_at）

## [1.3.0] - 2026-06-20
### Added
- 好感度排行命令 `/好感度 排行 [N]`，群聊中显示精美排行图片（头像、昵称、好感度、级别）
- 前端管理页面（Dashboard Plugin Page），支持好感度增删改查
- 群名注入：系统提示词注入群名称和群号
- SQLite 存储（aiosqlite），替代 KV 存储，启动时自动迁移旧数据
- 排行配置项：rank_default_count、rank_max_count

### Changed
- 数据存储从 KV 迁移到 SQLite
- 群聊系统提示词从 `[当前对话:群聊{group_id}]` 改为 `[当前对话:群聊{群名}({群号})]`

## [1.1.0] - 2026-06-20
### Added
- 好感度系统：自管理，支持负数、可配置范围和初始值、范围段自定义显示
- LLM 驱动好感度变化：自动注入提示，解析 `<affection>±N</affection>` 标记
- 时间区间注入：分钟级颗粒度，格式完全自定义，去重不重复注入
- 变更注入机制：好感段/时间区间/用户变化时才注入上下文
- 回复概率系统：线性插值、冷淡提示注入、冻结与恢复机制
- 好感度衰减：超时后按速率衰减，有衰减下限
- 管理指令：`/好感度` 指令组（查看/设置/增加/减少/重置）
- 查看指令速率限制
