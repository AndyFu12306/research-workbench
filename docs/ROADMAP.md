# 路线与阶段门槛

当前：**Phase 1，核心只读链路 PASS，保留扩展验收和启动器问题，不推进产品开发**。检查日期：2026-11-03（Asia/Shanghai）。

| Phase | 内容 | 状态 |
| --- | --- | --- |
| 0 | 产品 / 架构设计 | 基本完成；原则已写入仓库 |
| 1 | Zotero Technical Spike | 核心读取通过；非空批注未测，Python subprocess 启动器待修复 |
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
- 初始化本地 Git 并切换到 `feat/zotero-spike`；未创建远程仓库、未提交、未推送。
- 首轮确认 Zotero 未运行、Local API 拒绝连接；本轮用户启动后，TCP 与实际 API JSON GET 均通过。
- 安装 uv 0.13.6 和独立 uv tool zotero-mcp-server 0.15.2，完成本地只读配置与 Codex CLI skill 安装。
- 显式使用 API 后端，搜索返回 2 个真实条目，metadata、非空摘要和 PDF 附件关系读取均通过。
- 发现 get fulltext 截断 10/20 页，随后使用 read 完整读取 20 页、109,551 字符，每页非空。
- annotations 查询成功返回空数组；没有创建测试批注。明确记录 PDF 本地定位/提取与 API metadata 的路径差异。

## Phase 1 待完成

1. 修复 Python subprocess 调用 uv tool 启动器时的 `failed to canonicalize script path`；PowerShell 中 CLI 已通过，不能据此假设 Python 后端启动方式也已通过。
2. 如需扩展验收，由用户指定已有批注的论文，验证非空 annotations 内容与页定位。本轮核心通过标准不要求为无批注论文创造批注。
3. 单独排查 PowerShell Invoke-WebRequest 的提前断开/超时；curl 和 API 后端 CLI 已成功，该差异不等于整个架构失败。
4. 继续维护脱敏报告；仅在用户决定推进时才开展 Phase 2。

不自动推进后续阶段。本轮仅验证 Zotero，不开发数据库、完整 Web UI、RAG、复杂多论文分析、Inbox、知识图谱或自动化。

## 长期交付

v0.1.0 = Project + Question + Zotero Source + AI Analysis；v0.2.0 = Inbox；v0.3.0 = Claim + Evidence。仅在形成可用阶段后发布 Release，不为每个 commit 发版。

ROADMAP 记录产品路线，GitHub Issues 记录具体可执行任务。远程仓库确认后可创建一个 Zotero Technical Spike Issue，不批量建立形式化任务。自动测试出现后才引入简单 CI。
