# DeepSeek Responses hardening：证据与验收

最新记录：2026-11-05（Asia/Shanghai）。真实运行结果由用户提供；本轮工程实现未调用真实DeepSeek、未读取实际密钥、未运行真实Zotero E2E。Issue [#11](https://github.com/AndyFu12306/research-workbench/issues/11)，分支codex/deepseek-responses-hardening，main基线aceca63；Chat [PR #10](https://github.com/AndyFu12306/research-workbench/pull/10)保持未合并，不修改或关闭。

## 当前决策与证据分层

正式方向已确定为Option A：Responses + json_schema + strict=True + Workbench完整本地校验。SDK2.54.0，OUTPUT_SCHEMA/INSTRUCTIONS、analysis-v1 / analysis-schema-v1不变；无依赖或migration变化。协议选择不是永久可靠性声明。

| 对象 | 状态 | 证据边界 |
| --- | --- | --- |
| Chat JSON（Option B）Level A | 用户单次真实PASS，2026-11-05 | PR #10；不是Responses证据 |
| Responses minimal schema（省略strict） | 用户单次真实PASS | HTTP200/completed，2.26s；最小answer诊断 |
| Responses minimal schema + strict=True | 用户单次真实PASS | 1.767s，92/20 input/output tokens；仍是最小诊断 |
| Responses full analysis-schema-v1 | 用户单次真实PASS | 19.732s，1141/3435 tokens；本地schema/citation通过，未执行AnalysisService |
| Responses本地原型完整Level A | 用户单次真实PASS | 真实AnalysisService + Fake Zotero + 临时DB；见下表 |
| 正式hardening provider Level A | **NOT TESTED** | 本PR离线通过，不用原型或mock代替正式版本真实验收 |
| 真实Zotero摘要Level B | **NOT TESTED** | 需要独立外发同意和明确item key，本轮不执行 |
| DeepSeek workflow | **NOT FULLY VALIDATED** | 不自动进入4B；OpenAI真实验收独立NOT TESTED |

### 用户提供的原型完整Level A结果

| 项目 | 结果 |
| --- | --- |
| API / model | Responses / deepseek-flash |
| format | 完整json_schema + strict=True |
| HTTP / response_status | 200 / completed |
| AnalysisService / AnalysisRun | executed / completed |
| elapsed_seconds / provider_elapsed_seconds | 10.494 / 10.353 |
| input_tokens / output_tokens | 1049 / 1397 |
| max_output_tokens / provider_calls / max_retries | 4096 / 1 / 0 |
| manifest / fingerprints / provenance / metadata | verified=true |
| immutable_history_verified / security_check | true / PASS |

不保存实际response ID、请求/响应正文、源内容或key。最新用户报告未单独提供response ID存在性，不推断该值。原型使用PR #10分支的共享pipeline及错误helper，不是本PR正式adapter；本PR最终代码需单独验收。

### 保留Option B的真实证据

用户于2026-11-05报告Chat JSON synthetic Level A：1 passed in11.97s，deepseek-flash / SDK2.54.0，completed；input/output tokens=877/1036，response_id_present=true，manifest/security通过；timeout120秒，Zotero E2E关闭。这是单次成功，不与Responses的provider耗时作性能排名，不将其prompt版本analysis-v1.deepseek-json-v1写入当前Responses历史。

## 历史故障（用户证据，不在本轮重现）

- Flash Chat plain / Responses plain曾成功；Responses json_object及minimal json_schema（包括省略strict）重复503 service_unavailable。
- 原Workbench Responses Level A曾失败，未生成completed分析；仅凭泛化错误无法还原精确HTTP根因。
- Pro Responses plain曾io_timeout>30s；另一次等待的最终结果未知；models可见不证明推理/结构化可用。

这些是当时观察，不证明永久不支持JSON Schema，也不证明strict是唯一原因。新成功不能隔离时段、模型、协议、容量、timeout等因果因素；未有严格根因确认，不声称历史问题永久消失。两协议各一次A PASS不证明长期稳定或完整研究质量。

## 本PR加固与离线验证

- HTTP5xx→provider_unavailable；generic404→provider_invalid_request，已知schema/model code继续细分。HTTP200但status=failed时保留安全机器code映射；未知code→provider_failed。旧provider_responses_incompatible目录值保留以兼容历史，不重写旧记录。
- 仅接受completed response、reasoning元数据（忽略）和单条completed assistant、单个output_text；拒绝工具/未知output、异常role/status、多消息/多片段及结构不符。不拼接、不regex提取、不修复。
- 空白→provider_empty_json；非法/非标准常量/超长JSON→provider_invalid_json；refusal/incomplete和异常SDK结构分别拒绝。合法JSON原文保留给应用层，重复key、重复/未知citation（S99）仍由共享校验拒绝；repository复核映射后的结果。
- Responses usage/id可选为None；非法类型/负数/id格式拒绝。只存生成分析与manifest，日志和错误不含payload、密钥或provider prose。SDK网络retry=0，repair=0。
- 315普通离线tests PASS，8 integration默认排除；Responses专项105 PASS；Ruff格式/lint和uv lock --check通过。普通测试阻断未mock的网络/DNS/subprocess；数据库全部临时。
- 回归包含5xx/404、failed响应错误持久化、空白/非法JSON、重复key/label、S99、未知结构/工具、incomplete/refusal、usage/id异常和缺失、单次请求/无retry、成功完整pipeline、manifest/hash、终态不可覆盖与敏感内容不落库/不日志输出。
- 正式synthetic验收入口增加版本、单次调用和终态不可覆盖检查，只打印安全运行元数据。关闭真实开关时两条测试跳过；skip不是真实PASS。CI仅离线，最终head的Windows/Ubuntu CI及self-review见独立PR。

## 正式版本的用户验收

在**新worktree**的同一PowerShell配置现有环境，不在聊天发送key，不自动加载.env。确认DEEPSEEK_MODEL为deepseek-flash，DEEPSEEK_TIMEOUT_SECONDS=120，RUN_DEEPSEEK_INTEGRATION=1，RUN_DEEPSEEK_ZOTERO_E2E=0；key由用户在自己的终端持有。只运行synthetic节点，发出一次请求：

```powershell
uv run --directory C:\Projects\research-workbench-responses --frozen --extra dev pytest -m deepseek_integration tests/integration/test_analysis_deepseek_integration.py::test_synthetic_deepseek_analysis -q -rPs --tb=short
```

此命令测试正式DeepSeekAnalysisProvider，不是ignored local原型，也不是原Chat worktree。使用1合成摘要/Fake Zotero/新临时SQLite，经过AnalysisService、schema/citation及repository校验，检查manifest/fingerprint、usage/id、版本、单次调用和immutable history；不读取或发送真实文献。未运行本命令之前正式版本仍NOT TESTED。成功不自动授权Level B、merge或4B。

## 风险与限制

- 4096输出预算不变。原型单来源1397 tokens约34.1%，但双来源FULL_SCHEMA3435约83.9%、仅余661；不能以一次余量保证未来不截断，reasoning也可能消耗预算。incomplete失败，不自动扩容。
- SDK逐I/O timeout不是墙钟deadline；本地诊断的子进程deadline未引入正式provider。终止本地等待也不保证服务端停止计费。
- 本次采用严格单条最终消息/片段契约；服务返回不同合法形态也会失败关闭，应先收集安全证据并单独评估，而非静默拼接或放松。
- schema接受/本地通过不证明服务端执行了每个关键字，更不证明论文语义正确。原文不持久化、hash不足以完整重放；生成内容仍需人工审阅。
- 非空真实批注、真实PDF+LLM和真实OpenAI不在本轮范围。SQLite/远端无分布式事务，崩溃仍可能留下running历史；无后台自动恢复。
