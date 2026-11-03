# 长期工作规则

- 工作前读取 README.md、docs/ROADMAP.md；涉及设计时读取 PRODUCT.md、ARCHITECTURE.md、DECISIONS.md。及时更新对应文档。
- Zotero 是唯一文献事实源。Workbench 只保存研究数据和来源引用，不复制完整文献库，不重新实现 Zotero / zotero-mcp 已有能力。
- PR #6 已合并；当前授权 Phase 4A.1 独立 DeepSeek provider 与 opt-in 验证，保留 OpenAI、现有 AnalysisProvider / schema / immutable history。禁止 Web UI、RAG、Embeddings、Inbox、Claim/Evidence、知识图谱或自动化。
- AI provider 仅为 compute service。源内容只在内存，持久化分析及来源 fingerprint；不自动加载 .env、不记录 payload/密钥，真实付费测试独立 opt-in。本阶段无 schema 变更，不改已合并 migrations。DeepSeek 真实测试由用户在本机运行，Zotero 摘要外发需独立 opt-in 和显式 item key；不自动选择文献。
- 禁止本阶段写入 Zotero，包括 metadata、Tags、Collections、Notes、Annotations、PDF、删除和批量操作。不得授权本地写入，也不得用云端或直接数据库读取掩盖 Local API 失败。
- 后续外部写操作必须先展示预览并获得明确批准；删除需强确认。本次授权建立仓库、上下文文档和验证分支，不代表授权 Zotero 写入。
- 优先 Python 标准库和既有能力；新增依赖说明用途、替代方案及维护成本。初期 Python 后端、SQLite；不 fork zotero-mcp。
- 区分来源原文、用户观点、AI 生成与 AI 推断；重要结论保留来源和证据追溯。
- 检查工作目录、Git 状态、分支及 remote；大功能使用单一目的分支。提交前检查 diff、敏感信息和相关验证结果，采用 Conventional Commits。
- 不提交密钥、.env、本地数据库、缓存、PDF、Zotero 数据或私人报告。示例配置只含占位值。
- 未经明确确认，不执行破坏性 Git 操作、历史改写、force push、远程删除或仓库权限变更；不覆盖用户已有修改。
- 如实区分通过、失败、阻塞和未测试，按 Workbench / CLI / MCP / Local API / Client 分层定位；不把空结果当作连接成功。
- 业务代码只调用 ZoteroClient；subprocess 统一放在集成 runner，使用绝对 executable、参数列表、shell=False 和有限超时。CLI 先读取安装版帮助，不猜子命令。
- 普通 pytest 不访问真实 Zotero；真实读取测试用 integration marker 显式选择。DTO 仅在运行时存在，不持久化 metadata。
- Workbench 数据库只存自己的研究对象；正式 schema 由 Alembic 管理，不以 create_all 替代迁移。数据库测试只使用临时文件，不读取用户真实数据库；不提供研究对象硬删除。
- Source 只持久化 provider/scope/external_key 和关系身份、时间；metadata 仅运行时从 ZoteroClient 获取。repository 不调用 Zotero；application service 编排验证、原子关联和 hydration。detach 仅删除指定 association，保留 SourceRef 与 Zotero item；本阶段此操作已获授权。已合并 migration 不可修改。
- 用户是初学者：完成代码并解释重要语法、设计、环境和验证方法，不要求用户补代码。保持简单、可理解、可测试、可替换。

<!-- BEGIN zotero-cli skill (managed by `zotero-mcp install-skill`) -->

## Zotero library access

Read and write a Zotero library from the shell with the `zotero-cli` command - search papers by keyword or meaning, read PDF full text and page ranges, get and set metadata, manage collections, tags, notes and annotations, add items by DOI/URL/ISBN, and export bibliographies. Use whenever the user asks about their Zotero library, references, citations, papers they have saved, or their reading notes.

Read `.agents/skills/zotero-cli/SKILL.md` before using it. Prefer it over the Zotero MCP server when you have shell access: the MCP tool schemas cost ~13k tokens of context on every request, this costs only what you run.

Quick check that it is set up: `zotero-cli config`. Pass `--json` whenever you will parse the output.

<!-- END zotero-cli skill -->
