"""Interactive, consent-gated two-document workflow; no credentials in arguments."""

import argparse
import getpass
import json
import re
import sys
import unicodedata
import warnings
from decimal import Decimal, InvalidOperation
from pathlib import Path

from research_workbench.analysis.cross_document_pipeline import ERRORS, CrossBudget, exact_sum
from research_workbench.analysis.document_pipeline import Pricing
from research_workbench.analysis.errors import (
    INCOMPLETE_REASONS,
    PROVIDER_FAILURE_MESSAGES,
    AnalysisProviderError,
)
from research_workbench.analysis.planning import PlanningBudget
from research_workbench.analysis.stage_contracts import (
    MAX_STAGE_OUTPUT_TOKENS,
    StageBudget,
    StageBudgetError,
)
from research_workbench.analysis.stage_validation import validate_budget
from research_workbench.application.checkpoints import CheckpointUnavailable, inspect_execution
from research_workbench.application.cross_document_analysis import CrossDocumentAnalysisService
from research_workbench.application.resume import service_for_resume
from research_workbench.application.source_links import QuestionSourceService
from research_workbench.integrations.ai.stages import (
    DEEPSEEK_MODEL_LIMITS,
    DeepSeekStageProvider,
    validate_model_budget,
)
from research_workbench.integrations.zotero import ZoteroClient
from research_workbench.integrations.zotero.errors import ZoteroError
from research_workbench.persistence import Database, ProjectRepository, QuestionRepository
from research_workbench.persistence.cross_document_repository import CrossDocumentRepository
from research_workbench.persistence.safe_migration import backup_and_upgrade
from research_workbench.persistence.source_repositories import SourceRepository


class CliError(ValueError):
    pass


def display(value):
    """Do not let source/provider text inject terminal control or bidi sequences."""
    text = str(value)
    print("".join(c for c in text if c == "\n" or not unicodedata.category(c).startswith("C")))


def ask(prompt):
    value = input(prompt).strip()
    if not value:
        raise CliError("输入不能为空；已停止。")
    return value


def confirm(prompt, word):
    if input(f"{prompt}\n输入 {word} 继续，其他输入取消：").strip() != word:
        raise CliError("已取消；未发出 AI 请求。")


def positive_decimal(prompt):
    try:
        value = Decimal(ask(prompt))
        if not value.is_finite() or value <= 0:
            raise ValueError
        return value
    except (InvalidOperation, ValueError):
        raise CliError("价格和费用上限必须是大于零的有限数字。") from None


def database_path(raw):
    path = Path(raw).expanduser().resolve()
    if path.name.lower().startswith("zotero") or path.suffix.lower() not in (
        ".db",
        ".sqlite",
        ".sqlite3",
    ):
        raise CliError("请选择独立的 Workbench .db 文件，禁止使用 Zotero 数据库。")
    return path


def interactive_terminal():
    return sys.stdin.isatty() and sys.stdout.isatty()


class ReportingProvider:
    """Keep fixed adapter diagnostics without exposing exceptions or response bodies."""

    def __init__(self, provider):
        self.delegate = provider
        self.last_error = None
        self.last_incomplete_reason = None
        self.calls = 0

    @property
    def provider(self):
        return self.delegate.provider

    @property
    def model(self):
        return self.delegate.model

    @property
    def capabilities(self):
        return self.delegate.capabilities

    def estimate_stage(self, request):
        try:
            return self.delegate.estimate_stage(request)
        except StageBudgetError as exc:
            display(f"Workbench 操作估算超限：{request.stage}（不是服务商真实 token 超限判定）。")
            for label, value in (
                ("输入估算", exc.input_units),
                ("输入额度", exc.input_limit),
                ("context估算", exc.context_units),
                ("context额度", exc.context_limit),
            ):
                if type(value) is int:
                    display(f"{label}={value}")
            display(
                "可调高 --stage-input-units / --stage-context-tokens；不删除任何 Finding 或引用。"
            )
            display(
                "若真实 token 输入确实超过模型窗口，需按文献分组做分层 Reduce，再综合各组结果。"
            )
            raise

    def authorize_stage(self, request):
        confirm(f"即将发送新的 {request.stage} API 请求（可能付费）；确认本次调用？", "SEND")

    def generate_stage(self, request):
        self.calls += 1
        display(f"调用 {self.calls}: {request.stage}（无自动重试）")
        try:
            return self.delegate.generate_stage(request)
        except AnalysisProviderError as exc:
            self.last_error = (
                exc.code if exc.code in PROVIDER_FAILURE_MESSAGES else "provider_failed"
            )
            reason = getattr(exc, "reason", None)
            if (
                exc.code == "provider_incomplete"
                and type(reason) is str
                and reason in INCOMPLETE_REASONS
            ):
                self.last_incomplete_reason = reason
            raise


def select_two(reader):
    keys = []
    for number in (1, 2):
        query = ask(f"第 {number} 篇：输入 Zotero 搜索词：")
        items = reader.search_items(query, limit=10)
        if not items:
            raise CliError("搜索无结果；请调整搜索词后重跑。这不代表全文可用。")
        for i, item in enumerate(items, 1):
            display(f"{i}. {item.title} [{item.item_key}]")
        try:
            index = int(ask("选择结果序号："))
            if not 1 <= index <= len(items):
                raise ValueError
        except ValueError:
            raise CliError("序号无效；已停止。") from None
        item = items[index - 1]
        if item.item_key in keys:
            raise CliError("必须选择两篇不同的文献。")
        keys.append(item.item_key)
    return tuple(keys)


def preview(prepared):
    display("\n外发预览：目标 DeepSeek https://api.deepseek.com / Responses API")
    display(f"模型：{prepared.selection.model}")
    display("数据：研究问题标题及描述、两篇所选文献的全部提取文本（按块发送），")
    display("以及 AI 生成的中间分析、引用标签、范围与 fingerprint。不会发送整个 Zotero 库。")
    for i, (doc, plan) in enumerate(
        zip(prepared.selection.documents, prepared.documents, strict=True), 1
    ):
        display(
            f"文献 {i} [{doc.identity.external_key}]：{len(doc.text)} 字符，"
            f"{len(plan.requests)} 个 Map + 1 个 Document Reduce"
        )
    allocation, budget = prepared.allocation, prepared.budget
    display(f"最后 1 个 Cross Reduce；预计最多 {allocation.calls} 次调用，失败即停。")
    display(
        f"整体操作单位估算/上限：{allocation.context_tokens}/{budget.max_estimated_tokens}；"
        f"调用上限：{budget.max_calls}。"
    )
    grouped = (
        ("map", tuple(a for g in allocation.documents for a in g[:-1])),
        ("document_reduce", tuple(g[-1] for g in allocation.documents)),
        ("cross_document_reduce", (allocation.cross,)),
    )
    for stage, allowances in grouped:
        b = prepared.budget_for(stage)
        cost = exact_sum(a.cost for a in allowances) if prepared.pricing else None
        display(
            f"{stage}: 输出={b.output_tokens} tokens；调用={len(allowances)}；"
            f"操作单位预留={sum(a.context_tokens for a in allowances)}；"
            f"名义费用预留={cost} {prepared.pricing.currency if prepared.pricing else 'unknown'}"
        )
    display(
        f"单次输入操作单位上限={prepared.stage_budget.max_input_tokens}，"
        f"context 操作单位上限={prepared.stage_budget.context_tokens}；"
        "输出额度包含 reasoning tokens。"
    )
    limits = DEEPSEEK_MODEL_LIMITS.get(prepared.selection.model)
    if limits:
        display(f"公开模型容量（token）：context={limits[0]}，最大输出={limits[1]}。")
    else:
        display("该模型无内置容量资料；请按供应商能力配置，模型真实限制未在本机核实。")
    display("每个 Document Reduce 和 Cross Reduce 均提前预留完整输入额度，费用预览含这些预留。")
    display(
        f"按所填单价估算预留：{allocation.cost} {prepared.pricing.currency}；"
        f"费用阈值：{budget.max_estimated_cost} {prepared.pricing.currency}。"
    )
    display("估算基于 UTF-8 bytes，非精确 token；费用阈值不是服务商账单保证。")
    display("动态 Reduce 仍可能超限失败；无截断、降级、输出修补或自动重试。")
    display("本地保存生成分析、引用链、执行历史及 fingerprint；不保存原文或 Key。")
    display("AI 分析可能复述敏感内容；引用链不证明语义正确，结果需人工核验。")


def show_run(database, run_id):
    repo = CrossDocumentRepository(database)
    run = repo.get(run_id)
    if run is None:
        raise CliError("找不到该执行 ID。")
    display(
        f"\n执行 ID={run.id} Question={run.question_id} 状态={run.status} "
        f"模型={run.model} 时间={run.created_at.isoformat()} 错误={run.error_code or '-'}"
    )
    sources = SourceRepository(database)
    for member in repo.get_members(run.id):
        source = sources.get(member.source_id)
        display(
            f"文献 {member.ordinal}: SourceRef={member.source_id} "
            f"{source.provider}/{source.scope}/{source.external_key} "
            f"子执行={member.document_execution_id}"
        )
    for call in repo.get_call_usage(run.id):
        display(
            f"{call.scope}/{call.ordinal} {call.stage}: {call.status} "
            f"outcome={call.call_outcome} tokens={call.input_tokens}/{call.output_tokens} "
            f"error={call.error_code or '-'}"
        )
    costs = repo.get_costs(run.id)
    price_config = json.loads(run.config_json)["pricing"]
    currency = price_config["currency"] if price_config else "unknown"
    display(
        f"名义费用（{currency}）：已知部分={costs.known_usage_cost} 全部={costs.usage_cost}；"
        f"未知调用={costs.unknown_calls} 未开始={costs.not_started_calls}（None=未知）"
    )
    from sqlalchemy import inspect, select

    from research_workbench.persistence.checkpoint_models import checkpoints, resumes

    with database._session() as session:
        if inspect(session.connection()).has_table("execution_resumes"):
            lineage = (
                session.execute(select(resumes).where(resumes.c.run_id == run.id))
                .mappings()
                .first()
            )
            reused = list(
                session.execute(
                    select(checkpoints).where(
                        checkpoints.c.run_id == run.id, checkpoints.c.origin == "reuse"
                    )
                ).mappings()
            )
            if lineage:
                historical = repo.get_costs(lineage.source_run_id)
                new_calls = sum(
                    c.call_outcome not in ("reused", "not_started")
                    for c in repo.get_call_usage(run.id)
                )
                display(
                    f"复用来源执行={lineage.source_run_id}；复用阶段={len(reused)}；"
                    f"新增调用={new_calls}"
                )
                display(
                    f"历史已知费用={historical.known_usage_cost} "
                    f"历史全部费用={historical.usage_cost}；"
                    f"本执行新增已知费用={costs.known_usage_cost} 新增全部费用={costs.usage_cost}"
                )
                for ancestor_id, ancestor in repo.get_ancestor_costs(run.id)[1:]:
                    display(
                        f"更早执行={ancestor_id} 自身已知费用={ancestor.known_usage_cost} "
                        f"自身全部费用={ancestor.usage_cost}（独立历史，不计入新增费用）"
                    )
    if run.status == "completed":
        data = json.loads(repo.get_history(run.id).result_json)
        display(f"\nAI 生成分析 / AI 推断（需人工核验）：{data['status']}")
        for finding in data["findings"]:
            display(f"\n[{finding['kind']} / {finding['category']}] {finding['text']}")
            display("直接引用：" + json.dumps(finding["citations"], ensure_ascii=False))
            display("来源块：" + json.dumps(finding["chunk_refs"], ensure_ascii=False))


def configured_stage_budget(output_tokens, context_tokens, input_units=512_000):
    budget = StageBudget(
        output_tokens=output_tokens, context_tokens=context_tokens, max_input_tokens=input_units
    )
    try:
        validate_budget(budget)
    except ValueError:
        raise CliError(
            f"阶段输入/context必须为正整数，输出额度为 1–{MAX_STAGE_OUTPUT_TOKENS}。"
        ) from None
    if context_tokens < budget.max_input_tokens + output_tokens + budget.safety_margin_tokens:
        raise CliError("context 额度不足以容纳输入、输出与安全预留；请显式配置更大的 context。")
    return budget


def show_failure(database, execution_id):
    """Automatic diagnostics contain no source keys, titles, findings or response IDs."""
    repo = CrossDocumentRepository(database)
    run = repo.get(execution_id)
    display(f"执行 ID={execution_id} 状态={run.status if run else 'unknown'}")
    calls = repo.get_call_usage(execution_id)
    display(
        f"completed={sum(c.status == 'completed' for c in calls)} "
        f"not_started={sum(c.call_outcome == 'not_started' for c in calls)}"
    )
    for call in calls:
        if call.status in ("failed", "interrupted"):
            display(
                f"{call.scope}/{call.ordinal} {call.stage}: {call.error_code} "
                f"tokens={call.input_tokens}/{call.output_tokens}"
            )


def require_model_budgets(model, budgets):
    """Reject budgets the model cannot serve before keys, source reads or paid calls."""
    try:
        for budget in budgets:
            if budget is not None:
                validate_model_budget(model, budget)
    except ValueError:
        raise CliError(
            "所配置或历史保存的阶段输出额度超过该模型容量；未登记模型按保守输出上限处理。"
            "不会自动修改历史预算。请按模型能力调整。"
        ) from None


def require_history_model_budgets(model, config_json):
    """Check the saved Map / Document Reduce / Cross Reduce budgets without any I/O."""
    config = json.loads(config_json)
    reduce_budgets = config.get("reduce_stage_budgets", {})
    require_model_budgets(
        model,
        (
            StageBudget(**config["stage_budget"]),
            StageBudget(**reduce_budgets.get("document_reduce", config["stage_budget"])),
            StageBudget(**reduce_budgets.get("cross_document_reduce", config["stage_budget"])),
        ),
    )


def run_workflow(
    database,
    *,
    stage_budget=None,
    document_reduce_budget=None,
    cross_reduce_budget=None,
    max_estimated_units=8_000_000,
):
    confirm("允许在本机检查 Zotero 并搜索/读取你选择的两篇全文？此时不会调用 AI。", "READ")
    reader = ZoteroClient()
    health = reader.health_check()
    display(
        f"Zotero：Client={health.client.status} Local API={health.local_api.status} "
        f"CLI={health.cli.status}"
    )
    if health.status != "healthy":
        raise CliError("Zotero 连接检查未通过；请打开 Zotero、启用本机通信并检查 zotero-cli。")
    keys = select_two(reader)
    project_title = ask("Research Project 名称：")
    question_title = ask("研究 Question：")
    description = input("问题补充说明（可留空）：").strip()
    model = ask("DeepSeek 模型名称（填写账户实际可用且支持 Responses/结构化输出的模型）：")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}", model):
        raise CliError("模型名称格式无效。")
    if stage_budget is not None:
        require_model_budgets(model, (stage_budget, document_reduce_budget, cross_reduce_budget))
    display("请按服务商控制台填写当前单价；程序不内置价格，也不联网查询价格。")
    currency = ask("币种（三位字母，例如 CNY）：").upper()
    input_price = positive_decimal("输入价格 / 百万 tokens（保守使用非缓存价）：")
    output_price = positive_decimal("输出价格 / 百万 tokens：")
    cap = positive_decimal("本次名义费用上限（同币种）：")
    pricing = Pricing(currency, "user-entered-v1", input_price, output_price)
    # GetPassWarning means hidden input is unavailable: refuse its echoing fallback.
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", getpass.GetPassWarning)
            key = getpass.getpass("DeepSeek API Key（隐藏输入，仅本进程使用）：")
    except getpass.GetPassWarning:
        raise CliError("当前终端不能隐藏 Key；请使用原生 PowerShell 终端重新启动。") from None
    config = {"DEEPSEEK_API_KEY": key, "DEEPSEEK_MODEL": model, "DEEPSEEK_TIMEOUT_SECONDS": "60"}
    try:
        with DeepSeekStageProvider(environ=config) as delegate:
            provider = ReportingProvider(delegate)
            project = ProjectRepository(database).create(project_title)
            question = QuestionRepository(database).create(project.id, question_title, description)
            display(f"Project ID={project.id}；Question ID={question.id}")
            links = QuestionSourceService(database, reader)
            source_ids = tuple(links.attach_zotero_source(question.id, k).source.id for k in keys)
            service = CrossDocumentAnalysisService(
                database,
                reader,
                provider,
                stage_budget=stage_budget,
                document_reduce_budget=document_reduce_budget,
                cross_reduce_budget=cross_reduce_budget,
                planning_budget=PlanningBudget(
                    context_tokens=stage_budget.context_tokens,
                    max_input_tokens=stage_budget.max_input_tokens,
                    reserved_output_tokens=stage_budget.output_tokens,
                    max_total_estimated_tokens=max_estimated_units,
                )
                if stage_budget is not None
                else None,
                cross_budget=CrossBudget(
                    max_estimated_tokens=max_estimated_units, max_estimated_cost=cap
                ),
                pricing=pricing,
            )
            prepared = service.prepare_analysis(question.id, source_ids)
            preview(prepared)
            confirm("确认将上述数据外发，并接受本次可能产生的 API 费用？", "SEND")
            try:
                run = service.execute_prepared(prepared)
            except BaseException:
                if provider.last_error:
                    display(f"AI 安全诊断：{provider.last_error}")
                    display(
                        PROVIDER_FAILURE_MESSAGES.get(
                            provider.last_error, "Provider request failed."
                        )
                    )
                    if provider.last_error == "provider_incomplete":
                        display(
                            "incomplete_details.reason="
                            + (provider.last_incomplete_reason or "unknown")
                        )
                # This question was just created; its only run is this attempt.
                runs = CrossDocumentRepository(database).list_by_question(question.id)
                if runs:
                    show_failure(database, runs[0].id)
                raise
            show_run(database, run.id)
    finally:
        config.clear()
        key = None


def resume_workflow(database, source_id):
    assessment = inspect_execution(database, source_id)
    if not assessment.checkpoint_resume_available:
        raise CheckpointUnavailable()
    old = CrossDocumentRepository(database).get(source_id)
    display(f"原执行={source_id}；原模型={old.model}。将创建新执行，历史保持不变。")
    model = ask("当前 DeepSeek 模型名称（必须与原执行完全一致）：")
    if (old.provider, old.model) != ("deepseek", model):
        raise CheckpointUnavailable()
    # Historical Map/Reduce budgets are checked before Zotero reads, the key prompt
    # and any new paid call; saved budgets are never rewritten.
    require_history_model_budgets(model, old.config_json)
    confirm(
        "允许重新只读获取原执行选定的 Zotero 全文以校验 fingerprint？不会搜索或写入 Zotero。",
        "READ",
    )
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", getpass.GetPassWarning)
            key = getpass.getpass("DeepSeek API Key（隐藏输入，仅本进程使用）：")
    except getpass.GetPassWarning:
        raise CliError("当前终端不能隐藏 Key；请使用原生 PowerShell。") from None
    config = {"DEEPSEEK_API_KEY": key, "DEEPSEEK_MODEL": model, "DEEPSEEK_TIMEOUT_SECONDS": "60"}
    try:
        with DeepSeekStageProvider(environ=config) as delegate:
            provider = ReportingProvider(delegate)
            service, prepared, resume = service_for_resume(
                database, ZoteroClient(), provider, source_id
            )
            display(
                "外发预览：DeepSeek https://api.deepseek.com / Responses API；"
                "原问题、剩余块全文及生成分析/引用。"
            )
            display(
                "本地仅保存规范生成 JSON、引用和 fingerprint；不保存原文、请求 payload 或 Key。"
            )
            for m, n, result in resume.results:
                display(
                    f"复用：原执行 {source_id} document-{m}/{n} "
                    f"{result.request.stage}；零新增调用。"
                )
            historical = CrossDocumentRepository(database).get_costs(source_id)
            display(
                f"历史已知费用={historical.known_usage_cost} "
                f"历史全部费用={historical.usage_cost}（None=未知）"
            )
            for ancestor_id, ancestor in CrossDocumentRepository(database).get_ancestor_costs(
                source_id
            ):
                display(
                    f"更早执行={ancestor_id} 自身已知费用={ancestor.known_usage_cost} "
                    f"自身全部费用={ancestor.usage_cost}（不计入新增费用）"
                )
            display(
                f"预计新增付费调用={resume.new_calls}；"
                f"新增 context 操作单位预留={resume.new_context_units}；"
                f"新增名义费用预留={resume.new_cost} "
                f"{prepared.pricing.currency if prepared.pricing else 'unknown'}"
            )
            display(
                "严格校验全部来源/问题/模型/版本/预算/请求/成功结果；"
                "动态 Reduce 仍可能超限。无自动重试。"
            )
            confirm("确认启动新执行？每次新 API 调用仍需再次输入 SEND。", "SEND")
            run = service.execute_prepared(prepared, _resume=resume)
            show_run(database, run.id)
    finally:
        config.clear()
        key = None


def main(argv=None):
    parser = argparse.ArgumentParser(description="Research Workbench：两篇 Zotero 文献的交互式分析")
    parser.add_argument(
        "--database", default="data/workbench-cli.db", help="独立 Workbench SQLite 路径"
    )
    commands = parser.add_subparsers(dest="command", required=True)
    run_parser = commands.add_parser("run", help="交互式创建研究、选择两篇文献并确认执行")
    run_parser.add_argument(
        "--stage-output-tokens",
        type=int,
        default=None,
        help="兼容选项：统一设置三阶段输出；独立阶段选项优先",
    )
    for name, default in (("map", 8192), ("document-reduce", 32768), ("cross-reduce", 32768)):
        options = [f"--{name}-output-tokens"]
        if name == "cross-reduce":
            options.append("--cross-document-reduce-output-tokens")
        run_parser.add_argument(
            *options,
            type=int,
            default=None,
            help=f"该阶段输出tokens，默认{default}，包含reasoning",
        )
    run_parser.add_argument(
        "--stage-context-units",
        "--stage-context-tokens",
        dest="stage_context_tokens",
        type=int,
        default=1_000_000,
        help="单次context操作估算额度，默认1000000；UTF-8字节代理，不是真实token计数",
    )
    run_parser.add_argument(
        "--stage-input-units",
        type=int,
        default=512_000,
        help="完整阶段输入操作估算额度，默认512000，适用于Map及两种Reduce",
    )
    run_parser.add_argument(
        "--max-estimated-units",
        type=int,
        default=8_000_000,
        help="本次整体操作估算额度，默认8000000；费用上限仍独立生效",
    )
    history = commands.add_parser("history", help="离线列出某个 Question 的执行历史")
    history.add_argument("--question", type=int, required=True)
    show = commands.add_parser("show", help="离线查看执行结果、来源和用量")
    show.add_argument("execution_id", type=int)
    inspect = commands.add_parser(
        "inspect", help="离线检查安全诊断/检查点条件，不读Zotero、不调用AI"
    )
    inspect.add_argument("execution_id", type=int)
    resume = commands.add_parser("resume", help="严格校验检查点，预览剩余调用后创建新执行")
    resume.add_argument("execution_id", type=int)
    commands.add_parser("migrate", help="完整备份并校验历史不变后，使用 Alembic 升级数据库")
    args = parser.parse_args(argv)
    try:
        path = database_path(args.database)
        stage_budget = document_reduce_budget = cross_reduce_budget = None
        if args.command == "run":
            budgets = []
            for value, default in (
                (args.map_output_tokens, 8192),
                (args.document_reduce_output_tokens, 32768),
                (args.cross_reduce_output_tokens, 32768),
            ):
                output = (
                    value
                    if value is not None
                    else (
                        args.stage_output_tokens
                        if args.stage_output_tokens is not None
                        else default
                    )
                )
                budgets.append(
                    configured_stage_budget(
                        output, args.stage_context_tokens, args.stage_input_units
                    )
                )
            stage_budget, document_reduce_budget, cross_reduce_budget = budgets
        if args.command == "run" and args.max_estimated_units < max(
            b.max_input_tokens + b.output_tokens + b.safety_margin_tokens for b in budgets
        ):
            raise CliError("整体操作额度不足以预留一次Reduce；请调整 --max-estimated-units。")
        if args.command in ("run", "resume") and not interactive_terminal():
            raise CliError("真实运行必须使用交互式终端；不接受管道确认或重定向输出。")
        if args.command != "run" and not path.is_file():
            raise CliError("Workbench 数据库不存在；请先使用 run。")
        display(f"Workbench 数据库：{path}")
        with Database(path) as database:
            if args.command == "run":
                display(
                    "通过 Alembic 升级上述独立 Workbench 数据库到 head（不操作 Zotero 数据库）。"
                )
                backup_and_upgrade(database, on_backup=lambda p: display(f"迁移前已验证备份：{p}"))
                run_workflow(
                    database,
                    stage_budget=stage_budget,
                    document_reduce_budget=document_reduce_budget,
                    cross_reduce_budget=cross_reduce_budget,
                    max_estimated_units=args.max_estimated_units,
                )
            elif args.command == "migrate":
                backup = backup_and_upgrade(
                    database, on_backup=lambda p: display(f"迁移前已验证备份：{p}")
                )
                display(f"Alembic 升级及 integrity/FK/历史逐表内容检查通过；备份={backup}")
            elif args.command == "resume":
                resume_workflow(database, args.execution_id)
            elif args.command == "inspect":
                result = inspect_execution(database, args.execution_id)
                display(
                    f"completed={result.completed} failed={result.failed} "
                    f"not_started={result.not_started}"
                )
                repo = CrossDocumentRepository(database)
                for call in repo.get_call_usage(args.execution_id):
                    if call.status in ("failed", "interrupted"):
                        display(f"{call.scope}/{call.ordinal} {call.stage}: {call.error_code}")
                display(
                    f"失败候选已保存={result.raw_failed_candidate_saved}；细分类别已保存={result.diagnostic_available}"
                )
                if result.failed and not result.diagnostic_available:
                    display("旧 invalid_output 无法追溯具体类别；不会猜测Citation或Finding错误。")
                display(
                    f"checkpoint_resume_available={result.checkpoint_resume_available}；"
                    "仅为离线完整性初检；resume 仍须重验来源及全部绑定。"
                )
                display("离线结果重建需重验来源/版本/预算/模型/全部Finding ID；不能直接发起续跑。")
                if not result.checkpoint_resume_available:
                    display(
                        "缺少完整规范检查点、状态不允许或已启动过续跑：拒绝续跑；"
                        "不猜测/重新分配 Finding ID。"
                    )
            elif args.command == "history":
                runs = CrossDocumentRepository(database).list_by_question(args.question)
                if not runs:
                    display("没有执行历史；请核对 Question ID。")
                for run in runs:
                    display(
                        f"ID={run.id} {run.status} {run.created_at.isoformat()} "
                        f"{run.error_code or '-'}"
                    )
            else:
                show_run(database, args.execution_id)
        return 0
    except (KeyboardInterrupt, EOFError):
        display("已中止；已发出的调用可能产生费用。使用 history/show 检查状态，不会自动重试。")
        return 130
    except CliError as exc:
        display(str(exc))
        return 2
    except CheckpointUnavailable:
        display(
            "拒绝续跑：检查点缺失/损坏，或问题、来源、模型、版本、预算、绑定不一致，或已启动过续跑。未自动重试。"
        )
        return 2
    except ZoteroError as exc:
        display(f"Zotero 读取失败，层级：{exc.layer}；未展示可能含私人内容的原始诊断。")
        return 1
    except Exception as exc:
        code = getattr(exc, "code", None)
        if code in ERRORS:
            display(f"执行失败：{code} / {ERRORS[code]}")
            for name, value in getattr(exc, "counts", ()):
                display(f"{name}={value}")
        elif code in PROVIDER_FAILURE_MESSAGES:
            display(f"AI 失败：{code} / {PROVIDER_FAILURE_MESSAGES[code]}")
        else:
            display("安全停止：配置、输入、全文、预算或数据库检查失败；未打印原始异常。")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
