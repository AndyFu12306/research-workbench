"""5 + 9 Maps, large Reduce output, independent SDK limits and immutable bindings."""

import json
from dataclasses import asdict, replace
from decimal import Decimal

import pytest
from sqlalchemy import update
from test_stage_providers import wire  # noqa: F401

from research_workbench.analysis.chunking import ChunkingConfig
from research_workbench.analysis.cross_document_pipeline import CrossBudget, CrossDocumentError
from research_workbench.analysis.document_pipeline import Pricing
from research_workbench.analysis.planning import PlanningBudget
from research_workbench.application.cross_document_analysis import CrossDocumentAnalysisService
from research_workbench.application.document_analysis import DocumentAnalysisService
from research_workbench.cli import configured_stage_budget, preview
from research_workbench.persistence import Database
from research_workbench.persistence.cross_document_models import runs
from research_workbench.persistence.cross_document_repository import (
    CrossDocumentRepository,
    plan_config,
)
from research_workbench.persistence.document_repository import DocumentAnalysisRepository


@pytest.fixture
def large_stages(cross_setup, wire):  # noqa: F811
    s = cross_setup
    s.reader.texts.update(FAKE0001="a" * 500, FAKE0002="b" * 900)
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
        is_map = request.stage == "map"
        required = 4096 if is_map else 24576
        body = wire["body"]
        body.update(
            status="completed",
            incomplete_details=None,
            usage={"input_tokens": 100, "output_tokens": required, "total_tokens": 100 + required},
        )
        raw = json.dumps(
            {
                "status": "findings",
                "findings": [
                    {
                        "text": "研究" * (249 if is_map else 749) + f"{i:02}",
                        "kind": "source_summary",
                        "category": "finding",
                        "citations": labels,
                    }
                    for i in range(6 if is_map else 12)
                ],
            },
            ensure_ascii=False,
        )
        body["output"][0]["content"][0]["text"] = raw
        # Simulated service cap, not a claim that character/byte count is a tokenizer.
        if request.budget.output_tokens < required:
            body.update(
                status="incomplete", incomplete_details={"reason": "max_output_tokens"}, usage=None
            )
        return original(request)

    provider.generate_stage = generate
    selected = tuple(source.id for source in s.sources[:2])
    map_budget = configured_stage_budget(8192, 1_000_000)
    doc_budget = replace(map_budget, output_tokens=32768)
    cross_budget = replace(map_budget, output_tokens=49152)
    price = Pricing("CNY", "synthetic-v1", Decimal("1"), Decimal("2"))

    def make(doc=doc_budget, cross=cross_budget, overall=None):
        return CrossDocumentAnalysisService(
            s.db,
            s.reader,
            provider,
            stage_budget=map_budget,
            document_reduce_budget=doc,
            cross_reduce_budget=cross,
            chunking=ChunkingConfig(100, 0),
            planning_budget=PlanningBudget(
                context_tokens=map_budget.context_tokens,
                max_input_tokens=map_budget.max_input_tokens,
                reserved_output_tokens=map_budget.output_tokens,
            ),
            pricing=price,
            cross_budget=overall
            or CrossBudget(max_estimated_tokens=8_000_000, max_estimated_cost=Decimal("10")),
        )

    return s, wire, selected, requests, map_budget, doc_budget, cross_budget, make


@pytest.mark.parametrize("wire", ["deepseek"], indirect=True)
@pytest.mark.parametrize("failure", [None, "document_reduce", "cross_document_reduce"])
def test_independent_limits_with_five_and_nine_maps(large_stages, failure, capsys):
    s, transport, selected, requests, map_b, doc_b, cross_b, make = large_stages
    chosen_doc = replace(doc_b, output_tokens=8192) if failure == "document_reduce" else doc_b
    chosen_cross = (
        replace(cross_b, output_tokens=8192) if failure == "cross_document_reduce" else cross_b
    )
    service = make(chosen_doc, chosen_cross)
    prepared = service.prepare_analysis(s.question.id, selected)
    assert [len(p.requests) for p in prepared.documents] == [5, 9]
    assert all(p.plan.budget.reserved_output_tokens == 8192 for p in prepared.documents)
    assert prepared.allocation.calls == 17 and transport["calls"] == []
    for group, plan in zip(prepared.allocation.documents, prepared.documents, strict=True):
        assert group[-1].context_tokens == 512000 + chosen_doc.output_tokens + 1024
        assert group[-1].cost == Decimal(512000 + 2 * chosen_doc.output_tokens) / 1_000_000
        for allowance, estimate in zip(group[:-1], plan.estimates, strict=True):
            assert allowance.context_tokens == estimate.input_tokens + 8192 + 1024
    assert prepared.allocation.cross.context_tokens == 512000 + chosen_cross.output_tokens + 1024
    assert (
        prepared.allocation.cross.cost
        == Decimal(512000 + 2 * chosen_cross.output_tokens) / 1_000_000
    )
    assert (
        prepared.allocation.context_tokens
        == sum(a.context_tokens for g in prepared.allocation.documents for a in g)
        + prepared.allocation.cross.context_tokens
    )
    preview(prepared)
    shown = capsys.readouterr().out
    assert "map: 输出=8192 tokens；调用=14" in shown
    assert f"document_reduce: 输出={chosen_doc.output_tokens} tokens；调用=2" in shown
    assert f"cross_document_reduce: 输出={chosen_cross.output_tokens} tokens；调用=1" in shown
    if failure:
        with pytest.raises(CrossDocumentError) as error:
            service.execute_prepared(prepared)
        assert error.value.code == "provider_incomplete"
    else:
        service.execute_prepared(prepared)
    run = s.repo.list_by_question(s.question.id)[0]
    calls = s.repo.get_call_usage(run.id)
    assert all(
        call["max_output_tokens"] == r.budget.output_tokens
        for call, r in zip(transport["calls"], requests, strict=True)
    )
    config = json.loads(run.config_json)
    assert config["stage_budget"] == asdict(map_b)
    assert config["reduce_stage_budgets"]["document_reduce"] == asdict(chosen_doc)
    assert config["reduce_stage_budgets"]["cross_document_reduce"] == asdict(chosen_cross)
    for member in s.repo.get_members(run.id):
        child = DocumentAnalysisRepository(s.db).get(member.document_execution_id)
        assert json.loads(child.config_json).get("reduce_stage_budget", asdict(map_b)) == asdict(
            chosen_doc
        )
    if failure:
        at = 6 if failure == "document_reduce" else 17
        assert len(transport["calls"]) == at
        assert run.status == "failed" and run.error_code == "provider_incomplete"
        assert calls[at - 1].call_outcome == "response_received"
        assert calls[at - 1].input_tokens is None and calls[at - 1].output_tokens is None
        assert all(c.call_outcome == "not_started" for c in calls[at:])
        assert s.repo.get_costs(run.id).usage_cost is None
        # New explicit execution with sufficient independent limits leaves the failure untouched.
        rerun = make().run_analysis(s.question.id, selected)
        assert rerun.status == "completed" and rerun.id != run.id
        assert s.repo.get(run.id) == run and s.repo.get_call_usage(run.id) == calls
    else:
        assert run.status == "completed" and len(transport["calls"]) == 17
        assert [sum(len(c.findings) for c in r.children) for r in requests if r.stage != "map"] == [
            30,
            54,
            24,
        ]
        assert len(json.loads(s.repo.get_history(run.id).result_json)["findings"]) == 12
        assert s.repo.get_costs(run.id).usage_cost is not None


@pytest.mark.parametrize("wire", ["deepseek"], indirect=True)
def test_split_limits_obey_context_overall_cost_and_registration_binding(large_stages):
    s, transport, selected, requests, map_b, doc_b, cross_b, make = large_stages
    base = make().prepare_analysis(s.question.id, selected)
    for service in (
        make(replace(doc_b, context_tokens=530000)),
        make(cross=replace(cross_b, context_tokens=540000)),
        make(overall=CrossBudget(max_estimated_tokens=base.allocation.context_tokens - 1)),
        make(
            overall=CrossBudget(
                max_estimated_tokens=8_000_000,
                max_estimated_cost=base.allocation.cost - Decimal("0.000001"),
            )
        ),
    ):
        with pytest.raises(CrossDocumentError):
            service.prepare_analysis(s.question.id, selected)
    assert transport["calls"] == [] and s.repo.list_by_question(s.question.id) == ()
    with pytest.raises(CrossDocumentError):
        make().execute_prepared(
            replace(base, cross_reduce_budget=replace(cross_b, output_tokens=8192))
        )
    assert s.repo.list_by_question(s.question.id) == ()
    handle = s.repo.register(base)
    # Tamper a child Reduce budget in the prepared plan: sealed parent config must reject it.
    altered = replace(base, document_reduce_budget=replace(doc_b, output_tokens=8192))
    with pytest.raises(CrossDocumentError):
        s.repo.start_document_stage(
            handle, altered, 1, 1, base.documents[0].requests[0], base.documents[0].estimates[0]
        )
    # Changing persisted configuration without its plan fingerprint must fail, too.
    config = plan_config(base)
    config["reduce_stage_budgets"]["cross_document_reduce"]["output_tokens"] = 8192
    with s.db._session() as session:
        session.execute(
            update(runs).where(runs.c.id == handle.run_id).values(config_json=json.dumps(config))
        )
    with pytest.raises(CrossDocumentError):
        s.repo.start_document_stage(
            handle, base, 1, 1, base.documents[0].requests[0], base.documents[0].estimates[0]
        )
    assert transport["calls"] == []


@pytest.mark.parametrize("wire", ["deepseek"], indirect=True)
def test_wrong_runtime_reduce_budget_is_rejected_before_sending(large_stages):
    s, transport, selected, requests, map_b, doc_b, cross_b, make = large_stages
    service = make()
    prepared = service.prepare_analysis(s.question.id, selected)
    provider = service._provider
    original = provider.generate_stage

    def changed_after_last_map(request):
        value = original(request)
        if len(requests) == 5:
            service._documents._reduce_stage_budget = replace(doc_b, context_tokens=999999)
        return value

    provider.generate_stage = changed_after_last_map
    with pytest.raises(CrossDocumentError):
        service.execute_prepared(prepared)
    assert len(transport["calls"]) == 5
    run = s.repo.list_by_question(s.question.id)[0]
    assert run.status == "failed"
    calls = s.repo.get_call_usage(run.id)
    assert all(c.status == "completed" for c in calls[:5])
    assert calls[5].call_outcome == "not_started"


def test_legacy_uniform_budget_history_remains_readable(cross_setup):
    s = cross_setup
    service = CrossDocumentAnalysisService(s.db, s.reader, s.provider)
    run = service.run_analysis(s.question.id, tuple(x.id for x in s.sources[:2]))
    config = json.loads(run.config_json)
    assert "reduce_stage_budgets" not in config
    for document in config["documents"]:
        assert "reduce_stage_budget" not in document["execution_config"]
    history = s.repo.get_history(run.id)
    with Database(s.db.path) as reopened:
        repo = CrossDocumentRepository(reopened)
        assert repo.get_history(run.id) == history
        assert repo.get(run.id) == run


@pytest.mark.parametrize("wire", ["deepseek"], indirect=True)
def test_standalone_document_planner_and_reduce_reserve_use_their_own_limits(large_stages):
    s, transport, selected, requests, map_b, doc_b, cross_b, make = large_stages
    run = DocumentAnalysisService(
        s.db,
        s.reader,
        make()._provider,
        chunking=ChunkingConfig(100, 0),
        stage_budget=map_b,
        reduce_stage_budget=doc_b,
    ).run_analysis(s.question.id, selected[0])
    assert run.status == "completed" and len(transport["calls"]) == 6
    assert [call["max_output_tokens"] for call in transport["calls"]] == [8192] * 5 + [32768]
    config = json.loads(run.config_json)
    assert config["planning_budget"]["reserved_output_tokens"] == 8192
    assert config["reduce_stage_budget"]["output_tokens"] == 32768
    assert config["execution_budget"]["reduce_reserved_tokens"] == 512000 + 32768 + 1024
