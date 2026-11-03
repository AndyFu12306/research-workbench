# Zotero Technical Spike

检查日期：2026-11-03（Asia/Shanghai）。本轮结论：**核心只读链路 PASS**。搜索、真实 item key、metadata、非空 abstract、PDF 全部页面均已实际读取。所选论文没有批注，非空 annotations 读取尚未验证；Python subprocess 调用 uv tool 启动器存在单独的启动问题。没有开始 Workbench 功能开发，也未自动进入 Phase 2。

## 环境与安装

- Windows / PowerShell 7.6.5；Zotero 10.0.7 已由用户启动，检测到运行进程。
- Git 2.56.0.windows.3；目录 `C:\Projects\research-workbench`，分支 `feat/zotero-spike`，尚无提交、无 origin。本轮未操作 GitHub。
- 系统默认 Python 3.15.2，另有 Python 3.13.17；工具安装显式选择 Python 3.13，安装后直接检查 tool 环境解释器确认是 3.13.17。
- 初始 `uv --version` 找不到命令；执行 `winget install --id=astral-sh.uv -e --accept-source-agreements --accept-package-agreements --disable-interactivity` 后安装 uv 0.13.6。winget 报告安装包哈希校验成功。
- 执行 `uv tool install --python 3.13 zotero-mcp-server`，解析并安装 83 个基础依赖包，最终版本 `zotero-mcp-server 0.15.2`。相关依赖包括 pyzotero 1.16.0、pdf-inspector 0.2.6、fastmcp 4.1.6。
- 未请求 semantic、scite、pdf 或 all extras。基础包已有文本提取能力，本轮未安装 PyMuPDF、向量数据库或模型索引。
- 工具由 uv 管理独立环境，入口位于用户 `.local\bin`，没有创建项目 `.venv` 或把工具加入项目依赖。安装后需刷新当前进程 PATH 或重启终端。
- uv 安装期间报告两条旧版依赖 version specifier 规范化警告；安装退出码为 0，不等于依赖完全无风险。

## 分层验收

| 层 / 能力 | 状态 | 实测证据 |
| --- | --- | --- |
| Zotero Client | PASS | 已安装并运行，检测到 Zotero 进程 |
| Zotero Local API | PASS | 指定的 Test-NetConnection 最终 TcpTestSucceeded=True；curl GET 返回 HTTP 200，JSON 含真实 item key、data、itemType |
| zotero-mcp installation | PASS | uv tool 安装 0.15.2 成功；setup-info 退出码 0 |
| zotero-cli | PASS | PowerShell 中 --help、config 和业务命令正常；JSON ok=true、退出码 0 |
| Local read-only configuration | PASS | ZOTERO_LOCAL=true；Web API key 与本地写入 key 均未配置；setup-info 显示 write mode=none |
| Codex Agent Skill | PASS | install-skill 成功安装至 .agents/skills/zotero-cli；已阅读 SKILL.md 与所需命令帮助 |
| Library search | PASS | 关键词 agent 返回 2 个真实条目，不是空结果 |
| Item key | PASS | 从搜索响应获取真实 key，并用于后续 metadata、附件、全文读取；可提交报告不公开真实标识 |
| Metadata | PASS | 两个条目均返回 title、creators、date、DOI 等，顶层 key 与 data.key 一致 |
| Abstract | PASS | 两个 abstractNote 分别为 1,216 和 1,252 字符，均非空 |
| PDF attachment | PASS | 第二个条目有 application/pdf、imported_file 附件；父 item 关系匹配；本地文件存在，3,060,113 字节 |
| PDF full text | PASS | 按页读取 1–20 页成功，109,551 字符，20 个连续页面标题且每页非空 |
| Annotations | PASS | 所选论文 annotations list 成功，返回空数组；仅验证读取接口和空结果处理 |
| 非空 annotations 内容及页定位 | NOT TESTED | 所选论文没有批注；未创建测试批注，也未扩大扫描整库 |
| Python subprocess → tool 启动器 | FAIL | 直接调用用户目录中的 zotero-cli.exe 返回 uv trampoline 路径解析错误；见问题记录 |
| MCP 协议握手 | NOT TESTED | 本轮验证 CLI；未启动 MCP 协议服务，不能以安装或 CLI 成功替代握手验收 |
| Workbench 服务 / 数据库 / UI | NOT TESTED | 未实现，按本轮范围保持不开发 |

核心 PASS 只适用于本机本轮读取验证，不代表所有文献、所有附件格式、长期稳定性或 Python 后端调用方式都已验证。

## 本轮执行方式

### 1. Local API 门槛

```powershell
Test-NetConnection localhost -Port 23119
```

IPv6 `::1` 尝试报告连接失败，随后 `127.0.0.1` 成功，最终 `TcpTestSucceeded=True`。IPv6 警告不等于整项失败。

```powershell
curl.exe --noproxy '*' --max-time 5 --silent --show-error --output NUL --write-out 'http_code=%{http_code} bytes=%{size_download}\n' 'http://localhost:23119/api/users/0/items?limit=1'
```

得到 `http_code=200 bytes=1512`。随后同一 API GET 的 JSON 解析成功，数组长度 1，具有 key、data 和 itemType。所有 API 请求均为本机只读 GET，没有切换云 API。

### 2. 工具与本地配置

```powershell
uv --version
uv tool install --python 3.13 zotero-mcp-server
zotero-cli --help
zotero-mcp setup-info
zotero-mcp setup --no-claude --skip-semantic-search
zotero-cli --json config
zotero-mcp install-skill
```

uv 不存在时先按用户指定的 winget 命令安装。本地配置保存在用户目录 `.config/zotero-mcp/config.json`，不在仓库中。setup-info 和 config 输出仅检查所需字段，未使用 --show-secrets；未配置 Web API key、未授权本地写入。

原有文档禁止未经授权的 setup；本轮用户明确批准上述 setup 和 install-skill，故执行这两个范围明确的配置步骤，没有扩大 Zotero 权限。

### 3. 明确 API 后端与来源读取

安装版 utils.py 支持 ZOTERO_BACKEND / ZOTERO_SEARCH_BACKEND；默认本地模式偏好 SQLite。本轮进程显式设定以下变量，config 已确认生效，不依靠默认 SQLite metadata 路径冒充 API 成功：

```powershell
$env:PYTHONUTF8 = '1'
$env:ZOTERO_LOCAL = 'true'
$env:ZOTERO_BACKEND = 'api'
$env:ZOTERO_SEARCH_BACKEND = 'api'
```

后端变量仅设置于本轮验证进程，没有自动永久写入全局环境或上游配置。新终端复测时必须重新设置。

已先阅读 search、get、get metadata、get children、get fulltext、annotations list、read、path 的帮助，再执行以下读取。尖括号是报告中的脱敏占位符，实际执行使用搜索所得真实 key，不能原样复制占位符运行。

```powershell
zotero-cli --json search 'agent' --limit 5 --detail keys_only
zotero-cli --json get metadata '<ITEM_KEY>'
zotero-cli --json get children '<ITEM_KEY>'
zotero-cli --json get metadata '<ATTACHMENT_KEY>'
zotero-cli --json get fulltext '<ITEM_KEY>'
zotero-cli --json read '<ITEM_KEY>' --start-page 1 --end-page 20 --format text
zotero-cli --json annotations list --item-key '<ITEM_KEY>' --format json
zotero-cli --json path '<ATTACHMENT_KEY>'
```

第一个候选条目只有 text/html、linked_url 附件，不将它当 PDF；第二个候选确认有本地 PDF 后才做全文验证。

所有业务响应检查退出码和 JSON ok。`get fulltext` 初次返回 44,310 字符但 `truncated=true`、pages_read=10、page_count=20，因此该次尝试不满足全文验收。随后使用上游已有的 read 命令完整读取 20 页，逐页核对连续性与非空文本，不自行编写 PDF 提取器。

### 4. 实际数据路径

```text
PowerShell → zotero-cli → 上游 API backend / pyzotero → Zotero Local API
                                                （搜索、metadata、附件关系等）
PowerShell → zotero-cli → 上游本地附件定位 / PDF 提取 → Zotero 本地 PDF
                                                （全文文本）
```

安装版 tools/read_pdf.py 的本地路径解析仍使用上游 LocalZoteroReader，优先读取 Zotero 本地附件文件；library.py 的附件路径查询也可能直接读 SQLite。这不会因 ZOTERO_BACKEND=api 而完全消失。因此本轮证明了 API 搜索与元数据，以及上游工具读取本地 PDF 的能力，**没有证明 PDF 全文全部经 Local API 传输**。这是如实记录的上游能力边界，未自行访问数据库或用它掩盖 API 失败。

## 问题、实际错误及下一步

| 命令 / 尝试 | 状态 | 实际错误或限制 | 层级与处理建议 |
| --- | --- | --- | --- |
| 安装前 uv --version | FAIL（已解决） | The term 'uv' is not recognized | 工具环境；winget 安装并刷新 PATH 后 uv --version 成功 |
| Invoke-WebRequest 到 http://127.0.0.1:23119/api/users/0/items?limit=1，5 秒超时、NoProxy | FAIL | HttpRequestException；内层 HttpIOException: The response ended prematurely. (ResponseEnded) | PowerShell HTTP 客户端 / 本地 HTTP 交互边界；根因未确认。保留错误，不等同端口拒绝连接 |
| Invoke-WebRequest 到 http://localhost:23119/api/users/0/items?limit=1，5 秒超时、NoProxy | FAIL | The request was canceled due to the configured HttpClient.Timeout of 5 seconds elapsing. | 同上；curl 对同一本机 API 已返回有效 JSON，且 API 后端 CLI 业务读取成功。后续单独对照 HTTP 客户端行为，不回退云端或改用数据库掩盖故障 |
| zotero-cli --json get fulltext '<ITEM_KEY>' | FAIL（该次完整性验收） | 命令本身 exit=0 / ok=true，但 truncated=true，仅读取 10/20 页 | 上游返回内容范围；调用已核实的 read 读取全部 20 页后全文验收通过 |
| Python subprocess.run([用户 .local/bin/zotero-cli.exe 的绝对路径, '--json', 'config'])，混合斜杠与 pathlib 原生路径均尝试 | FAIL | exit=1，stderr: error: uv trampoline failed to canonicalize script path | uv tool 进程启动层；PowerShell 直接调用成功。根因未确定，需后续核对 uv 启动器路径、Windows 应用目录映射及 Python 子进程环境，不通过改用数据库绕过。正式 Python 后端集成前应修复并重测 |
| annotations list 所选论文 | PASS（空结果） | 无已有批注 | 不是接口故障。若需非空批注验收，由用户指定已有批注的来源再读取；不创建批注 |

PowerShell 中临时诊断脚本曾在 Invoke-WebRequest 失败后继续解析空响应，产生级联错误；这些不作为 Zotero 返回内容的证据。后续有效 JSON 验证与 CLI 验证均独立检查成功状态。

## 安全与交付

- 本轮只允许的 Zotero 业务调用为搜索和读取，没有 metadata、Tags、Collections、Notes、Annotations、items 或 PDF 写操作，没有运行 authorize-local。
- uv / 工具环境、用户目录中的本地配置和仓库 skill 安装，是本轮用户明确批准的环境变更。
- 私人原始诊断输出暂存于受 Git 忽略的 local/，包括少量 metadata、来源 key、全文和空批注响应；不是 Workbench 数据库或长期镜像。后续提交前仍需确认它们不会被纳入 Git。可提交报告只保留脱敏统计。
- 全文读取只是文本提取能力验证，不保证数学公式、表格或图像内容的语义精度；未额外引入 OCR 或 PDF Reader。
- 当前分支无提交、无远程；不自动 commit、push、建 PR 或合并。
- 已核对可纳入 Git 的 12 个文件：UTF-8、本地 Markdown 链接、维护文档行尾空白与常见凭据模式检查通过；对照本轮取得的 4 个真实来源/附件 key，未在这些文件中发现泄漏。local/ 中的原始诊断结果和 .env、数据库、PDF 均由忽略规则排除。没有产品代码，因此本轮不引入单元测试或 CI。

## 下一步

保留当前阶段，不开发产品功能。优先修复 Python subprocess 启动器问题；如需扩展验收，再对用户指定的已有批注论文完成非空 annotations 读取。只有用户决定继续后，才讨论 Phase 2 的最小数据库。

## 历史与依据

上一轮检查时 Zotero 未运行、Local API 拒绝连接，uv 与 CLI 未找到，所有文献能力均未测试；本轮已用实际结果替换旧状态，不将过去失败推断成架构不可行。

本轮以安装版 0.15.2 的 --help、skill 和源文件为主要命令与数据路径依据。辅助资料：[上游仓库](https://github.com/54yyyu/zotero-mcp)、[Zotero API 官方文档](https://www.zotero.org/support/dev/web_api/v3/basics)。上游 main 文档可能变化，复测以固定安装版本为准。
