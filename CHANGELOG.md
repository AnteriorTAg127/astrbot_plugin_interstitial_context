# Changelog

## [1.5.3] - 2026-07-17
### Fixed
- **模板格式化崩溃（#2 #3 #12）**：`inject_template` / `inject_template_compact` / `affection_rules[].change_hint_template` 三处 `.format()` 缺少异常保护，管理员拼错占位符（如 `{nicknmae}`）会每条消息抛 `KeyError`、`on_llm_request` 崩溃、注入全线失效。现套用 `_format_relationship` 的 try/except 模式，格式化失败回退原文本并打 warning 日志。
- **Web API 非法请求体裸错误（#6）**：6 个 POST handler（添加/修改好感度、添加/解除屏蔽、添加/解绑关系）的 `request.get_json()` 原在 try/except 之外，非 JSON Content-Type 或坏 JSON 会抛 `AttributeError`/`BadRequest` 返回框架错误页。现 `get_json(silent=True) or {}` 移入 try，返回结构化 `{"ok":false,"error":"请求体必须是合法 JSON"}` 400。
- **添加关系静默覆盖（#10）**：POST `/relationships/add` 对已有关系用户直接 upsert 覆盖，丢失原 `bound_by`/`bound_at` 且无提示。现 `bind_relationship` 冲突时仅更新 `relation_type`/`relation_desc`、保留原 `bound_by`/`bound_at`；响应返回 `overwrote`/`previous` 使覆盖非静默。保留 upsert 语义以兼容管理面板「编辑关系」流程（app.js 复用 `/relationships/add` 做 add+edit）。
- **重复屏蔽记录（#8）**：`add_mute` 不查重，LLM 工具/Web API 重复调用产生多条活跃屏蔽，导致管理面板重复展示且 `remove_mute(id)` 仅清一条、其余永久活跃。现 `add_mute` 命中已有活跃屏蔽时刷新其信息并返回原 id。
- **用户列表昵称取字典序最大（#7）**：`list_users` 的 `MAX(nickname)` 按字典序返回，跨会话不同昵称时管理面板显示过时昵称。改用按 `last_active` 取最新昵称的相关子查询（利用既有列，无 schema 变更）。
- **空 `display_text` 渲染异常（#15）**：`_render_affection_display` 在管理员将某好感段 `display_text` 置空时返回 `""`（text）或 `"50（）"`（both）。现回退 `str(affection)`。仅影响 `/好感度 查看` 与排行榜展示，不影响 LLM 注入（注入始终用等级文字，PRD §5 C1）。
- **好感度过度衰减（#5）**：`_calculate_decay` 写好感度与 `_update_last_active` 刷活跃时间为独立事务，若前者成功后者失败，下条消息按陈旧 `last_active` 再次衰减。新增 `set_affection_and_last_active` 原子方法，衰减与活跃时间同事务提交。触发窗口窄（两次写之间 DB 异常）。
- **KV 迁移无幂等标记（#13）**：`migrate_from_kv` 成功后仅靠「KV 已删尽」隐式幂等，INSERT 提交后、KV 删尽前崩溃重跑可能用残留 KV 覆盖期间新数据。新增 `kv_migration_done` 标记文件，入口检查跳过。无 schema 变更。

### Changed
- **render_test.py 导入排序**：修复预存的 ruff I001（import 未分组排序）。

### Notes
- **review_01 核实**：经代码 + AstrBot 框架（CodeGraph）+ PRD 三重核实，review_01 的 15 项发现中 5 项为误报不改代码（#1 `system_prompt` 默认 `""`；#4/#9 asyncio 同步段原子；#14 `extra_user_content_parts` 为 `field(default_factory=list)`；#11 PRD §7.1 既定语义）。详见 `开发/v1.5.2/fix_plan_01.md`。
- 无 DB schema 变更，无配置迁移；修复均不触碰注入位置/内容，符合 PRD §3 注入统帅原则。

## [1.5.2] - 2026-07-14
### Fixed
- **默认 `inject_template` 补 `{user_id}`**：默认值由 `<{nickname}好感{affection_display}> <{time_segment}>` 改为 `<{user_id} {nickname}好感{affection_display}> <{time_segment}>`，与 PRD v1.5.0「`{user_id}` 必须保留」要求一致。仅影响新装默认值。

### Changed
- **澄清 `{affection_display}` 注入语义**：上下文注入的 `{affection_display}` 始终使用好感度等级文字（如「死党」）；`affection_display_mode`（number/text/both）仅影响 `/好感度 查看` 与排行榜展示，不影响注入。`_conf_schema.json` 相关 hint 同步说明。
- **注入日志重构**：拆为 system_prompt / user 消息两行分别打印；system 段补全 `[当前对话]`/`affection_change_hint`/`context_meta_hint`，并标注"已变动/未变动"；user 段含 mode 与原因标签（首次/变化/兜底/精简关闭/增量/跳过）；移除冗余的 `[等级语言模板]`/`[关系去歧义]` 重复打印与重复的 `[关系设定]` 前缀。
- **文档对齐**：README「注入内容一览」表按「不动→system_prompt、会变→user 消息、私聊群聊分离」原则重写；`change_hint_template` 位置修正为 user 消息；新增 `relationship_inject_template_group` 与 `relationship_disambiguation_template` 两行；关系注入 key 名由单数拆分为 `_private`/`_group`；CHANGELOG v1.4.0 条目加勘误；`_conf_schema.json` hint 更新；`开发/v1.4` 设计文档加 superseded 标注。

## [1.5.0] - 2026-07-01
### Added
- **精简注入档**：新增 `inject_template_compact` 配置项与 `enable_compact_inject` / `full_inject_interval` 开关，实现「完整档 / 精简档」双档轮转。默认精简模板 `<id={user_id}|{nickname}|{affection_display}|{relation_short}|{time_period}>`，样例下 78 字 → 30 字，**降本 61.5%**。
- **完整档触发条件**：首次发话、好感等级变化、关系类型变化、时间段变化、发话人变化，或距上次完整档满 `full_inject_interval` 轮（防遗忘兜底）。中间轮次走精简档。计数按 cache_key（群号:用户ID）独立，切换用户天然独立不受串扰。
- **`context_meta_hint` 元指令配置**：给 LLM 一次性说明用户消息前注入标签的含义。仅写入 system_prompt，**享受 provider 端 prompt caching**，摊销后基本零成本。可留空关闭。
- **精简模板变量**：`{relation_short}` = 有关系时为关系类型、无关系时为空串；`{time_period}` = 时段名（凌晨/上午/中午/下午/晚上）。`{user_id}` 保留（LLM 通过 id 精确锚定发话人）。

### Changed
- **`_get_time_segment` 返回三元组**：新增 `time_period` 分量，供精简模板使用。原返回值 `(segment_key, formatted)` → `(segment_key, formatted, time_period)`。仅内部接口变更，配置项与外部行为无影响。
- **注入日志加 mode 标记**：`注入(N字) → ...` → `注入(N字, mode=full) → ...` 或 `mode=compact`，方便观测双档收益。
- **关系去歧义拼接不受档位影响**：无论完整档还是精简档，触发去歧义时都会拼接一次提示；关系对象快照清空逻辑（v1.4.4 修复）保留。

### Backward compatibility
- 用户不修改任何配置：新版默认开启精简档，若不希望改变行为，可设 `enable_compact_inject=false` 或 `full_inject_interval=0` 完全回退到 v1.4.4 行为。
- 已有 `inject_template` / `relationship_inject_template_group` / `time_format_template` 完整档行为与 v1.4.4 一致。
- 无数据库 schema 变更，无迁移。

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
- **关系注入模板**：新增 `relationship_inject_template` 配置项（支持 `{user_id}` / `{relation_type}` / `{relation_desc}`），默认 `[关系设定] 你与对方的关系为「{relation_type}」。{relation_desc}`（注：实际为 `relationship_inject_template_private` + `relationship_inject_template_group` 两个 key，v1.5.2 勘误）
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
