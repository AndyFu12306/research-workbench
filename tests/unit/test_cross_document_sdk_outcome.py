"""Ambiguous SDK codes are not receipt evidence; all HTTP is MockTransport."""

from decimal import Decimal

import pytest
from sqlalchemy import select
from test_stage_providers import wire  # noqa: F401

from research_workbench.analysis.chunking import ChunkingConfig
from research_workbench.analysis.cross_document_pipeline import CrossDocumentError
from research_workbench.analysis.document_pipeline import Pricing
from research_workbench.analysis.errors import AnalysisProviderFailure
from research_workbench.application.cross_document_analysis import CrossDocumentAnalysisService
from research_workbench.integrations.ai import stages as stage_adapters
from research_workbench.persistence.cross_document_models import cross_stages
from research_workbench.persistence.document_repository import DocumentAnalysisRepository

STAGE_CALLS = [("map", 1), ("document_reduce", 3), ("cross_document_reduce", 10)]
PRICE = Pricing("USD", "synthetic-v1", Decimal("2"), Decimal("8"))


def history_stages(s, run):
    documents = DocumentAnalysisRepository(s.db)
    rows = [
        row
        for member in s.repo.get_members(run.id)
        for row in documents.get_stages(member.document_execution_id)
    ]
    with s.db._engine.connect() as c:
        rows.append(
            c.execute(select(cross_stages).where(cross_stages.c.run_id == run.id)).mappings().one()
        )
    return rows


def configure_sdk_fault(wire_state, monkeypatch, stage, fault):
    provider = wire_state["instance"]
    original_generate = provider.generate_stage
    original_create = provider._client.responses.create
    original_normalize = stage_adapters.normalize_response
    current = {"stage": None}
    attempts = []

    def create(**kwargs):
        if current["stage"] == stage and fault == "create_exception":
            raise RuntimeError("PRIVATE_SYNTHETIC_SDK_EXCEPTION")
        return original_create(**kwargs)

    def normalize(response):
        if current["stage"] == stage and fault == "normalizer_exception":
            raise RuntimeError("PRIVATE_SYNTHETIC_NORMALIZER_EXCEPTION")
        return original_normalize(response)

    def generate(request):
        current["stage"] = request.stage
        attempts.append(request.stage)
        citations = (
            [request.chunks[0].chunk_id]
            if request.stage == "map"
            else [f.finding_id for child in request.children for f in child.findings][:1]
        )
        from stage_fakes import output

        if request.stage == stage and fault == "illegal_reference":
            citations = ["f" * 64]
        wire_state["body"]["output"][0]["content"][0]["text"] = output(citations)
        if request.stage == stage:
            if fault == "invalid_shape":
                wire_state["body"]["output"][0]["role"] = "user"
            elif fault == "refusal":
                wire_state["body"]["output"][0]["content"] = [
                    {"type": "refusal", "refusal": "Synthetic refusal"}
                ]
            elif fault == "incomplete":
                wire_state["body"]["status"] = "incomplete"
                wire_state["body"]["incomplete_details"] = {"reason": "max_output_tokens"}
        return original_generate(request)

    monkeypatch.setattr(provider._client.responses, "create", create)
    monkeypatch.setattr(stage_adapters, "normalize_response", normalize)
    monkeypatch.setattr(provider, "generate_stage", generate)
    return attempts


@pytest.mark.parametrize("stage,at", STAGE_CALLS)
@pytest.mark.parametrize("fault", ["create_exception", "invalid_shape", "normalizer_exception"])
def test_ambiguous_sdk_failure_is_interrupted_unknown(
    cross_setup,
    wire,  # noqa: F811
    monkeypatch,
    stage,
    at,
    fault,
):
    s = cross_setup
    attempts = configure_sdk_fault(wire, monkeypatch, stage, fault)
    service = CrossDocumentAnalysisService(
        s.db, s.reader, wire["instance"], chunking=ChunkingConfig(100, 0), pricing=PRICE
    )
    with pytest.raises(CrossDocumentError) as caught:
        service.run_analysis(s.question.id, tuple(x.id for x in s.sources))
    assert caught.value.code == "interrupted"
    assert "PRIVATE_SYNTHETIC" not in str(caught.value)
    run = s.repo.list_by_question(s.question.id)[0]
    rows = history_stages(s, run)
    assert run.status == "interrupted" and len(attempts) == at
    assert len(wire["calls"]) == (at - 1 if fault == "create_exception" else at)
    assert all(row.status == "completed" for row in rows[: at - 1])
    failed = rows[at - 1]
    assert failed.stage == stage and failed.status == "interrupted"
    assert failed.call_outcome == "unknown"
    assert failed.input_tokens is None and failed.output_tokens is None
    assert failed.usage_cost is None and failed.response_id is None
    assert all(row.status == "not_started" for row in rows[at:])
    costs = s.repo.get_costs(run.id)
    assert costs.usage_cost is None and costs.unknown_calls == 1
    assert costs.known_usage_cost == Decimal("0.000198") * (at - 1)


@pytest.mark.parametrize("stage,at", STAGE_CALLS)
@pytest.mark.parametrize("fault", ["illegal_reference", "refusal", "incomplete"])
def test_reliable_receipt_failures_keep_response_received(
    cross_setup,
    wire,  # noqa: F811
    monkeypatch,
    stage,
    at,
    fault,
):
    s = cross_setup
    attempts = configure_sdk_fault(wire, monkeypatch, stage, fault)
    service = CrossDocumentAnalysisService(
        s.db, s.reader, wire["instance"], chunking=ChunkingConfig(100, 0), pricing=PRICE
    )
    with pytest.raises(CrossDocumentError):
        service.run_analysis(s.question.id, tuple(x.id for x in s.sources))
    run = s.repo.list_by_question(s.question.id)[0]
    rows = history_stages(s, run)
    assert run.status == "failed" and len(attempts) == at and len(wire["calls"]) == at
    failed = rows[at - 1]
    assert failed.status == "failed" and failed.call_outcome == "response_received"
    if fault == "illegal_reference":
        assert (failed.input_tokens, failed.output_tokens) == (31, 17)
        assert failed.usage_cost == "0.000198"
    else:
        assert failed.input_tokens is None and failed.output_tokens is None
        assert failed.usage_cost is None
    assert all(row.status == "completed" for row in rows[: at - 1])
    assert all(row.status == "not_started" for row in rows[at:])


@pytest.mark.parametrize("stage,at", STAGE_CALLS)
def test_sdk_code_after_explicit_return_keeps_receipt_evidence(cross_setup, monkeypatch, stage, at):
    s = cross_setup
    service = CrossDocumentAnalysisService(
        s.db, s.reader, s.provider, chunking=ChunkingConfig(100, 0), pricing=PRICE
    )
    name = "complete_cross" if stage == "cross_document_reduce" else "complete_document_stage"
    original = getattr(service._runs, name)

    def fail_after_return(*args):
        result = args[2] if stage == "cross_document_reduce" else args[4]
        if result.request.stage == stage:
            raise AnalysisProviderFailure("provider_sdk_incompatible")
        return original(*args)

    monkeypatch.setattr(service._runs, name, fail_after_return)
    with pytest.raises(CrossDocumentError) as caught:
        service.run_analysis(s.question.id, tuple(x.id for x in s.sources))
    run = s.repo.list_by_question(s.question.id)[0]
    failed = history_stages(s, run)[at - 1]
    assert caught.value.code == "invalid_output" and run.status == "failed"
    assert failed.call_outcome == "response_received" and failed.status == "failed"
    assert (failed.input_tokens, failed.output_tokens) == (100, 40)
    assert failed.usage_cost == "0.00052"
