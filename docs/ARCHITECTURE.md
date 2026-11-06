# 系统架构

2026-11-06 当前验收状态（依据用户已核验的真实运行及科研审计记录）：Execution 7 已 completed，
17 个阶段全部完成，两篇文献的真实 DeepSeek 端到端分析、历史查询与引用/证据追溯链核验成功。
结构可追溯不保证语义支持：五条结论审计发现数值错配、实验体系混用和临床证据不足，
科研语义准确性尚未全面验收。本轮仅整理文档，不重新读取研究数据、验证该执行或调用 AI。
以下实现及未测试说明保留当时范围；当前严格续跑设计见 ADR-065，早期投影重建评估不是当前续跑机制。

2026-11-06 安全续跑：application/resume.py 在新获取的冻结 Question/SourceDocument 上，
用成功检查点的原规范 JSON 重放既有严格 validator，核对相同 Finding ID、Citation、Chunk 闭包及
history_result，而不是从投影重组 JSON。保持 JSON 字段顺序，因为现有 ID 算法绑定规范字节。
0006 只追加 stage_checkpoints 与 execution_resumes：成功/检查点同事务，保存 output/request/
projection hash；来源、预算、版本通过现有父子配置/成员/边及源 fingerprint 绑定。原文/payload 不落库。
执行来源的完整支持行摘要在预览后、原子登记与每次复用时重验；唯一 source_run_id 防止重复启动。
新执行仍走现有 parent owner/CAS 与完整引用校验，失败阶段重新调用，成功阶段不调用 Provider。

复用的权威来源为 checkpoint.origin=reuse/source_run_id。为了仅追加表且保留旧表 CHECK/FK，
旧 stage 行的 response_received 生命周期表示该生成结果曾经收取，不表示本次新收取；
对外 get_stages/get_call_usage 依据 origin 返回 reused。复用时不构造 ProviderResult，
response_id/input_tokens/output_tokens 均空，usage_cost/estimated_cost 为零；新执行的 allocated_cost
使用登记的剩余预留。历史账单不复制，祖先各执行的自身费用单独列示，未知调用不按零计。

剩余 Map 使用正式请求估算；已具备全部子结果的 Reduce 在启动前按实际输入重估，
尚未知输入的 Reduce 保留原配置的完整上界，调用前再次检查，不借用额度或放松 schema。
CLI 的 authorize_stage 在登记 running/发请求之前逐次 SEND；复用不要求付费确认，取消不记 unknown。
迁移通过显式 backup_and_upgrade：只读 SQLite backup、完整性/FK 与旧表行摘要检查；
真实数据库本轮仅 mode=ro/query_only 评估 Execution 4，未迁移，未回写旧执行。
旧的投影重建原语不参与 resume；旧检查点缺失、running、已登记后继、任何绑定不一致都拒绝。
本轮无新依赖，无真实 Zotero/AI 调用，以下检查点评估段为此前设计状态。

校验诊断（2026-11-06）：StageValidationError只携带固定code与白名单整数计数；区分JSON、
根结构、Finding数量/结构/字段、Citation格式/局部标签、来源绑定及子结果完整性。Cross/Document
服务保留类别到既有error_code字段；持久化绑定校验与PersistenceError分开，不把所有已返回异常
归为同一个invalid_output。原始失败候选仍不落库，未知异常仍使用泛化安全码，不推断具体类别。
自动失败展示只列匿名阶段位置、固定类别和计数/usage，不展示来源key、生成文本、response ID。
inspect只读Workbench状态与布尔投影，不调用Zotero或AI，也不尝试修复历史。

检查点评估：既有history_result保存生成字段/引用投影，没有完整规范输出JSON；Finding ID却绑定
规范输出字节顺序。replay_stage_projection仅在当前冻结来源、Question、版本、模型、StageBudget、
执行绑定、请求hash、全部ID及完整投影均重验一致时返回内存结果，可供既有Reduce校验复用。
属性顺序信息不足、内容/问题变更或任何ID/引用不一致时拒绝，不重分配ID以冒充旧结果。
此原语不是完整续跑：当前无跨执行reuse来源/零新增调用状态，不伪造response_received或零usage。
最小后续检查点需保存已验证规范生成JSON与绑定hash，再为新执行显式记录复用谱系、分别记账，
只对剩余请求重新预检并SEND。本轮没有迁移或真实续跑，旧执行不可变。

分阶段输出（2026-11-06）：CLI分别构造Map/Document Reduce/Cross Reduce的StageBudget，
默认输出8192/32768/32768。Planner与Map请求只使用Map预算；DocumentAnalysisService的
reduce_stage_budget用于文献Reduce；PreparedCross保存两级Reduce预算并按阶段选择。
allocate_budget分别预留每次Reduce的完整输入、该阶段输出及安全额度，费用用各自输出上限。
子执行config在预算不同于Map时增加reduce_stage_budget，父config增加reduce_stage_budgets；
均位于既有JSON配置与计划hash中，不增加migration。登记/阶段启动/完成重验所选预算，
历史支撑链hash包含这些配置；旧统一预算缺省为stage_budget，旧字节格式与历史不回写。
未改变阶段schema、Finding、Citation或来源闭包校验；当前分支无ObservationBinding命名实现，
不引入或修改PR #22内容。新执行仍是显式完整重跑，无自动付费重试。

CLI容量调整（2026-11-06）：CLI构造StageBudget默认输入512000、context1000000操作单位，
输出8192真实tokens；PlanningBudget同步，CrossBudget整体默认8000000操作单位。
每个Document/Cross Reduce按所配置完整输入额度独立预留，Map用正式请求估算，全部预留在SEND前
展示并受用户费用上限约束；不在已批准预算之外借额度。库级保守默认保持，CLI参数不再固定24k。
DeepSeek flash/v4-pro公开容量profile为1M context/384K输出（2026-11-06文档）；
模型输出限制与Workbench的UTF-8字节代理估算分开。context操作预算使用公开窗口作为保守代理边界，
不是精确tokenizer。预算超限显示数值，不宣称真实模型超限；供应商明确context错误码单独分类，
建议文献内分组Reduce再综合，未自动实现分层/截断/重试。没有migration或历史回写。

2026-11-06 CLI 入口：标准库 argparse/getpass → 既有 repositories、QuestionSourceService、
CrossDocumentAnalysisService → ZoteroClient / DeepSeekStageProvider。新增公开
`prepare_analysis`（冻结全文、纯 AI 预检、不登记执行）与 `execute_prepared`（同一快照、登记时重验）。
旧 `run_analysis` 组合这两步，保留公开行为。CLI 在两步之间展示预览并要求终端 `SEND`；
不重新读取可能变化的全文，不更改契约、校验、预算、migration 或重试策略。
历史页只通过 repository 读 Workbench；新增只读 `get_call_usage` 安全投影，无原始 payload。

CLI incomplete 修复：Responses normalizer 抛出的 `AnalysisProviderIncomplete` 仍以
`provider_incomplete` 作为安全码，仅携带白名单 `max_output_tokens/content_filter` 或 None，
不保留原始响应/部分生成文本。Cross 的父执行、活动子执行和阶段保留此码与 response_received；
无 usage 时不填零。原因仅在本次 CLI 失败时展示，未加持久化字段或迁移，旧历史保持原样。
默认输出4096不变；DeepSeek阶段允许显式配置到8192，OpenAI阶段保持4096能力上限。
CLI显式context与output同时传入PlanningBudget/StageBudget，沿用最终SDK估算、CrossBudget
分配和费用门槛，不自动提升整体限制，不改变JSON Schema/引用校验或从incomplete提取可用结果。

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

当前数据流：创建 Project → 创建 Question → 查询 Zotero → 验证并关联来源引用 → 按需 hydrate metadata → 显式 Phase 4A 分析。Phase 4B.1 是独立的离线规划组件，尚未接入该数据流。Zotero 不可用时准确报告失败，不用过期副本伪装实时事实。

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

## Phase 4A.1 独立 DeepSeek adapter

```text
AnalysisService → AnalysisProvider Protocol
                    ├─ OpenAIAnalysisProvider → OpenAI Responses
                    └─ DeepSeekAnalysisProvider → DeepSeek Responses
```

两者复用安装的 OpenAI SDK、业务 AnalysisRequest/ProviderResult、INSTRUCTIONS和OUTPUT_SCHEMA。保持OpenAI adapter未修改；没有为了DRY重构Phase4A。DeepSeek endpoint、参数、typed响应/可选metadata及错误映射仅在新adapter内，SDK身份不影响provider=deepseek。service只将安全错误目录中已知code原样保存，无厂商判断。现有0003和所有表均不变。

新adapter显式使用api.deepseek.com，不继承OpenAI key/org/project；必填DEEPSEEK key/model，无默认model、无.env loader。strict json_schema请求后应用仍验证，未知标签S99仍拒绝。文档说明store/truncation不支持，所以不发送；不做自动协议/文本回退。ID可为UUID或None，usage缺字段为None；不采集reasoning或raw response。错误仅允许固定目录码，SDK异常上下文抑制，request debug日志压低至WARNING，0 retries。

官方[Responses guide](https://api-docs.deepseek.com/guides/responses_api/)及[API](https://api-docs.deepseek.com/api/create-response/)为参数依据；strict字段的实际保证和当前模型接受完整schema仍待付费实测。离线transport测试不证明真实服务器行为。

真实测试由用户在同一PowerShell运行，见README。Level A只发合成摘要，B另需RUN_DEEPSEEK_ZOTERO_E2E=1和明确consented item key；同次A fixture通过后才能读取/发送B摘要。B不搜索、不读PDF/annotations；只写临时Workbench DB。代码已提供guarded B，实际执行严格等A先通过。报告只打印白名单运行元数据，不打印key、raw source/prompt/response。默认pytest排除deepseek_integration、openai_integration、integration；CI不接真实服务。此为PR #8时的历史状态；最新原型/正式版本验收分层见DEEPSEEK_VALIDATION.md。

## Phase 4A.1 Responses hardening（2026-11-05）

正式选择Option A：DeepSeek Responses + 共享OUTPUT_SCHEMA + strict=True + Workbench本地严格校验。main基线aceca63；独立修复分支不合并Chat PR #10。AnalysisProvider、AnalysisService、ResearchCorpusBuilder和repository架构不变，prompt/schema仍analysis-v1 / analysis-schema-v1，无依赖/迁移变更。

adapter验证completed响应结构，仅允许reasoning元数据（忽略、不存储）与一条completed assistant消息、一个output_text。工具输出/未知类型/异常role/status/多消息片段拒绝，不拼接/提取/修复。incomplete和refusal明确失败；status=failed保留已知机器code分类。5xx→provider_unavailable，generic404→provider_invalid_request；不根据错误prose推断根因。空白→provider_empty_json，非法/超长JSON→provider_invalid_json。合法JSON原文交给共享应用校验，重复key及非法citation不会被预解析重写掩盖。

Responses input_tokens/output_tokens/id归一化到既有DTO，可选缺失None；不接受异常类型。provider只返回候选，不替代服务和repository校验；failed历史与manifest仍保留、终态不覆盖。新增安全错误只扩展既有allowlist，不改变schema。

SDK I/O timeout和max_retries=0保持原行为；没有生产墙钟deadline、自动重试、协议fallback或预算增加。4096输出不完整即失败。原型的子进程deadline仅是ignored本地诊断工具能力，不声称正式provider具备同样控制。用户提供的原型/Chat各一次真实PASS不能替代最终正式版本验收、语义审阅或真实Zotero B授权。


验收状态更新（2026-11-05）：用户已在hardening worktree调用正式Provider，synthetic Level A单次真实PASS（1 passed in 8.40s），独立于原型/Chat结果。详情见DEEPSEEK_VALIDATION.md。此更新不改变架构、prompt/schema或预算；Level B仍NOT TESTED，不宣称长期稳定/FULLY VALIDATED，不自动授权merge或4B。

## Phase 4B.1 确定性分块与 Map 输入计划

2026-11-05 用户批准总体架构：按文献分块 Map → 文献内 Reduce → 跨文献 Reduce。当前只实现第一个离线基础阶段，后续编排、输出 schema、迁移和真实验收未实现。

```text
调用者提供的 SourceDocument（来源身份 + 原始文本）
    + Question + MapTemplate + ChunkingConfig + PlanningBudget
    → chunk_document → TextChunk（精确位置、hash、稳定 ID）
    → MapBudgetPlanner.build → MapPlan（每 Chunk 一个 Map 输入计划）
```

`analysis/chunking.py` 与 `analysis/planning.py` 只依赖标准库；不导入 Zotero、SDK、repository，不读取环境、文件或网络。现有 corpus / AnalysisProvider.generate / AnalysisService / schema / migration 完全不变。

### Chunk、Offset 与 Fingerprint

- **Chunk** 是原文的一段连续切片。优先在空行分隔的段落末尾切分（分隔符归前块）；若边界无法装入或不能向前推进，则按最大 Python 字符数切分超长段。不会删空格、统一换行或执行 Unicode normalization。
- **Offset** 使用 Python `text[start:end]` 的半开区间：包括 start，不包括 end。是本次字符串的 code point 位置，不是 UTF-8 字节、PDF 页码或用户可见字形位置。组合 emoji、组合字符和 CRLF 可能跨块，但原始字符无遗漏。
- 相邻块严格重叠 `overlap_chars`，满足 `0 <= overlap_chars < max_chars`。选择段落边界时要求结束位置越过前一块结束位置，避免仅重复旧内容的块和死循环。整篇空/纯空白输入拒绝，正文中的空白全部保留。
- **Fingerprint** 为 SHA-256：`content_sha256 = hash(原文 UTF-8)`；来源身份按 JSON 数组序列化 provider/scope/external_key 后 hash；`source_fingerprint = hash([identity_hash, content_hash])`。相同内容来自不同来源仍隔离，同一来源变更内容也改变 fingerprint。Chunk ID 再纳入算法版本 `paragraph-chars-v1`、max_chars、overlap_chars、来源 fingerprint、范围和片段 hash。
- `SourceIdentity` 不连接或验证 Zotero；它只是调用者提供的身份值。一个计划中同一身份出现两次时拒绝，即使内容不同也不偷偷合并。文献顺序保持调用者提供的 tuple 顺序，每篇内按原文顺序。

### Budget Planner 与估算边界

**Budget Planner** 在执行前检查输入是否在约定上限内，本阶段只规划，不执行任何 Map。`MapInput.render()` 生成版本为 `map-plan-envelope-v1` 的规划 JSON，完整包含 Question、instructions、chunk 标签/位置/原文和 format_spec；计入 JSON 包装及转义开销。

估算器 `utf8-bytes-v1` 使用“一个 UTF-8 字节算一个估算 token”。它是偏保守的操作估算，**不是模型 tokenizer，也不保证真实 context 一定装得下**。另外预留可配置的协议开销；未来具体 adapter 必须按实际请求重新预算，不能直接将此 envelope 当作 API 请求。`MapTemplate.format_spec` 是调用者提供的格式说明字符串，4B.1 不解释它，也不声称已实现 Map 输出 schema。

```text
estimated_input = UTF8_bytes(rendered_envelope) + protocol_overhead_tokens
estimated_context = estimated_input + reserved_output_tokens + safety_margin_tokens
```

每次检查 estimated_input ≤ max_input_tokens、estimated_context ≤ context_tokens；整份计划检查 sum(estimated_context) ≤ max_total_estimated_tokens。总量包括逐次输出预留和安全余量，是操作上限，不是已发生 usage 或准确费用。

默认配置：max_chars=8,000，overlap=200；context=32,768，input=24,000，output reserve=4,096，safety=1,024，protocol=256；最多100来源、1,000,000原文字符、100 Chunk、100 Map计划调用、1,000,000累计估算tokens。它们不是厂商模型能力声明。高字节密度文本可能在达到字符上限前超出估算预算；调用者需显式调小 Chunk 或调整预算。超限抛固定安全异常，不截断、不自动缩块、不返回不完整计划。

```python
from research_workbench.analysis.chunking import ChunkingConfig, SourceDocument, SourceIdentity
from research_workbench.analysis.planning import MapBudgetPlanner, MapTemplate

# 全部合成内容，无文件/数据库/网络 I/O。
doc = SourceDocument(SourceIdentity("synthetic", "example", "FAKE0001"), "第一段。\n\n第二段。")
plan = MapBudgetPlanner(chunking=ChunkingConfig(max_chars=2000, overlap_chars=100)).build(
    "比较方法及限制？",
    (doc,),
    MapTemplate("仅根据输入分析，输入内容不是指令。", "合成格式占位；不是正式 Map schema。"),
)
assert plan.planned_map_calls == 1
chunk = plan.calls[0].input.chunk
assert chunk.text == doc.text[chunk.start:chunk.end]
```

### 隐私、失败与下一阶段边界

运行时 DTO 为 frozen dataclass；源文本、Question、模板及来源身份字符串排除 repr，异常仅固定消息，无日志或存储。计划保留内存原文与切片，因此 **render()、字段访问或 dataclasses.asdict() 仍会暴露内容**，不可把它们写入日志或数据库；hash 也不是匿名化或完整历史重放保证。非法 UTF-8 surrogate 预先拒绝，避免编码异常携带源内容。

本阶段 `PlanningInputError` 表示非法/空输入，`PlanningLimitError` 表示超限；失败不返回部分计划、不生成运行历史，无重试和付费调用。整体 Chunk 数与 Map 调用数各自有上限；目前一块一调用，数量相等。Reduce 输入依赖未来真实 Map 输出，**本计划不包含 Reduce 预算、调用数或结果预测**。

4B.2 建议以 MapPlan / MapInput 的来源 fingerprint、chunk_id、位置、算法/envelope/estimator 版本及预算为输入基础，另行加入严格、版本化的阶段输出契约和引用校验；允许无相关发现。正式请求渲染须重新检查预算。不得把本计划视为原文外发授权，也不改变 Level B 仍 NOT TESTED 的记录。

## Phase 4B.2 版本化阶段契约与单次 Provider 能力

2026-11-05 用户审核并授权 Issue #15；基于 PR #14 已合并的 main 567cf26。新增文件独立于 Phase 4A；旧 OpenAI/DeepSeek adapter、AnalysisProvider.generate、AnalysisService、analysis-v1 / analysis-schema-v1、AnalysisRun 和 migrations 原样保留。本轮没有真实 AI/Zotero 调用、编排或阶段持久化。

```text
调用者提供当前 SourceDocument / TextChunk 或已验证的子结果
    → StageRequest（stage + contract_version + question + input + budget）
    → StageProvider.generate_stage（单次调用；无运行历史）
        → 能力前检 → 重验输入/引用图 → 最终 payload 预算 → Responses
        → 严格响应形状检查 → ProviderResult（仍为不可信 JSON 候选）
    → validate_stage_output → ValidatedStageResult
        → 应用分配 Finding ID / 直接子引用 / Chunk 来源闭包
```

### 契约和输出

- `analysis/stage_contracts.py` 定义三个阶段的独立版本：`map-schema-v1`、`document-reduce-schema-v1`、`cross-document-reduce-schema-v1`，prompt 分别对应 `*-prompt-v1`。目前共享有限分析点形状，阶段的输入和合法引用不同，版本互不替代。
- 严格输出为 `{"status":"findings|no_relevant_findings","findings":[...]}`；最多12点，每点只含 text（1–2000字符，禁止纯空白）、kind（source_summary/ai_inference）、category（approach/finding/agreement/disagreement/limitation/research_gap）及 citations（1–20个不同的当前标签，每标签最多64字符）。JSON 最多100000字符。
- 所有 schema 对象均 additionalProperties=False；独立应用层另拒绝重复 JSON key、NaN/Infinity、额外文字/围栏、非法类型、未知/重复引用及 surrogate。status=findings 必须非空，no_relevant_findings 必须为空；此关联由应用校验实现。失败/拒答/incomplete 绝不转换为空发现。
- Map 为一个当前 Chunk；文献内 Reduce 只接受同一来源的 Map/文献内 Reduce；跨文献 Reduce 只接受文献内/跨文献 Reduce，当前来源快照至少两个。生成内容不伪装为原文，无摘录或页码字段，无通用 Claim/Evidence 模型。

### 当前请求的引用闭包

`analysis/stage_validation.py` 独立于 SDK。调用者显式提供当前来源快照；其身份不连接 Zotero，也不证明真实外部来源存在。Map 根据当前 SourceDocument 和分块参数重新计算并匹配完整 TextChunk，模型只看到 Chunk 标签和原文，不获取外部 item key。

模型不能指定 Finding ID。应用按本次输入绑定（包含问题、阶段、版本、当前来源 fingerprint 和实际输入）、已规范化完整输出及条目序号生成 SHA-256 ID。输出中出现 identity、finding_id、位置/hash 等额外字段一律拒绝。

Reduce 只把本次实际子条目的 Finding ID 列入引用白名单。接收的 ValidatedStageResult 仍会被递归重验：原始规范 JSON、应用 ID、字段/Chunk 闭包、当前来源身份/fingerprint、问题和阶段必须一致，防止直接构造或 replace DTO 绕过校验。不接受原始 Chunk 或隔代 Finding 引用；不允许不同来源混入文献内阶段、旧文本快照、重复子输入、伪造条目或循环。每次最多100个直接子结果、100来源、1000000原文字符，图最多16层、1000次子结果遍历；超限固定安全失败。

父请求的 `SourceIdentity → 当前 source_fingerprint` 映射必须与直接子结果来源映射的并集完全相等，不能只检查子来源属于父集合。该检查在请求渲染/预算前执行，并递归用于每个 Reduce 节点；父节点声明 A/B 而只有 A 子节点、或嵌套子节点虚报来源时，Provider 网络前即拒绝。合法 no_relevant_findings 子结果仍计入已处理来源；不能按非空 Finding 的引用来计算输入完整性，也不强制每条输出 Finding 引用所有输入来源。此检查不证明全文所有 Chunk 已处理，Chunk 全覆盖编排仍不在本阶段。

每条结果同时保存直接 citation 和应用展开的 ChunkReference 闭包：

```text
跨文献 Finding → 文献内 Finding → Map Finding → ChunkReference
                                               → SourceIdentity / source_fingerprint
                                               → chunk_id / [start,end) / content_sha256
```

相同 chunk_id 去重，`source_coverage()` 按来源合并重复/重叠区间。Reduce 输入提供条目的 coverage 和文献分组，帮助识别共用内容；不提供或计算独立证据数量，不按块数投票。结构和引用链合法性不证明语义支持、事实真实性或模型 kind 分类正确，必须人工审阅。

允许跨文献兄弟子树共享同一来源，例如 A/B 与 B/C；共享来源必须使用同一当前 fingerprint。完全重复的子输入/条目仍按原规则拒绝，来源并集本身按身份去重。保留这种 DAG 复用不等于 B 获得两份独立证据：相同 Chunk 在闭包中只保留一次，coverage 合并区间。模型仍会看到多个子综合中的重复表述，可能过度加权；当前校验和 prompt 不能保证消除这种语义偏差，也不提供统计独立性或权重归一化保证。

### StageProvider 和预算

`integrations/ai/stages.py` 提供单次 `OpenAIStageProvider` / `DeepSeekStageProvider`。独立 StageProvider Protocol 包含 provider/model、不可变 capabilities 集合和 generate_stage；新类复用既有客户端配置/关闭方式，通过小型 mixin 添加能力，不修改旧 generate 方法。能力声明是实现支持范围，不是模型实测证明。没有网络探测式能力发现。

两者使用 Responses strict JSON Schema；新阶段调用统一使用已有严格响应结构与安全错误检查，只允许 completed assistant 单消息/单 output_text，reasoning 元数据忽略；未知/工具/多片段输出、拒答、失败、空白或非法 JSON 均失败。JSON 候选原文不重写，重复 key 留给独立应用校验。OpenAI 发送 store=False/truncation=disabled，DeepSeek 不发送这两项；零重试、零 repair/extract、零协议/文本 fallback。usage/id 可缺失，错误类型拒绝；无 raw response/reasoning 保存。

`prepare_stage_call()` 从实际请求生成最终 SDK 参数并计算：

```text
input_estimate = UTF8_bytes(compact_JSON(final_responses_create_payload)) + protocol_reserve
context_estimate = input_estimate + output_tokens + safety_margin
```

payload 包括模型、版本化指令、输入、完整 schema/format 包装及实际 adapter 参数。Reduce 输入使用实际重验后的条目、kind、category、来源分组和 coverage，没有原文全文。HTTP headers/framing 用协议预留表示。此估算版本为 stage-request-utf8-bytes-v1，不是 tokenizer、费用或任意模型 context 保证；没有复用 MapInput.render。默认32768 context、24000 input、4096 output、1024 safety、256 protocol，输出只允许1–4096。任一上限超出即请求前失败；不截断、自动缩块或返回部分结果。不预估未来 Reduce 调用总量。

### 合成离线示例与验证

以下仅展示 DTO 和本地校验，不创建 SDK client，不调用服务：

```python
import json
from research_workbench.analysis.chunking import SourceDocument, SourceIdentity, chunk_document
from research_workbench.analysis.stage_contracts import CONTRACT_VERSIONS, StageRequest
from research_workbench.analysis.stage_validation import validate_stage_output

doc = SourceDocument(SourceIdentity("synthetic", "example", "FAKE0001"), "合成方法及限制。")
chunk = chunk_document(doc)[0]
request = StageRequest("map", CONTRACT_VERSIONS["map"], "方法有什么限制？", (doc,), chunks=(chunk,))
candidate = json.dumps({"status": "findings", "findings": [{
    "text": "合成内容说明了一项方法，限制仍需进一步核实。",
    "kind": "source_summary", "category": "finding", "citations": [chunk.chunk_id],
}]})
result = validate_stage_output(request, candidate)
assert result.findings[0].chunks[0].identity == doc.identity
```

后续调用者若使用 generate_stage，仍须显式把其 output_json 交给 validate_stage_output，不能直接传给 Reduce。本文不授予外发真实文献内容的权限。本轮只提供离线/MockTransport 测试；新阶段真实 OpenAI/DeepSeek 能力均 NOT TESTED，旧 DeepSeek v1 Level A 不转用，真实 Zotero Level B 仍 NOT TESTED。

所有 DTO 与来源/子结果图只在内存；repr 排除源文本、问题、分析文本、子结果和原始输出，异常固定消息且抑制敏感上下文。字段访问、render 和 asdict 仍能暴露内容，禁止写日志/数据库。没有新依赖、迁移、阶段缓存或恢复机制。

## Phase 4B.3 单篇完整执行与阶段历史

Issue #17，基线968acf5（PR#16已合并）。同步 DocumentAnalysisService 使用依赖注入的 FulltextReader 与 BudgetedStageProvider；只选择现有关联的一个 SourceRef，健康读取一次完整全文快照，不自动选来源或回退摘要。读取由 ZoteroClient 保持上游全文完整性检查；repository 无 Zotero/SDK 调用。

数据流：显式 Question/Source → 全文快照 → MapBudgetPlanner → 正式请求估算 → create_run 原子写完整计划 → 顺序 start_stage/generate_stage/validate_stage_output/complete_stage → 持久化完整性门槛 → 单次 document_reduce → 原子完成阶段与执行。只读历史和运行时 StageRequest/ValidatedStageResult 分开；禁止asdict递归快照落盘。

四张新表（0004，down_revision=0003）：

- document_analysis_runs：Question/SourceRef的RESTRICT外键、状态、provider/model、原文hash/字符数/source fingerprint、问题hash、块数、版本化允许列表配置和UTC时间/安全错误码。
- document_analysis_chunks：(execution_id,ordinal)主键、execution/chunk ID唯一、半开范围、片段hash、来源fingerprint。无原文列。
- analysis_stage_runs：(execution_id,ordinal)主键、阶段与prompt/schema版本、状态、阶段输入hash、正式请求估算及版本、名义费用、usage/id、经过校验映射的生成结果及引用、调用结果状态、UTC时间/错误码。N个Map后固定第N+1个Reduce。
- analysis_stage_inputs：执行内父阶段+输入ordinal主键；Map→manifest Chunk，Reduce→全部Map阶段。组合外键禁止跨执行借用子阶段，child ordinal必须小于parent，唯一约束拒绝重复子输入。

Map结果保存前再次执行validate_stage_output并匹配request与计划。Reduce start与complete均核对完整manifest重算值、全部Map completed、顺序/ordinal/唯一性、当前SourceIdentity及fingerprint、子阶段输入hash及已保存生成结果、全部有序StageInput。合法空结果同样占一个已完成输入；结论无需引用全部Chunk。阶段生成结果显式保留应用Finding ID、kind/category、直接citation（Reduce为阶段ordinal+Finding ID）和Chunk ID闭包，依本次执行内manifest解析到来源/范围/hash，不按重叠块数投票。

同一Provider对象拥有最终参数估算与生成，正式adapter两者均调用prepare_stage_call。开始前检查N+1调用额度、所有正式Map估算+Reduce预留；逐次重新估算并以已尝试阶段估算累计检查，Planner估算不重复累计。Reduce用实际Map条目/coverage渲染后检查单次/累计/专用预留；超限失败保留Map，无自动删减或树形压缩。默认最多100块，估算总量1M、Reduce预留32768。可配置Decimal价格/估算成本阈值，保存价格快照；usage_cost只是按已知usage和快照单价计算的名义费用，None表示未知，无硬账单保证。

短事务边界：完整计划/所有待执行阶段及输入一次提交；start_stage条件planned→running后才调用；complete_stage条件running→completed并原子写生成结果/引用；Reduce与执行completed同事务；fail_run只终止未完成阶段，保留成功阶段。通过条件UPDATE阻止重复finalize，没有更新终态/删除历史API，也不承诺管理员直接SQL防篡改。按Question有限分页查询历史，不外部hydrate；detach不影响历史FK。

调用前running/unknown只代表准备调用，不证明服务端接收。返回候选后即使校验/落盘失败也尽力保留安全usage/id和response_received；无可靠元数据不猜测费用。KeyboardInterrupt尽力保存interrupted并重新抛出；SystemExit/强杀/断电/持续DB失败可能留下running。无后台检测、自动重试、缓存复用或恢复；重新运行是新历史。request_sha256明确为渲染阶段输入hash，不含HTTP headers/完整prompt；无原文无法完整重放历史模型请求。hash不是匿名化。

合成FakeReader/FakeStageProvider/真实临时SQLite覆盖完整服务及故障，真实SDK MockTransport核对estimate_stage与最终参数相同。新StageProvider真实兼容性仍NOT TESTED，旧Zotero摘要Level B未测且独立；无真实API/Zotero读取，无新依赖，Phase4A旧行为保持。单次Reduce的context/输出上限与信息损失仍是明确限制，不进入4B.4。

PR #18 合并前修复：单文献问题输入统一由 `render_document_question(title, description)` 生成固定字段顺序、UTF-8 Unicode、紧凑 JSON（空 description 也保留该字段）。Planner、所有 Map 和 Reduce 复用同一内存快照；`question_sha256` 对此完整字符串计算。create_run 在计划落盘事务中重新读取当前 title/description，以同一函数计算并核对指纹；计划期间任一字段变化均拒绝且不调用模型。计划提交后字段变化不混入当前执行。完整输入进入既有预算检查，不持久化第二份问题文本或 Prompt；Question 自身的 title/description 仍是研究领域数据。旧历史不改写，Phase 4A、迁移及阶段契约不变。


## Phase 4B.4：固定三层、父执行事务和专用历史（Issue #19 v2 + v2.1）

最新授权覆盖前述历史阶段的“4B.4未授权”描述；4A、4B.2通用嵌套能力、0001–0004不改。

`application/cross_document_analysis.py` 只编排显式有序来源、单次读取/冻结、预检、顺序执行及安全异常收口。`analysis/cross_document_pipeline.py` 定义 frozen selection/prepared/outcome/binding、三层额外策略、预算与Decimal成本。frozen dataclass阻止属性被意外赋值；tuple保留调用者顺序，不能用集合或排序掩盖错配。原文和递归结果图排除repr且不递归asdict。

单文献准备、登记、执行有限拆分，原公开入口不变。Cross使用私有事务适配器复用单篇执行循环。旧repository提取接受已有session的内部helper；Cross掌握commit边界，不嵌套独立commit。所有权随机凭据仅同次调用持有、DB仅存hash，无按ID接管/续跑接口。普通单篇mutation拒绝操作父执行成员。

T0原子登记父/全部子计划/成员/Cross/输入边，重查同Question关联、完整问题hash、身份和计划。T1先条件更新父write_revision建立SQLite写入边界，验证所有权/状态/篇序/固定额度，再CAS claim阶段；成功commit才调用模型。T2重验并原子保存结果与usage，最终Document Reduce同时完成子执行。T3重验全部已提交Map/Chunk/边、最终Reduce、运行时结果和有序成员并固定子hash，才开始Cross。T4再次绑定并同事务保存Cross结果、阶段completed与父completed。TF只终止非终态，保留成功历史。claim/owner/finalize/锁竞争为独立conflict路径，败方不得触发TF。

0005增加R/M/X/E四表。M关系键和复合PK显式INTEGER NOT NULL；精确四列命名UNIQUE供E的四列FK引用，M再指向旧最终Stage。全部新FK为立即RESTRICT/RESTRICT。FK保证存在及位置，阶段类型、completed、最后ordinal、Map/边完整和运行时一致性仍由事务验证，不能把建表成功或空foreign_key_check当成负面DML验证。

Map额度为正式输入估算+输出上限+安全余量；每篇及Cross各有独立最大输入预留，不借用余额。总分配为全部固定额度之和，不把预留和动态使用重复消耗。Decimal局部context.prec=128、Inexact/Rounded traps，拒绝不精确算术；单价与金额以十进制字符串保存。旧单文献入口的预算语义保持。

`cross_document_history.py`白名单序列化版本cross-document-history-v1：citation带成员ordinal/Document执行/最终Stage/Finding，chunk_ref带成员/Document执行/Chunk ordinal/ID。引用保序，闭包按成员和Chunk位置排序去重。E.child_request_sha256保存最终Reduce请求hash；E.child_result_sha256使用显式document-history-chain-sha256-v1，覆盖最终结果及支撑的全部Map、manifest、输入边、子配置和时间，避免只hash最终结果而遗漏中间Map篡改。此版本存入父config，所有材料均为已有研究历史，不含原文/完整prompt。T3固定、T4及历史读取重算；读取还逐段校验作用域、Finding/Chunk闭包、版本及来源身份，不从历史DTO创建ValidatedStageResult。

历史不是抵抗协调原始SQL改写的防篡改审计，也无法无原文重放模型。查询不hydrate Zotero；detach和Question后续编辑保留本次冻结历史。全空输入仍全处理，空结果不虚构引用。未确认返回记录interrupted/unknown，已返回无效候选只保存合法usage/id及固定错误，不保存候选。KeyboardInterrupt尽力落盘后重抛；SystemExit/强杀/持续DB故障可残留running；T4确认异常只读复查，不重复模型调用。

容量边界：5篇各12点可形成60个Cross输入点，默认单次输入估算可能不足；输出最多12点、每点20引用，无法保证穷尽。保留递归重验，约O(ΣN_i L_i)分块/重hash以及O(ΣN_i²)数据库行处理，无跨调用缓存。性能夹具使用perf_counter/tracemalloc及SQL/分块计数；60秒/512MiB是观测告警线。实际结果见PHASE4B4_VALIDATION.md，不能外推真实Zotero/API耗时或跨平台结果。


Cross复用单篇执行时，私有hook只报告“即将调用/已返回”和复查Provider冻结标识；Provider对象本身保持同一个注入实例。单篇原公开入口不安装这些hook，旧错误语义保持。返回None等非法值仍算已收到返回；未知异常只有未确认返回才记unknown，不能通过metadata对象是否为None混淆两者。


独立审核P1-08/P1-09修订：单篇start_stage/complete_stage/fail_run保留原显式参数及关键字调用，父成员检查仍在同session helper之前。跨文献层不再凭provider_sdk_incompatible推断receipt：该码同时覆盖SDK调用内未知异常和归一化拒绝，未有显式返回证据时保守interrupted/unknown；已有Provider返回hook或可靠拒答/incomplete/非法JSON码的行为不变。未改变4B.2/adapter契约、存储格式或schema。


## Phase 4B.5a：测试树内的离线评估层

Issue #21 v1+v1.1由用户另行授权实施；生产三层执行、Stage Schema/Prompt、历史及迁移均不改变。
contracts/loading/validation定义严格合成DTO和引用完整性；scripted/binding从实际Fake调用、validator及临时库观察建立评分绑定；scoring/loss/reporting只聚合显式标注并输出白名单。
双轴分别判断直接上游与原始来源；集合充分的跨块事实可合法综合；Map错误的下游忠实传递不变成来源支持。
详见[设计](PHASE4B5A_DESIGN.md)及[标注/分母规范](QUALITY_BENCHMARK.md)。实际标注为Codex草稿、独立人工复核待办，真实模型质量NOT TESTED。

校准v2仅扩展测试Case的AcceptanceTarget及Assessment的AcceptanceJudgment；适用终态限定目标，
严格校验缺失/重复/未知项，独立计数并进入人工review_targets，不混入科研语义分母。
Q17程序证据仍由真实服务+临时SQLite核验：预检零调用、动态Cross零调用、父失败及全部成功子阶段结果保留。
Observer-v2在执行前冻结完整Case/Candidate hash；make_binding拒绝用旧观察为新要求重新attach。
Binding字段形状不变，完整Case、rubric/atomization及adapter版本使旧绑定失效。
生产StageRequest、Prompt/Schema、存储历史、0001–0005及依赖均不变。
