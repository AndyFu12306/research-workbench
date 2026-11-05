"""Sequential synthetic execution and failure states on real temporary SQLite."""

import json

import pytest

from research_workbench.analysis.chunking import ChunkingConfig
from research_workbench.analysis.cross_document_pipeline import CrossDocumentError
from research_workbench.analysis.errors import AnalysisProviderFailure
from research_workbench.application.cross_document_analysis import CrossDocumentAnalysisService
from research_workbench.persistence.document_repository import DocumentAnalysisRepository


def execute(s, **kwargs):
    return CrossDocumentAnalysisService(
        s.db, s.reader, s.provider, chunking=ChunkingConfig(100, 0), **kwargs
    ).run_analysis(s.question.id, tuple(x.id for x in s.sources))


@pytest.mark.parametrize("empty", ["none", "mixed", "all"])
def test_complete_ordered_execution_and_scoped_history(cross_setup, empty):
    s = cross_setup
    s.provider.empty = (
        {1, 2, 3} if empty == "mixed" else set(range(1, 20)) if empty == "all" else set()
    )
    run = execute(s)
    assert run.status == "completed"
    assert [r.stage for r in s.provider.calls] == [
        "map",
        "map",
        "document_reduce",
        "map",
        "map",
        "map",
        "document_reduce",
        "map",
        "document_reduce",
        "cross_document_reduce",
    ]
    assert s.reader.reads == ["FAKE0001", "FAKE0002", "FAKE0003"]
    history = s.repo.get_history(run.id)
    assert history.history_version == "cross-document-history-v1"
    assert (history.status == "no_relevant_findings") == (empty == "all")
    assert len(s.repo.get_members(run.id)) == 3
    data = json.loads(history.result_json)
    assert data["cross_execution_id"] == run.id
    if empty == "mixed":
        assert data["findings"][0]["citations"][0]["input_ordinal"] == 2


@pytest.mark.parametrize("at", [1, 2, 3, 4, 7, 9, 10])
def test_invalid_return_stops_immediately_and_preserves_known_usage(cross_setup, at):
    s = cross_setup
    s.provider.invalid_at = at
    with pytest.raises(CrossDocumentError):
        execute(s)
    assert len(s.provider.calls) == at
    run = s.repo.list_by_question(s.question.id)[0]
    assert run.status == "failed"
    with pytest.raises(CrossDocumentError):
        s.repo.get_history(run.id)
    documents = DocumentAnalysisRepository(s.db)
    if at == 10:
        from sqlalchemy import select

        from research_workbench.persistence.cross_document_models import cross_stages

        with s.db._engine.connect() as c:
            failed = (
                c.execute(select(cross_stages).where(cross_stages.c.run_id == run.id))
                .mappings()
                .one()
            )
    else:
        failures = [
            stage
            for b in s.repo.get_members(run.id)
            for stage in documents.get_stages(b.document_execution_id)
            if stage.status == "failed"
        ]
        assert len(failures) == 1
        failed = failures[0]
    assert failed.call_outcome == "response_received"
    assert (failed.input_tokens, failed.output_tokens, failed.response_id) == (
        100,
        40,
        f"fake-{at}",
    )
    assert failed.usage_cost is None  # No price configured; known usage is still retained.
    states = [documents.get(b.document_execution_id).status for b in s.repo.get_members(run.id)]
    if at == 4:
        assert states == ["completed", "failed", "failed"]


@pytest.mark.parametrize(
    "error",
    [
        AnalysisProviderFailure("provider_timeout"),
        AnalysisProviderFailure("provider_network"),
        TimeoutError("PRIVATE"),
        ConnectionError("PRIVATE"),
        RuntimeError("PRIVATE unconfirmed provider exception"),
    ],
)
@pytest.mark.parametrize("at", [4, 10])
def test_unconfirmed_return_is_interrupted_unknown(cross_setup, error, at):
    s = cross_setup
    s.provider.error_at, s.provider.error = at, error
    with pytest.raises(CrossDocumentError) as e:
        execute(s)
    assert e.value.code == "interrupted" and "PRIVATE" not in str(e.value)
    run = s.repo.list_by_question(s.question.id)[0]
    assert run.status == "interrupted"
    assert len(s.provider.calls) == at
    documents = DocumentAnalysisRepository(s.db)
    if at == 4:
        b = s.repo.get_members(run.id)[1]
        stage = documents.get_stages(b.document_execution_id)[0]
        assert stage.status == "interrupted" and stage.call_outcome == "unknown"
        assert stage.usage_cost is None and stage.input_tokens is None


@pytest.mark.parametrize("at", [1, 4, 10])
def test_keyboard_interrupt_and_system_exit_are_distinct(cross_setup, at):
    s = cross_setup
    s.provider.error_at, s.provider.error = at, KeyboardInterrupt()
    with pytest.raises(KeyboardInterrupt):
        execute(s)
    assert s.repo.list_by_question(s.question.id)[0].status == "interrupted"
    s.provider.calls.clear()
    s.provider.error = SystemExit()
    with pytest.raises(SystemExit):
        execute(s)
    assert s.repo.list_by_question(s.question.id)[1].status == "running"


def test_duplicate_content_refused_without_history_or_model_calls(cross_setup):
    s = cross_setup
    s.reader.texts["FAKE0002"] = s.reader.texts["FAKE0001"]
    with pytest.raises(CrossDocumentError) as e:
        execute(s)
    assert e.value.code == "duplicate_content"
    assert s.repo.list_by_question(s.question.id) == () and s.provider.calls == []


def test_exactly_two_explicit_sources_does_not_read_unselected_document(cross_setup):
    s = cross_setup
    service = CrossDocumentAnalysisService(
        s.db, s.reader, s.provider, chunking=ChunkingConfig(100, 0)
    )
    result = service.run_analysis(s.question.id, (s.sources[1].id, s.sources[0].id))
    assert s.reader.reads == ["FAKE0002", "FAKE0001"]
    assert [m.source_id for m in s.repo.get_members(result.id)] == [
        s.sources[1].id,
        s.sources[0].id,
    ]
    assert len(s.provider.calls) == 8


@pytest.mark.parametrize("selection", [[1, 2], (1,), (1, 1), (True, 2), (0, 2), (1, 2, 3, 4, 5, 6)])
def test_invalid_selection_zero_reads_history_and_provider_calls(cross_setup, selection):
    s = cross_setup
    service = CrossDocumentAnalysisService(s.db, s.reader, s.provider)
    with pytest.raises(CrossDocumentError):
        service.run_analysis(s.question.id, selection)
    assert s.reader.reads == [] and s.provider.calls == []
    assert s.repo.list_by_question(s.question.id) == ()


def test_invalid_none_return_is_received_invalid_output_not_unknown(cross_setup):
    from sqlalchemy import select

    from research_workbench.persistence.cross_document_models import cross_stages

    s = cross_setup
    original = s.provider.generate_stage

    def generate(request):
        value = original(request)
        return None if request.stage == "cross_document_reduce" else value

    s.provider.generate_stage = generate
    with pytest.raises(CrossDocumentError) as e:
        execute(s)
    assert e.value.code == "invalid_output"
    with s.db._engine.connect() as c:
        row = c.execute(select(cross_stages)).mappings().one()
        assert row.status == "failed" and row.call_outcome == "response_received"
        assert row.usage_cost is None
