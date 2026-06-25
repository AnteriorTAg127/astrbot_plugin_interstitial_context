# Changelog

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
