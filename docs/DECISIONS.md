# 架构决定

日期：2026-11-03。除注明暂定外，以下原则已接受；接受原则不代表已实现。

## ADR-001：Zotero 是唯一文献事实源

文献 metadata、PDF、附件、笔记与批注归 Zotero 管理。Workbench 提供研究认知层，避免重复建设与同步冲突。

## ADR-002：保存引用而非文献镜像

Workbench 保存研究对象及 Zotero item 引用，按需读取事实。后续为引用增加明确 library 身份，避免跨库 key 冲突。不复制完整 Zotero library。

## ADR-003：后端优先 CLI / Local API

优先复用 zotero-cli 与 Local API；MCP 留给 Agent Client。CLI 是进程依赖，应处理退出码、超时和结构化响应。当前未实现适配器。

## ADR-004：早期不 fork zotero-mcp

54yyyu/zotero-mcp 作为外部依赖，避免承担上游维护。只有验证出无法通过配置或上游协作解决的实际需求，才重新评估。

## ADR-005：第一阶段仅做只读 Spike

先验证环境、搜索、引用、metadata、abstract、PDF 全文、annotations。禁止 Zotero 写入与写权限授权；失败按层定位，不用 workaround 伪装成功。通过前不建设产品功能。

## ADR-006：Python + SQLite，逐阶段实现

初期 Python 后端与 SQLite，数据库仅保存研究数据。暂定使用本机已有 Python 3.13，降低新解释器与第三方依赖兼容性风险；实际兼容性以固定版本安装测试为准。PostgreSQL 和复杂基础设施按需求再评估。

## ADR-007：AI 与人工、原文分离

原始来源、用户观点、AI 内容和推断必须可辨识，重要结论逐步建立证据追溯，不将生成内容呈现为论文事实。

## ADR-008：CLI 与 Local API 分别验收

上游当前文档说明部分本地读取直接使用 zotero.sqlite。将 CLI 成功与 API 成功分开记录；不自行实现数据库适配或以此绕过失败。后端正式使用哪些读取路径，必须在固定发布版的 Spike 完成后明确，不默默改变目标链路。

## ADR-009：最小 Git 流程与隐私保护

稳定主分支名为 main；功能分支单一目的，提交前 review、验证和敏感信息检查。早期不建复杂 CI。不提交本地研究数据或秘密；外部写入先预览和明确批准，高风险 Git 操作需确认。

## ADR-010：Zotero CLI 使用独立 uv tool 环境

依用户 2026-11-03 明确要求，工具以 `uv tool install --python 3.13 zotero-mcp-server` 安装，不进入 Workbench 项目依赖。当前安装版 0.15.2，未请求 extras。产品未来自己的依赖仍使用项目虚拟环境。复现时固定已验证版本，不自动升级。

## ADR-011：显式验收 API，复用上游本地 PDF 能力

本轮验证进程指定 API backend，实际完成 Local API 搜索、metadata、非空摘要和附件关系读取。全文复用上游本地 PDF 定位和文本提取，20 页完整读取通过；如实披露其本地数据库/文件路径，不声称 PDF 全部经 API 传输。非空批注尚未测试，Python subprocess 启动器问题保留待解决；不因核心读取成功而自动推进产品开发。
