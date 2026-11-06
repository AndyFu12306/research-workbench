"""First-release three-level policy and fixed per-call allowances; no I/O."""

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal, DecimalException, Inexact, Rounded, localcontext

from .chunking import SourceDocument, fingerprint
from .document_pipeline import PreparedDocument, Pricing, RequestEstimate
from .stage_contracts import CONTRACT_VERSIONS, StageBudget, StageRequest, ValidatedStageResult
from .stage_validation import validate_budget, validate_stage_output

PIPELINE_VERSION = "cross-document-pipeline-v1"
HISTORY_VERSION = "cross-document-history-v1"
QUESTION_VERSION = "document-question-json-v1"
ERRORS = {
    "invalid_plan": "Cross-document plan or ordered binding is invalid.",
    "budget_exceeded": "Cross-document fixed allowance exceeded.",
    "source_unavailable": "Selected linked fulltext is unavailable.",
    "duplicate_content": "Selected documents must have distinct identities and content.",
    "conflict": "Cross-document execution ownership or state conflict.",
    "history_integrity": "Cross-document history integrity check failed.",
    "persistence_failed": "Cross-document persistence failed.",
    "invalid_output": "Cross-document output validation failed.",
    "provider_failed": "Cross-document provider failed.",
    "provider_incomplete": "Provider output is incomplete; synthesis is unavailable.",
    "provider_context_length": "Model context exceeded; hierarchical Reduce is needed.",
    "interrupted": "Cross-document call outcome is unknown or execution interrupted.",
    "parent_aborted": "Parent execution stopped before this document completed.",
}


class CrossDocumentError(ValueError):
    def __init__(self, code="invalid_plan"):
        self.code = code if code in ERRORS else "invalid_plan"
        super().__init__(ERRORS[self.code])


class CrossConflict(CrossDocumentError):
    def __init__(self):
        super().__init__("conflict")


def require(condition, code="invalid_plan"):
    if not condition:
        raise CrossDocumentError(code)


def validate_selection(source_ids):
    require(type(source_ids) is tuple and 2 <= len(source_ids) <= 5)
    require(all(type(i) is int and i > 0 for i in source_ids))
    require(len(set(source_ids)) == len(source_ids))


def exact_cost(price, input_tokens, output_tokens):
    if price is None or input_tokens is None or output_tokens is None:
        return None
    require(type(price) is Pricing, "budget_exceeded")
    require(
        all(type(n) is int and n >= 0 for n in (input_tokens, output_tokens)), "budget_exceeded"
    )
    try:
        with localcontext() as context:
            context.prec = 128
            context.traps[Inexact] = context.traps[Rounded] = True
            return price.cost(input_tokens, output_tokens)
    except DecimalException:
        raise CrossDocumentError("budget_exceeded") from None


def exact_sum(values):
    try:
        with localcontext() as context:
            context.prec = 128
            context.traps[Inexact] = context.traps[Rounded] = True
            return sum(values, Decimal(0))
    except DecimalException:
        raise CrossDocumentError("budget_exceeded") from None


@dataclass(frozen=True, slots=True)
class CrossBudget:
    max_calls: int = 106
    max_estimated_tokens: int = 1_000_000
    max_estimated_cost: Decimal | None = None

    def __post_init__(self):
        require(all(type(i) is int and i > 0 for i in (self.max_calls, self.max_estimated_tokens)))
        c = self.max_estimated_cost
        require(c is None or (type(c) is Decimal and c.is_finite() and c >= 0))


@dataclass(frozen=True, slots=True)
class CallAllowance:
    input_tokens: int
    context_tokens: int
    estimator_version: str
    cost: Decimal | None


@dataclass(frozen=True, slots=True)
class Allocation:
    documents: tuple[tuple[CallAllowance, ...], ...]
    cross: CallAllowance
    calls: int
    context_tokens: int
    cost: Decimal | None


def check_allowance(allowance, estimate, budget, price):
    require(type(estimate) is RequestEstimate, "budget_exceeded")
    require(
        type(estimate.input_tokens) is int and type(estimate.context_tokens) is int,
        "budget_exceeded",
    )
    require(
        0 < estimate.input_tokens <= min(allowance.input_tokens, budget.max_input_tokens)
        and estimate.context_tokens
        == estimate.input_tokens + budget.output_tokens + budget.safety_margin_tokens
        and estimate.context_tokens <= min(allowance.context_tokens, budget.context_tokens)
        and estimate.estimator_version == allowance.estimator_version,
        "budget_exceeded",
    )
    cost = exact_cost(price, estimate.input_tokens, budget.output_tokens)
    require(
        allowance.cost is None or (cost is not None and cost <= allowance.cost), "budget_exceeded"
    )
    return cost


def allocate_budget(groups, stage_budget, budget, price=None):
    validate_budget(stage_budget)
    require(type(budget) is CrossBudget)
    require(type(groups) is tuple and 2 <= len(groups) <= 5)
    require(all(type(g) is tuple and len(g) > 0 for g in groups))
    require(sum(map(len, groups)) <= 100, "budget_exceeded")
    require(budget.max_estimated_cost is None or price is not None, "budget_exceeded")
    versions = {e.estimator_version for g in groups for e in g}
    require(len(versions) == 1, "budget_exceeded")
    version = next(iter(versions))
    require(type(version) is str and 1 <= len(version) <= 64)
    reserve = CallAllowance(
        stage_budget.max_input_tokens,
        stage_budget.max_input_tokens
        + stage_budget.output_tokens
        + stage_budget.safety_margin_tokens,
        version,
        exact_cost(price, stage_budget.max_input_tokens, stage_budget.output_tokens),
    )
    check_allowance(
        reserve,
        RequestEstimate(reserve.input_tokens, reserve.context_tokens, version),
        stage_budget,
        price,
    )
    documents = []
    for group in groups:
        allowances = []
        for e in group:
            a = CallAllowance(
                e.input_tokens,
                e.context_tokens,
                e.estimator_version,
                exact_cost(price, e.input_tokens, stage_budget.output_tokens),
            )
            check_allowance(a, e, stage_budget, price)
            allowances.append(a)
        documents.append((*allowances, reserve))
    all_calls = [a for g in documents for a in g] + [reserve]
    total = sum(a.context_tokens for a in all_calls)
    cost = exact_sum(a.cost for a in all_calls) if price else None
    require(
        len(all_calls) <= budget.max_calls and total <= budget.max_estimated_tokens,
        "budget_exceeded",
    )
    require(
        budget.max_estimated_cost is None or cost <= budget.max_estimated_cost, "budget_exceeded"
    )
    return Allocation(tuple(documents), reserve, len(all_calls), total, cost)


@dataclass(frozen=True, slots=True)
class FrozenSelection:
    question_id: int
    source_ids: tuple[int, ...]
    question: str = field(repr=False)
    documents: tuple[SourceDocument, ...] = field(repr=False)
    read_at: tuple[datetime, ...]
    provider: str
    model: str

    def __post_init__(self):
        validate_selection(self.source_ids)
        require(len(self.documents) == len(self.source_ids) == len(self.read_at))
        require(
            len({d.identity for d in self.documents}) == len(self.documents), "duplicate_content"
        )
        require(
            len({d.content_sha256 for d in self.documents}) == len(self.documents),
            "duplicate_content",
        )
        require(sum(len(d.text) for d in self.documents) <= 1_000_000, "budget_exceeded")
        require(
            all(
                (d.identity.provider, d.identity.scope) == ("zotero", "personal")
                for d in self.documents
            ),
            "source_unavailable",
        )


@dataclass(frozen=True, slots=True)
class PreparedCross:
    selection: FrozenSelection = field(repr=False)
    documents: tuple[PreparedDocument, ...] = field(repr=False)
    allocation: Allocation
    stage_budget: StageBudget
    budget: CrossBudget
    pricing: Pricing | None


@dataclass(frozen=True, slots=True)
class MemberBinding:
    ordinal: int
    source_id: int
    document_execution_id: int
    final_stage_ordinal: int


@dataclass(frozen=True, slots=True)
class _CrossExecutionHandle:
    run_id: int
    owner: str = field(repr=False)
    bindings: tuple[MemberBinding, ...]


def validate_topology(request, prepared, outcomes):
    """Additional first-release policy. Generic 4B.2 validator stays untouched."""
    selection = prepared.selection
    require(type(request) is StageRequest and request.stage == "cross_document_reduce")
    require(
        request.contract_version == CONTRACT_VERSIONS[request.stage]
        and request.question == selection.question
        and request.documents == selection.documents
        and not request.chunks
        and request.budget == prepared.stage_budget
    )
    require(len(outcomes) == len(prepared.documents) == len(request.children))
    for i, (p, outcome, child) in enumerate(
        zip(prepared.documents, outcomes, request.children, strict=True)
    ):
        require(type(child) is ValidatedStageResult and child == outcome.result)
        require(
            outcome.run.source_id == selection.source_ids[i]
            and outcome.run.status == "completed"
            and outcome.final_stage_ordinal == len(p.requests) + 1
        )
        r = child.request
        require(
            r.stage == "document_reduce"
            and r.question == selection.question
            and r.documents == (selection.documents[i],)
            and not r.chunks
            and r.chunking == p.plan.chunking
            and r.budget == prepared.stage_budget
            and len(r.children) == len(p.requests)
        )
        for map_result, expected in zip(r.children, p.requests, strict=True):
            require(
                type(map_result) is ValidatedStageResult
                and map_result.request == expected
                and expected.stage == "map"
                and not expected.children
                and len(expected.chunks) == 1
            )
        require(validate_stage_output(r, child.output_json) == child)
    # Replays full descendant provenance. No output is invented from history DTOs.
    from .stage_validation import render_stage_input

    render_stage_input(request)


def identity_hash(identity):
    import json

    return fingerprint(
        json.dumps(
            [identity.provider, identity.scope, identity.external_key],
            ensure_ascii=False,
            separators=(",", ":"),
        )
    )
