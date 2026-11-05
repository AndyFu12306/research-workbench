# 架构决定

日期：2026-11-03。除注明暂定外，以下原则已接受；接受原则不代表已实现。

## ADR-001：Zotero 是唯一文献事实源

文献 metadata、PDF、附件、笔记与批注归 Zotero 管理。Workbench 提供研究认知层，避免重复建设与同步冲突。

## ADR-002：保存引用而非文献镜像

Workbench 保存研究对象及 Zotero item 引用，按需读取事实。后续为引用增加明确 library 身份，避免跨库 key 冲突。不复制完整 Zotero library。

## ADR-003：后端优先 CLI / Local API

优先复用 zotero-cli 与 Local API；MCP 留给 Agent Client。CLI 是进程依赖，应处理退出码、超时和结构化响应。Phase 1.5 已实现只读 ZoteroClient，后续业务不直接访问 subprocess。

## ADR-004：早期不 fork zotero-mcp

54yyyu/zotero-mcp 作为外部依赖，避免承担上游维护。只有验证出无法通过配置或上游协作解决的实际需求，才重新评估。

## ADR-005：第一阶段仅做只读 Spike

先验证环境、搜索、引用、metadata、abstract、PDF 全文、annotations。禁止 Zotero 写入与写权限授权；失败按层定位，不用 workaround 伪装成功。通过前不建设产品功能。

## ADR-006：Python + SQLite，逐阶段实现

初期 Python 后端与 SQLite，数据库仅保存研究数据。暂定使用本机已有 Python 3.13，降低新解释器与第三方依赖兼容性风险；实际兼容性以固定版本安装测试为准。PostgreSQL 和复杂基础设施按需求再评估。

## ADR-007：AI 与人工、原文分离

原始来源、用户观点、AI 内容和推断必须可辨识，重要结论逐步建立证据追溯，不将生成内容呈现为论文事实。

## ADR-008：CLI 与 Local API 分别验收

上游当前文档说明部分本地读取直接使用 zotero.sqlite。将 CLI 成功与 API 成功分开记录；不自行实现数据库适配或以此绕过失败。后端正式使用哪些读取路径，必须在固定发布版的 Spike 完成后明确，不默默改变目标链路。

## ADR-009：最小 Git 流程与隐私保护

稳定主分支名为 main；功能分支单一目的，提交前 review、验证和敏感信息检查。早期不建复杂 CI。不提交本地研究数据或秘密；外部写入先预览和明确批准，高风险 Git 操作需确认。

## ADR-010：Zotero CLI 使用独立 uv tool 环境

依用户 2026-11-03 明确要求，工具以 `uv tool install --python 3.13 zotero-mcp-server` 安装，不进入 Workbench 项目依赖。当前安装版 0.15.2，未请求 extras。产品未来自己的依赖仍使用项目虚拟环境。复现时固定已验证版本，不自动升级。

## ADR-011：显式验收 API，复用上游本地 PDF 能力

Phase 1 验证进程指定 API backend，实际完成 Local API 搜索、metadata、非空摘要和附件关系读取。全文复用上游本地 PDF 定位和文本提取，20 页完整读取通过；如实披露其本地数据库/文件路径，不声称 PDF 全部经 API 传输。非空批注尚未测试；启动器问题由 Phase 1.5 解决。核心读取成功不代表自动获准进入产品功能阶段。

## ADR-012：Windows launcher 修复属于安装环境而非适配器特殊分支

Phase 1.5 实测发现两个独立因素：旧 Codex 进程 PATH 不含用户工具 bin，导致 shutil.which 两种名称均返回 None；刷新为相同 PATH 后，PowerShell Get-Command / where 与 Python which 指向相同原生 PE .exe，但 Python 启动失败。

进一步检查 PE 中内嵌的 shebang 解释器路径指向 `%APPDATA%/uv/tools/.../Scripts/python.exe`。该路径在 PowerShell 中可见，在外部 Python 中不存在；setup-info 返回的实际解释器位于 Codex 的 `LocalCache/Roaming` 应用目录映射下，并且该实际解释器可直接启动。实际目录下的 CLI launcher 仍携带相同旧路径，也失败。因此问题是本次安装环境的 AppData 映射与 launcher 记录路径不一致，而不是 shell=False、命令引号或仅仅 executable 未使用绝对路径。

修复时将 UV_TOOL_DIR 指向用户 `.local/share/uv/tools`，然后 `uv tool install --force --python 3.13 zotero-mcp-server==0.15.2` 重新生成同版本环境及入口，并将 UV_TOOL_DIR 保存为用户环境变量，使未来 uv 管理同一环境。未手动删除原有环境，未修改 Zotero 数据、配置密钥或上游源码。

Python 随后以 shutil.which 发现的绝对 .exe、参数列表、shell=False 成功执行 config 和完整业务流程。适配器无需 Codex 名称、个人路径、PowerShell 包装、python -m 回退或 shell=True。其他 Windows 用户不必盲目应用本机修复；应先重复 PATH、文件可见性和解释器路径诊断。

## ADR-013：标准库 DTO、集中 runner 与有限错误边界

运行时使用标准库 dataclass，不为轻量 DTO 增加 Pydantic。client 只构造允许的读取命令，runner 是唯一进程层。使用 cli_path / ZOTERO_CLI_PATH / shutil.which 的发现顺序；显式覆盖必须为原生可执行绝对路径，不能默默回退。

每个进程有有限超时，UTF-8 严格解码，校验 exit、ok、schema 和响应命令。错误保留原始诊断但不自动记录私人内容。区分 executable、cli、cli_output 和 local_api 层。health 的 Client 状态从 API 推断，避免引入跨平台进程枚举依赖。

## ADR-014：截断、样本与阶段验收

get_fulltext 遇截断时调用上游 read 补读全部页面并验证覆盖，不将摘要或部分正文当全文。兼容正常空白页；不自行实现 PDF 提取。默认单元测试不调用 Zotero，真实测试用 integration marker 显式选择，真实 item key 通过环境变量提供。没有非空批注样本时标记 NOT TESTED WITH NON-EMPTY DATA，不为验收写入 Zotero、不阻塞 Phase 1.5。

开发依赖增加 pytest、Ruff，分别用于测试和代码检查；Hatchling 只做打包。所有产品运行逻辑仍无第三方依赖。Phase 1.5 结束先 review，保持 feature branch，不自动合并或进入数据库/UI/AI Analysis。

上述为 Phase 1.5 决定时的范围。PR #1 已由用户合并；Phase 2 由后续明确请求授权，以下决定取代“尚无数据库/运行时依赖”的旧阶段描述，仍禁止 Zotero 写入和后续产品功能。

## ADR-015：SQLite 是初始 Workbench 研究数据库

个人本地项目先使用 SQLite。默认文件在启动工作目录的 data/research_workbench.db，可通过 WORKBENCH_DATABASE_PATH 或构造参数覆盖，不使用个人硬编码路径。暂不引入跨平台目录依赖或 PostgreSQL。真实数据库、journal/WAL、临时输出均不提交；测试仅使用临时文件。

## ADR-016：SQLAlchemy 2.x 负责持久化，repository 隔离 session

原有项目没有 persistence stack。SQLAlchemy 提供声明式映射、关系、参数化查询和事务，避免在标准库 sqlite3 之上自行维护同等能力。没有功能重叠 ORM。当前 lock 固定 SQLAlchemy 2.1.6，依赖声明兼容 2.x。

只建立 ProjectRepository 和 QuestionRepository；每次操作独立 session/transaction，返回标准库不可变 dataclass 领域值，未来 UI/CLI 不直接操作 session。内部使用 SQLAlchemy Row 映射，不将 ORM 当作业务公共 API，不建立 generic repository 框架。SQL 错误包装为领域可用异常，原始诊断不自动写日志。时间在 SQLite 中为 UTC，返回值带 UTC 时区，避免数据库丢失时区造成误读。

## ADR-017：第一份正式 schema 就由 Alembic 管理

Alembic 与 SQLAlchemy 使用相同映射元数据，当前 lock 为 Alembic 1.20.1；不同时维护另一套自制 migration 工具。初始版本 0001 创建 projects、questions、CHECK、外键与索引。正式使用显式 upgrade，不以 Base.metadata.create_all 管理 schema。

migration 环境放在 persistence 包内，与 wheel 一起分发；根目录 alembic.ini 与程序化 Database.upgrade 使用相同文件。SQLite 事务和 foreign_keys 在每个连接明确配置；非空且没有 Alembic 版本记录的库拒绝迁移，不猜测其归属。降级会删除领域表，仅可在一次性测试数据或明确批准的备份方案下执行。

## ADR-018：首批持久化对象仅 Project 与 Question

两个对象都有本地整数主键、title、description、active/archived 状态及创建/更新时间；Question 只属于一个 Project，通过必填外键关联。标题限 1–300 字符，不加唯一约束。列表默认 active，明确支持归档查询和有限分页。archive 幂等且不级联，不提供硬删除、恢复状态、跨项目移动或复杂 workflow。关系本身不附加“归档父项目后禁止所有子操作”等未被要求的规则。

原路线中 Phase 3/4 的最小 Project/Question 模型纳入用户本轮指定的 Phase 2 一起交付，不另行重复实现。Source 与 AI Analysis 等对象仍未开始。

## ADR-019：文献事实与研究领域事实分开持久化

Zotero 是 bibliographic source of truth；Workbench SQLite 是 research-domain source of truth。数据库本阶段只有 projects、questions（加 Alembic 版本表），Project/Question 标题属于研究任务，不是文献 metadata 镜像。没有 paper title、authors、DOI、journal、abstract、PDF 或 Zotero 来源表。既有 ZoteroClient 不变，Phase 2 不调用 Zotero。

## ADR-020：来源身份采用 provider / scope / external_key

Phase 3 由用户明确授权，取代旧阶段“尚无 Source”的范围限制。SourceRef 仅持久化本地 id、provider、scope、external_key、created_at；三元组 UNIQUE 防止同一库重复来源，也防止不同 library 的同名 key 被混用。当前 provider 约束 zotero，服务 scope 固定 personal，其他 scope 不向个人库查询。scope 为未来身份预留，不代表已经支持 group library。标准库 frozen dataclass 仍为公共返回值，不增加 Pydantic 或 metadata 数据库。

## ADR-021：Question / Source 多对多，detach 只删除关系

question_sources 仅保存 question_id、source_id、created_at；组合主键直接实现关系唯一性，避免冗余唯一索引。两个 RESTRICT 外键阻止孤立关系；source_id 单独建索引。SQLAlchemy SQLite upsert 保证重复 attach 不新增记录，创建时间保留。用户本轮明确授权 detach，因此允许删除指定本地关联，但不删除 SourceRef、不清理孤立引用、更不删除 Zotero item；Project/Question 的 archive-only 规则不变。

## ADR-022：应用服务编排验证与运行时 hydrate

QuestionSourceService 先确认 Question，再通过 ZoteroClient 读取并校验文献，成功后 repository 单事务创建/复用 SourceRef 和关联，事务内复查 Question。失败不得留下部分引用。CLI 在事务外执行，避免持锁等待外部进程；Zotero 与 SQLite 不做分布式事务，之后来源被删除由 hydrate 如实报告。repository 不依赖 Zotero，低层身份接口不承诺远端验证；业务入口使用 service。metadata 只存在 LinkedSource 的内存 DTO，每次列表实时读取，无持久化镜像或缓存。

## ADR-023：来源失效和服务不可用都保留研究关系

resolved 携带当前内存 metadata；unresolved 表示 missing/deleted 或未支持的 scope；unavailable 表示 CLI/连接/超时问题；error 表示损坏响应等数据错误。列表逐项处理，单个来源失败不抹掉其引用或其他来源；hydrate=False 可离线列关系，detach 同样无需 Zotero。未预期程序异常不吞掉；attach 验证失败直接抛清晰领域错误且不写入。没有自动修复、移除或替换来源。

## ADR-024：严格兼容 CLI 的 missing-item 响应

实际只读探测与安装版源码确认，zotero-mcp-server 0.15.2 的 get metadata 对 missing key 返回成功 JSON 内的精确文本 `No item found with key: <key>`。仅完整匹配该响应映射 ZoteroItemNotFoundError，deleted metadata 映射同类错误并标记 deleted；其他未知文本仍是 ZoteroParseError。避免将缺失、连接失败与损坏响应混为一类。契约单测固定此行为；不 fork 上游、不引入 shell=True 或新依赖。

## ADR-025：追加 0002，不重写已合并 migration

0001 已进入 main，Phase 3 追加 down_revision=0001 的 0002_source_links。空库升级和已有 0001 数据升级走同一历史；升级重复执行安全，并验证研究数据保留和映射一致性。真实集成只读现有 Zotero metadata，Workbench 写入使用临时 SQLite；普通测试完全离线。没有读取 PDF、创建 Zotero 数据或对真实库执行 downgrade。

## ADR-026：AnalysisRun 是不可覆盖的研究历史

用户授权 Phase 4A。一次显式分析创建新 running run；完成或失败终态不可再次 finalize，条件 UPDATE 防止覆盖。保留版本、模型、usage、结果或固定错误，返回 frozen DTO。无 raw SQL 防篡改/审计或后台恢复承诺；崩溃可能留下 running。preflight 错误不创建 run。分析是 AI 生成研究数据，不是 Zotero 文献事实。

## ADR-027：独立 provider，首个 adapter 使用官方 Responses SDK

AnalysisProvider Protocol 只接受临时 AnalysisRequest 并返回 ProviderResult；业务不导入 OpenAI SDK，provider 不访问 Zotero。首个 adapter 用官方 Responses API + strict JSON schema，SDK 2.54.0，直接依赖 openai>=2,<3，uv.lock 固定间接依赖。标准库 HTTP 替代会增加协议维护，且用户要求官方 SDK；不增加 LLM framework。环境提供 API key/model/timeout，无默认 model、个人路径/endpoint 或 .env 自动加载。store=False、truncation=disabled，不承诺 provider 全局零保留政策。

## ADR-028：Prompt 与 schema 显式版本化并独立校验

analysis-v1 / analysis-schema-v1 独立常量，每个 run 保存。模型只看到 S1/S2 label 与必要内容，不暴露 DB 对象；只根据输入综合，区别共识/分歧/不确定，不虚构引用，源文本不可作为指令。JSON Schema 约束输出之后仍校验 required/type/bounds/unknown keys/duplicate keys/labels；未知 citation 拒绝，合法 label 映射 SourceRef IDs 后保存。lightweight attribution 不等于完整 Claim/Evidence 或语义正确性。

## ADR-029：Manifest 保存输入 fingerprint，不保存文献镜像

每个 run 的 manifest 记录 source_id、ordinal、input_kind、SHA-256 和字符数，不保存 title/authors/abstract/fulltext。hash 对实际 provider source content 字符串的 UTF-8 计算，其中包括用于上下文的 metadata 与输入种类，label 位于外层。manifest 与 running run 原子保存，可追踪失败尝试准备了什么，不保证 provider 已收到。没有原始内容和 Question 快照，无法完整重建旧 prompt；接受此隐私与最小模型边界。

## ADR-030：显式模式与严格 corpus/input budget

abstracts 仅当前摘要，fulltext_preferred 优先完整全文、缺失/不完整且 API 健康时明确 abstract_fallback。连接/超时/来源删除失败，没内容严格拒绝，不静默少一篇。默认最多5来源、每来源24,000字符、总请求60,000字符；全部为字符预算，不是模型 context 保证，超限明确报错，不截断。限制可调小，schema v1 不超过5来源；大 corpus chunking/map-reduce 留给4B。输出最多4096tokens，不完整拒绝。

## ADR-031：失败脱敏、零自动重试与显式付费测试

provider / schema 失败保存 fixed safe code/message，不保存 raw payload/exception、key 或完整 prompt，SDK debug request 日志抑制。数据库失败单独映射；不可写时可能无法落盘 failed，不误称成功。网络重试=0、输出repair=0，显式重跑新建历史，避免重复隐含费用。普通测试屏蔽网络/进程，真实 Zotero marker 与 paid openai_integration marker 均默认排除；key/model 缺失 paid test skip，不能视为 PASS。paid test仅一个合成摘要，CI不放个人key，不外发真实文献做验收。

## ADR-032：追加 0003 与持久化约束

0003 的 down_revision=0002，新建 analysis_runs / analysis_source_inputs，0001/0002 不变。外键 RESTRICT、manifest主键/顺序唯一、status/lifecycle/hash/usage CHECK，与现有SQLite事务和Alembic一致。仅临时库验证空库、逐版、已有0002及重复升级和映射一致性，不迁移真实开发库，不执行真实降级。

## ADR-033：DeepSeek 是独立的一等 provider

用户授权Phase4A.1。AnalysisProvider下保留OpenAI并增加DeepSeekAnalysisProvider，selection由composition注入，不把OpenAI类改成暗中访问另一厂商的通用类。既有AnalysisRequest/ProviderResult、analysis-schema-v1与immutable history不变，0001–0003不变；不是Phase4B。

## ADR-034：SDK复用不改变provider身份

复用已锁定官方OpenAI SDK 2.54.0，0新依赖，不新增requests或非官方SDK。DeepSeek显式endpoint为api.deepseek.com，使用Responses而非Chat Completions，运行记录provider=deepseek及显式环境model。key/model必填，无默认model/自动.env，不继承OpenAI凭据及account headers。

## ADR-035：差异仅限adapter，安全错误目录保持通用

官方DeepSeek文档支持Responses/json_schema，但store/truncation不支持；新adapter仅发送需要的参数及strict=true，不更改OpenAI行为。strict保证和实际schema接受仍需实测，应用验证与S99拒绝始终保留。optional usage/id缺失返回None，不猜值；不持久化reasoning/raw response。typed HTTP/机器code映射固定安全目录，不保存错误prose；service仅识别统一allowlist，无DeepSeek分支。未知400/404不猜精确根因，网络与输出重试均0。

## ADR-036：真实验证必须分级与显式opt-in

deepseek_integration默认与现有两个real markers一起排除。RUN_DEEPSEEK_INTEGRATION=1、key、model全部满足才可付费调用；用户在自己的同一PowerShell运行，Codex不需要知道key。Level A用1合成摘要经过AnalysisService；Level B同次必须先通过A，不用环境缓存flag伪造成功。代码提供gated B，但未通过A不会执行B的检索/外发。2条测试关闭flag均SKIP，不将fake或skip记作PASS。

## ADR-037：外发私人Zotero内容需单独授权

B额外要求RUN_DEEPSEEK_ZOTERO_E2E=1和用户明确选择的ZOTERO_TEST_ITEM_KEY；旧item配置不自动代表同意。无key/授权则skip，不随机搜索。只发送当前title/authors和abstract，禁止全文/annotations读取，只写临时Workbench DB。测试检查generated analysis、manifest/hash、usage/id与内容不落库；报告只含allowlisted运行元数据。A/B都实际PASS才能标DeepSeek FULLY VALIDATED，OpenAI验收仍独立。

## ADR-042：正式保留Responses，独立hardening PR

2026-11-05用户决定最终采用Option A：Responses + JSON Schema + strict=True + Workbench严格本地校验。PR #8已在main；PR #10的Chat实现保持独立未合并，不先merge再反向修改。两方案各有用户单次真实Level A PASS，Responses还完成minimal/strict-minimal/full-schema原型诊断。这足以支持当前协议选择，不证明长期可靠性或历史503根因。独立Issue #11/主分支worktree实现；不是再次选型或Phase4B。ADR-038至041位于未合并PR #10，本分支保留编号空间，不复制其Chat决策。

## ADR-043：Responses失败关闭与安全分类

共享OUTPUT_SCHEMA/INSTRUCTIONS及analysis-v1 / analysis-schema-v1保持；不引入Chat序列化指令、prompt后缀或service/repository版本转发。adapter只接受reasoning元数据加单条completed assistant/单个output_text；未知/工具输出、多消息/片段、异常角色/状态均拒绝。拒答、incomplete、异常结构与空白/非法JSON区分。预解析不重写原文；重复keys和citation仍由共享应用与repository校验。5xx及已知failed服务不可用code安全分类，generic404不推断永久不兼容；status=failed继续落fixed-code失败历史。usage/id可选缺失但类型异常拒绝。保留旧错误目录值以兼容旧history，无migration。

## ADR-044：原型证据不代替正式版本验收

用户真实Responses原型Level A：completed，10.494s（provider10.353s），1049/1397 tokens，4096预算，calls1/retries0，manifest/fingerprint/provenance/metadata/immutable/security通过。Chat单次A PASS仍保留。新正式hardening版本必须由用户另行显式运行单个synthetic A，普通测试/CI不使用真实凭据；真实Zotero B仍未测，必须另行同意及显式item key。历史503/timeout根因未严格证明，不宣称已永久恢复或FULLY VALIDATED。SDK逐I/O timeout不是墙钟deadline；预算4096不增加，不repair/extract/fallback/retry。


验收更新（2026-11-05，非架构变更）：用户通过hardening worktree的正式Provider完成独立Level A，**1 passed in 8.40s**，deepseek-flash / Responses / completed；1049/925 tokens，response-id存在，analysis-v1 / analysis-schema-v1，4096预算、calls1/retries0，immutable/manifest/security通过。正式Level A现为**单次真实PASS**；上述ADR中“须另行验收”为决策时门槛，现已满足。原型与Chat证据仍独立保留，历史503/timeout根因仍未严格证明。Level B仍NOT TESTED，workflow仍NOT FULLY VALIDATED；本次仅文档记录，不授权merge或4B。

## ADR-045：Phase 4B 按文献分层，首批仅交付离线分块与 Map 计划

2026-11-05 用户批准按文献 Map → 文献内 Reduce → 跨文献 Reduce，优于把所有文献块混在同一棵综合树中，可保留文献边界并降低长论文因块多而被重复加权的风险。仅授权 4B.1，Issue #13，从 main b3d36a0 建立独立 worktree / codex/phase4b-chunk-planning；旧 Chat / Responses worktree 不变。后续阶段需另行授权，旧 ADR 的4B未授权语句保留为历史范围，不覆盖本次授权。

优先段落边界，超长段按 Python 字符位置确定性切分；有限且精确的字符重叠保证推进，不采用模型分段或依赖 PDF 页标记。保留原始空格/换行，返回精确半开区间；不承诺页码或 grapheme 边界。来源身份、原文 hash、版本/参数/范围共同建立稳定 Chunk 身份；不建立全文缓存或通用 Claim/Evidence 系统。

## ADR-046：预算针对完整规划输入，估算与真实 token 明确分开

4B.1 使用标准库 UTF-8 字节估算，版本 utf8-bytes-v1；替代方案为模型 tokenizer，但当前增加厂商适配、版本和依赖维护成本，暂不引入。字节估算加完整 JSON 包装、Question/指令/格式材料和显式协议预留，另保留输出与安全余量；不等同精确计数或厂商 context 保证。

每调用输入/context、总来源/字符/Chunk/Map调用和累计估算量都受限，超限固定安全失败，不自动缩块/截断/重试。仅计划一块一 Map 调用，不预估尚未生成的 Reduce 动态输入；未来真实阶段 adapter 必须按正式契约重新预算。规划 DTO 只在内存且敏感字段不进入 repr，无日志/数据库写入。Phase4A provider.generate/service/schema/history、依赖和 migrations 不变。真实 Zotero Level B 仍 NOT TESTED，不因进入4B而获得外发授权。

## ADR-047：4B.2 使用独立阶段契约，保留 v1 生产行为

2026-11-05 用户审核架构并授权 Issue #15。PR #14 已合并；从 main 567cf26 建立独立 worktree / codex/phase4b-stage-contracts，保留全部旧 worktree。标准库 frozen dataclass 定义 StageRequest / StageBudget / StageFinding / ValidatedStageResult；map、document_reduce、cross_document_reduce 分别版本化 prompt 与 schema。共享有限的分析点形状，不建立通用 Claim/Evidence 模型。

输出只有 status/findings，分析点含 text/kind/category/citations；source_summary 与 ai_inference 明确区分。严格 JSON Schema 禁止额外字段并限制类型/数量/长度；独立应用校验另检查空结果状态一致性、重复 key/citation、非标准 JSON 及局部引用。状态关联与引用语义在应用层实现，避免依赖 provider 不一致的条件 schema 支持。no_relevant_findings 是合法、明确的分析结果，不能由异常转换得到。结构与来源链不证明语义真实性，模型的 kind 分类仍需人工审阅。

## ADR-048：引用仅限直接输入，ID 与证据闭包由应用生成

Map 只接收一个当前 Chunk，通过当前 SourceDocument 和 ChunkingConfig 重算分块，核对身份、源 hash、chunk_id、位置和片段 hash。模型只引用受信任标签，不能返回或覆盖身份、指纹、位置或 Finding ID。应用按输入绑定、规范输出及 ordinal 计算 SHA-256 Finding ID；它是稳定的运行时身份，不是持久化主键或匿名化保证。

Reduce 输入为已校验的子结果；不凭 DTO 类型直接信任，而是递归重放其 JSON 校验、ID 与 Chunk 闭包，核对当前来源快照、问题、允许的阶段及本次实际子条目。文献内阶段只接受 Map/文献内 Reduce，跨文献阶段只接受文献内/跨文献 Reduce。不允许引用祖孙捷径、其他请求、旧快照、重复子输入、重复 citation、伪造条目或循环。最多16层、1000次子结果遍历，超限失败；这不是多阶段执行编排。

结果保留直接 Finding 引用和通向 Chunk/SourceIdentity 的受信任闭包。相同 Chunk 去重，重叠区间按来源合并为 coverage，Reduce 请求也提供这些范围以识别共享内容；没有“独立证据数量”字段或加权投票。原文与生成内容始终分开。子结果原始规范 JSON、来源快照与引用图只在内存，供重验使用，不引入阶段历史或 DB schema。

合并前补充审核（2026-11-05，PR #16）：在 ca1b2d6 上，最小合成回归确认“父声明 A/B、实际只有 A 的 document_reduce 子结果、输出仅引用 A”未被拒绝。原校验只保证子来源属于父集合，缺少反向完整性。修复要求父 `SourceIdentity → 当前 fingerprint` 映射等于直接子结果来源映射的并集；保留逐子来源/版本检查以拒绝冲突，并在每层重放时执行，不能让虚报完整的中间节点掩盖遗漏。合法空结果子节点也代表来源已处理；输入完整性与输出引用范围分开，不要求每条结论引用所有文献，不声称检查了全文 Chunk 全覆盖。

兄弟跨文献子树可共享同一当前来源（如 A/B 与 B/C），保持原有 DAG 复用能力；不允许混用同一身份的不同 fingerprint。完全重复子输入/条目仍拒绝，相同 Chunk/重叠范围仅去重/合并，不计独立证据。模型可能因多个子综合重复表述同一来源而过度加权；结构校验和 prompt 不保证语义去重、统计独立性或权重归一化，需后续质量评估与人工审阅。本修复不修改阶段 schema/prompt、Phase 4A、持久化或依赖。

## ADR-049：独立 StageProvider 能力与严格单次 Responses 调用

StageProvider Protocol 的 generate_stage 返回未被信任的 ProviderResult JSON 候选，必须显式 validate_stage_output 后才可供 Reduce 使用。新增 OpenAIStageProvider / DeepSeekStageProvider 通过小型 mixin 复用原有客户端配置和生命周期，继承的 generate 完全不改；原有生产类、AnalysisService、v1 prompt/schema、immutable AnalysisRun、迁移与依赖均不改。没有为 DRY 大范围重构。

两家新阶段 adapter 均复用已存在的严格 Responses shape/metadata 检查和安全错误分类（目前位于 DeepSeek 模块，但逻辑不依赖厂商身份）。OpenAI 阶段调用保留 store=False/truncation=disabled，DeepSeek 不发送不支持的参数。各阶段/契约能力在网络前检查；不支持即失败，无 v1/Chat/普通文本 fallback，0网络重试、0输出修补。MockTransport 证明锁定 SDK 请求/响应契约，不证明真实模型已接受新 schema；旧 v1 Level A PASS 不能转记为阶段能力实测。

## ADR-050：预算计算针对最终 SDK 请求参数，保持估算边界

StageRequest 显式携带预算；从实际指令、实际 Map 原文或已重验的 Reduce 条目及 coverage、完整 JSON Schema、模型和参数形成最终 responses.create payload。对该 payload 的紧凑 JSON UTF-8 bytes 估算，再加入协议预留、输出预留和安全余量，分别检查输入/context。版本 stage-request-utf8-bytes-v1，不复用 4B.1 MapInput 估算，不声称精确 tokenizer 或真实 context 保证。HTTP headers/framing 由显式协议预留表示。

默认 context32768/input24000/output4096/safety1024/protocol256；阶段输出只允许显式调小至1–4096，不自动增大。真实输出超限/incomplete 仍失败。数量/文本上限与预算同时生效；Reduce 使用实际子结果，不预测未来动态输出或总 Reduce 成本。后续编排与全流程预算留给4B.3/4B.4，真实调用需另行 opt-in。repr/异常不含原文或生成内容；render/字段/asdict 仍含敏感数据，禁止写日志或 DB。

## ADR-051：4B.3采用完整单文献计划和单次Reduce

2026-11-05用户审核后授权Issue#17，从968acf5建立独立worktree/branch，保留全部旧工作。显式Question+已关联Source，只读一次符合ZoteroClient上游完整性检查的全文快照。复用4B.1规划、4B.2单次StageProvider与严格校验，新增独立DocumentAnalysisService，Phase4A不改。全部N个Map按序成功且已落盘后才允许一次Document Reduce，最多N+1调用；合法空结果计入完成。单次Reduce过大时明确失败并保留Map，不自动删块/截断/递归压缩/降级。跨文献与后续质量/真实验收仍需授权。

## ADR-052：0004独立保存执行/Chunk/阶段/输入关系

只追加0004（down_revision=0003），新增document_analysis_runs、document_analysis_chunks、analysis_stage_runs、analysis_stage_inputs；0001–0003、旧AnalysisRun/schema不改。复用SQLAlchemy Core与既有Base.metadata/Alembic，无新增依赖；schema不用create_all。完整计划先原子保存，引用按execution+stage作用域解释。运行时ValidatedStageResult含原文与递归图，禁止直接序列化；只保存严格重验的生成字段/直接引用/Chunk闭包和无内容manifest。允许不同历史有相同确定性Finding ID，不能全局查ID越过作用域。

## ADR-053：同一adapter预算，动态Reduce与显式费用未知

BudgetedStageProvider组合原StageProvider与纯估算能力，不改变原协议/单次方法；同一注入对象用相同最终参数prepare_stage_call估算及调用。Planner只规划且独立记版本，不与正式估算相加。预检N+1名额和全部Map+明确Reduce预留，逐次检查单次context/累计估算与可选成本阈值；未来Map输出未知，允许Reduce动态预算失败。Decimal价格由调用者显式提供并保存版本/币种/单价，不内置现价。估算费用与按已知usage计算的名义费用分开；缺价格或任一usage则费用未知，不把超时/失败算零，不承诺实际账单绝对上限。

## ADR-054：阶段成功原子落盘、终态不可覆盖、未知中断不猜测

Map完成、生成结果与引用原子提交；最终Reduce与执行completed同事务。每阶段完成需再次重验并绑定完整manifest和阶段输入，Reduce额外核对全部已持久化Map及顺序/指纹/结果。遇错即停，已完成阶段保留、未开始阶段明确not_started，终态条件UPDATE拒绝覆盖，显式重跑创建新执行。先写running再调用可在强杀时留下unknown；KeyboardInterrupt尽力记录interrupted后重新抛出。持久化持续不可用/强退允许残留running，不能自动判成功、失败或费用为零。无自动恢复worker/重试/缓存；历史仅可追溯，因不存原文/完整prompt不声称完整重放。仅离线Fake/Mock+临时库验收，真实阶段API和旧Level B仍NOT TESTED。


## ADR-055：4B.4有序显式选择与固定三层

用户正式授权Issue #19 v2 + v2.1，从main 41b1361建立独立分支/worktree。仅2–5篇已关联zotero/personal来源，冻结title+description及逐篇读取的完整全文，拒绝重复身份及不同身份同全文hash。Cross直接子只能是每篇最终Document Reduce，其直接子恰为该篇全部Map；编排层额外策略不收紧4B.2通用validator。原单文献公开行为保留。

## ADR-056：父所有权、短事务与0005精确成员FK

追加0005四表，0001–0004不变。T0原子登记所有计划；父owner hash及write_revision条件UPDATE串行化mutation，Stage CAS恰影响一行；普通单篇mutation不能绕过父门槛。E四列立即FK精确匹配M命名四列UNIQUE，所有关系键含复合PK显式INTEGER NOT NULL，FK全部RESTRICT/RESTRICT。事务再核对阶段类型/终态/最后位置/完整引用链。冲突败方不清理获胜者，无重试或续跑。

## ADR-057：逐调用固定额度与精确十进制

Map用正式预检值固定输入/context/费用，每篇与Cross分别按最大输入独立预留；不借用、不双算、不自动扩大或截断。三篇验收案例总198200、Decimal费用0.621680。Decimal局部128位精度且捕获Inexact/Rounded；不通过float或向下舍入获取通过。价格快照显式，无新依赖；名义usage与预算都不是实际账单保证，缺usage或未确认返回不计零费。

## ADR-058：专用Cross历史及支撑链摘要

cross-document-history-v1为白名单投影，保存精确成员/执行/阶段引用与有界Chunk闭包，不复用旧单篇格式。child_result_sha256明确版本document-history-chain-sha256-v1，绑定最终结果及全部支撑Map/manifest/边/配置；只hash最终结果无法检测中间Map文本等损坏。T3固定、T4及读取重验，查询逐段严格校验链和闭包，不全局借Finding ID、不将历史DTO伪装成运行时结果。生成历史可能敏感；hash不是匿名化、无原文不能重放、不承诺抵抗全库协调SQL篡改。

## ADR-059：未知返回与安全失败不制造完整综合

超时/连接异常未确认返回用interrupted/unknown；已收到无效候选仍尽力保存合法usage/id，候选不落库。失败即停，保留成功子历史，剩余not_started。T4仅一次原子成功；确认不明只读复查完成状态，不重发请求。KeyboardInterrupt尽力TF后重抛，SystemExit/强杀/持续DB故障可残留running，无后台修复。仅离线合成/Fake/SDK MockTransport；真实API/Zotero、PR、merge、关闭Issue及4B.5均未授权。
