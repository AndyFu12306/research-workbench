import hashlib
import json
from dataclasses import FrozenInstanceError, replace

import pytest
from analysis_fakes import FakeAnalysisProvider, FakeZoteroClient, valid_output
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError

from research_workbench.analysis.corpus import InputBudget, ResearchCorpusBuilder, manifest
from research_workbench.analysis.errors import (
    AnalysisCorpusIncompleteError,
    AnalysisInputTooLargeError,
    AnalysisNoSourcesError,
    AnalysisOutputValidationError,
    AnalysisProviderError,
    AnalysisRetrievalUnavailableError,
    AnalysisStateError,
)
from research_workbench.analysis.schema import validate_and_map
from research_workbench.application.analysis import AnalysisService
from research_workbench.integrations.zotero import (
    ZoteroCommandError,
    ZoteroItemNotFoundError,
    ZoteroTimeoutError,
    ZoteroUnavailableError,
)
from research_workbench.persistence import Database, EntityNotFoundError
from research_workbench.persistence.analysis_repository import AnalysisRepository


@pytest.fixture
def setup_analysis(database, projects, questions, links):
    q = questions.create(projects.create("Synthetic project").id, "Synthetic question")
    link = links.attach_reference(q.id, "FAKE0001")
    reader, provider = FakeZoteroClient(), FakeAnalysisProvider()
    builder = ResearchCorpusBuilder(reader)
    service = AnalysisService(database, builder, provider)
    return q, link, reader, provider, builder, service, AnalysisRepository(database)


def test_analysis_happy_path_history_and_no_source_payload_in_database(database, setup_analysis):
    q, link, reader, provider, builder, service, runs = setup_analysis
    run = service.run_analysis(q.id)
    assert run.status == "completed" and run.completed_at >= run.created_at
    assert run.input_tokens == 100 and run.output_tokens == 50
    assert run.provider_response_id == "resp_synthetic"
    assert run.prompt_version == "analysis-v1" and run.schema_version == "analysis-schema-v1"
    assert json.loads(run.result_json)["summary"]["source_ids"] == [link.source_id]
    source = runs.get_sources(run.id)[0]
    assert source.input_kind == "abstract" and source.ordinal == 1
    content = provider.request.sources[0].content
    assert source.content_sha256 == hashlib.sha256(content.encode()).hexdigest()
    assert source.input_chars == len(content)
    assert reader.fulltext_calls == 0
    data = database.path.read_bytes()
    assert b"PRIVATE_SOURCE_SENTINEL" not in data and b"SYNTHETIC_METADATA_ONLY" not in data
    assert "PRIVATE_SOURCE_SENTINEL" not in repr(provider.request)
    second = service.run_analysis(q.id)
    assert second.id != run.id and runs.get(run.id) == run
    assert runs.list_by_question(q.id) == (run, second)
    assert runs.get_sources(second.id)[0].content_sha256 == source.content_sha256
    with pytest.raises(FrozenInstanceError):
        run.status = "running"


def test_source_order_mapping_and_detach_keeps_history(database, setup_analysis, links):
    q, first, reader, provider, builder, service, runs = setup_analysis
    other = links.attach_reference(q.id, "FAKE0002")
    original = reader.get_item
    reader.get_item = lambda key: replace(original(key), item_key=key)
    provider.output = valid_output("S2")
    run = service.run_analysis(q.id)
    assert [s.source_id for s in runs.get_sources(run.id)] == [first.source_id, other.source_id]
    assert json.loads(run.result_json)["key_findings"][0]["source_ids"] == [other.source_id]
    links.detach(q.id, other.source_id)
    assert len(runs.get_sources(run.id)) == 2


@pytest.mark.parametrize(
    "error",
    [
        ZoteroUnavailableError("private raw error"),
        ZoteroItemNotFoundError("missing"),
    ],
)
def test_failed_retrieval_preserves_relationships(setup_analysis, links, error):
    q, link, reader, provider, builder, service, runs = setup_analysis
    reader.error = error
    expected = (
        AnalysisCorpusIncompleteError
        if isinstance(error, ZoteroItemNotFoundError)
        else AnalysisRetrievalUnavailableError
    )
    with pytest.raises(expected):
        service.run_analysis(q.id)
    assert links.list_for_question(q.id) == (link,)
    assert runs.list_by_question(q.id) == () and provider.calls == 0


def test_unavailable_health_no_retrieval_or_provider(setup_analysis):
    q, link, reader, provider, builder, service, runs = setup_analysis
    reader.health = "degraded"
    with pytest.raises(AnalysisRetrievalUnavailableError):
        service.run_analysis(q.id)
    assert provider.calls == 0


def test_no_sources_and_missing_question_fail_without_provider(setup_analysis, links):
    q, link, reader, provider, builder, service, runs = setup_analysis
    links.detach(q.id, link.source_id)
    with pytest.raises(AnalysisNoSourcesError):
        service.run_analysis(q.id)
    with pytest.raises(EntityNotFoundError):
        service.run_analysis(9999)
    assert provider.calls == 0


def test_no_usable_content_is_strict(setup_analysis):
    q, link, reader, provider, builder, service, runs = setup_analysis
    reader.item = replace(reader.item, abstract="  ")
    with pytest.raises(AnalysisCorpusIncompleteError) as error:
        service.run_analysis(q.id)
    assert error.value.source_id == link.source_id and provider.calls == 0


@pytest.mark.parametrize(
    "budget", [InputBudget(max_chars_per_source=5), InputBudget(max_total_chars=5)]
)
def test_budget_exceeded_does_not_call_provider(database, setup_analysis, budget):
    q, link, reader, provider, builder, service, runs = setup_analysis
    service = AnalysisService(database, ResearchCorpusBuilder(reader, budget), provider)
    with pytest.raises(AnalysisInputTooLargeError):
        service.run_analysis(q.id)
    assert provider.calls == 0 and runs.list_by_question(q.id) == ()


def test_more_sources_than_budget_cannot_be_silently_omitted(setup_analysis, links):
    q, link, reader, provider, builder, service, runs = setup_analysis
    for index in range(2, 8):
        links.attach_reference(q.id, f"FAKE{index:04}")
    with pytest.raises(AnalysisInputTooLargeError):
        service.run_analysis(q.id)
    assert provider.calls == 0


@pytest.mark.parametrize(
    "fulltext_error,kind", [(None, "fulltext"), (ZoteroCommandError("no PDF"), "abstract_fallback")]
)
def test_fulltext_policy_and_fingerprint(setup_analysis, fulltext_error, kind):
    q, link, reader, provider, builder, service, runs = setup_analysis
    reader.fulltext_error = fulltext_error
    run = service.run_analysis(q.id, "fulltext_preferred")
    assert runs.get_sources(run.id)[0].input_kind == kind
    assert kind == json.loads(provider.request.sources[0].content)["input_kind"]


def test_fulltext_timeout_does_not_masquerade_as_fallback(setup_analysis):
    q, link, reader, provider, builder, service, runs = setup_analysis
    reader.fulltext_error = ZoteroTimeoutError("timeout")
    with pytest.raises(AnalysisRetrievalUnavailableError):
        service.run_analysis(q.id, "fulltext_preferred")
    assert provider.calls == 0


@pytest.mark.parametrize(
    "failure", [AnalysisProviderError(), RuntimeError("API_SECRET PRIVATE_SOURCE_SENTINEL")]
)
def test_provider_failure_records_safe_failed_run_and_preserves_links(
    setup_analysis, links, failure
):
    q, link, reader, provider, builder, service, runs = setup_analysis
    provider.failure = failure
    with pytest.raises(AnalysisProviderError) as error:
        service.run_analysis(q.id)
    run = runs.list_by_question(q.id)[0]
    assert run.status == "failed" and run.result_json is None and run.completed_at
    assert run.error_code == "provider_failed"
    assert "API_SECRET" not in run.error_message + str(error.value)
    assert links.list_for_question(q.id) == (link,)
    assert len(runs.get_sources(run.id)) == 1 and provider.calls == 1


def test_invalid_citation_marks_run_failed_without_retry(setup_analysis):
    q, link, reader, provider, builder, service, runs = setup_analysis
    provider.output = valid_output("S99")
    with pytest.raises(AnalysisOutputValidationError):
        service.run_analysis(q.id)
    assert runs.list_by_question(q.id)[0].error_code == "invalid_output" and provider.calls == 1


@pytest.mark.parametrize("raw", ["not json", "{}", "[]", '{"summary":1,"summary":2}'])
def test_invalid_json_is_rejected(raw):
    with pytest.raises(AnalysisOutputValidationError):
        validate_and_map(raw, {"S1": 1})


@pytest.mark.parametrize(
    "change",
    [
        lambda d: d.update(extra="unknown"),
        lambda d: d.update(summary="plain string"),
        lambda d: d.update(next_questions=[]),
        lambda d: d.update(key_findings=[]),
        lambda d: d["summary"].update(text=" "),
        lambda d: d["summary"].update(source_labels=[]),
        lambda d: d["summary"].update(source_labels=["S1", "S1"]),
        lambda d: d["summary"].update(source_labels=[7]),
        lambda d: d.update(agreements=[d["summary"]] * 13),
    ],
)
def test_schema_types_bounds_and_unknown_keys(change):
    output = valid_output()
    change(output)
    with pytest.raises(AnalysisOutputValidationError):
        validate_and_map(json.dumps(output), {"S1": 1})


def test_terminal_runs_cannot_be_completed_or_failed_again(setup_analysis):
    q, link, reader, provider, builder, service, runs = setup_analysis
    completed = service.run_analysis(q.id)
    result = validate_and_map(json.dumps(valid_output()), {"S1": link.source_id})
    with pytest.raises(AnalysisStateError):
        runs.complete_run(completed.id, result)
    with pytest.raises(AnalysisStateError):
        runs.fail_run(completed.id, "provider_failed")
    pending = runs.create_run(
        q.id, provider="fake", model="test", sources=manifest(provider.request)
    )
    failed = runs.fail_run(pending.id, "provider_failed")
    with pytest.raises(AnalysisStateError):
        runs.complete_run(failed.id, result)
    assert runs.get(completed.id) == completed


def test_reopen_and_manifest_schema(database, setup_analysis):
    q, link, reader, provider, builder, service, runs = setup_analysis
    run = service.run_analysis(q.id)
    database.close()
    with Database(database.path) as reopened:
        repository = AnalysisRepository(reopened)
        assert repository.get(run.id) == run
        assert repository.get_sources(run.id)[0].source_id == link.source_id
        assert {
            c["name"] for c in inspect(reopened._engine).get_columns("analysis_source_inputs")
        } == {"analysis_id", "source_id", "ordinal", "input_kind", "content_sha256", "input_chars"}


def test_analysis_foreign_key_enforcement(database):
    with pytest.raises(IntegrityError), database._engine.begin() as c:
        c.execute(
            text(
                "INSERT INTO analysis_runs "
                "(question_id,status,provider,model,prompt_version,schema_version) "
                "VALUES (999,'running','fake','test','v1','v1')"
            )
        )


def test_invalid_manifest_rolls_back_entire_run(setup_analysis):
    q, link, reader, provider, builder, service, runs = setup_analysis
    request = builder.build(q, (link,), "abstracts")
    bad = replace(manifest(request)[0], source_id=9999)
    with pytest.raises(EntityNotFoundError):
        runs.create_run(q.id, provider="fake", model="test", sources=(bad,))
    assert runs.list_by_question(q.id) == ()


def test_persistence_failure_is_not_reported_as_provider_failure(setup_analysis, monkeypatch):
    from research_workbench.analysis.errors import AnalysisPersistenceError
    from research_workbench.persistence.errors import PersistenceError

    q, link, reader, provider, builder, service, runs = setup_analysis

    def failed_save(*args, **kwargs):
        raise PersistenceError("Synthetic storage unavailable")

    monkeypatch.setattr(service._runs, "complete_run", failed_save)
    with pytest.raises(AnalysisPersistenceError):
        service.run_analysis(q.id)
    assert runs.list_by_question(q.id)[0].error_code == "persistence_failed"


def test_no_database_transaction_is_held_during_provider_call(setup_analysis, database):
    q, link, reader, provider, builder, service, runs = setup_analysis
    original = provider.generate

    def generate(request):
        # A distinct connection can take a write lock while external compute runs.
        with database._engine.begin() as connection:
            connection.execute(text("UPDATE projects SET description='parallel edit'"))
        return original(request)

    provider.generate = generate
    assert service.run_analysis(q.id).status == "completed"


@pytest.mark.parametrize("source_case", ["empty_pdf", "no_pdf_no_abstract", "wrong_fulltext_key"])
def test_fulltext_content_edge_cases(setup_analysis, source_case):
    q, link, reader, provider, builder, service, runs = setup_analysis
    reader.fulltext = replace(reader.fulltext, text=" ")
    if source_case == "wrong_fulltext_key":
        reader.fulltext = replace(reader.fulltext, item_key="FAKE9999")
        with pytest.raises(AnalysisRetrievalUnavailableError):
            service.run_analysis(q.id, "fulltext_preferred")
    elif source_case == "no_pdf_no_abstract":
        reader.item = replace(reader.item, abstract=None)
        with pytest.raises(AnalysisCorpusIncompleteError):
            service.run_analysis(q.id, "fulltext_preferred")
    else:
        run = service.run_analysis(q.id, "fulltext_preferred")
        assert runs.get_sources(run.id)[0].input_kind == "abstract_fallback"


def test_manifest_order_and_unique_constraints_enforced(database, setup_analysis):
    q, link, reader, provider, builder, service, runs = setup_analysis
    run = service.run_analysis(q.id)
    with pytest.raises(IntegrityError), database._engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO analysis_source_inputs "
                "(analysis_id,source_id,ordinal,input_kind,content_sha256,input_chars) "
                "VALUES (:a,:s,1,'abstract',:h,10)"
            ),
            {"a": run.id, "s": link.source_id, "h": "a" * 64},
        )
