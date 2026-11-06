"""Incomplete SDK responses never become usable output or resume a failed execution."""

import json
from dataclasses import replace
from decimal import Decimal

import pytest
from stage_fakes import map_request, output
from test_stage_providers import wire  # noqa: F401

from research_workbench.analysis.chunking import ChunkingConfig
from research_workbench.analysis.cross_document_pipeline import CrossBudget, CrossDocumentError
from research_workbench.analysis.document_pipeline import Pricing
from research_workbench.analysis.errors import AnalysisProviderIncomplete
from research_workbench.analysis.stage_contracts import (
    StageBudget,
    StageBudgetError,
    StageCapabilityError,
    StageError,
)
from research_workbench.application.cross_document_analysis import CrossDocumentAnalysisService
from research_workbench.integrations.ai.stages import prepare_stage_call


@pytest.mark.parametrize(
    "reason", ["max_output_tokens", "content_filter", None, "PRIVATE_REASON", ["PRIVATE_REASON"]]
)
def test_second_map_incomplete_stops_seventeen_call_plan(cross_setup, wire, reason, caplog):  # noqa: F811
    s = cross_setup
    for key, char in (("FAKE0001", "a"), ("FAKE0002", "b")):
        s.reader.texts[key] = char * 700
    provider = wire["instance"]
    original = provider.generate_stage
    attempts, exceptions = [], []
    fail = True

    def generate(request):
        attempts.append(request.stage)
        wire["body"].update(
            status="completed",
            incomplete_details=None,
            usage={"input_tokens": 2428, "output_tokens": 2562, "total_tokens": 4990},
        )
        labels = (
            [request.chunks[0].chunk_id]
            if request.stage == "map"
            else [f.finding_id for child in request.children for f in child.findings][:1]
        )
        wire["body"]["output"][0]["content"][0]["text"] = output(labels)
        if fail and len(attempts) == 2:
            wire["body"].update(
                status="incomplete",
                usage=None,
                incomplete_details={"reason": reason} if reason is not None else None,
            )
            # Even syntactically valid partial output must never be accepted.
            partial = json.loads(output(labels))
            partial["findings"][0]["text"] = "PRIVATE_PARTIAL"
            wire["body"]["output"][0]["content"][0]["text"] = json.dumps(partial)
        try:
            return original(request)
        except AnalysisProviderIncomplete as exc:
            exceptions.append(exc)
            raise

    provider.generate_stage = generate
    service = CrossDocumentAnalysisService(
        s.db,
        s.reader,
        provider,
        chunking=ChunkingConfig(100, 0),
        pricing=Pricing("USD", "synthetic-v1", Decimal("2"), Decimal("8")),
    )
    selected = tuple(x.id for x in s.sources[:2])
    prepared = service.prepare_analysis(s.question.id, selected)
    assert prepared.allocation.calls == 17 and wire["calls"] == []
    with pytest.raises(CrossDocumentError) as caught:
        service.execute_prepared(prepared)
    assert caught.value.code == "provider_incomplete"
    assert len(wire["calls"]) == 2 and attempts == ["map", "map"]
    run = s.repo.list_by_question(s.question.id)[0]
    calls = s.repo.get_call_usage(run.id)
    assert run.status == "failed" and run.error_code == "provider_incomplete"
    assert (calls[0].input_tokens, calls[0].output_tokens) == (2428, 2562)
    assert calls[0].status == "completed"
    assert calls[1].status == "failed" and calls[1].error_code == "provider_incomplete"
    assert calls[1].call_outcome == "response_received"
    assert calls[1].input_tokens is None and calls[1].output_tokens is None
    assert len(calls[2:]) == 15 and all(c.call_outcome == "not_started" for c in calls[2:])
    costs = s.repo.get_costs(run.id)
    assert costs.usage_cost is None and costs.known_usage_cost == Decimal("0.025352")
    assert costs.not_started_calls == 15
    assert exceptions[0].reason == (
        reason
        if type(reason) is str and reason in ("max_output_tokens", "content_filter")
        else None
    )
    for private in ("PRIVATE_PARTIAL", "PRIVATE_REASON", "synthetic-stage-key"):
        assert private not in str(exceptions[0]) + repr(exceptions[0]) + caplog.text
        assert private.encode() not in s.db.path.read_bytes()
    with pytest.raises(CrossDocumentError):
        s.repo.get_history(run.id)
    # Explicit new execution is allowed; the original failure and usage are immutable.
    fail = False
    new = service.run_analysis(s.question.id, selected)
    assert new.id != run.id and new.status == "completed"
    assert s.repo.get(run.id) == run and s.repo.get_call_usage(run.id) == calls


def test_configured_output_reaches_sdk_and_both_budgets(cross_setup, wire):  # noqa: F811
    s = cross_setup
    budget = StageBudget(output_tokens=8192, context_tokens=65536)
    request = replace(map_request(), budget=budget)
    if wire["provider"] == "openai":
        with pytest.raises(StageCapabilityError):
            wire["instance"].generate_stage(request)
        assert wire["calls"] == []
        return
    payload, estimate = prepare_stage_call(
        request, model=wire["instance"].model, provider=wire["provider"]
    )
    wire["instance"].generate_stage(request)
    assert wire["calls"][-1] == payload and payload["max_output_tokens"] == 8192
    assert estimate.context_tokens == estimate.input_tokens + 8192 + 1024
    service = CrossDocumentAnalysisService(
        s.db,
        s.reader,
        wire["instance"],
        stage_budget=budget,
        chunking=ChunkingConfig(100, 0),
        pricing=Pricing("USD", "synthetic-v1", Decimal("2"), Decimal("8")),
    )
    selected = tuple(x.id for x in s.sources[:2])
    prepared = service.prepare_analysis(s.question.id, selected)
    assert all(r.budget.output_tokens == 8192 for p in prepared.documents for r in p.requests)
    assert prepared.allocation.cross.context_tokens == 24000 + 8192 + 1024
    assert prepared.allocation.cost is not None
    # Same output request, insufficient context: final wire preflight must still reject it.
    with pytest.raises(StageBudgetError):
        prepare_stage_call(
            replace(request, budget=replace(budget, context_tokens=estimate.context_tokens - 1)),
            model=wire["instance"].model,
            provider=wire["provider"],
        )
    with pytest.raises(StageError):
        prepare_stage_call(
            replace(request, budget=replace(budget, output_tokens=8193)),
            model=wire["instance"].model,
            provider=wire["provider"],
        )
    # Higher output cannot escape the unchanged overall token/cost ceilings.
    for overall in (
        CrossBudget(max_estimated_tokens=prepared.allocation.context_tokens - 1),
        CrossBudget(max_estimated_cost=prepared.allocation.cost - Decimal("0.000001")),
    ):
        limited = CrossDocumentAnalysisService(
            s.db,
            s.reader,
            wire["instance"],
            stage_budget=budget,
            chunking=ChunkingConfig(100, 0),
            cross_budget=overall,
            pricing=Pricing("USD", "synthetic-v1", Decimal("2"), Decimal("8")),
        )
        with pytest.raises(CrossDocumentError, match="allowance"):
            limited.prepare_analysis(s.question.id, selected)
    assert len(wire["calls"]) == 1
