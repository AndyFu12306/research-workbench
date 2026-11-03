# 路线与阶段门槛

当前：**Phase 1.5，只读 Zotero Integration Adapter 已实现、验证并 review；停留本阶段，不自动推进数据库、UI 或 AI Analysis**。检查日期：2026-11-03（Asia/Shanghai）。

| Phase | 内容 | 状态 |
| --- | --- | --- |
| 0 | 产品 / 架构设计 | 基本完成；原则已写入仓库 |
| 1 | Zotero Technical Spike | Core read-only spike: PASS |
| 1.5 | Zotero Integration Adapter | 已完成本地实现与 review；非空真实批注样本仍未测 |
| 2 | Minimal Workbench database | 未开始；优先 SQLite |
| 3 | Project model | 未开始 |
| 4 | Question model | 未开始 |
| 5 | Zotero Source association | 未开始 |
| 6 | AI Analysis | 未开始 |
| 7 | Basic UI | 未开始 |
| 8 | Research Inbox | 延后 |
| 9 | Claim + Evidence | 延后 |
| 10 | Concept + Knowledge Graph | 延后 |
| 11 | Research Memory | 延后 |
| 12 | Automation | 延后 |

## 已完成

- 阅读产品、数据边界、只读要求和 Git 工作流要求，建立长期上下文文档。
- 检查工作目录、Git、Python、PowerShell、虚拟环境、安装登记、进程、工具可用性及回环 API。
- 首轮初始化本地 Git；Phase 1.5 创建 feat/zotero-adapter，先提交 Phase 1 文档基线并建立 main 引用，再提交适配器阶段成果。未创建远程仓库、未推送或合并。
- 首轮确认 Zotero 未运行、Local API 拒绝连接；本轮用户启动后，TCP 与实际 API JSON GET 均通过。
- 安装 uv 0.13.6 和独立 uv tool zotero-mcp-server 0.15.2，完成本地只读配置与 Codex CLI skill 安装。
- 显式使用 API 后端，搜索返回 2 个真实条目，metadata、非空摘要和 PDF 附件关系读取均通过。
- 发现 get fulltext 截断 10/20 页，随后使用 read 完整读取 20 页、109,551 字符，每页非空。
- annotations 查询成功返回空数组；没有创建测试批注。明确记录 PDF 本地定位/提取与 API metadata 的路径差异。

## Phase 1.5 已完成

- 定位旧 PATH 与 AppData 映射下 uv launcher 记录路径不可达两个原因；重装同版本工具到非映射目录，Python shell=False 成功，无源码中的个人路径。
- 建立集中 runner、只读 ZoteroClient、不可变 DTO 和领域异常；正常业务不直接调用 subprocess。
- health_check 分别报告 Client / API / CLI；Client 从 API 推断，不对未知进程状态作错误断言。
- 从 Python 真实验证搜索、metadata、非空摘要和完整 20 页 PDF；annotations 查询成功但样本为空。
- 63 个单元测试通过，默认排除 3 个 integration；显式集成测试 2 通过、1 因非空批注样本缺失跳过。
- Ruff 格式和 lint 通过；配置 uv.lock、标准 src 目录、测试隔离与最小 Windows / Ubuntu CI（远程尚未运行）。
- 文档与代码 review 完成：确认只读命令、无个人路径、无 metadata 持久化，错误/截断不被伪装为成功。review 中修复成功 stderr 保留、HTTP 重定向禁用和正常空白页处理。

## 剩余限制与后续门槛

1. Annotations with non-empty sample：NOT TESTED WITH NON-EMPTY DATA。用户可指定已有批注论文补验，不创建数据，不阻塞本阶段。
2. PowerShell Invoke-WebRequest 历史提前断开/超时尚未单独查明；Python GET 与 API 后端 CLI 已通过，不据此判定架构不可行。
3. Windows launcher 修复只针对本机已定位原因；其他安装环境需要按 ADR-012 诊断，不能假设全平台已验收。CI 文件尚未推送执行。
4. 当前只支持个人库、CLI schema=1 / 已测版本 0.15.2；批注读取有显式 limit，PDF 文字提取不保证图表公式精度。空白或扫描 PDF 的 OCR 不在本阶段范围。
5. 停留在 feat/zotero-adapter，用户 review 后再决定是否合并或推进 Phase 2。没有数据库、Project model、Web UI 或 AI Analysis。

## 长期交付

v0.1.0 = Project + Question + Zotero Source + AI Analysis；v0.2.0 = Inbox；v0.3.0 = Claim + Evidence。仅在形成可用阶段后发布 Release，不为每个 commit 发版。

ROADMAP 记录产品路线，GitHub Issues 记录具体可执行任务。远程仓库确认后可创建一个 Zotero Technical Spike Issue，不批量建立形式化任务。已有自动测试后建立简单 CI，仅覆盖离线检查，不依赖真实 Zotero。
