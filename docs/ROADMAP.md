# 路线与阶段门槛

当前：**Phase 4B.3 — 单文献 Map/Reduce 编排与不可变阶段历史**。用户于 2026-11-05（Asia/Shanghai）审核架构后正式授权实现，[Issue #17](https://github.com/AndyFu12306/research-workbench/issues/17)；从最新 main `968acf5d1d38b2b62f3e368f5b5dfb4d415846b7` 建立独立 `research-workbench-phase4b3` / `codex/phase4b-document-pipeline`。PR #16 已合并、Issue #15 Completed；全部旧 worktree/分支保留。仅离线验证，PR 保持 Open、不自动 merge；不进入 4B.4。新 StageProvider 真实 API 与旧真实 Zotero Level B 均仍 NOT TESTED。

| Phase | 内容 | 状态 |
| --- | --- | --- |
| 0 | 产品 / 架构设计 | 原则已记录 |
| 1 | Zotero Technical Spike | Core read-only spike: PASS |
| 1.5 | Zotero Integration Adapter | PR #1 已合并 |
| 2 | Project + Question + Minimal Database | PR #2 已合并，main 包含 6bb505ab |
| 3 | Question ↔ Zotero Source Association | PR #4 已合并 |
| 4A | Structured AI Analysis MVP | PR #6 已合并；真实 OpenAI 仍 NOT TESTED |
| 4A.1 | DeepSeek provider + hardening | PR #8 / #12 已合并；正式 Level A 单次真实 PASS；真实 Zotero B 待测 |
| 4B.1 | 确定性 chunking / 有界 Map 输入计划 | PR #14 已合并；Issue #13 Completed |
| 4B.2 | 阶段契约 / 局部引用 / 单次 StageProvider | PR #16 已合并；Issue #15 Completed；真实 API 未测 |
| 4B.3 | 单文献顺序 Map / 单次 Reduce / 阶段历史 | 当前授权开发与离线验收；Issue #17，PR 保持 Open |
| 4B.4–4B.5 | 跨文献编排及受控质量/真实验收 | 未开始，需另行授权 |
| 后续 | Basic UI | 未开始，需另行授权 |
| 后续 | Inbox / Claim + Evidence / Knowledge Graph / Memory / Automation | 延后 |

原路线中 Project/Question 两个里程碑已纳入 Phase 2；本轮按用户明确命名，将来源关联称为 Phase 3，不重复实现既有模型。

## Phase 4B 分期与验收

- **4B.1**：精确 `[start, end)`、段落优先、超长段安全切分、有限重叠、来源隔离、版本和 hash；预算包括 Question、指令、格式包装、输出预留和安全余量，限制输入字符数、来源数、Chunk 数、Map 调用数及估算总量。超限拒绝；Map 一块一计划调用，不预测 Reduce 动态输入。
- **4B.2**：独立的三阶段 prompt/schema 版本与 frozen DTO；允许合法空结果，独立 JSON/结构/局部引用校验，应用分配 Finding ID 并重新验证子结果。Map 绑定当前 Chunk，Reduce 仅引用当前子条目；闭包追溯 SourceIdentity / fingerprint / Chunk，合并覆盖范围而非统计证据数。OpenAI/DeepSeek 单次 Responses StageProvider、能力前检与最终请求预算；不改变 v1 generate 行为，4B.1 MapTemplate 仍只是规划材料。
- **4B.3 / 4B.4**：单篇文献分层编排与历史，再扩展多文献分层综合。需要时另设计新 migration，不改 0001–0003；不把生成摘要伪装成原文。
- **4B.5**：冲突和信息损失的质量评估，以及用户独立 opt-in 的真实验收。旧 Level B 摘要验收与 4B 全文/分层验收分别记录，均不能由离线测试代替。
- 4B.1 验收：合成中英文、emoji、CRLF、空白/超长段、重叠和完整覆盖、确定性/hash/来源隔离、预算临界值、数量限制及隐私测试；完整离线回归、Ruff、锁检查、最终 PR head 双平台 CI 和自审。具体结果见 PR #14。
- 4B.2 本地离线验收（PR #16 合并前完整性修复后）：**601 passed，8 integration deselected**，其中阶段验证/provider 测试161项；Ruff format/check 与 `uv lock --check` 通过。覆盖三阶段/空结果/结构和数量限制、局部引用、伪造子结果/旧 fingerprint/来源隔离/循环/图上限、重叠区间、最终请求预算临界值、两家真实 SDK MockTransport、日志/repr/异常隐私及 v1 全量回归。补充18项测试：原 ca1b2d6 最小复现确认父声明 A/B 而仅提供 A 子结果会被接受；现要求每层父来源身份/fingerprint 映射等于直接子结果来源并集，含空结果和嵌套节点，并验证 Provider 网络前拒绝。兄弟子树共享来源仍允许，模型重复加权局限见 ARCHITECTURE/ADR-048。最终 PR head 的 Windows/Ubuntu CI 和审查结果保存在 PR；新阶段真实 API 均 NOT TESTED，真实 Zotero 未调用。

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

## Phase 4A.1 原始PR #8验收记录（历史）

- 独立DeepSeekAnalysisProvider，既有SDK 2.54.0，0新依赖/0migration。OpenAI及schema/contract保持；service只有统一安全错误码持久化改动，无provider-specific逻辑。
- 250个离线测试通过，8个integration默认排除；DeepSeek真实测试关闭opt-in时2 skipped。Ruff及双平台CI为交付检查，最终head结果见PR。
- Level A：synthetic + real DeepSeek = **NOT TESTED**；Level B：consented Zotero abstract + real DeepSeek = **NOT TESTED**。真实测试由用户在本机配置后运行，当前不能标FULLY VALIDATED。
- B只有同次A成功后才执行，独立外发开关和item key必需，不自动搜索、不读全文/批注、不写Zotero。通过后仅记录安全的日期/SDK/model/status/usage/id presence与验证结果，不记录文本或credential。
- 已查官方Responses/json_schema文档，strict参数完整schema实际兼容性仍需用户实测。未知400/404不能仅凭generic response断言根因；无prose猜测/无限retry/普通文本回退。
- 仅A+B都真实PASS才标 DeepSeek real workflow FULLY VALIDATED；不据此自动授权4B。OpenAI真实状态单独记录，不被DeepSeek覆盖。

## Responses hardening 交付门槛（2026-11-05，历史阶段记录）

- Option A方向已确定；仅Responses adapter、安全错误目录、回归测试/验收入口与文档。OpenAI、共享prompt/schema、corpus、Zotero、数据库/migrations、依赖不改。
- 315离线测试通过，8真实测试默认排除；Ruff和锁检查通过。包含503/404、完整响应结构、JSON/重复key/引用、failed分类持久化、usage/id和immutable history。最终双平台CI和self-review见独立PR。
- Responses原型A单次真实PASS（10.494s，1049/1397 input/output tokens）与Chat A单次PASS均为用户证据，不代表正式hardening版本或长期可靠性验收。历史故障保留，见DEEPSEEK_VALIDATION.md。
- 正式版本已由用户在独立worktree运行synthetic A单次PASS（2026-11-05，1049/925 tokens、immutable/manifest/security通过）；真实Zotero B仍需独立外发同意及明确item key，未在本轮执行。保留analysis-v1 / analysis-schema-v1、4096预算、零retry/fallback。PR #10未关闭/合并，4B未授权。

以上保留当时的阶段边界；随后 PR #12 已合并、PR #10 已关闭未合并，用户现已授权 4B.1，当前状态以上表为准。Level B 未测的事实不变。

## Phase 4B.3 授权范围与验收

2026-11-05 用户通过单次 Reduce 架构并授权 Issue #17，取代旧阶段“4B.3未授权”描述；历史4B.1/4B.2范围与验收不改。完整计划原子落盘，顺序 Map，持久化全覆盖门槛，单次 Document Reduce，不可变阶段历史。追加0004、保留0001–0003和Phase4A；全流程预算含Reduce预留，Decimal名义成本与未知费用明确区分。仅完整全文，无摘要fallback、自动缩块、树形Reduce、重试、恢复或缓存。

验收覆盖合成长文献/单块、混合及全部空结果、Map失败/拒答/超时、Reduce动态超限、持久化遗漏/篡改、事务提交失败/回滚、中断/强退残留running、终态不可覆盖/显式重跑、历史重开、detach追溯、局部引用/跨执行隔离、预算/费用和隐私，以及0003旧库升级/重复升级/映射一致性和Phase4A全量回归。实际测试与最终head双平台CI证据在本轮PR记录；不将Fake/Mock或跳过标记转记真实PASS。新阶段真实API与旧Level B均仍未测。4B.4–4B.5及真实付费/全文外发均未授权。

4B.3本机实际验收：**674 passed，8 integration deselected**（新增执行/迁移73项）；Ruff check/format、锁检查、临时Alembic upgrade/current/check/repeat、wheel迁移资源检查和新增diff安全/技术自审通过。最终head Windows/Ubuntu CI以PR记录为准。真实AI/Zotero均未调用。

PR #18 合并前修复：补齐 Question.description。先新增回归并确认旧代码7项失败，再统一 title/description JSON 快照、规划/阶段请求和完整问题指纹，并在 create_run 事务中重验数据库当前字段；未修改迁移、阶段契约或 Phase 4A。补充8项离线测试，覆盖空描述、描述更新、计划期间/提交后变更、Planner/Provider预算及隐私。最终验证与新 head 双平台 CI 记录在现有 PR #18，保持 Open，不创建新分支/PR，不进入4B.4。

本次修复本机验证：**682 passed，8 deselected**；迁移回归19项通过，Ruff check/format、锁检查及变更敏感信息扫描通过。真实服务未调用；最终 head 双平台 CI 结果以 PR #18 为准。
