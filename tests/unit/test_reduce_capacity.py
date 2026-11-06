"""Ordinary five-Map documents with long findings: complete inputs, MockTransport only."""

import json
from dataclasses import replace
from decimal import Decimal

import pytest
from stage_fakes import map_request
from test_stage_providers import wire  # noqa: F401

from research_workbench.analysis.chunking import ChunkingConfig
from research_workbench.analysis.cross_document_pipeline import CrossBudget, CrossDocumentError
from research_workbench.analysis.document_pipeline import Pricing
from research_workbench.analysis.stage_contracts import StageBudget, StageCapabilityError
from research_workbench.application.cross_document_analysis import CrossDocumentAnalysisService
from research_workbench.cli import ReportingProvider, configured_stage_budget


@pytest.mark.parametrize("wire", ["deepseek"], indirect=True)
def test_complete_long_reduces_without_dropping_findings(cross_setup, wire, capsys):  # noqa: F811
    s = cross_setup
    s.reader.texts.update(FAKE0001="a" * 500, FAKE0002="b" * 500)
    provider = wire["instance"]
    provider.model = "deepseek-flash"
    original = provider.generate_stage
    requests = []

    def generate(request):
        requests.append(request)
        labels = (
            [request.chunks[0].chunk_id]
            if request.stage == "map"
            else [child.findings[0].finding_id for child in request.children]
        )
        raw = json.dumps(
            {
                "status": "findings",
                "findings": [
                    {
                        "text": "研究" * 249 + f"{i:02}",
                        "kind": "source_summary",
                        "category": "finding",
                        "citations": labels,
                    }
                    for i in range(12)
                ],
            },
            ensure_ascii=False,
        )
        wire["body"]["output"][0]["content"][0]["text"] = raw
        return original(request)

    provider.generate_stage = generate
    pricing = Pricing("CNY", "synthetic-v1", Decimal("1"), Decimal("2"))
    selected = tuple(x.id for x in s.sources[:2])

    def service(stage, overall):
        return CrossDocumentAnalysisService(
            s.db,
            s.reader,
            ReportingProvider(provider),
            stage_budget=stage,
            chunking=ChunkingConfig(100, 0),
            cross_budget=overall,
            pricing=pricing,
        )

    old = service(StageBudget(output_tokens=8192, context_tokens=65536), CrossBudget())
    with pytest.raises(CrossDocumentError) as error:
        old.run_analysis(s.question.id, selected)
    assert error.value.code == "budget_exceeded"
    assert len(wire["calls"]) == 5
    failed = s.repo.list_by_question(s.question.id)[0]
    old_calls = s.repo.get_call_usage(failed.id)
    assert all(c.status == "completed" for c in old_calls[:5])
    assert old_calls[5].call_outcome == "not_started"
    assert "不是服务商真实 token 超限判定" in capsys.readouterr().out

    requests.clear()
    stage = configured_stage_budget(8192, 1_000_000)
    overall = CrossBudget(max_estimated_tokens=8_000_000, max_estimated_cost=Decimal("10"))
    new = service(stage, overall)
    plan = new.prepare_analysis(s.question.id, selected)
    assert plan.allocation.calls == 13
    assert plan.allocation.cross.input_tokens == 512000
    assert plan.allocation.context_tokens < overall.max_estimated_tokens
    assert plan.allocation.cost < overall.max_estimated_cost
    run = new.execute_prepared(plan)
    assert run.status == "completed" and len(requests) == 13
    reduces = [r for r in requests if r.stage != "map"]
    assert [sum(len(child.findings) for child in r.children) for r in reduces] == [60, 60, 24]
    # Real final wire estimates exceed the old cap for BOTH Reduce levels.
    calls = s.repo.get_call_usage(run.id)
    for index in (5, 11, 12):
        payload = wire["calls"][5 + index]
        units = len(json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()) + 256
        assert 24000 < units <= 512000
        assert calls[index].status == "completed"
    assert len(json.loads(s.repo.get_history(run.id).result_json)["findings"]) == 12
    assert s.repo.get(failed.id) == failed and s.repo.get_call_usage(failed.id) == old_calls
    # Same ample capacity still obeys the explicitly approved cost ceiling, before any call.
    limited = service(stage, replace(overall, max_estimated_cost=Decimal("0.000001")))
    count = len(wire["calls"])
    with pytest.raises(CrossDocumentError, match="allowance"):
        limited.prepare_analysis(s.question.id, selected)
    assert len(wire["calls"]) == count


@pytest.mark.parametrize("wire", ["deepseek"], indirect=True)
def test_known_model_capacity_and_actual_context_rejection(cross_setup, wire, capsys):  # noqa: F811
    s = cross_setup
    provider = wire["instance"]
    provider.model = "deepseek-flash"
    request = replace(
        map_request(), budget=StageBudget(output_tokens=32768, context_tokens=1_000_000)
    )
    provider.generate_stage(request)
    assert wire["calls"][0]["max_output_tokens"] == 32768
    for budget in (
        replace(request.budget, context_tokens=1_000_001),
        replace(request.budget, output_tokens=384001),
    ):
        with pytest.raises((StageCapabilityError, ValueError)):
            provider.generate_stage(replace(request, budget=budget))
    assert len(wire["calls"]) == 1
    wire.update(
        status=400, body={"error": {"code": "context_length_exceeded", "message": "PRIVATE_RAW"}}
    )
    svc = CrossDocumentAnalysisService(s.db, s.reader, ReportingProvider(provider))
    with pytest.raises(CrossDocumentError) as error:
        svc.run_analysis(s.question.id, tuple(x.id for x in s.sources[:2]))
    assert error.value.code == "provider_context_length"
    run = s.repo.list_by_question(s.question.id)[0]
    assert run.error_code == "provider_context_length" and run.status == "failed"
    assert s.repo.get_call_usage(run.id)[0].call_outcome == "response_received"
    assert len(wire["calls"]) == 2
    assert "PRIVATE_RAW" not in capsys.readouterr().out + str(error.value)
