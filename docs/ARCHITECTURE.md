# 系统架构

## 分层与目标

```text
Zotero                 文献事实层
54yyyu/zotero-mcp       外部 Zotero 控制能力
Research Workbench     AI 研究认知层
```

目标形态为 Browser → Frontend → Python Backend；后端分别访问 Workbench SQLite 和 Zotero 集成适配器。数据库不是请求必须穿过的网络层。当前实现只读 Zotero Adapter、Phase 2 的 Project/Question 持久化，以及 Phase 3 的来源关联应用服务；Phase 4A 增加同步结构化 AI 分析，没有 Web 应用或 UI。

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

Zotero = bibliographic source of truth；Workbench SQLite = research-domain source of truth。Phase 2 创建 `projects` 与 `questions`；Phase 3 的 migration `0002` 增加 `sources` 与 `question_sources`，另有 Alembic 版本表。Phase 4A 的 `analysis_runs` / `analysis_source_inputs` 保存研究分析与来源 fingerprint；任何文献 metadata 都不进入来源 schema。

来源引用可包含 source_id、zotero_item_key、project_id、question_id。后续 schema 需要明确 library 身份：item key 不应被假设为跨个人库与 group library 全局唯一。第一阶段不由此扩展多用户功能。

Phase 1.5 仅支持个人库 users/0，不暴露组库切换。ZoteroItem、ZoteroFulltext、ZoteroAnnotation 是不可变运行时 DTO，不进入 Workbench 数据库，没有持久化 metadata 镜像。批注 DTO 按安装版真实 JSON 结构设计，真实非空批注仍未验证。Phase 2 完全不访问或修改 ZoteroClient。

title、authors、DOI、journal、publication_date、abstract、PDF、attachments、annotations 等尽可能按需从 Zotero 获取。不持久化完整 metadata，也不导入整库。分析与来源定位属于研究数据；临时读取结果不转成长期镜像。若未来需要缓存，先记录一致性、失效与隐私决策。

## 数据流与故障边界

当前数据流：创建 Project → 创建 Question → 查询 Zotero → 验证并关联来源引用 → 按需 hydrate metadata。AI Analysis 是未来目标。Zotero 不可用时准确报告失败，不用过期副本伪装实时事实。

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

Phase 2 的 114 个普通测试现扩展至 Phase 3 的 161 个，4 个 Zotero integration 默认排除；本阶段仅显式运行新的 metadata 关联集成测试，历史全文证据不作为本轮新验证。现有 Windows / Ubuntu CI 自动覆盖新增数据库和 migration 测试，无需 Zotero、网络服务或真实研究数据。将来替换 Zotero 集成路径应保持 ZoteroClient DTO 与错误边界，业务不感知 subprocess。

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
- `persistence/models.py`：内部 ProjectRow、QuestionRow 一对多映射，外键 RESTRICT，project_id 有索引。Project/Question 自身没有多对多或级联删除；Phase 3 单独映射来源多对多。没有文献字段。
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

## Phase 3 应用与来源边界

```text
调用者 → QuestionSourceService → ZoteroClient → shell=False runner → zotero-cli
                 ↓                    ↓
        QuestionSourceRepository    内存 ZoteroItem（不持久化）
                 ↓
     sources + question_sources / SQLite
```

- SourceRef 字段严格为 id、provider、scope、external_key、created_at；当前 provider 约束 zotero。scope 为 library 身份，三元组唯一。repository 可保存明确的未来 scope，服务只 hydrate personal，其他 scope 返回 unsupported_scope，绝不向个人库查询同名 key。
- QuestionSource 为 question_id、source_id、created_at；组合主键去重，两个 RESTRICT 外键防孤立关系，source_id 独立索引用于反向访问，question_id 使用主键前缀索引。没有 bibliography 列、JSON payload、PDF 路径或内容 blob。
- attach 先只读确认 Question，随后在数据库事务外调用 ZoteroClient.get_item，校验身份和 bibliographic 类型。成功后 repository 在一个事务中复查 Question、upsert SourceRef、upsert 关系；任一步异常均回滚。不存在持有数据库锁等待 CLI 的操作。Zotero 外部删除仍可能发生在验证和 commit 之间；下次 hydrate 会如实标记。
- repositories 不导入 Zotero；应用服务是用户操作入口。返回 frozen dataclass，不暴露 ORM/session。SourceRepository 提供底层身份操作，不承诺外部来源已验证。
- 列表按 source_id 有限分页（默认 100），顺序实时 hydrate，无长期缓存；hydrate=False 完全离线。缺失/回收站 item → unresolved，命令/超时/CLI 缺失/API 不可用 → unavailable，解析或类型错误 → error。每项独立处理，保留引用；意外程序异常不吞掉。
- 安装版 CLI 0.15.2 对缺失 item 返回 exit=0、ok=true 和精确文本 `No item found with key: <key>`。adapter 仅识别完整匹配，映射 ZoteroItemNotFoundError；其他未知 prose 仍为 ParseError。回收站项同样映射 typed error，并携带 deleted 标记。这是兼容边界，不是根据空结果推断成功。
- detach 是用户明确批准的本地关系删除；不删除 SourceRef、不调用 Zotero。无关联引用保留，不自动清理。Project/Question 仍只归档。
- Alembic 新版 0002 的 down_revision 为 0001；不改旧 migration。测试验证空库到 head、0001 数据保留升级、重复升级、实际约束与 ORM/schema 无差异。CLI upgrade/current/check 使用包含中文、空格、#、% 的临时路径通过。降级仅属 schema 管理，不对真实数据库执行。

依赖沿用 SQLAlchemy/Alembic，无新增包。普通测试用 mock reader 与临时 SQLite，屏蔽未 mock 进程/网络；真实测试独立 integration 标记，只用临时 Workbench DB 读取已有 Zotero metadata，不读取全文或批注。未来 UI/AI/索引不在当前范围。

## Phase 4A 分析边界

```text
AnalysisService → QuestionRepository / QuestionSourceRepository
       ↓
ResearchCorpusBuilder → ZoteroClient（只读、当前内容）
       ↓
AnalysisRequest（内存，S1/S2，不含内部 DB 对象）
       ↓
AnalysisProvider Protocol → OpenAIAnalysisProvider → official Responses API
       ↓
严格 schema / citation validation → label 映射 SourceRef id
       ↓
AnalysisRepository → analysis_runs + analysis_source_inputs
```

AI SDK 仅在 integrations/ai/openai.py；core/application 不导入 SDK。analysis/contracts.py 定义 Protocol 与不可变临时 DTO；schema.py 统一 JSON Schema 及独立应用校验；corpus.py 管理模式、检索、预算和 fingerprint；application/analysis.py 编排短事务。repository 不访问 Zotero 或网络，不暴露 ORM session。无新增 service/queue/vector 系统。

0003 追加两表，旧 0001/0002 未修改。analysis_runs 是运行快照：question_id、status、provider/model、prompt/schema versions、结果 JSON、response ID、usage、UTC 时间、固定安全错误。question_id 索引/FK RESTRICT，CHECK 约束 running/completed/failed 与 result/completed_at/error 组合。analysis_source_inputs 组合主键 analysis/source，analysis/ordinal 唯一，source_id 索引，两个外键 RESTRICT；仅六列 analysis_id/source_id/ordinal/input_kind/content_sha256/input_chars。manifest 与 running run 原子创建，完成时条件更新 status=running；终态无 update/delete 接口。

输出模型使用 source_labels；持久化结果只保留映射后的 source_ids。JSON 的 required keys、额外字段、类型、空值、长度、集合上限、重复 JSON key/label 和未知 label 均独立校验。summary、主要分析点必须引用来源；next_questions 为生成的问题。结构与引用有效不代表语义正确、来源真支持结论，仍需人工审阅；没有 page-level evidence。

Corpus 默认 5 sources / 24,000 chars per source / 60,000 total，具体计算与调用见 README。读取 max_sources+1 个引用以检测超限，不受既有 repository 默认分页100的静默影响。hash 覆盖实际 source content 字符串的 UTF-8 bytes，包含 title/authors/选定输入种类与全文或摘要；label 位于外层，不进入 fingerprint。Question 和 instructions/schema 进入总预算，但不持久化原文；不能仅凭 hashes 完整重建旧请求，Zotero/Question 后续变化是明确限制。

SDK 使用 Responses text.format 的 strict json_schema、store=False、truncation=disabled、4096 output tokens；要求调用者配置支持的 model。无 model/个人 endpoint 硬编码，无 .env loader；key 只在 SDK 内存。官方 SDK lock 2.54.0，新增直接 openai 一项；间接依赖由 uv.lock 固定。HTTP 0 retries、repair 0 次，所有重跑显式创建新 run。Refusal/不完整或空结果失败，原始异常不持久化/日志输出；SDK debug request 日志抑制。store=False 是本次 API 参数，不是对 provider 所有数据保留政策的承诺。

preflight 失败不创建 run，指出安全 SourceRef id；provider/输出失败保留 failed run 与 manifest。保存结果失败独立映射 persistence_failed，数据库持续不可用时可能无法写 failed，保留 running；没有后台恢复/重试。所有 I/O 在 DB 事务外，manifest 表示准备调用时的输入，不证明失败请求已被外部收到。

验证：210 个离线测试（6 个真实测试默认排除）；含官方 SDK + httpx.MockTransport 契约、非法引用、失败脱敏、immutable 终态、rollback/FK/manifest、输入上限与全文 fallback。真实 Zotero abstracts + fake provider + 临时 DB 为 1 passed；OpenAI key/model 未配置，真实测试 1 skipped，NOT TESTED。新真实付费测试使用一个合成摘要，不调用 Zotero；普通 CI 不配置 key，paid 测试额外要求 RUN_OPENAI_INTEGRATION=1。真实全文/非空批注不在本轮验证范围。

官方依据：[Structured Outputs](https://developers.openai.com/api/docs/guides/structured-outputs?api-mode=responses)。SDK 参数同时核对锁定安装版；升级需重跑 transport 契约与 opt-in 集成。
