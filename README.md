# Personal Research Workbench

建立在 Zotero 之上的个人 AI 研究工作台。Zotero 是唯一文献事实源，Workbench 保存研究对象及来源引用，不建立第二套文献库。

当前交付 **Phase 1.5：只读 Zotero Integration Adapter**。Phase 1 核心 Spike 已通过，Python launcher 问题已修复；尚无数据库、Project model、Web UI 或 AI Analysis。阶段证据见 [ZOTERO_SPIKE.md](docs/ZOTERO_SPIKE.md)。

## 环境与安装

本机验证环境：Windows、PowerShell 7.6.5、Python 3.13.17、uv 0.13.6、独立工具 zotero-mcp-server 0.15.2。`.python-version` 指定 3.13；系统默认 Python 3.15.2 不代表项目解释器。

Zotero CLI 使用独立工具环境，不是项目 Python 依赖；基础工具包已提供文本提取，不安装 semantic、scite 或 all extras。新机器需要先安装 uv，再安装工具：

```powershell
uv tool install --python 3.13 zotero-mcp-server==0.15.2
```

用户启动 Zotero 并启用本机通信。已有本地配置时不必重复 setup；首次可使用已验证的 `zotero-mcp setup --no-claude --skip-semantic-search`。禁止授权写入或配置 Web API key。

本机曾出现 Codex AppData 映射导致 uv launcher 内嵌解释器路径不可达，已通过重装到非映射工具目录解决，见 [ADR-012](docs/DECISIONS.md#adr-012windows-launcher-修复属于安装环境而非适配器特殊分支)。不需要 shell=True 或源码中的个人路径。

项目运行时无第三方依赖。开发环境只增加 pytest 和 Ruff：前者测试，后者格式与静态检查；Hatchling 仅用于包构建。dataclass 已满足轻量 DTO 需求，因此没有增加 Pydantic。`uv.lock` 固定项目和开发依赖，工具版本独立管理。

```powershell
uv sync --frozen --extra dev
uv run --frozen --extra dev pytest
uv run --frozen --extra dev ruff check src tests
uv run --frozen --extra dev ruff format --check src tests
```

默认 pytest 排除 integration，单元测试会阻止未 mock 的进程与网络调用，无需 Zotero 正在运行。Windows 和 Ubuntu 的基础 CI 仅运行格式、lint 和单元测试，不安装 Zotero；GitHub Initial Integration 已触发远程 CI，最新结果以 PR checks 为准。

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

本机结果：63 个单元测试通过；2 个集成测试通过，1 个非空批注样本测试跳过。普通 pytest 选择 63 个单元测试、排除 3 个集成测试。

## 结构与开发流程

`src/research_workbench/integrations/zotero/` 中 client.py 提供业务接口，runner.py 是唯一 subprocess 边界，models.py 是运行时数据结构，errors.py 定义领域错误。未来业务模块统一调用 ZoteroClient，不直接运行 CLI。

工作前核对 Git 目录、状态、分支和 remote；提交前 review diff、检查敏感信息并运行相关检查。当前分支 `feat/zotero-adapter`；main 保留 Phase 1 文档基线，不自动合并。

远程仓库为 public [AndyFu12306/research-workbench](https://github.com/AndyFu12306/research-workbench)，origin 使用该仓库的 HTTPS clone URL。main 与 feat/zotero-adapter 已分别推送并建立 upstream；[PR #1](https://github.com/AndyFu12306/research-workbench/pull/1) 提议 feature → main，等待用户决定是否合并。不自动推进 Phase 2。公开推送前已检查全部初始历史，私人诊断输出仍只存在受忽略的 local/。

- [长期协作规则](AGENTS.md)
- [产品定位](docs/PRODUCT.md)
- [架构与集成边界](docs/ARCHITECTURE.md)
- [阶段路线](docs/ROADMAP.md)
- [架构决定与 Windows 修复](docs/DECISIONS.md)
- [验证报告与限制](docs/ZOTERO_SPIKE.md)
