# Personal Research Workbench

**2026-11-06 当前优先事项：最小可用 CLI。** 从最新远程 main `b716ad7` 建立独立
`codex/minimal-research-cli` / `C:/Projects/research-workbench-cli`，暂停 Phase 4B.5a 额外人工及多模型复核，
保留 PR #22 和全部既有结果。下方阶段授权描述为历史范围，本次用户授权覆盖其中的旧限制。
实现两篇文献的本机选择、Project/Question/来源关联、全文预检、外发预览和终端确认，
复用 DeepSeekStageProvider 完成 Map → Document Reduce → Cross Reduce，并离线查看历史。
用户首次真实运行报告：连接、全文及预算预检通过，第1次 Map 成功，第2次 Map incomplete，
三层流水线尚未完整成功。CLI 故障修复仅做离线验证，保留原失败执行；
[PowerShell 运行及修复指南](docs/CLI_QUICKSTART.md)。

建立在 Zotero 之上的个人 AI 研究工作台。Zotero 是唯一文献事实源，Workbench 保存研究对象及来源引用，不建立第二套文献库。

> 以下旧阶段段落保留各自实现/验收时的范围；当前开发状态以本页开头和末尾 Phase 4B.3 说明为准。

当前开发 **Phase 4B.4：有界跨文献综合与作用域历史**，[Issue #19](https://github.com/AndyFu12306/research-workbench/issues/19)，设计 v2 + v2.1，基线 `41b136113aaaa56a9801590f938fcc745a0e546d`（PR #18 已合并）。独立分支 `codex/phase4b4-cross-document-synthesis` / worktree `C:/Projects/research-workbench-phase4b4`；本轮只允许实现、离线验证和本地提交，不创建 PR、不合并、不关闭 Issue、不进入 4B.5。全部旧分支/worktree 保留。真实 StageProvider API、Zotero Level B 仍 NOT TESTED。

## 环境与安装

本机验证环境：Windows、PowerShell 7.6.5、Python 3.13.17、uv 0.13.6、独立工具 zotero-mcp-server 0.15.2。`.python-version` 指定 3.13；系统默认 Python 3.15.2 不代表项目解释器。

Zotero CLI 使用独立工具环境，不是项目 Python 依赖；基础工具包已提供文本提取，不安装 semantic、scite 或 all extras。新机器需要先安装 uv，再安装工具：

```powershell
uv tool install --python 3.13 zotero-mcp-server==0.15.2
```

用户启动 Zotero 并启用本机通信。已有本地配置时不必重复 setup；首次可使用已验证的 `zotero-mcp setup --no-claude --skip-semantic-search`。禁止授权写入或配置 Web API key。

本机曾出现 Codex AppData 映射导致 uv launcher 内嵌解释器路径不可达，已通过重装到非映射工具目录解决，见 [ADR-012](docs/DECISIONS.md#adr-012windows-launcher-修复属于安装环境而非适配器特殊分支)。不需要 shell=True 或源码中的个人路径。

Phase 2 新增运行时依赖 SQLAlchemy 2.x 和 Alembic，当前锁定 2.1.6 / 1.20.1。SQLAlchemy 管理映射与事务，Alembic 管理 schema 历史；不再叠加其他 ORM。标准库 sqlite3 是底层驱动，但单独使用需要自行维护上述能力。pytest 和 Ruff 分别用于测试与代码检查；Hatchling 仅用于包构建。领域返回值仍用 dataclass；Phase 4A 的官方 OpenAI SDK 间接依赖 Pydantic，但业务 DTO 和校验不使用它。`uv.lock` 固定项目和开发依赖，Zotero CLI 工具独立管理。

```powershell
uv sync --frozen --extra dev
uv run --frozen --extra dev pytest
uv run --frozen --extra dev ruff check src tests
uv run --frozen --extra dev ruff format --check src tests
```

默认 pytest 排除 integration，单元测试会阻止未 mock 的进程与网络调用，无需 Zotero 正在运行。Windows 和 Ubuntu 的基础 CI 仅运行格式、lint 和单元测试，不安装 Zotero；GitHub Initial Integration 已触发远程 CI，最新结果以 PR checks 为准。

## Workbench 数据库与领域接口

两类数据各自有清晰边界：Zotero 是文献事实源；Workbench SQLite 是研究项目与问题的事实源。当前数据库包含 projects、questions、sources、question_sources、analysis_runs、analysis_source_inputs 及 Alembic 版本表，不保存 paper title、authors、DOI、abstract、PDF 或任何 Zotero metadata。Project 的 title 是研究任务名称，Question 的 title 是用户的研究问题。

默认文件为**启动工作目录**下的 `data/research_workbench.db`，不是源码目录或用户固定路径。可通过 `WORKBENCH_DATABASE_PATH` 或 `Database(path=...)` 覆盖；构造参数优先。路径可以包含空格、中文、`#` 和 `%`，应指定文件路径而非数据库 URL。运行文件及 SQLite journal/WAL 均受 Git 忽略。当前不自动加载 .env，需显式导出进程环境。

首次使用必须先迁移，程序不会偷偷调用 create_all 建表：

```powershell
# 在项目目录执行；可先设置 $env:WORKBENCH_DATABASE_PATH 指向自选文件。
uv run --frozen --extra dev alembic upgrade head
uv run --frozen --extra dev alembic current
uv run --frozen --extra dev alembic check
```

第一份 migration `0001` 保持不变；新增 `0002` 建立 sources、question_sources、唯一身份、外键和索引，支持已有 0001 数据库升级。新增 `0003` 管理分析历史和 fingerprint manifest，`0001` / `0002` 均未修改。迁移能从空库重复建立，也能重复运行 upgrade 而保留已有数据。迁移文件随 Python 包一起分发；若指定的库已有其他表却没有 Alembic 版本记录，会拒绝迁移，避免误用其他应用的数据库。不要指向 Zotero 数据库。schema downgrade 会移除表，不应对真实数据执行，除非先有明确批准的备份和恢复方案。

Python 调用示例（会在选定的 Workbench 数据库中创建自己的研究数据）：

```python
from research_workbench.persistence import Database, ProjectRepository, QuestionRepository

with Database() as database:
    database.upgrade()  # 和 Alembic CLI 使用同一份 migration
    projects = ProjectRepository(database)
    questions = QuestionRepository(database)
    project = projects.create("AI Agent 技术研究", "长期研究任务")
    question = questions.create(project.id, "Agent Memory 有哪些主要实现路线？")
    assert questions.get(question.id).project_id == project.id
```

repository 内部处理 session、commit 和 rollback；调用者得到不可变 Project / Question 值，不会得到活动 SQLAlchemy session 或需要延迟加载的 ORM 对象。异常时整次操作回滚，底层 SQLAlchemy 异常保留为 cause，不自动记录包含研究内容的 SQL 参数。

两个对象都使用整数 id、title、description、active/archived 状态和 UTC created_at/updated_at，Question 另有必填 project_id。标题去除首尾空白后需 1–300 字符。每个 Question 只属于一个 Project。创建时 active；get 不存在返回 None，update/archive 不存在则抛 EntityNotFoundError。

ProjectRepository 提供 create/get/list/update/archive；QuestionRepository 提供 create/get/list_by_project/update/archive。列表默认只返回 active，可设 include_archived=True，按 id 排序，支持 limit/offset；list_by_project 对无结果的项目返回空 tuple。归档可重复执行，不硬删除，也不自动改变子 Question 状态。归档对象仍可通过 get 找到；本阶段没有恢复状态、跨项目移动或复杂 workflow。

所有正式表由 Alembic 建立；测试也实际跑 migration，并使用 pytest 临时文件。测试不会读写默认开发数据库、用户 home 数据或真实 Zotero。SQLite 每个连接都启用 foreign_keys，并使用明确事务管理；Project/Question 只归档；唯一 detach 接口仅删除 Workbench 的 Question–Source 关系，不删除 SourceRef 或 Zotero item。

## 使用适配器

```python
from research_workbench.integrations.zotero import ZoteroClient

client = ZoteroClient(timeout=30, api_timeout=5)
health = client.health_check()
items = client.search_items("agent", limit=5)
if items:
    item = client.get_item(items[0].item_key)
    print(item.title, item.authors, item.abstract)
```

`search_items` 返回 `tuple[ZoteroItem, ...]`；类型注解说明对象类型，tuple 和 frozen dataclass 防止意外修改。`get_item` 读取完整条目后只返回需要的字段，DTO 仅在内存中存在。只有具备已有 PDF 的来源才能成功执行 `client.get_fulltext(item_key)`；`get_annotations(item_key, limit=100)` 只读有限数量批注，不声称返回全部批注。

`get_fulltext` 若发现 CLI 截断，使用上游 read 命令补读完整页面并核对页码，不能补全时抛异常。初始命令和补读命令各有 timeout，所以一次完整读取最多包含两个有界进程。PDF 提取复用上游本地附件能力，不保证公式、图表精度。

`health_check()` 返回 overall status 及 client、local_api、cli 三项。客户端可用性由 API 成功推断；API 失败时不武断报告进程已退出。overall 仅在 API 和 CLI 均成功时 healthy，一项失败为 degraded，两项均失败为 unavailable。它不遍历文献或读取全文。

适配器统一处理进程发现、超时、UTF-8、退出码和 JSON；上层只需处理 `ZoteroError` 及其细分类型，见 [错误定义](src/research_workbench/integrations/zotero/errors.py)。异常保留失败 stdout / stderr / returncode，成功命令的 stderr 可从 `client.last_stderr` 获取；默认不记录私人内容。一个 client 供一个工作流使用，诊断字段不用于跨线程共享。

## CLI 发现与配置

优先级为构造参数 cli_path → 环境变量 `ZOTERO_CLI_PATH` → 当前 Python PATH 的 `shutil.which()`。显式路径必须是存在、可执行的绝对路径，Windows 只接受原生 .exe，不接受 .cmd/.bat。所有进程通过参数列表与 shell=False 启动。

安装后旧 Codex / 终端可能仍有旧 PATH；重启终端，或在当前终端显式更新 `.local\bin`。也可动态设置覆盖，不在源码硬编码：

```powershell
$env:ZOTERO_CLI_PATH = (Get-Command zotero-cli).Source
```

适配器向子进程强制传递 local=true、API backend、个人库 users/0，并清空 Web key 与本地写 key。它不会修改父进程环境、全局配置或 Zotero 数据，不自动回退云端/SQLite metadata。

`.env.example` 只列变量和占位值；当前不自动加载 .env，需要在启动进程前导出环境变量。.env、虚拟环境、数据库、PDF 和 local/ 原始诊断输出均受 Git 忽略。

## 显式集成测试

选择库中已有摘要、已有本地 PDF 的真实论文，查询词应在前 10 个搜索结果中包含它。不要将实际 item key 写进测试源码或提交到 Git。以下尖括号为占位符，需替换后再运行：

```powershell
$env:ZOTERO_TEST_QUERY = 'agent'
$env:ZOTERO_TEST_ITEM_KEY = '<已有论文的真实 item key>'
uv run --frozen --extra dev pytest -m integration -q -rs
```

选中 integration 后必须有真实可用的 Zotero 和 CLI；health 不通过会失败，不伪装成 skip。未指定 PDF 样本时 workflow 测试明确跳过。可额外设置 `ZOTERO_TEST_ANNOTATED_ITEM_KEY` 验证已有非空批注；未设置时显示 `NOT TESTED WITH NON-EMPTY DATA`，不创建批注、不阻塞 Phase 1.5。

Phase 3 普通离线测试 **161 passed，4 integration deselected**。只运行新增的 metadata 关联测试：

```powershell
uv run --frozen --extra dev pytest -m integration tests/integration/test_source_links_integration.py -q -rs
```

该测试使用临时 Workbench 数据库，读取当前个人库中已有文献，验证 attach、重复 attach、运行时 hydrate、detach 与来源仍可读；不读取 PDF 或批注。旧的完整 integration 命令会包含 PDF 测试，不属于 Phase 3 验证范围。本轮 Local API 曾连接被拒绝，准确失败在 health；用户启动 Zotero 后同一测试通过：**1 passed**，最新交付证据见 PR。历史全文和非空批注状态见 Spike。

## Question 与来源关联

```python
from research_workbench.application import QuestionSourceService

service = QuestionSourceService(database, client)
# question.id 来自 Workbench；item_key 来自 client.search_items 的真实结果。
linked = service.attach_zotero_source(question.id, item_key)
assert linked.source.external_key == item_key
current = service.list_question_sources(question.id)  # 每次读取当前 metadata
references = service.list_question_sources(question.id, hydrate=False)  # 可离线列引用
service.detach_source(question.id, linked.source.id)  # 只移除本地关系
```

SourceRef 只保存 id、provider、scope、external_key、created_at。当前服务只支持 zotero / personal；唯一身份为三元组，不能把不同 library 的同名 key 混用。QuestionSource 是多对多关系，包含 question_id、source_id、created_at，组合主键确保重复关联不增加记录。

attach 先确认 Question 并通过 ZoteroClient 验证文献，再在单个数据库事务中创建/复用引用与关系。验证失败不写入数据库；重复 attach 幂等，但仍重新验证。列表默认实时 hydrate；每项返回 resolved / unresolved / unavailable / error 和可选内存 metadata。删除/缺失的 Zotero item、CLI 不可用或响应损坏都保留原引用和关系。detach 不需要 Zotero 在线，保留无关联的 SourceRef。

这些 dataclass 是不可变运行时返回值，metadata 不会传给持久化层。repository 不访问 Zotero；业务应使用 QuestionSourceService 完成外部验证，底层 repository 只负责引用数据。

## 结构与开发流程

`domain/` 定义研究领域值；`persistence/` 管理 SQLite、repositories 和 migrations；`application/source_links.py` 编排验证、关联与临时 hydrate；`integrations/zotero/` 是唯一 Zotero 读取入口，runner 是唯一 subprocess 入口。Phase 3 没有新增依赖；Phase 4A 增加官方 OpenAI SDK。

远程仓库：[AndyFu12306/research-workbench](https://github.com/AndyFu12306/research-workbench)。Phase 3 使用独立 Issue、分支与 PR；公开前检查全部新增历史、测试和敏感信息，不提交数据库、私人文献数据或诊断文件。最终 CI 与 review 以 PR 当前 head 为准，不自动开始下一阶段。

- [长期协作规则](AGENTS.md)
- [产品定位](docs/PRODUCT.md)
- [架构与集成边界](docs/ARCHITECTURE.md)
- [阶段路线](docs/ROADMAP.md)
- [架构决定与 Windows 修复](docs/DECISIONS.md)
- [验证报告与限制](docs/ZOTERO_SPIKE.md)

## Structured AI Analysis（Phase 4A）

Zotero 是 bibliographic source of truth；Workbench 是 research workflow + AI analysis source of truth；AI provider 是 compute service，不是事实源。输出是 AI 生成的研究综合，来源 ID 只能追踪到输入文献，不证明每个结论已被人工核实。

新增直接依赖 `openai>=2,<3`，lock 为 2.54.0。官方 SDK 提供 Responses API 类型、HTTP 错误与连接生命周期；自行用 urllib/httpx 调 REST 会增加协议维护，用户也明确要求官方 SDK。它间接增加 HTTPX、Pydantic 等依赖；不增加其他 AI framework、ORM 或 JSON-schema 包，锁文件统一管理。

环境变量必须在启动 Python 前显式设置，`.env` 不自动加载，不在 DB 保存 key：

```powershell
$env:OPENAI_API_KEY = '<your-api-key>'
$env:OPENAI_MODEL = '<model-supporting-Responses-Structured-Outputs>'
$env:OPENAI_TIMEOUT_SECONDS = '60'
```

model 没有源码默认值，需要账户可用且支持 strict Structured Outputs 的模型；未配置时抛 AnalysisConfigurationError。SDK 使用其官方默认 endpoint，程序不硬编码个人 endpoint / organization；不为 CI 配置真实 key。调用会将选定 Question 和来源内容发送给 provider，需主动调用下面的服务或显式 paid integration 才会发生。

```python
from research_workbench.analysis.corpus import InputBudget, ResearchCorpusBuilder
from research_workbench.application.analysis import AnalysisService
from research_workbench.integrations.ai.openai import OpenAIAnalysisProvider
from research_workbench.persistence.analysis_repository import AnalysisRepository

# database 已升级，question_id 已存在并关联真实 Zotero 文献；client 是 ZoteroClient。
with OpenAIAnalysisProvider() as provider:
    service = AnalysisService(database, ResearchCorpusBuilder(client, InputBudget()), provider)
    run = service.run_analysis(question_id, mode="abstracts")
    assert run.status == "completed"
    history = AnalysisRepository(database).list_by_question(question_id)
```

模式 `abstracts` 仅用当前 title/authors/abstract；`fulltext_preferred` 读取完整 PDF text，不能读取或提取不完整时，只在 Local API 仍健康且有当前摘要时明确使用 `abstract_fallback`。超时、连接不可用、来源删除直接失败；无可用内容严格报 AnalysisCorpusIncompleteError，不遗漏论文。当前真实集成仅验证 abstracts，全文分支由离线 fake 测试覆盖。

默认输入上限：5 个来源，每来源规范 JSON 内容 24,000 字符，总请求 60,000 字符。总量包含渲染后的 Question/source 输入、instructions 和 schema 文本；均为 Unicode 字符数而非 token。预算是保守的 MVP 操作上限，不保证任意配置模型的 context 能容纳；模型不支持或 token 超限会明确失败。可通过 InputBudget 调小，max_sources 不可超过 v1 的 5。超限直接 AnalysisInputTooLargeError，不截断、不自动摘要或 chunk。Responses 设置 truncation=disabled，输出最多 4096 tokens；不完整输出拒绝保存。

`AnalysisRequest` / `CorpusSource` 是临时 frozen dataclass，repr 排除内容。每个 source 的 content 是实际发给 provider 的规范 JSON 字符串（含当前 title/authors/input_kind/text），manifest 保存该字符串 UTF-8 的 SHA-256 和 Unicode 字符数；不保存字符串本身。label S1/S2 依本次顺序分配，输出经严格本地校验后仅保存映射的 SourceRef IDs，S99 等非法引用拒绝。summary 和重要条目包含 text/source_ids；next_questions 是后续研究问题。来源引用是 lightweight attribution，不是页级证据或正确性保证。

每次创建新 AnalysisRun：running → completed 或 failed，终态不再更新；返回不可变值。manifest 与 running run 在调用前短事务保存，记录准备发送的输入（失败不保证 provider 已收到）。provider / validation 失败保存固定安全错误码和消息，不保存 raw exception、prompt 或 provider payload。缺少 Question、无来源、检索失败、输入超限等 preflight 在创建 run 前直接抛错；关系保持不变。存储不可用时无法保证落盘 failed，可能保留 running，需要人工检查；没有恢复 worker。

没有 DB transaction 跨 Zotero/HTTP I/O。完成结果与状态原子落盘；manifest 作为历史保留，detach 不删旧分析。repository 通过条件 UPDATE 限制终态再写；这不是阻止管理员直接 SQL 改文件的审计系统。provider_response_id 只接受安全标识，usage 可为空，不保存固定价格。SDK 自动网络重试=0，输出 repair/retry=0；重新运行需显式操作，并产生新历史。SDK 请求 debug 日志被抑制；本阶段不增加日志系统。

验证命令：

```powershell
# 完全离线：210 passed，6 个 integration 默认排除
uv run --frozen --extra dev pytest
# 真实 Zotero + fake provider；临时 Workbench DB，abstracts，已验证 1 passed
uv run --frozen --extra dev pytest -m integration tests/integration/test_analysis_zotero_integration.py -q -rs
# 明确选择付费测试；1 个合成摘要，不外发私人 Zotero 内容
$env:RUN_OPENAI_INTEGRATION = '1'
uv run --frozen --extra dev pytest -m openai_integration tests/integration/test_analysis_openai_integration.py -q -rs
```

本机未配置 key/model：**Real OpenAI provider: NOT TESTED**，显式测试 1 skipped。SDK 的 HTTP transport mock 测试通过，不等于真实 provider PASS。普通测试屏蔽 socket/DNS、Zotero HTTP 和 subprocess；CI 不调用服务，两个真实 marker 默认排除；paid 测试还需 RUN_OPENAI_INTEGRATION=1，避免其他 marker 表达式意外选中。真实 Zotero 集成 1 passed；Ruff、迁移和 wheel 资源检查通过，最终 Windows/Ubuntu CI 与技术 review 见 PR。已合并的 Phase 3 验证是历史记录，不混为本轮结果。

## DeepSeek provider（Phase 4A.1）

```text
AnalysisProvider
├── OpenAIAnalysisProvider
└── DeepSeekAnalysisProvider
```

`integrations/ai/deepseek.py` 使用既有官方 OpenAI SDK 2.54.0 调用 DeepSeek Responses；SDK 兼容不改变 provider 身份，成功/失败历史均记录 provider=deepseek。没有新增依赖、migration 或另一套业务 schema。AnalysisService 不识别厂商；仅注入 provider：

```python
from research_workbench.integrations.ai.deepseek import DeepSeekAnalysisProvider

with DeepSeekAnalysisProvider() as provider:
    service = AnalysisService(database, ResearchCorpusBuilder(client), provider)
    run = service.run_analysis(question_id, "abstracts")
```

配置来自进程环境：`DEEPSEEK_API_KEY`、`DEEPSEEK_MODEL` 必填；`DEEPSEEK_TIMEOUT_SECONDS` 缺失/空值使用60秒，必须正且有限；`RUN_DEEPSEEK_INTEGRATION=1` 仅为真实测试 opt-in。应用没有默认 model，不读取 .env，不依赖 OPENAI_API_KEY，也不继承 OpenAI organization/project headers。

2026-11-03 核对 [DeepSeek Responses guide](https://api-docs.deepseek.com/guides/responses_api/) 与 [API reference](https://api-docs.deepseek.com/api/create-response/)：官方 endpoint 是 https://api.deepseek.com，支持 Responses / json_schema；当前模型示例为 deepseek-flash、deepseek-v4-pro。示例不是源码默认或长期可用保证，选择账户实际可用模型。

adapter 发送现有 analysis-schema-v1、strict=true、同一 prompt 和4096 output token上限；应用层仍校验完整结构与所有 source_labels，再映射来源ID。官方文档未单独说明 strict 字段的保证，因此仅发送 strict 请求不算真实验收。DeepSeek guide 标记 store/truncation 参数不支持，adapter 不发送它们；不回退 Chat Completions/普通 JSON/普通文本，不放松校验。预算仍为5来源、每来源24k字符、总60k字符；context超限和不完整输出失败，不自动截断或重试。推理 tokens 可能消耗输出预算；用户已分别单次验证本地 Responses 原型和正式 hardening Provider，不代表长期稳定性已验证。

错误仅使用固定安全码和消息：5xx（包括503）→provider_unavailable；通用404→provider_invalid_request，不宣称Responses不兼容。已知model/schema机器code仍分类；HTTP200但status=failed的安全code继续通过服务落为failed历史。空白、非法JSON、拒答、incomplete和异常SDK结构分别报告；未知错误仍provider_failed，不猜测或记录provider prose。

Responses只接受reasoning元数据与单条completed assistant message、单个output_text；不拼接多个消息/片段，不接受工具或未知output。合法JSON候选保持原文交给应用层：重复key、schema不符、未知/重复source_labels（含S99）均拒绝。usage/id可缺失为None，异常类型拒绝；不保存raw response/reasoning。prompt/schema仍为analysis-v1 / analysis-schema-v1。网络和repair均零重试。

DEEPSEEK_TIMEOUT_SECONDS是SDK逐I/O超时，不是总墙钟期限；本次未引入生产worker/deadline架构。输出4096不自动扩大，incomplete严格失败。

### 用户运行 Level A：synthetic + real DeepSeek

在项目根目录、同一个 PowerShell 配置并运行；key 输入被遮蔽，变量不回显：

```powershell
uv sync --frozen --extra dev
$secure = Read-Host 'DeepSeek API key' -AsSecureString
$env:DEEPSEEK_API_KEY = [System.Net.NetworkCredential]::new('', $secure).Password
$env:DEEPSEEK_MODEL = Read-Host 'DeepSeek model ID (for example deepseek-flash)'
$env:DEEPSEEK_TIMEOUT_SECONDS = '120'
$env:RUN_DEEPSEEK_INTEGRATION = '1'
$env:RUN_DEEPSEEK_ZOTERO_E2E = '0'
if ($env:DEEPSEEK_API_KEY) { 'DeepSeek API key: set' } else { 'DeepSeek API key: missing' }
uv run --frozen --extra dev pytest -m deepseek_integration tests/integration/test_analysis_deepseek_integration.py::test_synthetic_deepseek_analysis -q -rPs --tb=short
```

只有1个合成问题/摘要，FakeZoteroClient，无私人内容；通过真实 AnalysisService、严格校验和临时SQLite保存 completed历史。不是裸 HTTP200 判定。成功输出仅含model、SDK版本、status、tokens、response-id是否存在、manifest/security结果，不输出原始文本或响应。

### Level A 通过后，用户运行 Level B：选定 Zotero 摘要 + real DeepSeek

启动本地 Zotero，确保 Local API / zotero-cli 可用。只能选择明确愿意将当前 title/authors/abstract 发给 DeepSeek 的文献。旧的测试 item key 不代表本次授权，需重新确认选择并单独开启以下开关：

```powershell
$env:ZOTERO_TEST_ITEM_KEY = Read-Host 'Zotero item key whose abstract you consent to send to DeepSeek'
$env:RUN_DEEPSEEK_ZOTERO_E2E = '1'
uv run --frozen --extra dev pytest -m deepseek_integration tests/integration/test_analysis_deepseek_integration.py -q -rPs --tb=short
```

本命令会在同次运行重新完成A一次，再运行B一次（共最多两次模型请求，不是自动 retry）。module fixture 只复用本次A的真实成功，不能用旧缓存/环境flag伪装A通过。若A失败，B不执行。缺少显式 item key / 独立外发授权时B跳过，不搜索随机文献。B只处理1 Question + 1 source，abstracts模式；临时Project/Question/数据库，禁止 PDF / annotations 调用。测试核对完成历史、source attribution、manifest/hash和usage/id，并检查临时DB不存在完整原始摘要、源content、prompt或key。只有生成分析与provenance入库；生成分析内容仍需人工核实。

不需要将key发送给Codex。完成后可清除当前终端中的key：

```powershell
Remove-Item Env:DEEPSEEK_API_KEY
Remove-Variable secure
$env:RUN_DEEPSEEK_INTEGRATION = '0'
$env:RUN_DEEPSEEK_ZOTERO_E2E = '0'
```

本轮 hardening 离线验收：**315 passed，8 integration deselected**，Ruff及依赖锁检查通过。Codex未读取实际key、未发送真实provider请求。真实开关关闭时两条DeepSeek测试跳过；skip不是PASS。

用户证据（2026-11-05）：Chat JSON Level A单次PASS；Responses原型的minimal、strict-minimal、完整schema及完整AnalysisService Level A单次PASS。正式hardening版本 **Level A: PASS（单次真实验收，1 passed in 8.40s）**；真实Zotero **Level B: NOT TESTED**；workflow仍 **NOT FULLY VALIDATED**。旧503/timeout保留为历史观察，根因未严格证明，不能凭单次成功认定长期稳定。OpenAI真实状态独立。本次正式验收通过uv run --directory明确选择hardening worktree，1049/925 input/output tokens、response-id存在、manifest/immutable/security通过；与原型证据分开记录。上述命令仅供后续显式复验参考，不自动授权Level B或4B。详见[验证记录](docs/DEEPSEEK_VALIDATION.md)。

## Phase 4B.3：单文献执行与历史

`application.document_analysis.DocumentAnalysisService` 独立于旧 `AnalysisService`。入口 `run_analysis(question_id, source_id)` 只处理明确指定、已关联该 Question 的来源；只接受只读接口 `get_fulltext()` 成功返回的完整全文，health 必须 healthy。无摘要 fallback、自动选文献或元数据复制。生产读取接口为现有 ZoteroClient；本轮验收全部注入 FakeReader。

顺序为：一次全文快照 → 完整 MapPlan → 正式 Map 请求预检 → 原子保存全部计划 → 每块一次 Map/严格校验/原子提交 → 已持久化全部 Map 的集合、顺序、ordinal、hash、来源 fingerprint 与计划完全一致 → 全部 Map（含合法空结果）参与一次 document_reduce → 动态预算/严格校验 → Reduce 与执行完成原子提交。N 块最多 N+1 次调用，无重试、修补、续跑或多级 Reduce。任何一块失败或未落盘都不能完成；不把结果引用范围当作全文处理范围。

Provider 作为同一个注入对象提供 `estimate_stage(request)` 和 `generate_stage(request)`；旧 StageProvider/AnalysisProvider 协议行为保留，新增 BudgetedStageProvider 仅描述这组组合能力。OpenAI/DeepSeek 新阶段 adapter 的估算调用同一个 `prepare_stage_call`，本身不联网。Protocol 表示接口约定，Fake 无需继承生产类，只需实现这些方法。

`ExecutionBudget` 默认 max_calls=101、max_estimated_tokens=1,000,000、reduce_reserved_tokens=32,768；同时受 Planner 最多100块和 StageBudget 各项限制。开始前预检所有正式 Map 估算并额外保留 Reduce 名额与额度；每次调用前重新估算、检查累计 context 估算。Reduce 只使用其保留额度，超限明确 reduce_budget_exceeded，不删块/截断/增预算。Planner envelope 估算单独记版本，不与正式请求估算重复累计。估算是 UTF-8 字节操作界限，不是 tokenizer、真实 context 或账单保证；未来 Map 输出未知，允许所有 Map 成功后 Reduce 因实际输入过大失败。

可选 `Pricing(currency, version, input_per_million, output_per_million)` 要求 Decimal 单价；执行配置保存价格快照。`max_estimated_cost` 需同时提供价格，开始前和每阶段检查估算费用（为 Reduce 按最大输入及输出预留）。StageRun 的 estimated_cost 是输入估算加输出预留的费用，usage_cost 是已知 input/output usage 对该价格的名义计算；二者都不是实际账单。缺任一 usage 或价格时 usage_cost=None，无价格时 estimated_cost=None。部分阶段 usage 已知不代表整个执行费用已知，超时/强杀不能当零费用。

新增 migration 0004（down_revision=0003），四表为 document_analysis_runs、document_analysis_chunks、analysis_stage_runs、analysis_stage_inputs；0001–0003不变。仅临时测试库实际迁移，不自动升级用户数据库。新表使用 SQLAlchemy Core 并注册到既有 Base.metadata，Alembic 仍是 schema 唯一管理入口。旧 Phase 4A 表和分析 schema 不变。

`DocumentAnalysisRepository` 提供 get/list_by_question/get_chunks/get_stages/get_inputs；返回 frozen dataclass，禁止覆盖终态。引用在 execution_id + stage ordinal 下解释；Finding ID 允许不同运行确定性重复，不能用全局 ID 查找替代作用域。只持久化经过重验并映射的生成分析、引用、Chunk manifest、版本、预算、usage 和固定安全错误码。request_sha256 是 stage-input-sha256-v1（渲染阶段输入的 hash），不声称是完整 HTTP 请求或可重放凭据。未存原文、Chunk文本、metadata、完整prompt、原始响应、reasoning或密钥。生成分析本身仍可能含敏感内容，引用链不证明语义支持。

遇错即停，保留已完成阶段；其余 planned 标为 not_started。执行 running → completed/failed/interrupted，阶段另有 planned/running/not_started。call_outcome 分别为 not_started、unknown、response_received；running 在调用前落盘，因此 unknown 不证明服务端收到了请求。KeyboardInterrupt 尽力保存 interrupted 后重新抛出；强杀、SystemExit、断电或持续 DB 故障可留下 running，不自动猜测状态、不后台修复。重新运行创建新历史，detach 保留历史与 SourceRef；查询历史不会重新读取 Zotero，也不能把历史 DTO 直接传入新阶段。

离线验收（测试隔离未 mock 网络/进程；无需 Zotero、key 或 .env）：

```powershell
uv run --frozen --extra dev pytest tests/unit/test_document_analysis.py tests/unit/persistence/test_document_migrations.py -q
uv run --frozen --extra dev pytest
uv run --frozen --extra dev ruff check src tests
uv run --frozen --extra dev ruff format --check src tests
uv lock --check
```

执行服务需注入已配置的 reader/provider；测试中的 FakeReader 与 FakeStageProvider 是完整合成参考，不要求用户补业务代码。新 StageProvider 真实 API、真实全文分层执行与旧 Level B 仍 NOT TESTED；本轮不访问真实 Zotero 或 AI。

本轮本机离线验收（2026-11-05）：**674 passed，8 integration deselected**；新增文献执行/迁移测试73项。Ruff check及format（70文件）、uv lock --check、临时库Alembic upgrade/current/check/repeat和wheel内0001–0004资源检查通过。新增diff凭据/禁止产物扫描及技术自审通过；Phase4A、4B.2契约/引用校验、0001–0003和依赖文件均与基线相同。最终head双平台CI另以PR记录为准，不把本机通过视为远程CI通过。


## Phase 4B.4：显式跨文献执行

`CrossDocumentAnalysisService.run_analysis(question_id, source_ids)` 接受 **2–5 个已关联来源 ID 的有序 tuple**，冻结完整问题（title + description）和每篇全文；顺序执行每篇 Map → Document Reduce，最后只做一次 Cross Reduce。各篇全文只读取一次；不是 Zotero 跨篇原子快照。重复身份、不同身份同全文 hash 均拒绝，失败即停，不返回部分综合。

单文献 `DocumentAnalysisService.run_analysis(question_id, source_id)` 的公开返回行为保持。内部 `prepare_document` 只准备冻结计划，`register_document` 登记计划，`execute_registered` 执行已登记计划；Cross 复用后者并通过父执行事务保护所有子 mutation。

- 首版最多100个 Chunk、1,000,000个原文字符、106次结构调用；整体默认1,000,000操作估算。每个 Map 固定自己的正式估算额度；每篇 Document 和 Cross 分别预留完整 Reduce 输入/输出/安全余量，互不借用。
- Decimal 费用使用显式128位精度并拒绝不精确运算；费用上限必须同时给出价格/币种/版本。`get_costs()` 区分已知部分、整次未知费用、unknown 与 not_started 调用。预算与名义 usage 费用均不是实际账单保证。
- `CrossDocumentRepository.get_history(id)` 只读 Workbench 历史，返回 frozen `CrossHistory`，`result_json` 为 `cross-document-history-v1`；不会重新读取 Zotero，不可传回新 Reduce 充当运行时结果。读取严格遍历 Document Reduce → Map → Chunk，并核对完整子历史摘要及作用域。
- 完成/失败/中断历史不覆盖；显式重跑创建全新父子执行。未确认返回的超时/连接错误为 interrupted/unknown。强杀或持续 DB 故障可留下 running，不自动恢复。
- 仅追加 Alembic 0005；不自动升级用户数据库，不改0001–0004。没有新增依赖、UI、RAG、缓存、重试、并行调度或 Zotero 写入。

可直接运行已完成的合成示例测试，无需 Zotero、密钥或 `.env`：

```powershell
uv run --frozen --extra dev pytest tests/unit/test_cross_document_analysis.py -q
uv run --frozen --extra dev pytest tests/unit/test_cross_document_capacity.py -q -s
uv run --frozen --extra dev pytest
uv run --frozen --extra dev ruff check src tests
uv run --frozen --extra dev ruff format --check src tests
uv lock --check
```

容量测试会显式提高**合成整体预算**以测100块路径；默认单次 Reduce 仍可安全拒绝。完整门槛、实际性能和未测试项见 [4B.4 验收记录](docs/PHASE4B4_VALIDATION.md)。引用链只能证明结构追溯，不能证明语义支持或穷尽综述；输出生成分析仍可能复述敏感内容。
