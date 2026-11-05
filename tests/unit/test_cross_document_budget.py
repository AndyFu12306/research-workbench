"""No borrowing, drift, preflight atomicity or invented usage costs."""

from dataclasses import replace
from decimal import Decimal, localcontext

import pytest
from sqlalchemy import select
from test_cross_document_analysis import execute
from test_cross_document_gates import service

from research_workbench.analysis.cross_document_pipeline import CrossBudget, CrossDocumentError
from research_workbench.analysis.document_pipeline import Pricing
from research_workbench.persistence.cross_document_models import cross_stages
from research_workbench.persistence.document_repository import DocumentAnalysisRepository

PRICE = Pricing("USD", "synthetic-v1", Decimal("2"), Decimal("8"))


@pytest.mark.parametrize(
    "stage,expected_calls", [("document_reduce", 2), ("cross_document_reduce", 9)]
)
def test_dynamic_reduce_over_one_unit_never_borrows_global_balance(
    cross_setup, stage, expected_calls
):
    s = cross_setup
    original = s.provider.estimate_stage

    def estimate(request):
        result = original(request)
        if request.stage == stage:
            return replace(result, input_tokens=24001, context_tokens=29121)
        return result

    s.provider.estimate_stage = estimate
    with pytest.raises(CrossDocumentError) as e:
        execute(s)
    assert e.value.code == "budget_exceeded"
    assert len(s.provider.calls) == expected_calls
    run = s.repo.list_by_question(s.question.id)[0]
    repo = DocumentAnalysisRepository(s.db)
    children = [repo.get(b.document_execution_id) for b in s.repo.get_members(run.id)]
    assert run.status == "failed"
    if stage == "cross_document_reduce":
        assert all(c.status == "completed" for c in children)
    else:
        assert children[0].error_code == "budget_exceeded"
        assert repo.get_stages(children[0].id)[-1].status == "not_started"
    with s.db._engine.connect() as c:
        assert c.execute(select(cross_stages.c.status)).scalar_one() == "not_started"


@pytest.mark.parametrize("drift", ["input", "version", "provider", "model", "capability"])
def test_per_call_reestimate_and_configuration_drift_fail_before_next_call(cross_setup, drift):
    s = cross_setup
    original = s.provider.estimate_stage

    def estimate(request):
        e = original(request)
        if s.provider.calls and drift == "input":
            return replace(e, input_tokens=e.input_tokens + 1, context_tokens=e.context_tokens + 1)
        if s.provider.calls and drift == "version":
            return replace(e, estimator_version="drift-v2")
        return e

    def change(request):
        if len(s.provider.calls) == 1:
            if drift == "provider":
                s.provider.provider = "changed"
            if drift == "model":
                s.provider.model = "changed"
            if drift == "capability":
                s.provider.capabilities = frozenset()

    s.provider.callback = change
    s.provider.estimate_stage = estimate
    with pytest.raises(CrossDocumentError):
        execute(s)
    assert len(s.provider.calls) == 1


@pytest.mark.parametrize(
    "budget",
    [
        CrossBudget(max_calls=9),
        CrossBudget(max_estimated_tokens=1),
        CrossBudget(max_estimated_cost=Decimal("0")),
    ],
)
def test_global_preflight_failure_has_no_history_or_calls(cross_setup, budget):
    s = cross_setup
    with pytest.raises(CrossDocumentError):
        execute(s, cross_budget=budget, pricing=PRICE)
    assert s.provider.calls == [] and s.repo.list_by_question(s.question.id) == ()


@pytest.mark.parametrize("usage", [(100, 40), (None, 40), (100, None), (None, None)])
def test_decimal_costs_and_missing_usage_remain_unknown(cross_setup, usage):
    s = cross_setup
    s.provider.usage = usage
    with localcontext() as context:
        context.prec = 2
        run = execute(s, pricing=PRICE)
    repo = DocumentAnalysisRepository(s.db)
    for b in s.repo.get_members(run.id):
        for stage in repo.get_stages(b.document_execution_id):
            assert stage.usage_cost == ("0.00052" if usage == (100, 40) else None)
    with s.db._engine.connect() as c:
        value = c.execute(select(cross_stages.c.usage_cost)).scalar_one()
        assert value == ("0.00052" if usage == (100, 40) else None)


def test_missing_price_with_explicit_cost_limit_rejected(cross_setup):
    s = cross_setup
    with pytest.raises(CrossDocumentError):
        execute(s, cross_budget=CrossBudget(max_estimated_cost=Decimal("1")))
    assert s.provider.calls == []


def test_capacity_over_100_chunks_fails_before_registration(cross_setup):
    s = cross_setup
    s.reader.texts["FAKE0001"] = "a" * 9800
    s.reader.texts["FAKE0002"] = "b" * 200
    s.reader.texts["FAKE0003"] = "c"
    with pytest.raises(CrossDocumentError):
        execute(s, cross_budget=CrossBudget(max_estimated_tokens=10_000_000))
    assert s.provider.calls == [] and s.repo.list_by_question(s.question.id) == ()


def test_no_execution_id_resume_and_duplicate_finalize_is_conflict(cross_setup, monkeypatch):
    s = cross_setup
    svc = service(s)
    original = svc._runs.complete_cross

    def double(*args):
        run = original(*args)
        with pytest.raises(CrossDocumentError) as e:
            original(*args)
        assert e.value.code == "conflict"
        return run

    monkeypatch.setattr(svc._runs, "complete_cross", double)
    run = svc.run_analysis(s.question.id, tuple(x.id for x in s.sources))
    assert s.repo.get_history(run.id).status == "findings"
    assert not hasattr(svc, "resume")


@pytest.mark.parametrize("when", ["preflight", "runtime"])
def test_provider_model_drift_during_estimate_never_reaches_provider(cross_setup, when):
    s = cross_setup
    original = s.provider.estimate_stage
    count = 0

    def drift(request):
        nonlocal count
        count += 1
        e = original(request)
        if (when == "preflight" and count == 1) or (when == "runtime" and count == 7):
            s.provider.model = "changed-during-estimation"
        return e

    s.provider.estimate_stage = drift
    with pytest.raises(CrossDocumentError):
        execute(s)
    assert s.provider.calls == []
    if when == "preflight":
        assert s.repo.list_by_question(s.question.id) == ()


def test_whole_usage_unknown_is_distinct_from_known_partial_and_unattempted(cross_setup):
    s = cross_setup
    s.provider.error_at, s.provider.error = 4, TimeoutError("PRIVATE")
    with pytest.raises(CrossDocumentError):
        execute(s, pricing=PRICE)
    costs = s.repo.get_costs(s.repo.list_by_question(s.question.id)[0].id)
    assert costs.known_usage_cost == Decimal("0.00156")
    assert costs.usage_cost is None and costs.unknown_calls == 1 and costs.not_started_calls == 6
