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
