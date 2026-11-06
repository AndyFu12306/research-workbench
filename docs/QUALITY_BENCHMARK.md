# 离线基准与评分操作规范

> 当前活动基准为校准后的v2，原v1数据完整保留作历史。Case格式增加验收目标，Assessment增加
> 独立验收判断，Report增加acceptance计数，均升级v2；Candidate/Question/Binding字段形状未变。
> 规则为rubric-v2/atomization-v2，Observer为v2。下文v1说明保留初版历史；当前口径、18组完整
> Question、版本对应及验收结果以[校准记录](PHASE4B5A_CALIBRATION.md)为准。

本页只描述 Issue #21 的测试工具，不是产品评分接口。所有来源、身份（FAKE0001等）、问题和候选均为合成。
18组（中文9、英文9）、46个脚本候选；Q01–Q18含义以manifest及各case文件为准。
`correct`只表示作者拟定的正例，不表示已经获得独立人工语义认可。

## 数据与版本

- research-quality-benchmark-v1：案例revision、来源原样文本、完整Question、事实/关系、阶段要求。
- quality-candidate-v1：候选revision及按位置排列的脚本；引用为测试逻辑别名。
- quality-assessment-v1：评分revision、origin、原子主张、两轴、充分性、复核。
- quality-rubric-v1 / quality-atomization-v1：人工判定规则。
- quality-observation-binding-v1 / quality-observer-v1：本次运行实际输入输出与评分绑定。
- quality-report-v1：报告分母/白名单语义。95%/90%只是待校准目标。

JSON只接受闭合字段、准确类型和枚举，不能用True代替整数；拒绝重复key、NaN、孤立代理字符、
重复ID、悬空引用、未知路径、失配hash和越界span。证据用Python字符偏移 `[start,end)`，
CRLF、空格、emoji原样保留，不是UTF-8字节位置。精确切片校验不证明语义。
frozen dataclass防止意外字段赋值；tuple固定顺序，不等于防恶意伪造或身份认证。

## 人工评分

1. 先审核事实、关系、关键性与阶段要求，再看候选；不以候选表现反向降低要求。
2. 每个Finding拆出全部独立主张；主张定位是本次运行/阶段/Finding、文本hash及字符范围。
   原子化确认表示评审已检查整条Finding，不是程序证明没有漏拆。
3. 直接上游轴只能看当前Chunk或实际直接子Findings。原始来源轴沿实际引用链查原文和必要上下文。
   两轴均为 supported/partial/unsupported/contradicted/indeterminate/unscored，不能互推。
4. 每个引用来源分别标supports/opposes/conditional/irrelevant/indeterminate，并附证据。
   最终集合判定人工完成，不投票、不按块数或文献数计算独立证据。条件不同不自动构成直接矛盾。
   确定性一致性底线：若所有引用均为opposes/irrelevant，不得同时标source_support=supported；
   严格加载与直接评分入口均拒绝这种矛盾。conditional、混合证据仍由人工审定，存在支持也不自动证明整体支持。
5. 事实在每层标 explicit_complete/collectively_sufficient/insufficient/indeterminate/unscored。
   collectively_sufficient必须指出多个实际输入、合成理由和相反/限定信息。不要要求每个Map包含跨块完整事实。
6. 原文60→Map90为首次失真；下游忠实90，上游轴通过但来源轴失败，是继承错误。
   Cross改95另记新失真和继承根；仅从90改回60是无上游支持纠正，不计忠实恢复。
7. primary/secondary须为不同真实人，关键事实、严重错误、两轴不同、跨块综合、多源冲突均须复核。
   分歧保留不同意见与理由，第三位adjudicator记录裁决；不能覆盖原意见。

本仓库的46份assessment为Codex草稿，origin=codex_draft、reviews为空。没有实际人类签名或独立审核。
机制单测使用mechanism_fixture，最多得到simulation_complete，不能完成实际报告的人类审核。
人工接管须创建新revision、origin=human、真实复核记录；程序校验字段/角色及覆盖，不认证现实身份。
全部实际草稿报告保持provisional；不能把CI机制测试成功当人类评分成功。

## 绑定与重跑

观察适配器执行生产chunker、最终请求估算、validator。它对实际请求的最终payload、预算、分块、
来源身份/fingerprint计算输入hash，对实际返回raw JSON和规范化输出分别hash，记录实际Finding、
引用、范围和阶段次序。临时库端到端再对照每篇全部Stage历史及严格Cross历史读接口。
不从Candidate Spec计算“实际输出”；Spec只决定Fake的脚本。

完整case、原样来源/Question、事实要求、candidate revision、配置/Prompt/Schema/adapter、
实际观察、本次run_alias、Finding映射、rubric/原子化版本变化均使旧记录失效。
同阶段另一Finding变化也可能改变所有Finding ID；不能仅按旧句子或候选名附着旧分数。
新运行必须显式attach新记录并核验；attach不授予人工认可。文本不同不能偷偷沿用旧原子范围。
临时DB整数ID、文件路径、墙钟等属于独立metadata，不进入语义绑定/黄金报告。
run_alias由调用者明确提供，既区分运行实例，也避免用随机时间制造跨平台黄金测试差异。
hash只检测失配，不提供抵抗协调篡改的认证。

## 指标字典

统一计数：N=J+I+U；J为pass/fail，I为indeterminate，U为unscored。NA另列且须有理由。
报告success/J、J/N及原始数；J=0时rate=null，不是100%。I/U非零时仅暂定。
count中的pass表示“该事件发生”，严重错误和信息损失指标的pass代表坏事件，不是质量通过。

| 指标 | 成功事件 / 分母 |
| --- | --- |
| structure.acceptance | validator接受合法候选 / 合法结构试验 |
| structure.rejection | validator拒绝非法候选 / 非法结构试验 |
| upstream_support | 直接上游supported原子主张 / 可判定原子主张 |
| source_support | 原始引用来源supported原子主张 / 可判定原子主张 |
| kind_correct | source_summary/ai_inference标注正确 / 可判定原子主张 |
| critical_fact_retention | 全链路忠实保留的关键fact / 关键fact机会 |
| qualification_retention | 全链路忠实保留的限定fact / 限定fact机会 |
| end_to_end_retention | 全链路保留且各层输入充分的fact / 全部要求保留fact |
| relation_classification | 正确关系分类 / 要求关系 |
| conflict_recall | 正确报告必需冲突 / 必需冲突；非冲突关系NA |
| conflict_precision | 正确冲突断言 / 实际冲突断言；无断言NA |
| severe_candidate_incidence | 存在严重错误的候选 / 严重性已判断候选 |
| loss.metric | 本层首次partial/absent/distorted / 输入充分且输出可判定fact |
| runtime.expected_outcome | 预期终态且调用边界吻合 / 场景 |
| runtime.completion | 真正完成 / 预置应成功场景；预期拒绝NA |

partial不算supported或完整保留。原子主张的kind及必需关系分类不能标NA来缩分母。
分层损失另列applicable、insufficient、indeterminate_input/output、unscored_input/output；
这些排除项不能静默隐藏。显式包含Chunk→Map集合的首次提取损失；上游已缺失不重复归因。
原文正确但无上游支持的重现单列，不能提高全链路忠实保留率；来源支持轴仍可以判supported。
端到端判定检查整条事实轨迹：任一层partial/absent/distorted或输入insufficient时为fail；
没有已知失败时，任一unscored保留为U，否则任一indeterminate保留为I；只有全链路明确保留且输入充分才可pass。
已知失败优先于未决，不能把它变成成功或借未评分移出失败分母。Q13_ambiguous的Map集合已partial，
即使Document/Cross后来preserved，F1仍为fail；若只有输入歧义、没有已知丢失，则保持I而非通过。
claims按阶段观测计数，by_stage独立展示；不能把多层重复事实当多个独立回答。
重叠Chunk按fact_id只占一个保留机会。无主张的支持率NA；有必需事实仍须评价召回，假空不通过。
预期拒绝没有语义分数，安全拒绝只通过边界测试；非预期中断/失败的完成率为0。
报告无总分，保留严重错误位置、独立起因和传播关系。统计不声称模型内部因果。

## 离线验证

在专用worktree运行，无需Zotero、密钥或.env：

```powershell
uv sync --frozen --extra dev --offline
uv run --frozen --offline --extra dev pytest tests/unit/quality -q --durations=5
uv run --frozen --offline --extra dev pytest -q --durations=8
uv run --frozen --offline --extra dev ruff check src tests
uv run --frozen --offline --extra dev ruff format --check src tests
```

首次离线安装要求锁定包已在本机缓存；没有缓存应报告环境前置缺失，不以真实服务代替。
测试继承tests/unit/conftest.py的网络/进程阻断。临时库必须显式pytest路径并用现有迁移。
18组纯数据/评分全覆盖；46候选小型内存执行；26组临时SQLite真实服务运行。
Q17使用少量大生成Finding及较小预算触发拒绝，不重复100块最大压力测试，不修改CI超时。

## 报告隐私

投影仅含固定版本/别名、hash、计数、枚举状态、事件位置和待复核数量。
不输出来源、问题、生成原文、理由正文、prompt、完整响应、SQL参数、路径或原始异常。
运行时原文和观察投影仅在测试内存；报告若写盘只用pytest临时目录，不提交临时DB/报告。
工具仅加载注册的合成夹具，不提供任意用户文献导入。synthetic标签不是私人材料检测器。
