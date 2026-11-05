# Phase 4B.5a — 离线质量评估（v1 + v1.1）

唯一实施依据：[Issue #21](https://github.com/AndyFu12306/research-workbench/issues/21)。
基线 b716ad74e8ad29efd31dad79cf88670f70d7bd81，独立 worktree
C:/Projects/research-workbench-phase4b5a，分支 codex/phase4b5a-quality-benchmark。
2026-11-06 用户授权实现、离线测试及本地 Conventional Commits；不授权 push、PR、merge、Issue 修改或 4B.5b/c/d。

## 已完成阶段的时间上下文

4B.4 PR #20 已合并、Issue #19 已关闭，最终开发 HEAD 6cc99bf，merge b716ad7。
已有最终离线证据为 978 passed / 8 deselected；PR CI 第二次运行双平台通过。
README/ROADMAP/旧ADR/验收记录中的“未授权PR”或首次Windows超时属于当时状态，不抹去历史。

## 边界与模块

评估全部位于 tests/quality_support，数据在 tests/fixtures/quality/v1，测试在 tests/unit/quality。
contracts 定义 frozen DTO；loading 严格解码；validation 检查定位/引用/标注完整性；
scripted 观察实际 Fake 调用及生产 validator 结果；scoring/loss 仅聚合显式标注；
reporting 仅输出白名单计数与理由码。生产代码、Prompt、Stage Schema、历史、0001–0005、依赖均不变。
标准库、pytest、既有 Fake 接口及临时 SQLite；零新依赖、零 migration。

## P1 规则

每个原子主张的 upstream_support 和 source_support 独立，不能用原文补充 Reduce 丢失的上游。
60→90为Map首次失真；忠实传递90的下游为继承错误，其原始来源轴仍失败。
仅有90的上游改回60是无上游支持纠正，不能计忠实恢复。

InputAvailability 为 explicit_complete / collectively_sufficient / insufficient /
indeterminate / unscored。集合充分须包含多个实际输入定位、合成理由及限定/反对信息。
评价 Chunk→Map集合→Document→Cross，不要求单Map独立表达跨块命题。
前两态且输出可判定才计当前层新增损失分母；其他状态单列，端到端保留率另报。

quality-observation-binding-v1绑定基准/来源/完整Question/要求、候选、配置/版本、
实际输入输出hash、作用域Finding映射、rubric/原子化规则及显式run_alias。
观察必须来自实际请求/返回/validator，临时库测试再与历史核对；不能从脚本推导观察。
新运行不能静默复用旧评分。墙钟、临时路径、DB ID只作独立元数据，不进稳定报告。

## 验证顺序

1. 严格数据契约与文档；2. 18组合成案例/候选/草稿标注；
3. 双轴/复核/分母；4. 提取损失与传播；5. 实际观察及临时库；6. 白名单/隐私/全回归。
关键P1先建立失败回归。纯函数覆盖变体，端到端仅短文少块，监测Windows10分钟CI限制。

## 人工复核真实性

本轮没有独立人工评审者。仓库标注为 Codex 编制的 draft，不是已审定人工证据。
机制测试可模拟评审状态，但不能把模拟记录当实际人类签名。实际报告必须 provisional。
真实模型/API、Zotero全文/Level B及文献外发均 NOT TESTED；95%/90%只是待校准目标。
