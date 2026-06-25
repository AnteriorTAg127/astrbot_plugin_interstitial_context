# astrbot_plugin_interstitial_context

轻量上下文注入插件 — 根据好感度、时间区间、关系绑定向 LLM 注入动态上下文，并从回复中解析好感度变化；额外支持基于好感度的临时屏蔽与等级语言风格切换。

## 功能

### 上下文注入
- **好感度系统**：自管理好感度，支持负数、可配置范围和初始值、范围段自定义显示
- **LLM 驱动好感度变化**：自动注入提示让 LLM 用 `<affection>±N</affection>` 标记返回变化，插件解析并更新
- **时间区间注入**：分钟级颗粒度，格式完全自定义，同一区间不重复注入
- **变更注入**：好感段/时间区间/用户任一变化时才注入，最小化额外 token
- **好感度等级语言模板** *(v1.4.0)*：每个等级可配置 `change_hint_template`，等级变化时注入 system prompt，引导 LLM 按当前等级语言风格回复
- **关系绑定与注入** *(v1.4.0)*：LLM 可通过工具绑定关系（如师徒/主从），关系描述自动注入 system prompt

### 行为控制
- **回复概率**：线性插值计算，低好感时概率不回复 + 冷淡提示，冻结与恢复机制
- **好感度衰减**：超时后按速率衰减，有衰减下限
- **临时屏蔽** *(v1.4.0)*：好感度低于阈值时 LLM 可调用 `mute_user` 工具屏蔽用户，被屏蔽用户在指定时长内消息直接被 bot 忽略

### 管理与可视化
- **管理指令**：`/好感度` 指令组（查看/设置/增加/减少/重置/排行/解绑关系）
- **好感度排行**：群聊排行命令，T2I 渲染的精美图片（头像、昵称、好感度、级别）
- **前端管理页面**：Dashboard 多标签页 — 按用户/按会话查看 + 屏蔽管理 + 关系管理
- **群名注入**：系统提示词注入群名称和群号
- **SQLite 存储**：数据持久化使用 aiosqlite，启动时自动迁移旧 KV 数据

## 安装

在 AstrBot 插件市场搜索 `轻量上下文注入` 安装，或手动克隆到 `data/plugins/`。

## 指令

| 指令 | 参数 | 权限 | 说明 |
|------|-----|------|------|
| /好感度 查看 | - | 普通用户 | 查看好感度（有速率限制） |
| /好感度 设置 | 数值 | 管理员 | 设置好感度 |
| /好感度 增加 | 数值 | 管理员 | 增加好感度 |
| /好感度 减少 | 数值 | 管理员 | 减少好感度 |
| /好感度 重置 | - | 管理员 | 重置好感度为初始值 |
| /好感度 排行 | [N] | 普通用户（群聊） | 显示群好感度排行图 |
| /好感度 解绑关系 | [@某人] | 本人可解除自己；@某人需管理员 | 解除关系绑定 |

## LLM 工具

| 工具 | 说明 | 好感度限制 |
|------|------|----------|
| `mute_user` | LLM 自主屏蔽指定用户一段时间 | 调用者好感度 < `mute_affection_threshold`（默认 -50） |
| `bind_relationship` | LLM 自主与用户绑定关系 | 被绑定用户好感度 ≥ `relationship_min_affection`（默认 30） |

两个工具均可通过 `enable_mute` / `enable_relationship` 独立开关控制，关闭后从 LLM 工具列表中卸载，对 LLM 完全不可见。

## 配置

参考插件 WebUI 配置面板。主要配置项：

- 好感度范围（最小值/最大值/初始值）
- 好感规则（范围段、显示文字、**等级语言变化模板** `change_hint_template`）
- 好感度显示模式（纯数值/纯文字/数值+文字）
- 时间粒度与格式模板
- 上下文注入模板 `inject_template`（用户消息侧，含好感度与时间区间）
- 系统提示词注入位置（before / after）
- 好感度变化提示 `affection_change_hint`（向 LLM 解释 `<affection>±N</affection>` 协议）
- 回复概率（线性插值、激活阈值、冻结恢复）— 低好感的"冷淡感"通过 `change_hint_template` 表达，已无独立冷淡模板
- 好感度衰减（超时、速率、下限）
- 查看指令速率限制
- 排行默认显示人数、最大显示人数、T2I 渲染端点
- **v1.4.0**：屏蔽 / 关系 / 等级语言模板的独立开关及阈值
- **v1.4.0**：关系注入模板 `relationship_inject_template`（变量 `{user_id}` / `{relation_type}` / `{relation_desc}`）
- **v1.4.0**：预设关系类型模板列表 `relationship_type_templates`（type + description）

## 注入内容一览

所有注入文本都可在配置中自定义，运行时会打 info 级日志便于排查：

| 注入位置 | 模板配置 | 触发时机 |
|---------|--------|---------|
| system_prompt | （内置 `[当前对话:...]`） | 每次 LLM 请求 |
| system_prompt | `affection_change_hint` | 每次 LLM 请求 |
| system_prompt | `affection_rules[i].change_hint_template` | 好感度等级变化时（同级不重复） |
| system_prompt | `relationship_inject_template` | 用户已绑定关系时 |
| user 消息 | `inject_template` | 好感度段 / 时间区间 / 用户变化时 |

## 升级注意

**v1.3 → v1.4 不兼容变更**：移除了 `cold_hint_template` 配置项。低好感语气改由好感规则的 `change_hint_template` 字段承担。预设规则已带默认冷淡模板，升级用户无需手动配置即可保持原行为；如果你曾自定义过 `cold_hint_template`，请把内容迁移到对应等级（一般是好感度 -100~0 段）的 `change_hint_template`。

## 数据存储

数据库路径：`data/plugin_data/astrbot_plugin_interstitial_context/affection.db`

主要表：

| 表 | 用途 |
|----|------|
| `affection` | (user_id, session_id) → 好感度、昵称、最后活跃时间 |
| `session_info` | session_id → 会话名称（群名等） |
| `freeze_list` | 屏蔽记录（含到期时间、is_active） |
| `relationship` | user_id → 关系类型、关系描述、绑定者、绑定时间 |

## 支持

- [AstrBot Repo](https://github.com/AstrBotDevs/AstrBot)
- [AstrBot Plugin Development Docs](https://docs.astrbot.app/dev/star/plugin-new.html)
