# Phase 4B.5a 本地实施验收

> 更新：下方初版结果保留为历史证据；PR #22 独立审核后两项P1的本地修复、1147项最终回归及未Push状态见本文末尾。

日期：2026-11-06（Asia/Shanghai）。依据 Issue #21 v1+v1.1。
基线 b716ad74e8ad29efd31dad79cf88670f70d7bd81；专用分支/worktree，未改main。

## 实际结果与证据范围

- 最终技术树完整回归：**1120 passed，8 deselected，177.67秒**。
- 测试所在提交：328110346b8331bdbc4348c768b8ff1a4cdae5a8。
- tests tree：ef8b143f4d0dcb9eed6c5ba87b100fbaf873f238。
- 其后仅追加本验收文档，不修改上述技术树；不将文档收尾提交冒称为重新执行完整测试。
- 原基线978项继续通过；新增**142项**，单独运行**142 passed，8.33秒**。
- 18组中英文基准、46候选全部严格加载、内存执行、绑定及provisional报告通过。
- 26组真实迁移临时SQLite、既有三层服务、FakeReader/Provider端到端通过；全部子阶段及Cross历史核对通过。
- Ruff check通过；format --check通过（105个Python文件）；uv lock --check --offline通过（28包）。
- 生产src（含0001–0005、4A、Stage Schema/Prompt/adapter/流水线/历史）、pyproject.toml、uv.lock、.github相对基线零diff。
- 修改文件diff空白检查、合成数据完整性、凭据模式及禁止产物扫描通过。不是完整安全审计或真实材料识别器。

第一轮完整回归1118 passed / 8 deselected，180.23秒，仅属于后续两项自审加固前的技术树，不代替最终结果。
最终慢项主要仍是既有容量测试：中文100块46.12秒，倾斜19.37秒，平衡18.44秒，最大闭包16.52秒。
新增测试未显著增加总成本；本机耗时不保证共享Windows runner的10分钟CI限制。
未修改CI timeout、删测试、降低原生产校验或运行真实服务。

## Issue #21 映射

| 要求 | 实现/测试 | 本轮状态 |
| --- | --- | --- |
| Q01–Q18、版本、严格JSON、span/hash、fact/relation | contracts/loading/validation、112份注册JSON夹具；test_contracts | 功能通过；语义标注待独立复核 |
| 双轴及60→90传播 | scoring/loss、Q18四种路径、test_scoring/test_pipeline | 机制通过 |
| 原始Chunk→Map集合→Document→Cross | Retention/InputAvailability、Q13正反例、loss_summary | 机制通过 |
| 实际观察绑定、逐字段失效 | scripted/binding、test_observation | 通过；实际请求/返回/validator和临时库核对 |
| 原子化、双评审、裁决、多来源 | Review/CitationJudgment、review_status、机制测试 | 机制通过；实际人工未完成 |
| N/J/I/U/NA及分项、严重/普通问题、运行率 | scoring/reporting、手算ratio_example、test_reporting | 机制通过；无总分 |
| 空结果、预检/动态容量拒绝 | Q15/16/17；零调用、Cross零调用及成功子历史保留 | 离线通过 |
| 恶意来源指令、合法链但无来源支持 | Q18预制错误候选及双轴 | 评估器通过；不证明模型抗注入 |
| 隐私、无外部IO、生产兼容 | 白名单报告、真实客户端/环境读取陷阱、父conftest阻断、git无diff | 本地通过 |
| 最终PR双平台CI及独立Code Review | 尚未获准push/PR | **未执行** |

## 关键失败回归及修复

1. 合同、评分/归因、观察绑定模块先有调用回归，再实现；未实现时明确导入失败，不当作语义检测证据。
2. 评分实现初次将“仅见90的上游写回原文60”误记first_distortion：1 failed / 5 passed；修复为unsupported_new。
3. 自审新增两项行为回归，旧实现**2 failed**：
   - changed关系但无直接矛盾的既有命题改写应记first_distortion；
   - F1不得借用另一来源的Map输入作为充分性依据。
   最小修复后2 passed；随后全部142项及最终完整1120项通过。
4. 上游忠实90不能获得来源支持；再改95保留新起因和继承根；同一错误传播不重复计独立起因。
5. Q13集合充分不是Map遗漏；缺数值/限定、歧义、Document丢限定分别计上游不足/未决/新增丢失。
   无支持重现单列，不能提高忠实端到端保留率。

## 人工语义复核与真实能力

**实际独立人工语义审核：未完成。** 46份评分均为Codex draft，reviews为空。
正确候选名不表示人工已认可；事实、限定、关系、全部原子拆分、双轴判定、严重性及合成理由均须人工复核。
mechanism_fixture只测试角色独立、分歧、第三人裁决与provisional门槛，不充当人类签名。
实际报告全部provisional；无法判断/未评分不得静默排除。

真实OpenAI/DeepSeek StageProvider、真实Zotero全文/Level B、真实文献外发、模型科研质量及抗注入能力均NOT TESTED。
95%/90%只作待校准目标。Fake名义usage不是实际token账单；本轮无AI API费用。

## 资源及剩余门槛

- 新开发环境初始化后及本轮结束均13个本地分支、7个worktree；此前12个分支/6个worktree全部保留。
- 除专用开发分支外，其余分支指针及worktree注册与实施前快照逐项一致。
- main=origin/main=b716ad74e8ad29efd31dad79cf88670f70d7bd81，主仓库工作区干净。
- Issue #21仍Open，结束时正文与开始时相同；无Issue更新、push、PR、merge或4B.5b/c/d。
- 没有已知未解决P0/P1实现阻塞；这不是独立Code Review结论。人类标注复核、双平台CI及独立代码审核仍待办。
- 本地功能具备进入代码PR审核准备的条件；需用户另行授权push/PR，不能标整个Issue已完全验收或可合并。
- 评审身份是声明而非认证；语义正确性依赖人工；绑定hash只检测失配；这些限制保留。

## 本地提交与完整变更文件

- 800b8c6：数据契约、合成案例及草稿。
- 7fdc58e：双轴、分母、复核机制及分层定位。
- 25dfc44：实际观察绑定、临时库和白名单报告。
- b16b982：来源作用域与失真归因加固、事实维度。
- 3281103：状态文档、评分及隐私规范。
- 最后一次仅提交本验收记录；准确SHA以交付报告和git log为准。

以下为相对基线的完整文件清单（仅docs/授权说明/tests；共135个文件）：

```text
AGENTS.md
docs/ARCHITECTURE.md
docs/DECISIONS.md
docs/PHASE4B5A_DESIGN.md
docs/PHASE4B5A_VALIDATION.md
docs/PRODUCT.md
docs/QUALITY_BENCHMARK.md
docs/ROADMAP.md
README.md
tests/fixtures/quality/v1/assessments/Q01_correct.json
tests/fixtures/quality/v1/assessments/Q01_paraphrase.json
tests/fixtures/quality/v1/assessments/Q01_wrong.json
tests/fixtures/quality/v1/assessments/Q02_correct.json
tests/fixtures/quality/v1/assessments/Q02_paraphrase.json
tests/fixtures/quality/v1/assessments/Q02_wrong.json
tests/fixtures/quality/v1/assessments/Q03_correct.json
tests/fixtures/quality/v1/assessments/Q03_wrong.json
tests/fixtures/quality/v1/assessments/Q04_correct.json
tests/fixtures/quality/v1/assessments/Q04_wrong.json
tests/fixtures/quality/v1/assessments/Q05_correct.json
tests/fixtures/quality/v1/assessments/Q05_wrong.json
tests/fixtures/quality/v1/assessments/Q06_correct.json
tests/fixtures/quality/v1/assessments/Q06_wrong.json
tests/fixtures/quality/v1/assessments/Q07_correct.json
tests/fixtures/quality/v1/assessments/Q07_wrong.json
tests/fixtures/quality/v1/assessments/Q08_correct.json
tests/fixtures/quality/v1/assessments/Q08_wrong.json
tests/fixtures/quality/v1/assessments/Q09_correct.json
tests/fixtures/quality/v1/assessments/Q09_wrong.json
tests/fixtures/quality/v1/assessments/Q10_correct.json
tests/fixtures/quality/v1/assessments/Q10_wrong.json
tests/fixtures/quality/v1/assessments/Q11_correct.json
tests/fixtures/quality/v1/assessments/Q11_wrong.json
tests/fixtures/quality/v1/assessments/Q12_correct.json
tests/fixtures/quality/v1/assessments/Q12_wrong.json
tests/fixtures/quality/v1/assessments/Q13_ambiguous.json
tests/fixtures/quality/v1/assessments/Q13_correct.json
tests/fixtures/quality/v1/assessments/Q13_document_loss.json
tests/fixtures/quality/v1/assessments/Q13_missing_condition.json
tests/fixtures/quality/v1/assessments/Q13_missing_value.json
tests/fixtures/quality/v1/assessments/Q13_paraphrase.json
tests/fixtures/quality/v1/assessments/Q13_wrong.json
tests/fixtures/quality/v1/assessments/Q14_correct.json
tests/fixtures/quality/v1/assessments/Q14_wrong.json
tests/fixtures/quality/v1/assessments/Q15_correct.json
tests/fixtures/quality/v1/assessments/Q15_wrong.json
tests/fixtures/quality/v1/assessments/Q16_correct.json
tests/fixtures/quality/v1/assessments/Q16_wrong.json
tests/fixtures/quality/v1/assessments/Q17_capacity.json
tests/fixtures/quality/v1/assessments/Q17_preflight.json
tests/fixtures/quality/v1/assessments/Q18_correct.json
tests/fixtures/quality/v1/assessments/Q18_new_distortion.json
tests/fixtures/quality/v1/assessments/Q18_paraphrase.json
tests/fixtures/quality/v1/assessments/Q18_unsupported_correction.json
tests/fixtures/quality/v1/assessments/Q18_wrong.json
tests/fixtures/quality/v1/candidates/Q01_correct.json
tests/fixtures/quality/v1/candidates/Q01_paraphrase.json
tests/fixtures/quality/v1/candidates/Q01_wrong.json
tests/fixtures/quality/v1/candidates/Q02_correct.json
tests/fixtures/quality/v1/candidates/Q02_paraphrase.json
tests/fixtures/quality/v1/candidates/Q02_wrong.json
tests/fixtures/quality/v1/candidates/Q03_correct.json
tests/fixtures/quality/v1/candidates/Q03_wrong.json
tests/fixtures/quality/v1/candidates/Q04_correct.json
tests/fixtures/quality/v1/candidates/Q04_wrong.json
tests/fixtures/quality/v1/candidates/Q05_correct.json
tests/fixtures/quality/v1/candidates/Q05_wrong.json
tests/fixtures/quality/v1/candidates/Q06_correct.json
tests/fixtures/quality/v1/candidates/Q06_wrong.json
tests/fixtures/quality/v1/candidates/Q07_correct.json
tests/fixtures/quality/v1/candidates/Q07_wrong.json
tests/fixtures/quality/v1/candidates/Q08_correct.json
tests/fixtures/quality/v1/candidates/Q08_wrong.json
tests/fixtures/quality/v1/candidates/Q09_correct.json
tests/fixtures/quality/v1/candidates/Q09_wrong.json
tests/fixtures/quality/v1/candidates/Q10_correct.json
tests/fixtures/quality/v1/candidates/Q10_wrong.json
tests/fixtures/quality/v1/candidates/Q11_correct.json
tests/fixtures/quality/v1/candidates/Q11_wrong.json
tests/fixtures/quality/v1/candidates/Q12_correct.json
tests/fixtures/quality/v1/candidates/Q12_wrong.json
tests/fixtures/quality/v1/candidates/Q13_ambiguous.json
tests/fixtures/quality/v1/candidates/Q13_correct.json
tests/fixtures/quality/v1/candidates/Q13_document_loss.json
tests/fixtures/quality/v1/candidates/Q13_missing_condition.json
tests/fixtures/quality/v1/candidates/Q13_missing_value.json
tests/fixtures/quality/v1/candidates/Q13_paraphrase.json
tests/fixtures/quality/v1/candidates/Q13_wrong.json
tests/fixtures/quality/v1/candidates/Q14_correct.json
tests/fixtures/quality/v1/candidates/Q14_wrong.json
tests/fixtures/quality/v1/candidates/Q15_correct.json
tests/fixtures/quality/v1/candidates/Q15_wrong.json
tests/fixtures/quality/v1/candidates/Q16_correct.json
tests/fixtures/quality/v1/candidates/Q16_wrong.json
tests/fixtures/quality/v1/candidates/Q17_capacity.json
tests/fixtures/quality/v1/candidates/Q17_preflight.json
tests/fixtures/quality/v1/candidates/Q18_correct.json
tests/fixtures/quality/v1/candidates/Q18_new_distortion.json
tests/fixtures/quality/v1/candidates/Q18_paraphrase.json
tests/fixtures/quality/v1/candidates/Q18_unsupported_correction.json
tests/fixtures/quality/v1/candidates/Q18_wrong.json
tests/fixtures/quality/v1/cases/Q01.json
tests/fixtures/quality/v1/cases/Q02.json
tests/fixtures/quality/v1/cases/Q03.json
tests/fixtures/quality/v1/cases/Q04.json
tests/fixtures/quality/v1/cases/Q05.json
tests/fixtures/quality/v1/cases/Q06.json
tests/fixtures/quality/v1/cases/Q07.json
tests/fixtures/quality/v1/cases/Q08.json
tests/fixtures/quality/v1/cases/Q09.json
tests/fixtures/quality/v1/cases/Q10.json
tests/fixtures/quality/v1/cases/Q11.json
tests/fixtures/quality/v1/cases/Q12.json
tests/fixtures/quality/v1/cases/Q13.json
tests/fixtures/quality/v1/cases/Q14.json
tests/fixtures/quality/v1/cases/Q15.json
tests/fixtures/quality/v1/cases/Q16.json
tests/fixtures/quality/v1/cases/Q17.json
tests/fixtures/quality/v1/cases/Q18.json
tests/fixtures/quality/v1/expected_reports/ratio_example.json
tests/fixtures/quality/v1/manifest.json
tests/quality_support/__init__.py
tests/quality_support/binding.py
tests/quality_support/contracts.py
tests/quality_support/loading.py
tests/quality_support/loss.py
tests/quality_support/reporting.py
tests/quality_support/scoring.py
tests/quality_support/scripted.py
tests/quality_support/validation.py
tests/unit/quality/test_contracts.py
tests/unit/quality/test_observation.py
tests/unit/quality/test_pipeline.py
tests/unit/quality/test_reporting.py
tests/unit/quality/test_scoring.py
```

## PR #22 独立审核 P1 本地修复（2026-11-06）

审核基线fbae65e08f0e38236763db9f506b3d706c13e84b。用户仅授权原分支/worktree最小修复、
离线验证、本地commit；未授权Push、Ready、Merge、Issue修改或真实服务。本段覆盖初版验收结论，保留旧结果上下文。

### 先失败，再修复

在原实现上新增27项回归后运行：
`uv run --frozen --offline --extra dev pytest tests/unit/quality/test_scoring.py -q -k review`
结果 **22 failed / 6 passed / 6 deselected**（其中包括1项原有复核机制控制测试）。
P1-01实际失败14项，P1-02实际失败8项；不是只记录尚未实现模块的导入失败。
最小修复后同一筛选 **28 passed / 6 deselected**；评估测试集 **169 passed，7.68秒**。

- P1-01：retention_verdict检查完整已登记事实轨迹，而非仅检查最终Cross或insufficient+preserved。
  任一层partial/absent/distorted或输入insufficient计fail；没有已知失败时，unscored保留U，
  其余indeterminate保留I，不提升成功分子。Q13_ambiguous的F1因Map集合partial为fail，端到端为1/2。
  单独未知状态的I/U及已知失败优先均有断言；原Q13正确/集合充分案例仍通过。
- P1-02：共用validate_source_support作必要一致性约束：所有引用均opposes/irrelevant时，
  source_support不能为supported。严格标注校验及直接评分入口都拒绝矛盾记录，不能绕过加载器抬高来源支持率。
  Q18覆盖四种全负引用组合和两个入口；conditional、支持/反对混合及合法组合仍保留人工审定，
  不进行多数投票，也不因存在支持自动赋予整体supported。

### 最终技术树验证

- 实现提交：86b27a2c8a1ceb1b3f48ac79e760d5d7a8314fd5。
- 最终完整离线pytest：**1147 passed / 8 deselected，176.77秒**，即原1120项全部通过，加27项新回归。
- 测试树：5fffbbfe6ed5d58a455fee788cddfb6f4ffc7fbf；收尾仅更新本文件，不改变上述测试树。
- Ruff check和format --check通过（105个Python文件）；锁检查通过（28包）。
- 相对审核基线，src、依赖、migration、CI配置、全部tests/fixtures均零diff；18组研究含义、候选和标注未修改。
- 本轮仅5个文件变化：scoring.py、validation.py、test_scoring.py、QUALITY_BENCHMARK.md及本验收记录。
- 人工语义独立复核仍未完成，全部实际草稿provisional；离线机制测试不等于真实模型质量验收。
- 没有发现仍未解决的已报告P1；修复仍需独立复核，不能把本地自审声明为新的独立Approval。
- 本轮没有Push或远程CI；PR #22原fbae65e的绿灯不覆盖本次修复。远端保持审核基线，等待下一次授权。

## Benchmark Calibration 本地验收（2026-11-06）

用户另行授权从cd83a336db135a289816a6a9a655801c8998c921开展本地校准、离线验证及必要本地提交。
该起点是本轮核对的实际开发HEAD；上文早期Push/CI状态保留其历史时间上下文。

- 保留v1全部原始草稿及缺陷，新增活动v2；18个逐案例Question、原有46个候选ID，无新增候选。
- 数量：Fact 31→45（critical 38、非critical 7）；Relation 15→12；Claim 216→363；Retention 246→354；复核目标718→1004。
- Q15/Q17独立Case验收目标7个、适用候选的判断9条，不伪造Fact/Finding；全部实际记录仍codex_draft，人工复核0。
- 30项校准回归在旧数据上失败；3项旧观察重新attach回归在旧绑定实现上失败，随后最小修复测试观察器。
- 46候选全部重新执行实际Fake/validator并verify绑定：231阶段观察、361真实Finding映射；清单hash见校准记录。
- 评估器 **211 passed**，含26组临时SQLite/实际三层服务E2E；最终冻结数据完整离线 **1189 passed / 8 deselected，174.80秒**。
- Ruff check及format --check通过（106文件）；uv lock --check --offline通过（28包）。
- 已验证测试树 **1223db367d4560c15d8e9da7a7ff4ed4d70ad9da**；此后只补验收文档，再提交同一技术树。
- src、依赖、迁移、Prompt/Stage Schema及CI相对起点/main均无diff，旧v1无diff，18组完整来源对象不变。
- 范围、凭据模式、禁止产物、原始材料隔离及diff检查通过；不宣称完整安全审计。
- 未调用真实AI/Zotero，未读取.env/密钥或私人研究数据库，未Push/更新PR或Issue；新HEAD远程CI及独立Approval未执行。

18组实际Question、8重点候选、版本对应、复核范围及限制见[完整校准记录](PHASE4B5A_CALIBRATION.md)。
95%/90%仍待校准，不是CI质量阈值；所有实际报告provisional，不能自动合并或进入4B.5b/c/d。
