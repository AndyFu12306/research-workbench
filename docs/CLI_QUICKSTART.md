# 两篇文献的首次真实运行

本入口复用现有 DeepSeekStageProvider、ZoteroClient、来源关联服务与跨文献编排。
本轮只用合成内容与临时 SQLite 做离线验证，**真实 Zotero + 真实 Stage API 尚未验证**。
Phase 4B.5a 额外人工/多模型复核暂停；既有审计、Benchmark、Assessment 和 PR #22 保留，
既有 DeepSeek 语义复核不作为正式 human review。CLI 可运行不等于质量验收通过。

## 准备

1. 打开 Zotero，启用本机通信（允许本机其他应用通信），保持运行。
2. 在个人库准备两篇不同的文献主条目，确保各有已下载到本机、可提取文字的完整 PDF。
   不选择附件条目、笔记或扫描图像 PDF。第一次优先选择较短、非敏感文献。
3. 使用现有独立安装的 `zotero-cli`。新机器安装方法见 README；本入口不自动安装或配置 Zotero。
4. 准备 DeepSeek API Key、账户实际支持 Responses API 和严格结构化输出的模型名、
   当前输入/输出每百万 token 单价、币种与愿意接受的本次名义费用上限。
   不要把 Key 粘贴进聊天、命令参数、环境变量脚本或仓库文件。

## PowerShell 启动

```powershell
Set-Location C:\Projects\research-workbench-cli
$env:PYTHONUTF8 = '1'
uv sync --frozen --extra dev
$env:ZOTERO_CLI_PATH = (Get-Command zotero-cli -ErrorAction Stop).Source
uv run --frozen research-workbench run
```

命令会使用当前目录下 `data/workbench-cli.db`，通过现有 Alembic 迁移升级到 head。
这是独立的 Workbench 数据库；不选择或操作 `zotero.sqlite`。不增加或修改 migrations。
当前目录固定在此 worktree，之后查询时也使用同一目录，避免误用另一份数据库。
可用 `uv run --frozen python -m research_workbench --help` 查看同一个入口。
`--database` 是全局参数，必须放在 `run/history/show` 前。

## 按终端提示操作

1. 输入 `READ`，允许本机检查 Zotero 连接并读取你选择的两篇文献。其他输入取消。
   检查分别报告 Client、Local API、CLI；非 healthy 停止，不回退云端或直接读取 Zotero 数据库。
2. 分别输入搜索词，从最多十个结果里选择序号。始终恰好两篇，重复条目或同全文拒绝。
3. 填写 Project、Question 和可选的问题说明。这些是用户研究数据，将保存于 Workbench。
4. 输入模型、三位币种、当前单价与本次费用上限。程序不内置价格或凭据，不自动读取 `.env`。
5. 在 `API Key` 提示处粘贴 Key 并回车。屏幕不显示字符是正常现象。
   隐藏输入不可用时直接停止，不回退到明文输入。Key 仅留在当前进程内存供 SDK 使用，
   不写文件、数据库或环境变量；结束后关闭 SDK 连接。不承诺 Python 内存的密码学擦除。
6. 预检完整全文、分块、阶段能力、请求预算与费用。原文只保留在内存。
   预览显示目标 `https://api.deepseek.com`、模型、两篇全文字符数、Map 数、
   两个 Document Reduce、一个 Cross Reduce、总调用数、单次与整体预算和估算费用。
7. 阅读预览后输入 **`SEND`** 才会发出第一条真实付费请求。其他输入取消，AI 请求数为零。
   已创建的 Project/Question/来源关联保留；取消后没有分析执行记录。
8. 顺序执行 Map → Document Reduce → Cross Reduce。成功后显示 AI 生成分析与 AI 推断、
   来源引用、来源块、执行 ID、Question ID、各次调用 token 用量和名义费用。
   `input_ordinal` 对应文献 1/2；`document_execution_id` 指向文献子执行；
   `chunk_ordinal/chunk_id` 指向对应来源块。引用链只说明结构追溯，不保证语义正确或综述穷尽。

Key、原文与 metadata 不持久化，但生成分析可能复述原文敏感内容，研究数据库仍需妥善保管。
Workbench 不写 Zotero，不更改 metadata、Tags、Collections、Notes、Annotations 或 PDF。
全文提取沿用现有 ZoteroClient 与上游工具，不另建 Zotero SQLite 读取路径。

## 之后离线查看

下面数字 `1` 换成终端实际显示的 Question ID / 执行 ID；这些命令不需要 Key，不访问 Zotero/AI。

```powershell
Set-Location C:\Projects\research-workbench-cli
uv run --frozen research-workbench history --question 1
uv run --frozen research-workbench show 1
```

`history` 按 ID 列出该 Question 的前 100 次跨文献执行。
`show` 显示完成、失败、中断、未知与未开始的阶段；成功结果通过现有严格历史完整性校验后显示。
本版每次 `run` 新建 Project、Question 和执行，不提供恢复、重复利用项目的向导或自动续跑。

## 遇到失败

- 搜索为空：调整关键词重跑；空结果不当作全文连接成功。
- 全文/预算预检失败：没有 AI 请求，选择较短且可完整提取的 PDF 或检查连接。
  不自动省略文献、截断、摘要替代或提高限制。
- `provider_unavailable`：服务端暂不可用（现有 adapter 将 5xx 归类到此安全码）；
  `provider_timeout` / `provider_network`：请求结果可能未知，可能已经计费。
- `provider_sdk_incompatible` / `provider_structured_output` / `provider_invalid_request`：
  当前服务、模型、Responses 或结构化输出不兼容/拒绝，不能当作成功；不切换到 Chat/普通文本。
- Reduce 动态输入超限或引用/结构校验失败：停止，保留已完成阶段历史，不伪造最终综合。
- Ctrl+C 尽力保存 interrupted；强杀进程或持续数据库故障可留下 running。
  使用 `history/show` 核查，不把未知用量算作零费。

错误只展示固定安全消息与阶段状态，不打印原始 provider/Zotero 响应、全文、Key 或 traceback。
本版没有自动重试；再次执行 `run` 是新的、需要重新确认的研究和付费尝试。
预算采用 UTF-8 字节保守操作估算，非服务商精确 tokenizer；用户填写价格也可能过时，
估算/阈值与 usage 名义费用均不是账单保证。服务商账单为实际费用依据。

## 开发验证

定向离线测试使用合成 Zotero reader、FakeStageProvider / SDK MockTransport、临时文件数据库，
不访问真实 Zotero、AI、Key、`.env` 或私人研究库。
本轮不重复完整 pytest，不修改 CI，不修改冻结评估文件；实际命令与结果见本轮交付说明。

2026-11-06 本轮检查：下列定向集 **264 passed**；其中 CLI 20 项。
Ruff check 与 format --check（src/tests 共94文件）、`uv lock --check`、`git diff --check`，
console script 与 `python -m research_workbench` 的 `--help` 启动检查均通过。
迁移测试使用临时 SQLite，未初始化默认研究数据库。未重复完整 pytest；未测试真实 Zotero、
真实 API、账户模型兼容性或 Windows 用户终端的首次交互；远程新分支 CI 另行核验。

```powershell
uv run --frozen --extra dev pytest tests/unit/test_cli.py tests/unit/test_cross_document_analysis.py tests/unit/test_cross_document_plan.py tests/unit/test_cross_document_budget.py tests/unit/test_cross_document_history.py tests/unit/test_stage_providers.py tests/unit/persistence/test_cross_document_migrations.py -q
uv run --frozen --extra dev ruff check src tests
uv run --frozen --extra dev ruff format --check src tests
uv lock --check
```
