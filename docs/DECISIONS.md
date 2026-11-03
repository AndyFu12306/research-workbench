# 架构决定

日期：2026-11-03。除注明暂定外，以下原则已接受；接受原则不代表已实现。

## ADR-001：Zotero 是唯一文献事实源

文献 metadata、PDF、附件、笔记与批注归 Zotero 管理。Workbench 提供研究认知层，避免重复建设与同步冲突。

## ADR-002：保存引用而非文献镜像

Workbench 保存研究对象及 Zotero item 引用，按需读取事实。后续为引用增加明确 library 身份，避免跨库 key 冲突。不复制完整 Zotero library。

## ADR-003：后端优先 CLI / Local API

优先复用 zotero-cli 与 Local API；MCP 留给 Agent Client。CLI 是进程依赖，应处理退出码、超时和结构化响应。Phase 1.5 已实现只读 ZoteroClient，后续业务不直接访问 subprocess。

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

Phase 1 验证进程指定 API backend，实际完成 Local API 搜索、metadata、非空摘要和附件关系读取。全文复用上游本地 PDF 定位和文本提取，20 页完整读取通过；如实披露其本地数据库/文件路径，不声称 PDF 全部经 API 传输。非空批注尚未测试；启动器问题由 Phase 1.5 解决。核心读取成功不代表自动获准进入产品功能阶段。

## ADR-012：Windows launcher 修复属于安装环境而非适配器特殊分支

Phase 1.5 实测发现两个独立因素：旧 Codex 进程 PATH 不含用户工具 bin，导致 shutil.which 两种名称均返回 None；刷新为相同 PATH 后，PowerShell Get-Command / where 与 Python which 指向相同原生 PE .exe，但 Python 启动失败。

进一步检查 PE 中内嵌的 shebang 解释器路径指向 `%APPDATA%/uv/tools/.../Scripts/python.exe`。该路径在 PowerShell 中可见，在外部 Python 中不存在；setup-info 返回的实际解释器位于 Codex 的 `LocalCache/Roaming` 应用目录映射下，并且该实际解释器可直接启动。实际目录下的 CLI launcher 仍携带相同旧路径，也失败。因此问题是本次安装环境的 AppData 映射与 launcher 记录路径不一致，而不是 shell=False、命令引号或仅仅 executable 未使用绝对路径。

修复时将 UV_TOOL_DIR 指向用户 `.local/share/uv/tools`，然后 `uv tool install --force --python 3.13 zotero-mcp-server==0.15.2` 重新生成同版本环境及入口，并将 UV_TOOL_DIR 保存为用户环境变量，使未来 uv 管理同一环境。未手动删除原有环境，未修改 Zotero 数据、配置密钥或上游源码。

Python 随后以 shutil.which 发现的绝对 .exe、参数列表、shell=False 成功执行 config 和完整业务流程。适配器无需 Codex 名称、个人路径、PowerShell 包装、python -m 回退或 shell=True。其他 Windows 用户不必盲目应用本机修复；应先重复 PATH、文件可见性和解释器路径诊断。

## ADR-013：标准库 DTO、集中 runner 与有限错误边界

运行时使用标准库 dataclass，不为轻量 DTO 增加 Pydantic。client 只构造允许的读取命令，runner 是唯一进程层。使用 cli_path / ZOTERO_CLI_PATH / shutil.which 的发现顺序；显式覆盖必须为原生可执行绝对路径，不能默默回退。

每个进程有有限超时，UTF-8 严格解码，校验 exit、ok、schema 和响应命令。错误保留原始诊断但不自动记录私人内容。区分 executable、cli、cli_output 和 local_api 层。health 的 Client 状态从 API 推断，避免引入跨平台进程枚举依赖。

## ADR-014：截断、样本与阶段验收

get_fulltext 遇截断时调用上游 read 补读全部页面并验证覆盖，不将摘要或部分正文当全文。兼容正常空白页；不自行实现 PDF 提取。默认单元测试不调用 Zotero，真实测试用 integration marker 显式选择，真实 item key 通过环境变量提供。没有非空批注样本时标记 NOT TESTED WITH NON-EMPTY DATA，不为验收写入 Zotero、不阻塞 Phase 1.5。

开发依赖增加 pytest、Ruff，分别用于测试和代码检查；Hatchling 只做打包。所有产品运行逻辑仍无第三方依赖。Phase 1.5 结束先 review，保持 feature branch，不自动合并或进入数据库/UI/AI Analysis。
