# Phase 4B.4 实施与离线验收记录

本轮实施跨2026-11-05至2026-11-06（Asia/Shanghai）。设计基线为 [Issue #19](https://github.com/AndyFu12306/research-workbench/issues/19) v2 + v2.1；用户正式授权Branch + Implementation、本地提交和离线验证。没有授权推送、PR、merge、关闭Issue或4B.5。本记录以实际离线结果为准，不替代代码审核或真实能力验收。

## 仓库与交付边界

- 基线、原main和核验时远程main：`41b136113aaaa56a9801590f938fcc745a0e546d`。
- 分支：`codex/phase4b4-cross-document-synthesis`；worktree：`C:/Projects/research-workbench-phase4b4`。开发分支不追踪main，未推送。
- 原仓库`C:/Projects/research-workbench`仍为干净main；原4B.1、4B.2、4B.3、Responses分支/worktree全部保留。
- Issue #19仍Open；最终复查body与开始时相同。没有创建PR、merge、关闭Issue或推进4B.5。
- 全程只有合成FakeReader/FakeStageProvider、真实临时SQLite及SDK MockTransport。没有读取真实Zotero、AI API、密钥、`.env`或用户研究数据库，也没有Zotero写入。

## 六步实现

| 阶段 | 实际变化与验证 |
|---|---|
| 1 单文献有限重构 | 准备、登记、执行分离，新增PreparedDocument和DocumentExecutionOutcome；原run_analysis返回DocumentRun的行为保留。先建立失败回归，再修改实现；首轮针对性68项通过。 |
| 2 内存计划 | FrozenSelection/PreparedCross、显式有序tuple、重复身份/内容拒绝、严格三层额外策略、固定逐调用额度和精确Decimal计算。通用4B.2嵌套能力不改。 |
| 3 迁移与持久化 | 0005四表、精确四列UNIQUE/FK、显式NOT NULL/INTEGER/TEXT检查、原子登记、父owner/write_revision/CAS及篇序门槛。真实临时库负面INSERT/UPDATE/DELETE、提交失败和双session测试。 |
| 4 顺序编排 | 同一个Provider对象，逐篇Map→Document Reduce，全部完成后一次Cross；调用前后完整性重验，失败即停，未知返回interrupted/unknown，成功子历史保留。 |
| 5 专用历史 | cross-document-history-v1白名单序列化，完整作用域引用链及闭包严格读取；Document支撑链摘要在T3固定、T4/读取核对，不把历史DTO升级为运行时结果。 |
| 6 最终离线门槛 | 完整回归、Ruff、锁、临时Alembic CLI/metadata、wheel资源、容量/性能、diff/隐私与兼容性审查。最终数量和结果见下表。 |

frozen dataclass表示不允许意外改写属性；tuple保留调用者给定的文献顺序。CAS是带旧状态条件的UPDATE，并要求恰好改动一行，避免重复claim/finalize。SQLAlchemy session是一次短事务的边界，外部读取和模型调用均在事务外。Decimal只处理显式十进制，128位局部精度配合Inexact/Rounded traps，不能靠舍入越过限额。

## 修改文件（34个）

| 类别 | 文件 |
|---|---|
| 新增生产模块 | `analysis/cross_document_pipeline.py`、`application/cross_document_analysis.py`、`persistence/cross_document_models.py`、`persistence/cross_document_repository.py`、`persistence/cross_document_history.py`、`persistence/migrations/versions/0005_cross_document_analysis.py`，均位于`src/research_workbench/`。 |
| 有限修改 | `analysis/document_pipeline.py`、`application/document_analysis.py`、`persistence/document_repository.py`、`persistence/migrations/env.py`，均位于`src/research_workbench/`。单篇repository仅提取同session helper和父成员保护；新旧入口保留原有事务职责。 |
| 新增测试 | `tests/cross_fakes.py`；`tests/unit/test_document_preparation.py`；`tests/unit/test_cross_document_{plan,repository,analysis,budget,gates,history,transactions,providers,privacy,capacity}.py`；`tests/unit/persistence/test_cross_document_migrations.py`。 |
| 既有测试调整 | `tests/unit/conftest.py`只新增合成fixture；`tests/unit/persistence/test_{migrations,analysis_migrations,document_migrations}.py`仅更新head=0005及新增表清单，保留旧断言和用例。 |
| 文档 | `AGENTS.md`、`README.md`、`docs/PRODUCT.md`、`docs/ARCHITECTURE.md`、`docs/DECISIONS.md`（ADR-055至059）、`docs/ROADMAP.md`及本记录。 |

## 验证结果

前置基线实际运行：**682 passed，8 deselected**。历史完整离线回归（HEAD `1244f69780b4dc88f806c31f8300d4c8046144c8`）：**918 passed，8 deselected，170.65秒**；新增**236项**测试，既有682项继续通过。随后仅补充两项最大夹具的测量输出（2 passed / 3 deselected）以及无效返回usage/id保留断言（7 passed / 25 deselected），对应全部针对性重跑通过；生产代码未变，但测试文件有变更，因此该918项记录不能声明为初版最终HEAD `e5a342e9d7fea958a124f61dde19708bb64b6c33`的完整回归；该HEAD未重跑全量。不把deselected记作真实PASS。

| 检查 | 结果 |
|---|---|
| Ruff check / format | 通过，89个Python文件 |
| uv lock --check | 通过，28包；pyproject.toml和uv.lock不变 |
| Alembic CLI（显式临时文件） | upgrade head / current=0005 / check无差异 / repeat upgrade通过 |
| 真实SQLite | NULL、整数位置、文本hash、配对hash、生命周期、复合FK错配、孤立关系、唯一约束、被引用键限制、新连接foreign_keys=ON及foreign_key_check通过 |
| 0004旧库升级 | 原Document历史行逐值保留，重复升级和metadata比较通过；0001–0004无diff |
| wheel | 构建通过，包含0001–0005、Cross模块和migration资源；无DB/PDF/.env等禁止产物 |
| SDK MockTransport | OpenAI/DeepSeek全流程、最终请求与估算一致、非法引用/拒答/incomplete共8项通过；不是真实API验收 |
| 安全/兼容性 | 新增diff凭据形状与禁止路径扫描通过；4A、4B.2通用契约/validator、两家adapter、Zotero集成、0001–0004、依赖文件均无diff |
| 远程Windows/Ubuntu CI | **未运行**；本轮没有推送/PR授权，不把本机结果转记CI通过 |
| 真实StageProvider / Zotero Level B | **NOT TESTED** |

可复现入口：

```powershell
uv run --frozen --extra dev pytest -q -s
uv run --frozen --extra dev ruff check src tests
uv run --frozen --extra dev ruff format --check src tests
uv lock --check
```

普通pytest阻断未mock网络和subprocess，8个真实integration默认排除。临时Alembic验证由进程显式设置WORKBENCH_DATABASE_PATH到随机临时目录，结束后恢复环境；没有运行默认真实库迁移。环境：Windows / Python3.13.17 / SQLite3.50.4 / SQLAlchemy2.1.6 / Alembic1.20.1 / OpenAI SDK2.54.0。

## P1验收与失败状态机

- P1-01：合法通用嵌套回归保留；首版嵌套Cross/Document、Map冒充、仅改stage、伪造/历史DTO均拒绝。服务后再次经repository验证，负例精确断言零Cross调用。
- P1-02：逐位置绑定来源→冻结文献→子执行→最终阶段→本次结果→Cross children。重排（包括同步重排outcome）、遗漏、重复、旧执行、错ordinal和持久化Map/边/hash/manifest损坏均拒绝。问题description/关联/身份在T0重查；登记后编辑问题、detach和读取端内容变化不会混入快照。两篇反序选择和三/五篇流程均覆盖。
- P1-03：T0任一表写入失败全部回滚；T1/T2/Document最终提交/T4实际commit故障不制造部分成功。两session同阶段最多一次claim和模型调用，B不早于A，父终态后子不可继续；owner/claim/finalize/锁冲突败方不TF。T4确认异常只读复查，不重复调用。
- P1-04：合成三篇分配198200、Decimal费用0.621680；实际Reduce输入案例累计161200、按输出上限名义费用0.547680。精确与超一单位、估算/版本/model漂移、缺价格、usage缺失及整次费用未知均覆盖；不经float、不双算、不借用。Map调用前和生成前复查Provider标识，预检漂移不登记历史。
- P1-05：A成功/B失败/C未开始时保留A，B失败，C parent_aborted且Stage not_started，Cross not_started。Map/Document/Cross无效返回即停；未确认返回的超时、连接和未知Provider异常为interrupted/unknown。已返回的None等无效候选为failed/response_received。KeyboardInterrupt、阶段间中断、SystemExit残留running、持续TF失败、已知usage和无效元数据脱敏均按不同场景验证，不冒充落盘成功。
- P1-06：A/B/C最终ordinal为3/4/2；混配执行/位置/最终阶段及A中真实存在的Map ordinal=2被立即FK拒绝。所有M/E关系键分别实际INSERT NULL/UPDATE NULL；所有新FK为RESTRICT/RESTRICT，精确命名四列UNIQUE不省略。补充BLOB回归确认长度/GLOB不足，0005显式要求typeof(hash)='text'。
- P1-07：专用版本、根定位、严格字段/JSON/类型、直接Finding定位、Map/边/Chunk范围/hash/fingerprint及精确闭包逐段验证。破坏中间Map文本、Finding、StageInput等，即使顶层和直接目标仍存在，也报history_integrity。同确定性Finding ID的其他历史不能补借；重开与detach后无Zotero仍可读取。

E.child_result_sha256的明确版本为`document-history-chain-sha256-v1`：包含最终生成结果及已有支撑的Map、manifest、输入边、子配置和时间，避免仅hash最终结果遗漏中间损坏。此版本写入父config；T3固定，T4与读取复查。它没有复制源全文，也不是抵抗协调改写全库的审计签名。

## 容量与性能

使用perf_counter/tracemalloc计时/峰值及SQL、chunk_document调用计数。前三项每块1000字符，合计100,000原文字符；不是1,000,000字符真实语料的性能承诺。100块路径显式把**合成整体预算**提高至10,000,000，单次StageBudget保持默认。源字符总量1,000,000的精确/超一边界另有纯内存测试。

| 夹具 | 状态/调用 | 秒 | 峰值MiB | SQL | 分块次数 |
|---|---|---:|---:|---:|---:|
| 5×20块，全部空结果 | completed，106调用 | 22.164 | 1.882 | 5950 | 3730 |
| 96/1/1/1/1块，稀疏非空 | 安全拒绝Document Reduce，96次Map | 22.170 | 1.768 | 4621 | 878 |
| 中文emoji，5×20块空结果 | completed，106调用 | 49.172 | 1.849 | 5950 | 3730 |
| 60个近2000字符的Document Findings | 安全拒绝Cross，10次子调用 | 0.630 | 1.580 | 529 | 110 |
| 最大引用闭包（12点×20引用×100 Chunk） | completed，106调用 | 15.813 | 3.495 | 5950 | 3730 |

96块非空Map结果实际渲染超过默认24000输入估算，保留全部96个Map、Reduce及后续阶段not_started、零Cross调用。并非所有100块任务都能完成；不截断/遗漏/自动增额。原容量测试把此夹具误写为必须成功，定位容量根因后改为明确断言安全拒绝、完整Map保留和精确调用次数，没有放宽生产校验。

5篇各12个Document Findings、每点近2000字符时，60个Cross输入点超过默认输入估算；所有子文献completed且零Cross调用。最大引用夹具使用显式较大合成StageBudget：最终12点×20 citations，每点闭包100 Chunk，专用历史**208299字符**，确定性保存及严格读取通过，不套用Provider原始JSON的100000字符上限。

60秒/512MiB只是本机合成测试的初始告警线；上述五个测量未触发，中文路径接近时间线。峰值为tracemalloc跟踪的Python分配，不是整个进程RSS；不含真实CLI/网络/模型延迟，也不证明Ubuntu结果。递归重验和SQL计数仍高，无缓存优化承诺。最大Finding/引用夹具在全量后仅加入测量instrumentation，2项针对性重跑通过；计数包含执行及一次完整历史读取（安全拒绝夹具无成功历史可读）。

## Commits

| 完整SHA | 边界 |
|---|---|
| `966bb98bfc21af506d83e2b1f9f828ab599bb5ac` | 单文献准备/执行有限拆分 |
| `541be7522b9faab45c4be46949a49a8e95c018a1` | 纯内存计划、拓扑及精确固定预算 |
| `ff8a30f836a851c3be1cba5830b3b149de995f2b` | 父所有权、0005及原子持久化 |
| `f338c4e8d4f214efa74a3f4d67a73ef20ce08318` | 顺序执行、专用历史及关键边界加固 |
| `1244f69780b4dc88f806c31f8300d4c8046144c8` | 约束/故障/预算/隐私/SDK/容量测试 |
| `bd938c929be68c6f8b8dee6fad47e566aa0ed92f` | 最大夹具测量及已收到响应usage/id保留断言 |

本记录最终docs提交SHA见最终回复；完整提交链由`git log 41b136113aaaa56a9801590f938fcc745a0e546d..HEAD`完整列出；最终回复提供所有完整SHA。

## 质量门槛与未完成项

初版已完成实现及上述离线检查；独立Code Review随后发现P1-08/P1-09，原质量判断需经修复复核，不能沿用918项记录声明新最终HEAD完整通过。远程最终head双平台CI、真实StageProvider、Zotero Level B、语义质量/信息损失和4B.5均未验收；前两类远程/真实验证不能由离线Fake结果替代。本轮结束后保留未合并开发分支与Open Issue，等待用户代码审核以及是否创建PR的另行授权，不主动推进。


## 独立审核 P1-08 / P1-09 修复（2026-11-06）

本轮只授权现有分支/worktree内的这两项本地修复、测试、必要文档和本地提交；无push、PR、merge、Issue关闭、4B.5或真实外部调用。生产diff只涉及document_repository.py和cross_document_analysis.py；没有迁移、依赖、4A、4B.2通用契约或adapter协议改动。

P1-08先新增回归，确认 **9 failed / 12 passed**：三个方法的全关键字调用均因args[0]触发IndexError（普通子执行和父成员各三例），三个显式签名断言也失败。恢复start_stage/complete_stage/fail_run原显式签名及fail_run原keyword-only选项，按execution_id执行原父成员保护，再转发到同session helper；不改变返回值、事务边界或终态语义。21项新测试覆盖位置/关键字/混合调用、返回值、usage、终态和父成员拒绝；连同原单篇67项及准备1项 **89 passed**。

P1-09先在真实SDK+MockTransport建立失败回归，确认 **18 failed / 21 deselected**。两家StageProvider都使用_StageCalls.generate_stage，以下路径可能合并成provider_sdk_incompatible：

| 路径 | 能否仅凭该错误码证明收到响应 |
|---|---|
| responses.create期间除timeout/connection/status之外的未知异常 | 不能；可能根本没有服务端响应 |
| _normalize显式拒绝异常status/object/error、output类型、消息类型/角色/状态/数量、content类型/数量/part类型、text类型 | 该层可能已拿到对象，但跨文献服务没有独立可信返回标记；代码含义与上一行混用 |
| _normalize拒绝非法response_id、usage类型、input/output token类型或负数 | 同上；不得将该码当receipt证据 |
| normalize_response期间意外异常，被StageCalls第二个generic catch封装 | 同上；错误码仍有歧义 |

原DeepSeek v1 generate也有同名封装，但跨文献服务不调用它；未改其代码。JSON解析失败、空JSON、拒答和incomplete走既有独立错误码，不受本修复收紧影响。

跨文献层将该歧义码与timeout/network一起纳入“没有显式返回证据则unknown”，并从隐式response_received白名单移除。Provider确实返回过候选（包括None）时，已有response hook的布尔证据仍优先，不会被此码抹除。归一化错误即使在MockTransport中确实出现HTTP响应，服务层因协议不提供独立receipt凭据也保守记unknown；这表示不能确认结果，不表示远端没有处理。没有解析异常prose或上下文来猜测响应，也没有重试。

新增39项SDK结果测试：18项歧义码覆盖Map/Document/Cross、两家SDK及create异常/显式结构拒绝/归一化异常；18项可靠非法引用/拒答/incomplete覆盖同三阶段；3项显式返回后同码异常保留receipt和usage。未知usage和整次usage费用为None，已知部分单独保留，不推断零费。与既有跨文献SDK测试合计 **47 passed**。

最终验收方式：先提交全部修复、测试及本文档，再在干净的新最终HEAD运行完整离线pytest、Ruff、锁、临时Alembic及wheel检查；之后不为记录结果追加提交。最终完整SHA、命令结果、失败复现原始输出和修复patch保存到仓库外 `C:/Projects/PHASE4B4_P1_FIX_VALIDATION.txt` 及同前缀审核文件，最终回复提供链接。远程双平台CI和真实AI/Zotero仍未测。
