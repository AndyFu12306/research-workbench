# 系统架构

## 分层与目标

```text
Zotero                 文献事实层
54yyyu/zotero-mcp       外部 Zotero 控制能力
Research Workbench     AI 研究认知层
```

目标形态为 Browser → Frontend → Python Backend；后端分别访问 Workbench SQLite 和 Zotero 集成适配器。数据库不是请求必须穿过的网络层。当前只实现 Phase 1.5 集成适配器，没有应用服务、数据库或前端。

```text
Browser → Frontend → Python Backend
                         ├─ Workbench SQLite（研究数据）
                         └─ Zotero Adapter
                              ├─ zotero-cli（外部进程）
                              └─ Zotero Local API → Zotero Client
Agent Client → zotero-mcp（MCP）→ 上游提供的 Zotero 能力
```

后端优先使用 zotero-cli / Zotero Local API；MCP 主要面向 ChatGPT、Claude 和其他 Agent Client。不 fork 上游，不重新实现 Bridge、PDF 管理、DOI/arXiv 导入、基础 Annotation、Tag、Collection 或全文提取。

## 已核实的上游差异

2026-11-03 阅读上游 main 文档：`zotero-cli` 和 `zotero-mcp` 同属 `zotero-mcp-server` 包；部分本地读取直接访问 `zotero.sqlite`，不能假设所有 CLI 调用都经过 Local API。

因此分别验证 Client、Local API、CLI；CLI 成功不能覆盖 API 失败。直接数据库访问若发生，必须明确标记为上游实现路径，不自行实现 SQLite 读取，不把它当作目标 API 链路已通过。main 文档不是已安装版本保证。

安装版为 0.15.2。Phase 1 显式使用 API backend 验证搜索、metadata 和附件关系；上游仍通过 LocalZoteroReader 定位本地 PDF 并使用自带提取能力，20 页完整读取通过。不是所有 PDF 内容都经 Local API 传输。Phase 1.5 已修复 launcher 环境问题，适配器每次对子进程强制本地 API 配置，Python 集成测试已通过。

来源：[上游说明](https://github.com/54yyyu/zotero-mcp/blob/main/docs/for-agents.md)、[包定义](https://github.com/54yyyu/zotero-mcp/blob/main/pyproject.toml)。

## 数据所有权

Workbench 只保存 `projects`、`questions`、`source_links`、`ai_analyses` 和必要研究引用；首期 SQLite，实际 schema 和迁移方案留待 Phase 2。

来源引用可包含 source_id、zotero_item_key、project_id、question_id。后续 schema 需要明确 library 身份：item key 不应被假设为跨个人库与 group library 全局唯一。第一阶段不由此扩展多用户功能。

Phase 1.5 仅支持个人库 users/0，不暴露组库切换。ZoteroItem、ZoteroFulltext、ZoteroAnnotation 是不可变运行时 DTO；没有数据库、持久化缓存或 metadata 镜像。批注 DTO 按安装版真实 JSON 结构设计，非空样本解析有合成单元测试，但真实非空批注仍未验证。

title、authors、DOI、journal、publication_date、abstract、PDF、attachments、annotations 等尽可能按需从 Zotero 获取。不持久化完整 metadata，也不导入整库。分析与来源定位属于研究数据；临时读取结果不转成长期镜像。若未来需要缓存，先记录一致性、失效与隐私决策。

## 数据流与故障边界

未来数据流：创建 Project → 创建 Question → 查询 Zotero → 选择来源引用 → 按需读取内容 → 生成并保存有来源标识的 AI Analysis。Zotero 不可用时准确报告失败，不用过期副本伪装实时事实。

Spike 诊断层为 Workbench、zotero-cli、zotero-mcp、Zotero Local API、Zotero Client。记录退出码、HTTP 状态、缺失依赖、空字段和附件可用性；无 PDF、摘要或 annotation 不等于接口故障，也不等于该能力已完成正向验收。

## Phase 1.5 已实现的集成边界

- `client.py`：search_items、get_item、get_fulltext、get_annotations、health_check；业务模块以后只使用此接口。
- `runner.py`：唯一 subprocess 入口；发现 executable、构造参数列表、shell=False、有限 timeout、UTF-8、双流捕获、退出码与 schema=1 JSON 校验。先显式 cli_path，再 ZOTERO_CLI_PATH，再 shutil.which 两种命令名称。没有硬编码用户目录。
- `models.py`：标准库 dataclass 轻量返回值，不引入运行时依赖。
- `errors.py`：缺少 CLI、命令失败、超时、解析错误和 Local API 不可用的领域错误；命令失败保留 stdout/stderr/returncode/code，上层不用处理 CalledProcessError。
- 截断全文不会作为成功返回；读取上游报告的全部页面后检查身份、页范围、字符数和连续页标记。允许真实空白页，但全部无提取文本时不宣称全文通过。该页标记依赖 0.15.2 输出格式，升级需重跑契约测试。
- Health 对 API 做无代理、无重定向的有限超时 GET，并独立验证 CLI config。Client 运行状态从成功 API 推断；API 失败时只标记无法独立确定进程。API/CLI 均成功才 healthy，一项失败 degraded，两项失败 unavailable。
- 适配器只暴露读取，搜索 query 使用独立参数并放在 `--` 后，避免被解释为 CLI 选项。个人库、本地 API、空写入凭据及上游支持的 `ZOTERO_LOCAL_WRITE=false` 由 runner 强制传递。没有自动安装、setup、重试、云端 metadata 回退或 Zotero 写入路径；本地 PDF 定位及可能的附件获取仍由上游工具管理。
- `last_stderr` 保留最近一次 CLI 进程的诊断，默认不日志输出；失败异常保留双流。诊断可能包含私人数据，实例不用于跨线程共享。

## 验证与替换

运行时零第三方依赖。开发依赖仅 pytest、Ruff；Hatchling 用于包构建，uv.lock 固定开发解析结果。单元测试 mock 进程和网络，默认排除 integration；真实测试由 `pytest -m integration` 显式选择，PDF 样本通过进程环境指定，不提交真实 item key。

当前本机 63 个单元测试通过；2 个集成测试通过，1 个非空批注样本测试跳过。已建立 Windows / Ubuntu 最小 CI 工作流，仅执行依赖安装、格式/lint、单元测试；尚未在远程执行。将来替换 CLI 或接入其他 Zotero 路径，应保持 ZoteroClient DTO 与错误边界，业务代码不感知 subprocess。

## 配置、依赖与安全

- 初期选择 Python 3.13；默认系统 Python 不等于项目解释器。未来产品依赖安装到项目 `.venv`；Zotero CLI 按用户本轮要求使用独立 `uv tool` 环境，避免混入产品依赖。本轮解析到 zotero-mcp-server 0.15.2，记录版本供复现，不安装 extras。
- 标准库可以解决的诊断不新增依赖；zotero-mcp 为外部工具依赖，优先基础包，不安装 semantic/all 扩展。
- 回环 API 使用有限超时，不经系统代理、不自动回退云端。`WORKBENCH_*` 变量是候选设计，当前没有配置加载器。
- 不记录密钥或完整配置，不提交数据库、PDF、全文、私人文献标识和原始诊断输出。可提交报告只保留脱敏状态。
- 第一阶段 Zotero 业务只允许 GET 和经核实的只读 CLI；不执行 authorize-local、导入、编辑、删除或重建语义索引。用户本轮明确批准 `setup --no-claude --skip-semantic-search` 与 `install-skill`，仅用于本地连接配置与仓库 skill 安装，不授予 Zotero 写权限。
- 不引入微服务、Redis、消息队列、Kubernetes、多服务 Docker 或复杂云部署；随实际需求演进。
