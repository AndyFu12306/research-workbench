"""Safe validation categories and lossless checkpoint primitives; synthetic inputs only."""

import json
from dataclasses import replace
from decimal import Decimal

import pytest
from stage_fakes import map_request, output, reduce_request

from research_workbench.analysis.chunking import ChunkingConfig, SourceDocument, chunk_document
from research_workbench.analysis.contracts import ProviderResult
from research_workbench.analysis.cross_document_pipeline import CrossBudget, CrossDocumentError
from research_workbench.analysis.document_pipeline import DocumentAnalysisError, Pricing
from research_workbench.analysis.stage_contracts import StageValidationError
from research_workbench.analysis.stage_validation import validate_stage_output
from research_workbench.application.checkpoints import (
    CheckpointUnavailable,
    inspect_execution,
    replay_stage_projection,
)
from research_workbench.application.cross_document_analysis import CrossDocumentAnalysisService
from research_workbench.cli import main, show_failure
from research_workbench.persistence.document_repository import (
    DocumentAnalysisRepository,
    history_result,
)
from research_workbench.persistence.errors import PersistenceError


@pytest.mark.parametrize(
    "damage,code",
    [
        ("json", "json"),
        ("top", "structure"),
        ("fields", "finding_structure"),
        ("count", "finding_count"),
        ("kind", "finding_field"),
        ("citation", "citation_unknown"),
        ("citation_type", "citation_format"),
    ],
)
def test_output_categories_are_fixed_and_never_contain_candidate(damage, code, caplog):
    request = map_request()
    raw = output((request.chunks[0].chunk_id,))
    data = json.loads(raw)
    if damage == "json":
        raw = '{"PRIVATE_RESPONSE":'
    else:
        if damage == "top":
            data["PRIVATE_FIELD"] = "PRIVATE_RESPONSE"
        elif damage == "fields":
            data["findings"][0]["PRIVATE_FIELD"] = "PRIVATE_RESPONSE"
        elif damage == "count":
            data["findings"] *= 13
        elif damage == "kind":
            data["findings"][0]["kind"] = "PRIVATE_RESPONSE"
        elif damage == "citation":
            data["findings"][0]["citations"] = ["PRIVATE_RESPONSE"]
        else:
            data["findings"][0]["citations"] = "PRIVATE_RESPONSE"
        raw = json.dumps(data)
    with pytest.raises(StageValidationError) as error:
        validate_stage_output(request, raw)
    assert error.value.code == "invalid_output_" + code
    assert "PRIVATE" not in str(error.value) + repr(error.value) + caplog.text
    assert "PRIVATE" not in str(error.value.counts)


def test_fourth_map_diagnostic_preserves_usage_and_stops(cross_setup, monkeypatch, capsys):
    s = cross_setup
    s.reader.texts.update(FAKE0001="a" * 500, FAKE0002="b" * 900)
    original = s.provider.generate_stage

    def invalid(request):
        value = original(request)
        if len(s.provider.calls) == 10:
            raw = output(("PRIVATE_BAD_LABEL",))
            return ProviderResult(raw, "fake-invalid", 2454, 1657)
        return value

    monkeypatch.setattr(s.provider, "generate_stage", invalid)
    service = CrossDocumentAnalysisService(
        s.db,
        s.reader,
        s.provider,
        chunking=ChunkingConfig(100, 0),
        pricing=Pricing("CNY", "synthetic-v1", Decimal("1"), Decimal("2")),
        cross_budget=CrossBudget(max_estimated_tokens=8_000_000),
    )
    with pytest.raises(CrossDocumentError) as error:
        service.run_analysis(s.question.id, tuple(x.id for x in s.sources[:2]))
    assert error.value.code == "invalid_output_citation_unknown"
    run = s.repo.list_by_question(s.question.id)[0]
    calls = s.repo.get_call_usage(run.id)
    assert calls[9].scope == "document-2" and calls[9].ordinal == 4
    assert calls[9].error_code == error.value.code
    assert (calls[9].input_tokens, calls[9].output_tokens) == (2454, 1657)
    assert calls[9].call_outcome == "response_received"
    assert len(s.provider.calls) == 10 and all(c.call_outcome == "not_started" for c in calls[10:])
    snapshot = (s.repo.get(run.id), calls)
    info = inspect_execution(s.db, run.id)
    assert (info.completed, info.failed, info.not_started) == (9, 1, 7)
    assert info.diagnostic_available and not info.raw_failed_candidate_saved
    assert not info.checkpoint_resume_available
    show_failure(s.db, run.id)
    diagnostic = capsys.readouterr().out
    assert "document-2/4 map: invalid_output_citation_unknown" in diagnostic
    assert "FAKE0001" not in diagnostic and "FAKE0002" not in diagnostic
    assert "PRIVATE_BAD_LABEL" not in diagnostic
    assert main(["--database", str(s.db.path), "inspect", str(run.id)]) == 0
    shown = capsys.readouterr().out
    assert "document-2/4 map: invalid_output_citation_unknown" in shown
    assert "PRIVATE_BAD_LABEL" not in shown
    assert b"PRIVATE_BAD_LABEL" not in s.db.path.read_bytes()
    assert (s.repo.get(run.id), s.repo.get_call_usage(run.id)) == snapshot


def test_lossless_success_checkpoint_reuse_and_changed_sources_rejected(cross_setup):
    s = cross_setup
    service = CrossDocumentAnalysisService(s.db, s.reader, s.provider)
    run = service.run_analysis(s.question.id, tuple(x.id for x in s.sources[:2]))
    member = s.repo.get_members(run.id)[0]
    repo = DocumentAnalysisRepository(s.db)
    document = repo.get(member.document_execution_id)
    stored = repo.get_stages(document.id)[0]
    request = s.provider.calls[0]
    original_calls = len(s.provider.calls)
    snapshot = (s.repo.get(run.id), repo.get_stages(document.id))
    replayed = replay_stage_projection(
        request, document, stored, provider="fake", model="synthetic"
    )
    assert history_result(replayed) == stored.result_json
    # The verified result really can be reused by the existing Reduce validator, without a request.
    reduce = reduce_request((replayed,))
    assert validate_stage_output(reduce, output((replayed.findings[0].finding_id,))).findings
    changed = SourceDocument(request.documents[0].identity, "Changed synthetic source.")
    bad_request = replace(
        request, documents=(changed,), chunks=(chunk_document(changed, request.chunking)[0],)
    )
    for current, row, model in (
        (bad_request, stored, "synthetic"),
        (replace(request, question="Changed question"), stored, "synthetic"),
        (replace(request, budget=replace(request.budget, output_tokens=1)), stored, "synthetic"),
        (request, replace(stored, prompt_version="future"), "synthetic"),
        (request, replace(stored, contract_version="future"), "synthetic"),
        (request, replace(stored, execution_id=999), "synthetic"),
        (request, stored, "changed-model"),
    ):
        with pytest.raises(CheckpointUnavailable):
            replay_stage_projection(current, document, row, provider="fake", model=model)
    # Old projection discards JSON property order used by Finding IDs: refuse lossy reconstruction.
    parsed = json.loads(replayed.output_json)
    reordered = json.dumps({"findings": parsed["findings"], "status": parsed["status"]})
    different_ids = validate_stage_output(request, reordered)
    reordered_row = replace(stored, result_json=history_result(different_ids))
    with pytest.raises(CheckpointUnavailable):
        replay_stage_projection(
            request, document, reordered_row, provider="fake", model="synthetic"
        )
    assert len(s.provider.calls) == original_calls
    assert (s.repo.get(run.id), repo.get_stages(document.id)) == snapshot


@pytest.mark.parametrize("kind", ["source", "child"])
def test_source_and_child_integrity_diagnostics_are_distinct(kind):
    request = map_request()
    good = validate_stage_output(request, output((request.chunks[0].chunk_id,)))
    if kind == "source":
        changed = SourceDocument(request.documents[0].identity, "Changed source")
        request = replace(request, documents=(changed,))
        raw = output((request.chunks[0].chunk_id,))
        code = "invalid_output_source_binding"
    else:
        bad = replace(good, findings=(replace(good.findings[0], finding_id="f" * 64),))
        request = reduce_request((bad,))
        raw = output((bad.findings[0].finding_id,))
        code = "invalid_output_result_integrity"
    with pytest.raises(StageValidationError) as error:
        validate_stage_output(request, raw)
    assert error.value.code == code


@pytest.mark.parametrize("kind", ["binding", "storage"])
def test_binding_failure_is_not_reported_as_schema_or_storage_failure(
    cross_setup, monkeypatch, kind
):
    s = cross_setup
    service = CrossDocumentAnalysisService(s.db, s.reader, s.provider)

    def fail(*args):
        if kind == "binding":
            raise DocumentAnalysisError("invalid_plan")
        raise PersistenceError("PRIVATE_DB_MESSAGE")

    monkeypatch.setattr(service._runs, "complete_document_stage", fail)
    with pytest.raises(CrossDocumentError) as error:
        service.run_analysis(s.question.id, tuple(x.id for x in s.sources[:2]))
    assert error.value.code == (
        "invalid_output_persistence_binding" if kind == "binding" else "persistence_failed"
    )
    assert "PRIVATE" not in str(error.value)
    assert len(s.provider.calls) == 1
