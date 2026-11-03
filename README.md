# Personal Research Workbench

建立在 Zotero 之上的个人 AI 研究工作台。Zotero 是唯一文献事实源，Workbench 保存研究对象及来源引用，不建立第二套文献库。

当前交付 **Phase 4A：Structured AI Analysis MVP**。PR #4 已合并至 main（465e92c）；本轮从同步、干净的 main 创建 `feat/ai-analysis`，由 [Issue #5](https://github.com/AndyFu12306/research-workbench/issues/5) 跟踪。实现 Question + linked Sources → corpus → provider → validated analysis → immutable history，不开发 UI、RAG 或后台任务，不自动 merge。

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
