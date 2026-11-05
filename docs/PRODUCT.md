# 产品定位

Personal Research Workbench 是个人 AI 研究认知层，不是“AI 版 Zotero”。它帮助用户围绕长期研究项目与具体问题，把阅读转化为有来源、可追溯的分析和跨论文综合结论。

## 事实边界

Zotero 是唯一文献事实源，负责 PDF、Title、Authors、Journal、DOI、Abstract、Publication Date、Collections、Tags、Notes、PDF Annotations 和 Attachments。Workbench 不重新实现文献管理、PDF Reader、导入和全文提取，不形成第二套文献数据库。

Workbench 负责 Project、Question、Source relationship、AI Analysis，并逐步扩展 Claim、Evidence、Concept、Synthesis、Research Inbox、Research Memory、Workflow 和 Automation。Project 是研究任务，不等于 Zotero Collection。

## 长期领域对象

| 对象 | 含义与边界 |
| --- | --- |
| Source | 研究来源；首期以 Zotero item 引用表示，未来可支持新闻、博客、GitHub、报告、网站 |
| Project | 长期研究项目，例如 AI Agent 技术研究 |
| Question | 核心组织对象，例如 Agent Memory 有哪些主要实现路线；围绕它关联来源、分析、争议、证据和后续问题 |
| Claim | 某个来源提出的独立主张 |
| Evidence | 支持或反驳主张的具体证据，保留来源、页码、表格等定位 |
| Concept | 研究概念，例如 Episodic Memory、Harness、Context Engineering |
| Synthesis | 跨论文综合：共识、分歧、关键证据、代表论文、研究空白和未解决问题，而非摘要拼接 |
| InboxItem | 尚待判断的信息，支持 Ignore、Archive、Keep、Convert to Zotero、Attach to Project 等后续决策 |

## v0.1

范围严格限定为 `Project → Question → Zotero Source → AI Analysis`。分析回答技术路线、代表论文、核心结论、主要争议、研究空白与下一步问题。

暂不实现 Knowledge Graph、完整 Claim/Evidence、Inbox、Automation、Research Memory、多用户、云同步、复杂权限、精美 UI、大规模 RAG 或自定义 PDF Reader。

## Phase 2 已合并的研究模型

本阶段只持久化 Workbench 的 Project 与 Question，不建设完整 v0.1。Project 是长期研究任务；Question 是其下的研究问题，一个问题只属于一个项目，不引入多对多。

两个对象均具有 id、title、description、status、created_at、updated_at，Question 另有必填 project_id。状态只有 active、archived。提供创建、读取、有限列表、标题/描述更新与归档；不提供硬删除、恢复状态或复杂 workflow。归档是隐藏和保留，不会自动归档或删除子问题。列表默认排除归档对象，但可明确查询；get 仍可找到归档对象。

Workbench SQLite 是研究领域数据的事实源，与 Zotero 的文献事实源属于不同边界。Project/Question 的标题是研究任务或问题名称，不是复制的 paper title。Phase 3 增加引用及关系，不增加 metadata 缓存、Claim、Evidence、Synthesis、Inbox、AI Analysis 或 UI。

## Phase 3 已合并的来源关联

将已有 Question 与真实 Zotero bibliographic item 关联；一个 Question 可关联多个来源，一个来源可服务多个 Question。SourceRef 是 provider + scope + external_key 身份引用，不是第二份论文对象；当前只读个人 Zotero library。标题、作者、DOI、journal、abstract、PDF、全文与批注均不持久化。

attach 验证真实文献后原子保存引用及关系，重复操作不重复建记录；列表实时读取 metadata，也可离线仅列引用。缺失文献显示 unresolved，连接失败显示 unavailable，损坏响应显示 error，均保留研究关系供后续修复。detach 只移除选定 Question 的关联，保留 SourceRef 和 Zotero 内容；不进行孤立引用清理。

本阶段不读取 PDF、不新增依赖、不提供 UI 或分析功能；未来产品展示可直接消费这些状态，但该展示尚未实现。

## 信息来源与分析质量

始终区分论文作者原始信息、用户观点、AI 内容和 AI 推断。未来可通过 `origin = source | user | ai` 并附内容类型区分生成和推断；具体 schema 尚未确定。

AI Analysis 长期逐步结构化为 research_question、core_claims、methods、datasets、results、limitations、contradictions、related_concepts、open_questions。重要结论逐渐支持 `AI Analysis → Claim → Evidence → Source → Zotero PDF page` 追溯。v0.1 不提前实现完整证据系统。

## 权限

初期读取自动允许，外部写入需要明确批准，删除需要强确认。第一阶段 Zotero 写入全部禁止。未来写入采用 `Preview → User Approval → Execute`，不得默认自动修改用户 Library。

## Phase 4A 当前交付

一个 Question 及其已关联来源，可显式生成一次结构化 AI 综合，输出 summary、main_approaches、key_findings、agreements、disagreements、research_gaps、next_questions。重要条目保留 SourceRef IDs；这是 AI 生成分析而非文献事实，不能把引用校验当作结论已经证实。

每次创建独立 AnalysisRun 历史，记录 provider/model、prompt/schema version、usage、结果或安全错误；AnalysisSourceInput 只记录本次来源、顺序、输入类型和内容 fingerprint。源 title/authors/abstract/PDF text 只在内存，不构建文献镜像。Zotero 是文献事实源，Workbench 保存研究工作流与分析，AI provider 只提供计算。

支持 abstracts / fulltext_preferred，明确摘要 fallback，严格缺失和预算策略，无静默省略、无截断。完成和失败历史均不覆盖；无 UI、RAG、完整 Claim/Evidence、后台任务或自动反复分析。后续规模化 chunking / map-reduce / hierarchical synthesis 留给 Phase 4B，须另行授权。


## Phase 4B.4 当前交付边界

Issue #19（v2 + v2.1）将显式选择的2–5篇已关联文献按文献边界逐篇完整处理，再做单次跨文献比较。只允许完整全文，拒绝同身份或完全相同全文；近似重复、译本或来源独立性仍需人工判断。处理完整不等于输出引用覆盖全部输入，更不等于语义穷尽或事实正确。

Workbench只保存生成分析、执行/引用链、版本与fingerprint；Zotero仍是唯一文献事实源。容量不足明确失败并保留成功历史，不暗中截断、选文献、改用摘要或自动多级压缩。阶段及父执行的成功只在完整事务提交后出现。此阶段没有可视产品界面；真实外发与4B.5质量验收均未授权。
