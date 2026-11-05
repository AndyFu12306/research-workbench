# DeepSeek 实际兼容性与修复验收

历史记录日期：2026-11-03；最新更新：2026-11-05（Asia/Shanghai）。人工结果由用户提供；此前失败请求的各次完整时间/usage/response id未提供。本文件不包含key、原始prompt/response或研究内容。

| 历史路径 | 用户提供的真实结果 |
| --- | --- |
| deepseek-flash / Chat Completions plain | PASS |
| deepseek-flash / Responses plain | PASS |
| deepseek-flash / Responses json_object | FAIL，503 service_unavailable |
| deepseek-flash / Responses minimal json_schema | FAIL，503 service_unavailable，非仅strict问题 |
| deepseek-v4-pro / Responses plain | io_timeout >30s；未通过基础验证 |
| Workbench 旧Responses Level A | FAIL，未得到completed分析 |

这些事实定位到结构化请求的服务端可用性/兼容性风险，不证明独立backend的内部实现，也不证明所有时段/模型永远不支持。GET/models可见不等于推理或JSON模式验收。Option B的Level A已于2026-11-05获得单次真实PASS；这不代表此前失败根因已确定或整个workflow已完成验收。

官方依据：[JSON Output](https://api-docs.deepseek.com/guides/json_mode/)明确Chat response_format=json_object、JSON提示和空白风险；[Chat API](https://api-docs.deepseek.com/api/create-chat-completion/)定义finish_reason、usage和id。JSON mode不提供业务schema/citation保证，最终门槛必须由Workbench执行。

## 本轮实现与离线验收

- 既有OpenAI SDK 2.54.0，无新依赖或migration；OpenAI provider和schema不变。
- DeepSeek请求：model来自env；system=共享INSTRUCTIONS + 小段JSON序列化要求 + 现有OUTPUT_SCHEMA；user=render_input；response_format=json_object；max_tokens=4096；max_retries=0。
- 组合prompt_version=analysis-v1.deepseek-json-v1，schema_version=analysis-schema-v1。原AnalysisRun不覆盖。
- 完整JSON parse后仍执行应用schema、重复key和citation验证，不正则提取、不接受部分内容/未知source label。失败只存安全码；503→provider_unavailable。
- 268普通tests PASS，8个integration默认排除；显式关闭真实开关时两条DeepSeek integration SKIP。没有在Codex进程发真实API请求。

## 真实验收状态

| 新Chat实现 | 状态 |
| --- | --- |
| Level A：1 synthetic abstract → real DeepSeek → completed AnalysisRun | **PASS（单次真实验收），2026-11-05** |
| Level B：1 consented Zotero abstract → real DeepSeek → completed AnalysisRun | NOT TESTED |
| DeepSeek workflow | NOT FULLY VALIDATED |

## 2026-11-05 Level A 真实验收

证据来源：用户在本地PowerShell真实执行以下命令，并提供结果；Codex本轮仅记录文档，没有重新调用provider或读取API Key。

```powershell
uv run --frozen --extra dev pytest -m deepseek_integration tests/integration/test_analysis_deepseek_integration.py::test_synthetic_deepseek_analysis -q -rPs --tb=short
```

| 项目 | 实际结果 / 本次配置 |
| --- | --- |
| pytest | **1 passed in 11.97s** |
| provider / model | deepseek / deepseek-flash |
| OpenAI SDK | 2.54.0 |
| 模式与输入 | Chat Completions JSON mode；abstracts；1 synthetic abstract；FakeZoteroClient；temporary Workbench DB |
| AnalysisRun.status | completed |
| input_tokens / output_tokens | 877 / 1036 |
| response_id_present | true（仅记录存在性，不记录实际ID） |
| manifest_verified | true |
| security_check | PASS |
| DEEPSEEK_TIMEOUT_SECONDS | 120 |
| RUN_DEEPSEEK_INTEGRATION | 1 |
| RUN_DEEPSEEK_ZOTERO_E2E | 0 |

该完整synthetic测试通过表明：本次真实请求、SDK解析、应用schema/source-label校验、completed分析持久化及manifest/security检查通过。它没有读取或发送真实Zotero摘要，也没有执行Level B。

此前timeout/503在本次测试中未复现，历史记录完整保留。本次为Flash + Chat JSON mode，timeout配置为120秒，和此前Responses/Pro请求的路径及配置不同；单次成功不能隔离协议、容量、时间窗口或timeout配置的因果影响，不能认定已经证明全部故障根因、所有模型/场景可靠或永久消除503/timeout。

**当前结论：Level A = PASS（单次真实验收）；Level B = NOT TESTED；DeepSeek workflow = NOT FULLY VALIDATED。**

准确命令见README，继续使用现有deepseek_integration路径。Level B仍需独立同意和显式item key，不搜索、不读PDF/annotations，只写临时Workbench DB。同次完整模块会重新执行A作为前置，最多两次请求，不是retry；本次A成功不自动授权B或Phase 4B。

后续仅记录实际model、SDK、prompt/schema版本、日期、run状态、usage和response id是否存在、manifest/security结果。不得记录API Key或原始内容，也不得把HTTP200、JSON语法正确、Chat plain或离线mock当完整PASS。两级均真实通过后才可升级FULLY VALIDATED。

## 已知风险

- 官方JSON mode也可能为空白；拒绝为provider_empty_json，不自动修补。
- max_tokens=4096可能得到length/incomplete；不自动增预算，不默默接收截断JSON。
- 60秒SDK读超时不是墙钟deadline；服务保活可能延长等待。该风险未在本轮改变正式timeout架构。
- JSON/schema与来源ID合法不证明语义真实，人工核实仍必要。
- Option A的503根因尚未得到厂商确认；Option B目前仅有上述单次Level A真实PASS，重复稳定性及Level B仍未验证。无plain-text fallback或4B扩展。
