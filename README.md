# Personal Research Workbench

建立在 Zotero 之上的个人 AI 研究工作台，围绕研究问题组织来源与分析。Zotero 保存文献事实，Workbench 保存研究过程。当前仅有项目上下文文档和只读环境检查，尚无应用、数据库或 Web UI。

## 当前阶段

Phase 0 产品及架构原则已记录；Phase 1 Zotero Technical Spike **核心只读链路已实际通过**：搜索、item key、metadata、摘要、PDF 全部 20 页。所选论文无批注，非空批注读取尚未验证；Python subprocess 调用 uv tool 启动器存在待修复问题。具体证据见 [验证报告](docs/ZOTERO_SPIKE.md)。当前仍不开始 Workbench 功能开发。

## 开发环境与安装

- Windows、PowerShell 7、Git；项目拟使用 Python 3.13。
- 当前系统默认 Python 3.15.2，另有 Python 3.13.17；显式使用 `py -3.13` 避免调用错解释器。
- uv 0.13.6 与独立工具 zotero-mcp-server 0.15.2 已安装，未安装任何 extras。
- 该工具由 `uv tool` 管理，不是项目依赖。尚未创建项目虚拟环境，目前没有应用服务。
- 将来开始 Python 产品代码时再创建项目隔离环境，无需修改 PowerShell 执行策略：

```powershell
py -3.13 -m venv .venv
.\.venv\Scripts\python.exe --version
```

虚拟环境隔离项目依赖，不改变系统 Python。`zotero-cli` 与 `zotero-mcp` 来自同一个外部包 `zotero-mcp-server`。本轮执行 `uv tool install --python 3.13 zotero-mcp-server`，解析到 0.15.2；复现安装可指定 `zotero-mcp-server==0.15.2`。工具入口位于用户 `.local\bin`，首次安装后新终端可能需要刷新 PATH。

## 配置和运行

`.env.example` 只有候选变量和公开回环地址，目前没有配置加载器。未来本地秘密放 `.env`；该文件受 Git 忽略。第一阶段不需要云端 API key，也不授权 Zotero 写入。

当前没有 Workbench 启动命令。Zotero 已由用户启动；本地只读配置和仓库 Zotero CLI skill 已安装，未授权写入。复测前确认客户端仍运行，并按 [ZOTERO_SPIKE.md](docs/ZOTERO_SPIKE.md) 设置进程级 API 后端变量。CLI metadata 使用 Local API，但 PDF 定位与读取会使用上游的本地文件能力，不应混淆两条路径。

## 文档入口

- [长期协作规则](AGENTS.md)
- [产品定位与领域对象](docs/PRODUCT.md)
- [架构、依赖与数据边界](docs/ARCHITECTURE.md)
- [分阶段路线](docs/ROADMAP.md)
- [架构决定](docs/DECISIONS.md)
- [Zotero 只读验证报告](docs/ZOTERO_SPIKE.md)

## 基础开发流程

工作前执行 `git status`、`git branch --show-current`、`git remote -v`。功能使用独立分支，形成完整逻辑单元后检查 `git diff`、敏感信息并运行相关验证，再按 Conventional Commits 提交。重要功能经过 PR 和 review 合并 main。

本地仓库已初始化，当前分支为 `feat/zotero-spike`，尚无提交；`main` 是初始化时指定的稳定主分支名称，尚未形成实际分支引用。未配置 origin，未查询或创建 GitHub 仓库，未执行 push。

建议 GitHub 创建顺序：审阅并提交本地初始化内容 → 确认 GitHub 账号和可见性（个人研究项目建议私有）→ 创建名为 `research-workbench` 的空仓库，不预置 README → 配置并核对 origin → 推送当前分支 → 建立稳定 main 后通过 PR 交付后续功能。不推测远程地址，不执行高影响远程操作。

当前无产品代码，因此不引入格式化、静态检查、测试框架或 CI。代码出现时增加相应检查；自动测试出现后再增加简单 GitHub Actions。数据库到 Phase 2 才引入，并在首次 schema 建立时明确迁移方式。日志只记诊断状态，不记录密钥、私人文献全文或整个配置。
