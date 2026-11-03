# 系统架构

## 分层与目标

```text
Zotero                 文献事实层
54yyyu/zotero-mcp       外部 Zotero 控制能力
Research Workbench     AI 研究认知层
```

目标形态为 Browser → Frontend → Python Backend；后端分别访问 Workbench SQLite 和 Zotero 集成适配器。数据库不是请求必须穿过的网络层。当前实现只读 Zotero Adapter，以及 Phase 2 的 Project/Question 本地持久化；没有 Web 应用、UI 或 AI Analysis。

```text
Browser → Frontend → Python Backend
                         ├─ Workbench SQLite（研究数据）
                         └─ Zotero Adapter
                              ├─ zotero-cli（外部进程）
                              └─ Zotero Local API → Zotero Client
Agent Client → zotero-mcp（MCP）→ 上游提供的 Zotero 能力
```

后端优先使用 zotero-cli / Zotero Local API；MCP 主要面向 ChatGPT、Claude 和其他 Agent Client。不 fork 上游，不重新实现 Bridge、PDF 管理、DOI/arXiv 导入、基础 Annotation、Tag、Collection 或全文提取。

## 已核实的上游差异

2026-11-03 阅读上游 main 文档：`zotero-cli` 和 `zotero-mcp` 同属 `zotero-mcp-server` 包；部分本地读取直接访问 `zotero.sqlite`，不能假设所有 CLI 调用都经过 Local API。

因此分别验证 Client、Local API、CLI；CLI 成功不能覆盖 API 失败。直接数据库访问若发生，必须明确标记为上游实现路径，不自行实现 SQLite 读取，不把它当作目标 API 链路已通过。main 文档不是已安装版本保证。

安装版为 0.15.2。Phase 1 显式使用 API backend 验证搜索、metadata 和附件关系；上游仍通过 LocalZoteroReader 定位本地 PDF 并使用自带提取能力，20 页完整读取通过。不是所有 PDF 内容都经 Local API 传输。Phase 1.5 已修复 launcher 环境问题，适配器每次对子进程强制本地 API 配置，Python 集成测试已通过。

来源：[上游说明](https://github.com/54yyyu/zotero-mcp/blob/main/docs/for-agents.md)、[包定义](https://github.com/54yyyu/zotero-mcp/blob/main/pyproject.toml)。

## 数据所有权

Zotero = bibliographic source of truth；Workbench SQLite = research-domain source of truth。Phase 2 实际仅创建 `projects` 与 `questions`，另有 Alembic 版本表。`source_links`、`ai_analyses` 是后续目标，尚未实现；任何文献 metadata 都不进入当前 schema。

来源引用可包含 source_id、zotero_item_key、project_id、question_id。后续 schema 需要明确 library 身份：item key 不应被假设为跨个人库与 group library 全局唯一。第一阶段不由此扩展多用户功能。

Phase 1.5 仅支持个人库 users/0，不暴露组库切换。ZoteroItem、ZoteroFulltext、ZoteroAnnotation 是不可变运行时 DTO，不进入 Workbench 数据库，没有持久化 metadata 镜像。批注 DTO 按安装版真实 JSON 结构设计，真实非空批注仍未验证。Phase 2 完全不访问或修改 ZoteroClient。

title、authors、DOI、journal、publication_date、abstract、PDF、attachments、annotations 等尽可能按需从 Zotero 获取。不持久化完整 metadata，也不导入整库。分析与来源定位属于研究数据；临时读取结果不转成长期镜像。若未来需要缓存，先记录一致性、失效与隐私决策。

## 数据流与故障边界

未来数据流：创建 Project → 创建 Question → 查询 Zotero → 选择来源引用 → 按需读取内容 → 生成并保存有来源标识的 AI Analysis。Zotero 不可用时准确报告失败，不用过期副本伪装实时事实。

Spike 诊断层为 Workbench、zotero-cli、zotero-mcp、Zotero Local API、Zotero Client。记录退出码、HTTP 状态、缺失依赖、空字段和附件可用性；无 PDF、摘要或 annotation 不等于接口故障，也不等于该能力已完成正向验收。

## Phase 1.5 已实现的集成边界

- `client.py`：search_items、get_item、get_fulltext、get_annotations、health_check；业务模块以后只使用此接口。
- `runner.py`：唯一 subprocess 入口；发现 executable、构造参数列表、shell=False、有限 timeout、UTF-8、双流捕获、退出码与 schema=1 JSON 校验。先显式 cli_path，再 ZOTERO_CLI_PATH，再 shutil.which 两种命令名称。没有硬编码用户目录。
- `models.py`：标准库 dataclass 轻量返回值，不引入运行时依赖。
- `errors.py`：缺少 CLI、命令失败、超时、解析错误和 Local API 不可用的领域错误；命令失败保留 stdout/stderr/returncode/code，上层不用处理 CalledProcessError。
- 截断全文不会作为成功返回；读取上游报告的全部页面后检查身份、页范围、字符数和连续页标记。允许真实空白页，但全部无提取文本时不宣称全文通过。该页标记依赖 0.15.2 输出格式，升级需重跑契约测试。
- Health 对 API 做无代理、无重定向的有限超时 GET，并独立验证 CLI config。Client 运行状态从成功 API 推断；API 失败时只标记无法独立确定进程。API/CLI 均成功才 healthy，一项失败 degraded，两项失败 unavailable。
- 适配器只暴露读取，搜索 query 使用独立参数并放在 `--` 后，避免被解释为 CLI 选项。个人库、本地 API、空写入凭据及上游支持的 `ZOTERO_LOCAL_WRITE=false` 由 runner 强制传递。没有自动安装、setup、重试、云端 metadata 回退或 Zotero 写入路径；本地 PDF 定位及可能的附件获取仍由上游工具管理。
- `last_stderr` 保留最近一次 CLI 进程的诊断，默认不日志输出；失败异常保留双流。诊断可能包含私人数据，实例不用于跨线程共享。

## 验证与替换

Zotero Adapter 自身仍使用标准库。Phase 2 的持久化模块增加 SQLAlchemy 2.x 与 Alembic；开发依赖为 pytest、Ruff，Hatchling 用于构建，uv.lock 固定解析版本。普通测试屏蔽未 mock 的进程和网络；Zotero integration 显式选择。数据库测试运行真实本地 SQLite 与 migration，但文件全部来自 pytest 临时目录。

本阶段本机 114 个普通测试通过，3 个 Zotero integration 默认排除；历史 Zotero 集成结果见 Spike 报告，不在 Phase 2 重跑。现有 Windows / Ubuntu CI 自动覆盖新增数据库和 migration 测试，无需 Zotero、网络服务或真实研究数据。将来替换 Zotero 集成路径应保持 ZoteroClient DTO 与错误边界，业务不感知 subprocess。

## Phase 2 持久化边界

```text
未来 UI / CLI → ProjectRepository / QuestionRepository
                         ↓
                  Database（事务 / session）
                         ↓
                  SQLAlchemy 2.x → SQLite
                  Alembic → schema 版本
```

- `domain/models.py`：不可变 Project、Question 与 ResearchStatus，不依赖 ORM 或 Zotero。
- `persistence/models.py`：内部 ProjectRow、QuestionRow 一对多映射，外键 RESTRICT，project_id 有索引。没有多对多、级联删除或文献字段。
- `persistence/repositories.py`：两个明确的 repository，内部每次操作独立 session/transaction；成功 commit，失败 rollback。返回领域值，不暴露 ORM session、延迟关系或 generic repository 框架。get 缺失返回 None，修改缺失实体抛 EntityNotFoundError。
- `persistence/database.py`：文件数据库生命周期、SQLite foreign_keys 连接配置、有限 busy timeout、显式 upgrade。Python 3.13 的 sqlite3 使用 autocommit=False；设置外键 PRAGMA 时暂时切换到自动提交，随后恢复，保证连接和迁移事务行为明确。
- `persistence/config.py`：显式文件路径 > WORKBENCH_DATABASE_PATH > 启动 cwd 下 data/research_workbench.db。不硬编码个人路径，不自动读取 .env 或用户 home 数据；测试始终覆盖为临时文件。通过 SQLAlchemy URL 对象传入路径，兼容空格、中文、#、%。
- `persistence/migrations/`：migration 与模板打包进 wheel，CLI 的 alembic.ini 指向同一环境。版本 0001 从空库创建正式 schema，不调用 create_all。已有非空且无 Alembic 记录的库会被拒绝，避免修改其他应用的数据。upgrade 幂等；实际升级发生在事务中。downgrade 属于删除 schema 的管理操作，不是研究对象 delete API，真实数据需先批准备份/恢复方案。

字段：Project 的 id/title/description/status/created_at/updated_at；Question 加 project_id。id 为本地整数主键，标题去除首尾空白后 1–300 字符，描述为空字符串亦合法。状态值通过 Python enum 和 SQLite CHECK 约束保持 active/archived；表层也约束非空标题。时间以 UTC 存储，返回 timezone-aware UTC datetime。更新时间由 repository/ORM 维护，未设计原始 SQL 写入接口或数据库触发器。

列表默认 active，按 id 分页；归档仅改变目标记录状态，不改变其他记录，也不物理删除。没有跨 repository 的批量事务、乐观锁或多用户并发规则；本阶段每个操作是一个事务。SQLAlchemy 错误映射为 PersistenceError，原始 cause 仅供受控排障，不默认输出包含研究内容的 SQL。

验证包括：完整 CRUD/归档、关系双向访问、外键实际拒绝孤立记录、父记录删除被限制、约束与回滚、新连接外键开关、UTC、重开文件持久性、不同临时库隔离、两份空库的 upgrade head、重复 upgrade 保留数据以及 migration/ORM 无差异。未创建真实开发库。

## 配置、依赖与安全

- 初期选择 Python 3.13；默认系统 Python 不等于项目解释器。SQLAlchemy/Alembic 等项目依赖安装到 `.venv`；Zotero CLI 保持独立 uv tool 环境，版本 0.15.2，不安装 extras。
- 标准库可以解决的诊断不新增依赖；zotero-mcp 为外部工具依赖，优先基础包，不安装 semantic/all 扩展。
- 回环 API 使用有限超时，不经系统代理、不自动回退云端。WORKBENCH_DATABASE_PATH 已由持久化模块读取，仍无 .env 自动加载器；数据库路径与 Zotero 配置相互独立。
- 不记录密钥或完整配置，不提交数据库、PDF、全文、私人文献标识和原始诊断输出。可提交报告只保留脱敏状态。
- 第一阶段 Zotero 业务只允许 GET 和经核实的只读 CLI；不执行 authorize-local、导入、编辑、删除或重建语义索引。用户本轮明确批准 `setup --no-claude --skip-semantic-search` 与 `install-skill`，仅用于本地连接配置与仓库 skill 安装，不授予 Zotero 写权限。
- 不引入微服务、Redis、消息队列、Kubernetes、多服务 Docker 或复杂云部署；随实际需求演进。
