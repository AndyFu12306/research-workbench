# Personal Research Workbench

建立在 Zotero 之上的个人 AI 研究工作台。Zotero 是唯一文献事实源，Workbench 保存研究对象及来源引用，不建立第二套文献库。

当前交付 **Phase 2：Project / Question 领域模型与最小 SQLite 持久化**。PR #1 的只读 Zotero Adapter 已合并至 main；本阶段只建立 Workbench 自己的数据基础，不访问 Zotero，不提供 Web UI 或 AI Analysis。历史集成证据见 [ZOTERO_SPIKE.md](docs/ZOTERO_SPIKE.md)。

## 环境与安装

本机验证环境：Windows、PowerShell 7.6.5、Python 3.13.17、uv 0.13.6、独立工具 zotero-mcp-server 0.15.2。`.python-version` 指定 3.13；系统默认 Python 3.15.2 不代表项目解释器。

Zotero CLI 使用独立工具环境，不是项目 Python 依赖；基础工具包已提供文本提取，不安装 semantic、scite 或 all extras。新机器需要先安装 uv，再安装工具：

```powershell
uv tool install --python 3.13 zotero-mcp-server==0.15.2
```

用户启动 Zotero 并启用本机通信。已有本地配置时不必重复 setup；首次可使用已验证的 `zotero-mcp setup --no-claude --skip-semantic-search`。禁止授权写入或配置 Web API key。

本机曾出现 Codex AppData 映射导致 uv launcher 内嵌解释器路径不可达，已通过重装到非映射工具目录解决，见 [ADR-012](docs/DECISIONS.md#adr-012windows-launcher-修复属于安装环境而非适配器特殊分支)。不需要 shell=True 或源码中的个人路径。

Phase 2 新增运行时依赖 SQLAlchemy 2.x 和 Alembic，当前锁定 2.1.6 / 1.20.1。SQLAlchemy 管理映射与事务，Alembic 管理 schema 历史；不再叠加其他 ORM。标准库 sqlite3 是底层驱动，但单独使用需要自行维护上述能力。pytest 和 Ruff 分别用于测试与代码检查；Hatchling 仅用于包构建。领域返回值仍用 dataclass，不增加 Pydantic。`uv.lock` 固定项目和开发依赖，Zotero CLI 工具独立管理。

```powershell
uv sync --frozen --extra dev
uv run --frozen --extra dev pytest
uv run --frozen --extra dev ruff check src tests
uv run --frozen --extra dev ruff format --check src tests
```

默认 pytest 排除 integration，单元测试会阻止未 mock 的进程与网络调用，无需 Zotero 正在运行。Windows 和 Ubuntu 的基础 CI 仅运行格式、lint 和单元测试，不安装 Zotero；GitHub Initial Integration 已触发远程 CI，最新结果以 PR checks 为准。

## Workbench 数据库与领域接口

两类数据各自有清晰边界：Zotero 是文献事实源；Workbench SQLite 是研究项目与问题的事实源。当前数据库只有 projects、questions 及 Alembic 自己的版本表，不保存 paper title、authors、DOI、abstract、PDF 或任何 Zotero metadata。Project 的 title 是研究任务名称，Question 的 title 是用户的研究问题。

默认文件为**启动工作目录**下的 `data/research_workbench.db`，不是源码目录或用户固定路径。可通过 `WORKBENCH_DATABASE_PATH` 或 `Database(path=...)` 覆盖；构造参数优先。路径可以包含空格、中文、`#` 和 `%`，应指定文件路径而非数据库 URL。运行文件及 SQLite journal/WAL 均受 Git 忽略。当前不自动加载 .env，需显式导出进程环境。

首次使用必须先迁移，程序不会偷偷调用 create_all 建表：

```powershell
# 在项目目录执行；可先设置 $env:WORKBENCH_DATABASE_PATH 指向自选文件。
uv run --frozen --extra dev alembic upgrade head
uv run --frozen --extra dev alembic current
uv run --frozen --extra dev alembic check
```

第一份 migration 是 `0001`，建立两个领域表、questions.project_id 外键和索引。迁移能从空库重复建立，也能重复运行 upgrade 而保留已有数据。迁移文件随 Python 包一起分发；若指定的库已有其他表却没有 Alembic 版本记录，会拒绝迁移，避免误用其他应用的数据库。不要指向 Zotero 数据库。schema downgrade 会移除表，不应对真实数据执行，除非先有明确批准的备份和恢复方案。

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

所有正式表由 Alembic 建立；测试也实际跑 migration，并使用 pytest 临时文件。测试不会读写默认开发数据库、用户 home 数据或真实 Zotero。SQLite 每个连接都启用 foreign_keys，并使用明确事务管理；只做 archive 的业务接口没有 delete 方法。

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

Phase 2 当前普通测试为 **114 passed，3 个 Zotero integration deselected**，其中 51 个新增数据库测试。先前 Phase 1.5 的真实 Zotero 验证为 2 passed、1 skipped；本阶段不重新访问 Zotero，不能把历史结果当作新的读取记录。

## 结构与开发流程

`src/research_workbench/domain/` 定义研究领域值；`persistence/` 包含配置、内部 ORM 映射、Database、两个 repository 和 migrations。`integrations/zotero/` 保持原样，仍是独立只读边界。未来 UI/CLI 通过 repository 访问研究数据、通过 ZoteroClient 读取文献，不直接使用 session 或 subprocess。

工作前核对 Git 目录、状态、分支和 remote；提交前 review diff、检查敏感信息并运行相关检查。本阶段从包含 PR #1 合并提交的最新 main 创建 `feat/workbench-domain-model`，通过独立 PR 交付，不自动合并。

远程仓库为 public [AndyFu12306/research-workbench](https://github.com/AndyFu12306/research-workbench)，origin 使用该仓库的 HTTPS clone URL。[PR #1](https://github.com/AndyFu12306/research-workbench/pull/1) 已由用户合并，合并后 Windows/Ubuntu CI 通过。Phase 2 的公开 push 前继续检查完整新增历史，不提交数据库或私人诊断输出；未开始 Source、Web UI、AI Analysis 等后续能力。

- [长期协作规则](AGENTS.md)
- [产品定位](docs/PRODUCT.md)
- [架构与集成边界](docs/ARCHITECTURE.md)
- [阶段路线](docs/ROADMAP.md)
- [架构决定与 Windows 修复](docs/DECISIONS.md)
- [验证报告与限制](docs/ZOTERO_SPIKE.md)
