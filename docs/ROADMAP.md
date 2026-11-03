# 路线与阶段门槛

当前：**Phase 2 — Workbench Domain Model + Minimal Database 已实现并验证，等待用户 merge 决定**。[PR #2](https://github.com/AndyFu12306/research-workbench/pull/2) 提交 Project/Question 与 SQLite；本地测试和 Windows/Ubuntu CI 均通过，PR diff review 未发现阻塞问题。后续功能仍未授权。日期：2026-11-03（Asia/Shanghai）。

| Phase | 内容 | 状态 |
| --- | --- | --- |
| 0 | 产品 / 架构设计 | 原则已记录 |
| 1 | Zotero Technical Spike | Core read-only spike: PASS |
| 1.5 | Zotero Integration Adapter | PR #1 已由用户合并，合并后双平台 CI 通过 |
| 2 | Project + Question + Minimal Database | PR #2 待用户决定是否合并，不自动 merge |
| 3 | Project model（原里程碑） | 按本轮用户范围并入 Phase 2，不重复实现 |
| 4 | Question model（原里程碑） | 按本轮用户范围并入 Phase 2，不重复实现 |
| 5 | Zotero Source association | 未开始 |
| 6 | AI Analysis | 未开始 |
| 7 | Basic UI | 未开始 |
| 8 | Research Inbox | 延后 |
| 9 | Claim + Evidence | 延后 |
| 10 | Concept + Knowledge Graph | 延后 |
| 11 | Research Memory | 延后 |
| 12 | Automation | 延后 |

## 已完成的前置阶段

- 产品与架构原则、只读 Zotero 数据边界、安全规范与 Git 工作流已写入仓库。
- 完成真实 Zotero 搜索、item key、metadata、非空摘要、完整 20 页 PDF 读取；批注查询成功但样本为空。非空真实批注仍 NOT TESTED WITH NON-EMPTY DATA。
- 定位并修复 Windows uv launcher 的 PATH / AppData 映射问题；建立 ZoteroClient、集中 runner、领域错误与运行时 DTO。
- [PR #1](https://github.com/AndyFu12306/research-workbench/pull/1) 完成公开历史安全检查、push、review 和用户 merge；main 合并提交为 6634ccd。合并后的 Windows/Ubuntu CI 通过，本地同步且检查通过。

## Phase 2 实现与验证

- 从 clean、与 origin/main 同步的最新 main 创建 feat/workbench-domain-model，不在 main 开发。
- 原有无 persistence stack，新增 SQLAlchemy 2.x 和 Alembic，当前 lock 2.1.6 / 1.20.1；不引入额外 ORM 或目录管理依赖。
- SQLite 默认在启动 cwd/data/research_workbench.db，支持显式 path 和 WORKBENCH_DATABASE_PATH；运行文件受忽略，无个人路径硬编码。
- 仅持久化 Project / Question，一对多、必填外键、状态 active/archived。创建/查询/分页列表/更新/归档通过两个 repository，返回不可变领域值，不暴露 session。
- 没有硬删除；archive 幂等且不级联，不实现复杂 workflow。没有 Source 或任何 Zotero metadata 持久化。
- 初始 migration 0001 创建 projects、questions、CHECK、外键、索引。正式程序和 CLI 都走 Alembic，不调用 create_all；迁移资源已验证包含在 wheel 中。
- 已在两份独立临时库验证 upgrade head、重复升级保留数据、完整 CRUD、关系约束、回滚、UTC 与隔离。额外运行真实 Alembic CLI 的 upgrade/current/check 均成功，ORM 与迁移无差异。
- 普通测试 114 passed，3 个 Zotero integration deselected；新增 51 个数据库测试不依赖网络、GitHub、Zotero 或用户数据。沿用双平台 CI，自动包含 migration 测试。
- 未访问 Zotero、未修改 ZoteroClient、未初始化真实开发数据库。无 Web UI、AI Analysis、RAG、Inbox、Claim/Evidence 或知识图谱。
- 仓库尚无 Issues，本阶段以单一 PR 追踪，不为每个函数增加管理对象。PR 的 CI、review 和最终交付结论以 GitHub 当前 head 为准。
- [实现提交的双平台 CI](https://github.com/AndyFu12306/research-workbench/actions/runs/41588511385) 已成功，包含依赖安装、格式、lint 和 114 个普通测试；后续仅补充交付文档，最终 head 的运行结果继续以 PR checks 为准。开发数据库未创建，迁移均在临时文件中验证。

## 本阶段交付门槛

1. 完整 diff review、普通测试与 Ruff 通过。
2. 全部新增公开历史安全检查通过，不提交数据库、缓存、临时文件、私人研究内容或密钥。
3. 推送 feat/workbench-domain-model，向 main 建立 PR；Windows / Ubuntu CI 均通过。
4. 完成 PR-level review；报告测试、migration、架构决定与已知限制。
5. 不自动 merge，等待用户决定；不开始后续阶段。

## 已知限制

- 只有 Project / Question；每次 repository 方法是一个事务，没有多 repository 事务编排、乐观锁、多用户或远程同步。
- 列表有显式分页，默认过滤归档；归档项目不会改变子问题状态，未提供恢复、移动或删除接口。
- 仅支持 SQLite 文件路径，默认相对启动工作目录；没有 .env 自动加载或跨平台用户目录依赖。
- SQLite 初始迁移完成后应备份真实研究数据再做后续 schema 变更。初始 downgrade 会删除领域表，未对真实数据库执行。
- 非空 Zotero annotations 样本验证属于独立的旧阶段限制，不阻塞本阶段，也不以写入 Zotero 来补样本。

## 长期交付

v0.1.0 = Project + Question + Zotero Source + AI Analysis；v0.2.0 = Inbox；v0.3.0 = Claim + Evidence。阶段成立不等于自动发布 Release。ROADMAP 记录产品路线，具体独立工作可在有实际需要时用 Issue 跟踪。
