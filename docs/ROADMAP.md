# 路线与阶段门槛

当前：**Phase 4A.1 — DeepSeek Structured Output 修复（Option B）已实现，进入独立 PR / CI / review**。[Issue #9](https://github.com/AndyFu12306/research-workbench/issues/9)，分支 fix/deepseek-structured-output；main基线aceca63包含已合并PR#8。用户报告Responses结构化重复503，改用官方Chat JSON mode + 保持严格应用校验，真实新链路仍待用户验收。记录日期2026-11-03（Asia/Shanghai）。不自动merge，不进入4B。

| Phase | 内容 | 状态 |
| --- | --- | --- |
| 0 | 产品 / 架构设计 | 原则已记录 |
| 1 | Zotero Technical Spike | Core read-only spike: PASS |
| 1.5 | Zotero Integration Adapter | PR #1 已合并 |
| 2 | Project + Question + Minimal Database | PR #2 已合并，main 包含 6bb505ab |
| 3 | Question ↔ Zotero Source Association | PR #4 已合并 |
| 4A | Structured AI Analysis MVP | PR #6 已合并；真实 OpenAI 仍 NOT TESTED |
| 4A.1 | DeepSeek provider + real validation | PR #8已合并；Responses结构化人工FAIL，Chat JSON修复待真实A/B |
| 4B | 大 corpus / chunking / map-reduce / hierarchical synthesis | 延后，未授权 |
| 后续 | Basic UI | 未开始，需另行授权 |
| 后续 | Inbox / Claim + Evidence / Knowledge Graph / Memory / Automation | 延后 |

原路线中 Project/Question 两个里程碑已纳入 Phase 2；本轮按用户明确命名，将来源关联称为 Phase 3，不重复实现既有模型。

## 已完成的前置阶段

- 真实 Zotero 搜索、item key、metadata、非空摘要与完整 PDF 读取通过。批注查询通过，但非空真实批注仍 NOT TESTED WITH NON-EMPTY DATA，不为补样本写入 Zotero。
- Phase 1.5 定位 Windows launcher 环境问题，建立标准库 DTO、集中 shell=False runner 与错误边界。PR #1 已由用户合并。
- Phase 2 建立 Project/Question、SQLite、SQLAlchemy/Alembic、migration 0001 和 114 个普通测试。PR #2 现已 merged；本轮实际 fetch、switch main、ff-only pull，确认 local main = origin/main = 6bb505ab86ec7bea77cc7d7b382e663240e6e11d 且工作区干净，之后才建立 Phase 3 分支。

## Phase 3 实现与验证

- Issue #3 → feat/zotero-source-links → 独立 commits / push / PR；PR body 使用 Closes #3，不自动合并。
- SourceRef 只保存 provider、scope、external_key 及本地 id/created_at；不复制 title、authors、DOI、journal、abstract、PDF、全文或 annotations。
- QuestionSource 多对多，组合主键去重、真实外键约束、反向索引。detach 只删除关系，引用及 Zotero 数据保留。
- QuestionSourceService 先验证 Question 和真实文献，再原子保存引用及关系；repository 完全不调用 Zotero。列表按需实时 hydrate，也可完全离线列引用；来源缺失或 Zotero 不可用都保留关系并区分状态。
- migration 0002 新建两个来源表，不改 0001。临时库验证空库到 head、0001 旧数据升级、重复升级、真实唯一/FK约束、rollback、ORM/schema 一致性；Alembic CLI upgrade/current/check 均通过。没有初始化真实开发数据库。
- 普通离线测试 **161 passed，4 integration deselected**；Ruff 通过。新增真实 metadata 关联集成测试 **1 passed**，仅运行 tests/integration/test_source_links_integration.py；不运行历史 PDF 测试。
- 本轮首次真实测试因 Local API 拒绝连接失败，CLI 配置检查正常；用户启动 Zotero 后同一测试通过。没有用 Web API、直接数据库或其他回退掩盖失败。
- 无新增依赖，无 UI、AI、RAG、embeddings、知识图谱、Claim/Evidence、Inbox 或 Zotero 写入。

## 交付门槛

1. 完整 diff review、普通测试、格式与 lint 通过。
2. 公开新增历史通过敏感信息检查，不提交 DB、PDF、缓存、私人 metadata、真实 item key 或原始诊断。
3. 推送独立分支并建立 PR；最终 head 的 Windows / Ubuntu CI 均通过。
4. PR review 报告 migration、测试、ADRs 与已知限制；PR 保存最终远端证据。
5. 用户决定是否 merge，代理不自动推进下一阶段。

## 已知限制

- 当前 Zotero 服务只支持 personal；scope 身份已隔离，但尚无 group library、其他 provider、多用户或同步功能。
- hydrate 为有限分页后的顺序实时读取，CLI 每项启动，暂无缓存/批量 hydrate；性能以个人小规模使用为边界。
- 外部文献可能在验证后被删除；跨 Zotero/SQLite 不存在分布式事务，后续 hydrate 标记 unresolved。
- detach 保留无关联 SourceRef，无自动回收、来源合并或重新绑定；Project/Question 仍只支持归档。
- missing-item 文本兼容固定工具版 0.15.2，升级需重跑契约测试；损坏响应不会被当作 missing。
- 本阶段不新增全文/非空批注验证。旧阶段非空批注限制仍存在，但不阻塞来源关联。

## 长期交付

v0.1.0 = Project + Question + Zotero Source + AI Analysis；v0.2.0 = Inbox；v0.3.0 = Claim + Evidence。Phase 4A 增加最小 AI 分析，不等于发布完整 v0.1 或自动授权后续开发。

## Phase 4A 验证与限制

- AnalysisRun 新历史、AnalysisSourceInput manifest、0003 迁移、provider-independent pipeline、OpenAI Responses adapter、严格 schema/citation validation 已实现。
- 210 个离线测试通过，6 个真实 integration 默认排除；真实 Zotero abstracts + fake provider 为 1 passed。真实 OpenAI key/model 未配置，1 skipped：NOT TESTED；没有用 fake 测试伪装 PASS。
- 旧 migrations 不变；fresh / sequential 0001→0002→0003 / existing 0002 / repeat head / schema consistency 均测；真实开发数据库未创建。
- 默认5来源、每篇24k字符、总60k字符，输出4096tokens，超限失败无截断。0自动网络/输出重试；模型配置支持能力由用户提供。
- 输入只在内存，hash 不可反推完整内容；无 Question 原文快照，不能完整重放历史 prompt。分析与引用需要人工审阅，不能保证事实正确。
- 个人 Zotero library、顺序 corpus 读取、同步单次调用；不支持大 corpus、缓存、恢复 worker、成本估算或 UI。进程中断/数据库持续不可用可能留下 running。
- 未读取真实全文或非空 annotations；fulltext 分支用 fake 验证。保持没有 Zotero 写入。
- Phase 4B 才考虑规模化 chunking/map-reduce/hierarchical synthesis；其余 UI、RAG、graph、Inbox、Claim/Evidence、后台自动化均未实现。
- 最终 PR head 的 Windows/Ubuntu CI 和 technical self-review 以 PR 报告为准；不自动合并。

## Phase 4A.1 代码与验收门槛（PR #8 历史，最新状态见下方修复记录）

- 独立DeepSeekAnalysisProvider，既有SDK 2.54.0，0新依赖/0migration。OpenAI及schema/contract保持；service只有统一安全错误码持久化改动，无provider-specific逻辑。
- 250个离线测试通过，8个integration默认排除；DeepSeek真实测试关闭opt-in时2 skipped。Ruff及双平台CI为交付检查，最终head结果见PR。
- Level A：synthetic + real DeepSeek = **NOT TESTED**；Level B：consented Zotero abstract + real DeepSeek = **NOT TESTED**。真实测试由用户在本机配置后运行，当前不能标FULLY VALIDATED。
- B只有同次A成功后才执行，独立外发开关和item key必需，不自动搜索、不读全文/批注、不写Zotero。通过后仅记录安全的日期/SDK/model/status/usage/id presence与验证结果，不记录文本或credential。
- 已查官方Responses/json_schema文档，strict参数完整schema实际兼容性仍需用户实测。未知400/404不能仅凭generic response断言根因；无prose猜测/无限retry/普通文本回退。
- 仅A+B都真实PASS才标 DeepSeek real workflow FULLY VALIDATED；不据此自动授权4B。OpenAI真实状态单独记录，不被DeepSeek覆盖。

## Option B 修复与验收

- 用户真实证据：Flash Chat plain / Responses plain PASS；Responses json_object / minimal json_schema 503；Pro Responses plain io_timeout >30s。新Chat JSON模式尚未实测，不把plain或models可见当schema验收。
- DeepSeek改Chat JSON mode，共享schema/instructions，保留应用严格校验、citation/S99拒绝、immutable history。OpenAI adapter不变，无依赖/迁移；安全503分类与组合prompt版本可追溯。
- 268离线tests通过，8真实测试默认排除，显式关闭开关时2 skipped；无真实请求。CI及review最终证据见PR。
- 用户先在同一PowerShell运行既有synthetic node；通过后才允许单独授权的Zotero摘要E2E。安全命令、metadata记录边界、已知timeout/JSON空白/输出预算风险见README和DEEPSEEK_VALIDATION.md。
- Chat JSON A/B均NOT TESTED，因此DeepSeek仍NOT FULLY VALIDATED。旧Responses实现说明是历史，不再代表当前协议。4B仍未授权。
