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

## 信息来源与分析质量

始终区分论文作者原始信息、用户观点、AI 内容和 AI 推断。未来可通过 `origin = source | user | ai` 并附内容类型区分生成和推断；具体 schema 尚未确定。

AI Analysis 长期逐步结构化为 research_question、core_claims、methods、datasets、results、limitations、contradictions、related_concepts、open_questions。重要结论逐渐支持 `AI Analysis → Claim → Evidence → Source → Zotero PDF page` 追溯。v0.1 不提前实现完整证据系统。

## 权限

初期读取自动允许，外部写入需要明确批准，删除需要强确认。第一阶段 Zotero 写入全部禁止。未来写入采用 `Preview → User Approval → Execute`，不得默认自动修改用户 Library。
