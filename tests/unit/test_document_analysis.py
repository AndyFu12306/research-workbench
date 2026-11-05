"""Synthetic end-to-end pipeline, real migrated temporary SQLite, no external I/O."""

import json
from dataclasses import replace
from decimal import Decimal
from types import SimpleNamespace

import pytest
from sqlalchemy import delete, event, select, update
from sqlalchemy.exc import SQLAlchemyError

from research_workbench.analysis.chunking import ChunkingConfig
from research_workbench.analysis.contracts import ProviderResult
from research_workbench.analysis.document_pipeline import (
    DocumentAnalysisError,
    ExecutionBudget,
    Pricing,
    RequestEstimate,
)
from research_workbench.analysis.errors import AnalysisProviderFailure
from research_workbench.analysis.stage_contracts import CONTRACT_VERSIONS
from research_workbench.analysis.stage_validation import validate_stage_output
from research_workbench.application.document_analysis import DocumentAnalysisService
from research_workbench.integrations.ai.stages import prepare_stage_call
from research_workbench.integrations.zotero.models import ZoteroFulltext
from research_workbench.persistence import Database
from research_workbench.persistence.document_models import chunks, executions, inputs, stages
from research_workbench.persistence.document_repository import DocumentAnalysisRepository
from research_workbench.persistence.errors import PersistenceError

TEXT = "PRIVATE_SOURCE_SENTINEL 合成方法和局限。\r\n\r\n" * 30
QUESTION = "PRIVATE_QUESTION_SENTINEL Compare synthetic methods?"


class FakeReader:
    def __init__(self, text=TEXT):
        self.text = text
        self.reads = []
        self.health = "healthy"
        self.error = None

    def health_check(self):
        return SimpleNamespace(status=self.health)

    def get_fulltext(self, key):
        self.reads.append(key)
        if self.error:
            raise self.error
        return ZoteroFulltext(key, self.text, 10)


class FakeStageProvider:
    provider = "fake"
    model = "synthetic"
    capabilities = frozenset(CONTRACT_VERSIONS.items())

    def __init__(self):
        self.calls = []
        self.empty = set()
        self.error_at = None
        self.error = None
        self.invalid_at = None
        self.callback = None
        self.text_size = 50
        self.usage = (100, 40)

    def estimate_stage(self, request):
        # Explicit offline OpenAI wire profile, using the production final-payload estimator.
        _, estimate = prepare_stage_call(request, model=self.model, provider="openai")
        return RequestEstimate(estimate.input_tokens, estimate.context_tokens)

    def generate_stage(self, request):
        self.calls.append(request)
        if self.callback:
            self.callback(request)
        n = len(self.calls)
        if n == self.error_at:
            raise self.error
        labels = (
            [request.chunks[0].chunk_id]
            if request.stage == "map"
            else [f.finding_id for child in request.children for f in child.findings][:1]
        )
        empty = n in self.empty or not labels
        output = {
            "status": "no_relevant_findings" if empty else "findings",
            "findings": []
            if empty
            else [
                {
                    "text": "Generated synthetic interpretation. " + "x" * self.text_size,
                    "kind": "ai_inference",
                    "category": "finding",
                    "citations": labels,
                }
            ],
        }
        raw = (
            '{"status":"findings","findings":[],"private":"RAW_INVALID_SENTINEL"}'
            if n == self.invalid_at
            else json.dumps(output)
        )
        return ProviderResult(raw, f"fake-{n}", *self.usage)


@pytest.fixture
def setup_pipeline(database, projects, questions, links):
    question = questions.create(projects.create("Synthetic project").id, QUESTION)
    linked = links.attach_reference(question.id, "FAKE0001")
    reader, provider = FakeReader(), FakeStageProvider()
    repo = DocumentAnalysisRepository(database)

    def service(**kwargs):
        return DocumentAnalysisService(
            database,
            reader,
            provider,
            chunking=ChunkingConfig(240, 20),
            **kwargs,
        )

    return SimpleNamespace(
        db=database,
        question=question,
        source=linked.source,
        reader=reader,
        provider=provider,
        repo=repo,
        service=service,
        links=links,
    )


def execute(s, **kwargs):
    return s.service(**kwargs).run_analysis(s.question.id, s.source.id)


@pytest.mark.parametrize("empty", ["none", "mixed", "all"])
def test_complete_plan_all_maps_then_one_reduce_and_scoped_trace(setup_pipeline, empty):
    s = setup_pipeline
    if empty != "none":
        s.provider.empty = {1} if empty == "mixed" else set(range(1, 101))

    def inspect_before_call(request):
        run = s.repo.list_by_question(s.question.id)[0]
        history = s.repo.get_stages(run.id)
        manifest = s.repo.get_chunks(run.id)
        assert len(manifest) == run.chunk_count
        assert len(history) == run.chunk_count + 1
        assert len(s.repo.get_inputs(run.id)) == 2 * run.chunk_count
        if request.stage == "document_reduce":
            assert all(stage.status == "completed" for stage in history[:-1])
            assert [c.request.chunks[0].chunk_id for c in request.children] == [
                c.chunk_id for c in manifest
            ]

    s.provider.callback = inspect_before_call
    run = execute(s)
    assert run.status == "completed"
    history = s.repo.get_stages(run.id)
    assert len(s.provider.calls) == run.chunk_count + 1
    assert all(r.stage == "map" for r in s.provider.calls[:-1])
    assert s.provider.calls[-1].stage == "document_reduce"
    assert s.reader.reads == ["FAKE0001"]
    assert all(h.status == "completed" for h in history)
    assert all(h.call_outcome == "response_received" for h in history)
    assert all(run.created_at <= h.started_at <= h.completed_at for h in history)
    assert all(h.usage_cost is None and h.estimated_cost is None for h in history)
    root = json.loads(history[-1].result_json)
    if empty == "all":
        assert root == {"status": "no_relevant_findings", "findings": []}
    else:
        finding = root["findings"][0]
        citation = finding["citations"][0]
        parent = json.loads(history[citation["stage_ordinal"] - 1].result_json)
        assert citation["finding_id"] in [f["finding_id"] for f in parent["findings"]]
        assert set(finding["chunk_ids"]) <= {c.chunk_id for c in s.repo.get_chunks(run.id)}


@pytest.mark.parametrize(
    "code", ["provider_timeout", "provider_refusal", "provider_incomplete", "provider_unavailable"]
)
def test_map_failure_preserves_prior_success_and_never_reduces(setup_pipeline, code):
    s = setup_pipeline
    s.provider.error_at, s.provider.error = 2, AnalysisProviderFailure(code)
    with pytest.raises(DocumentAnalysisError) as exc:
        execute(s)
    assert exc.value.code == code
    run = s.repo.list_by_question(s.question.id)[0]
    history = s.repo.get_stages(run.id)
    assert run.status == "failed"
    assert history[0].status == "completed"
    assert history[1].status == "failed" and history[1].call_outcome == "unknown"
    assert history[1].input_tokens is None and history[1].usage_cost is None
    assert all(h.status == "not_started" for h in history[2:])
    assert len(s.provider.calls) == 2


def test_invalid_map_output_not_persisted_but_known_usage_is_preserved(setup_pipeline):
    s = setup_pipeline
    s.provider.invalid_at = 2
    with pytest.raises(DocumentAnalysisError, match="validation"):
        execute(s)
    run = s.repo.list_by_question(s.question.id)[0]
    bad = s.repo.get_stages(run.id)[1]
    assert bad.status == "failed" and bad.result_json is None
    assert bad.call_outcome == "response_received" and bad.input_tokens == 100
    assert len(s.provider.calls) == 2


@pytest.mark.parametrize("kind", ["call_limit", "token_limit", "cost_limit"])
def test_whole_execution_preflight_reserves_reduce_before_any_call(setup_pipeline, kind):
    s = setup_pipeline
    kwargs = {"execution_budget": ExecutionBudget(max_calls=1)}
    if kind == "token_limit":
        kwargs = {"execution_budget": ExecutionBudget(max_estimated_tokens=32768)}
    elif kind == "cost_limit":
        kwargs = {
            "execution_budget": ExecutionBudget(max_estimated_cost=Decimal("0.000001")),
            "pricing": Pricing("USD", "synthetic-v1", Decimal("1"), Decimal("2")),
        }
    with pytest.raises(DocumentAnalysisError):
        execute(s, **kwargs)
    assert s.provider.calls == []
    assert s.repo.list_by_question(s.question.id) == ()


@pytest.mark.parametrize("kind", ["reservation", "payload"])
def test_reduce_budget_failure_keeps_every_map_and_makes_zero_reduce_calls(setup_pipeline, kind):
    s = setup_pipeline
    kwargs = {"execution_budget": ExecutionBudget(reduce_reserved_tokens=1)}
    if kind == "payload":
        s.reader.text = TEXT * 4
        s.provider.text_size = 1900
        kwargs = {}
    with pytest.raises(DocumentAnalysisError) as exc:
        execute(s, **kwargs)
    assert exc.value.code == "reduce_budget_exceeded"
    run = s.repo.list_by_question(s.question.id)[0]
    assert run.status == "failed"
    assert len(s.provider.calls) == run.chunk_count
    assert all(h.status == "completed" for h in s.repo.get_stages(run.id)[:-1])
    assert s.repo.get_stages(run.id)[-1].status == "not_started"


@pytest.mark.parametrize(
    "damage", ["missing_map", "missing_chunk", "fingerprint", "ordinal", "edge", "output"]
)
def test_persisted_completeness_gate_rejects_damage_before_reduce(
    setup_pipeline, monkeypatch, damage
):
    s = setup_pipeline
    original = DocumentAnalysisRepository.complete_stage

    def complete(self, execution_id, ordinal, result, metadata):
        run = self.get(execution_id)
        if damage == "missing_map" and ordinal == 1:
            return run  # Simulate a defective repository that did not persist its claimed success.
        value = original(self, execution_id, ordinal, result, metadata)
        if ordinal == run.chunk_count:
            with s.db._session() as session:
                if damage == "missing_chunk":
                    session.execute(
                        delete(inputs).where(
                            inputs.c.execution_id == execution_id, inputs.c.chunk_ordinal == 1
                        )
                    )
                    session.execute(
                        delete(chunks).where(
                            chunks.c.execution_id == execution_id, chunks.c.ordinal == 1
                        )
                    )
                elif damage == "fingerprint":
                    session.execute(
                        update(chunks)
                        .where(chunks.c.execution_id == execution_id, chunks.c.ordinal == 1)
                        .values(source_fingerprint="a" * 64)
                    )
                elif damage == "ordinal":
                    session.execute(
                        update(executions)
                        .where(executions.c.id == execution_id)
                        .values(chunk_count=run.chunk_count + 1)
                    )
                elif damage == "edge":
                    session.execute(
                        delete(inputs).where(
                            inputs.c.execution_id == execution_id,
                            inputs.c.parent_ordinal == run.chunk_count + 1,
                            inputs.c.ordinal == 1,
                        )
                    )
                elif damage == "output":
                    session.execute(
                        update(stages)
                        .where(stages.c.execution_id == execution_id, stages.c.ordinal == 1)
                        .values(result_json='{"status":"no_relevant_findings","findings":[]}')
                    )
        return value

    monkeypatch.setattr(DocumentAnalysisRepository, "complete_stage", complete)
    with pytest.raises(DocumentAnalysisError):
        execute(s)
    assert all(c.stage == "map" for c in s.provider.calls)
    assert s.repo.list_by_question(s.question.id)[0].status == "failed"


@pytest.mark.parametrize("permanent", [False, True])
def test_commit_failure_blocks_next_call_and_completed_execution(
    setup_pipeline, monkeypatch, permanent
):
    s = setup_pipeline

    def fail(*args, **kwargs):
        raise PersistenceError("PRIVATE_DATABASE_ERROR")

    monkeypatch.setattr(DocumentAnalysisRepository, "complete_stage", fail)
    if permanent:
        monkeypatch.setattr(DocumentAnalysisRepository, "fail_run", fail)
    with pytest.raises(DocumentAnalysisError) as exc:
        execute(s)
    assert exc.value.code == "persistence_failed"
    run = s.repo.list_by_question(s.question.id)[0]
    assert run.status == ("running" if permanent else "failed")
    assert len(s.provider.calls) == 1
    assert s.repo.get_stages(run.id)[0].result_json is None


@pytest.mark.parametrize("error", [KeyboardInterrupt(), SystemExit(9)])
def test_interrupt_or_forced_exit_never_claims_completed(setup_pipeline, error):
    s = setup_pipeline
    s.provider.error_at, s.provider.error = 2, error
    with pytest.raises(type(error)):
        execute(s)
    run = s.repo.list_by_question(s.question.id)[0]
    assert run.status == ("interrupted" if isinstance(error, KeyboardInterrupt) else "running")
    history = s.repo.get_stages(run.id)
    assert history[0].status == "completed"
    assert history[1].call_outcome == "unknown"
    assert len(s.provider.calls) == 2


def test_immutable_finalization_rerun_reopen_detach_and_privacy(setup_pipeline, caplog, capsys):
    s = setup_pipeline
    first = execute(s)
    snapshot = s.repo.get_stages(first.id)
    with pytest.raises(DocumentAnalysisError, match="finalized"):
        s.repo.fail_run(first.id, "provider_failed")
    reduce_request = s.provider.calls[-1]
    refs = [f.finding_id for child in reduce_request.children for f in child.findings][:1]
    candidate = json.dumps(
        {
            "status": "findings",
            "findings": [
                {
                    "text": "Synthetic.",
                    "kind": "ai_inference",
                    "category": "finding",
                    "citations": refs,
                }
            ],
        }
    )
    with pytest.raises(DocumentAnalysisError, match="finalized"):
        s.repo.complete_stage(
            first.id,
            first.chunk_count + 1,
            validate_stage_output(reduce_request, candidate),
            ProviderResult(candidate),
        )
    second = execute(s)
    assert second.id != first.id
    assert s.repo.get_stages(first.id) == snapshot
    assert s.links.detach(s.question.id, s.source.id)
    assert s.repo.get(first.id) == first
    with Database(s.db.path) as reopened:
        assert DocumentAnalysisRepository(reopened).get_stages(first.id) == snapshot
    # Include SQLite pages/free pages, not just selected columns.
    data = s.db.path.read_bytes()
    for secret in (TEXT, "RAW_INVALID_SENTINEL", "PRIVATE_DATABASE_ERROR"):
        assert secret.encode() not in data
    assert QUESTION not in repr((first, snapshot))
    assert "PRIVATE_" not in repr((first, snapshot))
    assert "PRIVATE_" not in caplog.text
    assert capsys.readouterr() == ("", "")


@pytest.mark.parametrize("kind", ["unlinked", "unhealthy", "empty", "truncated", "wrong_key"])
def test_fulltext_preflight_has_no_fallback_or_automatic_source_selection(
    setup_pipeline, monkeypatch, kind
):
    s = setup_pipeline
    if kind == "unlinked":
        s.links.detach(s.question.id, s.source.id)
    elif kind == "unhealthy":
        s.reader.health = "degraded"
    elif kind == "empty":
        s.reader.text = " \n\t"
    elif kind == "truncated":
        s.reader.error = ValueError("PRIVATE_TRUNCATED_CONTENT")
    else:
        monkeypatch.setattr(s.reader, "get_fulltext", lambda key: ZoteroFulltext("FAKE9999", TEXT))
    with pytest.raises(DocumentAnalysisError, match="fulltext"):
        execute(s)
    assert s.provider.calls == []
    assert s.repo.list_by_question(s.question.id) == ()


@pytest.mark.parametrize("usage", [(100, 40), (None, None), (100, None)])
def test_decimal_pricing_distinguishes_estimate_known_usage_and_unknown(setup_pipeline, usage):
    s = setup_pipeline
    s.provider.usage = usage
    price = Pricing("USD", "synthetic-v1", Decimal("1.25"), Decimal("2.5"))
    run = execute(s, pricing=price)
    for h in s.repo.get_stages(run.id):
        assert Decimal(h.estimated_cost) == price.cost(h.estimated_input_tokens, 4096)
        expected = price.cost(*usage)
        assert h.usage_cost == (str(expected) if expected is not None else None)
    assert price.cost(None, 0) is None


@pytest.mark.parametrize("value", [Decimal("NaN"), Decimal("Infinity"), Decimal("-1"), 1.2])
def test_invalid_prices_rejected(value):
    with pytest.raises(DocumentAnalysisError):
        Pricing("USD", "synthetic-v1", value, Decimal("1"))


def test_request_estimator_is_same_wire_payload_as_generation(setup_pipeline):
    s = setup_pipeline
    run = execute(s)
    for request, history in zip(s.provider.calls, s.repo.get_stages(run.id), strict=True):
        _, estimate = prepare_stage_call(request, model="synthetic", provider="openai")
        assert history.estimated_input_tokens == estimate.input_tokens
        assert history.estimated_context_tokens == estimate.context_tokens


def test_stage_history_isolation_between_executions(setup_pipeline, monkeypatch):
    s = setup_pipeline
    first = execute(s)
    original = DocumentAnalysisRepository.complete_stage

    def inject(self, execution_id, ordinal, result, metadata):
        if ordinal == 1:
            # Same deterministic labels from a different history cannot be persisted
            # before its own stage has been started; the old execution is terminal.
            with pytest.raises(DocumentAnalysisError):
                original(self, first.id, ordinal, result, metadata)
        return original(self, execution_id, ordinal, result, metadata)

    monkeypatch.setattr(DocumentAnalysisRepository, "complete_stage", inject)
    second = execute(s)
    assert second.status == "completed"
    assert all(h.execution_id == second.id for h in s.repo.get_stages(second.id))


@pytest.mark.parametrize("target", ["plan", "map", "reduce"])
def test_actual_transaction_commit_failure_rolls_back_result_and_final_status(
    setup_pipeline, target
):
    s = setup_pipeline
    triggered = False

    def fail_commit(session):
        nonlocal triggered
        if triggered:
            return
        rows = session.execute(select(stages)).mappings().all()
        hit = bool(rows) and (
            (target == "plan" and all(r.status == "planned" for r in rows))
            or (target == "map" and any(r.stage == "map" and r.status == "completed" for r in rows))
            or (
                target == "reduce"
                and any(r.stage == "document_reduce" and r.status == "completed" for r in rows)
            )
        )
        if hit:
            triggered = True
            raise SQLAlchemyError("PRIVATE_COMMIT_SENTINEL")

    event.listen(s.db._sessions, "before_commit", fail_commit)
    try:
        with pytest.raises(DocumentAnalysisError) as exc:
            execute(s)
        assert exc.value.code == "persistence_failed"
    finally:
        event.remove(s.db._sessions, "before_commit", fail_commit)
    assert triggered
    history = s.repo.list_by_question(s.question.id)
    if target == "plan":
        assert history == () and s.provider.calls == []
    else:
        run = history[0]
        assert run.status == "failed"
        failed = [h for h in s.repo.get_stages(run.id) if h.status == "failed"]
        assert len(failed) == 1 and failed[0].result_json is None
        assert failed[0].call_outcome == "response_received"
        if target == "map":
            assert len(s.provider.calls) == 1
        else:
            assert all(h.status == "completed" for h in s.repo.get_stages(run.id)[:-1])


@pytest.mark.parametrize(
    "damage", ["foreign_chunk", "foreign_finding", "reversed_children", "forged_result"]
)
def test_reduce_rejects_foreign_labels_and_forged_runtime_results(
    setup_pipeline, monkeypatch, damage
):
    s = setup_pipeline
    original = DocumentAnalysisRepository.complete_stage

    def mutate(self, execution_id, ordinal, result, metadata):
        if result.request.stage == "document_reduce":
            if damage in ("foreign_chunk", "foreign_finding"):
                data = json.loads(result.output_json)
                if damage == "foreign_chunk":
                    label = result.request.children[0].request.chunks[0].chunk_id
                else:
                    label = "f" * 64
                data["findings"][0]["citations"] = [label]
                result = replace(result, output_json=json.dumps(data))
            elif damage == "reversed_children":
                result = replace(
                    result, request=replace(result.request, children=result.request.children[::-1])
                )
            else:
                result = replace(
                    result, findings=(replace(result.findings[0], finding_id="f" * 64),)
                )
        return original(self, execution_id, ordinal, result, metadata)

    monkeypatch.setattr(DocumentAnalysisRepository, "complete_stage", mutate)
    with pytest.raises(DocumentAnalysisError) as exc:
        execute(s)
    assert exc.value.code == "invalid_output"
    run = s.repo.list_by_question(s.question.id)[0]
    assert run.status == "failed" and s.repo.get_stages(run.id)[-1].result_json is None


def test_changed_real_request_estimate_is_rechecked_before_each_call(setup_pipeline, monkeypatch):
    s = setup_pipeline
    original = s.provider.estimate_stage
    estimates = 0

    def changed(request):
        nonlocal estimates
        estimates += 1
        estimate = original(request)
        if s.repo.list_by_question(s.question.id):
            return replace(estimate, input_tokens=100_000, context_tokens=105_120)
        return estimate

    monkeypatch.setattr(s.provider, "estimate_stage", changed)
    with pytest.raises(DocumentAnalysisError) as exc:
        execute(s)
    assert exc.value.code == "execution_budget_exceeded"
    assert estimates > 1 and s.provider.calls == []
    assert s.repo.list_by_question(s.question.id)[0].status == "failed"


def test_single_chunk_still_has_one_explicit_document_reduce(setup_pipeline):
    s = setup_pipeline
    s.reader.text = "Short synthetic fulltext."
    run = execute(s)
    assert run.chunk_count == 1 and len(s.provider.calls) == 2


def test_invalid_candidate_and_unexpected_exception_privacy(setup_pipeline, caplog):
    s = setup_pipeline
    s.provider.invalid_at = 1
    with pytest.raises(DocumentAnalysisError):
        execute(s)
    assert b"RAW_INVALID_SENTINEL" not in s.db.path.read_bytes()
    s.provider.invalid_at = None
    s.provider.error_at = len(s.provider.calls) + 1
    s.provider.error = RuntimeError("PRIVATE_EXCEPTION_SENTINEL synthetic-key")
    with pytest.raises(DocumentAnalysisError) as exc:
        execute(s)
    assert "PRIVATE_" not in str(exc.value) and exc.value.__suppress_context__
    assert b"PRIVATE_EXCEPTION_SENTINEL" not in s.db.path.read_bytes()
    assert "PRIVATE_EXCEPTION_SENTINEL" not in caplog.text


@pytest.mark.parametrize("point", ["provider", "validation"])
def test_reduce_failure_preserves_all_maps_and_no_final_result(setup_pipeline, monkeypatch, point):
    s = setup_pipeline
    original = s.provider.generate_stage

    def fail_reduce(request):
        if request.stage == "document_reduce":
            if point == "provider":
                s.provider.calls.append(request)
                raise AnalysisProviderFailure("provider_refusal")
            return ProviderResult('{"status":"findings","findings":[]}', "fake-bad", 55, 20)
        return original(request)

    monkeypatch.setattr(s.provider, "generate_stage", fail_reduce)
    with pytest.raises(DocumentAnalysisError) as exc:
        execute(s)
    assert exc.value.code == ("provider_refusal" if point == "provider" else "invalid_output")
    run = s.repo.list_by_question(s.question.id)[0]
    history = s.repo.get_stages(run.id)
    assert run.status == "failed" and history[-1].result_json is None
    assert all(h.status == "completed" for h in history[:-1])
    assert history[-1].status == "failed"


def test_selected_source_is_explicit_even_with_other_associations(setup_pipeline):
    s = setup_pipeline
    for key in ("FAKE0002", "FAKE0003"):
        s.links.attach_reference(s.question.id, key)
    run = execute(s)
    assert run.source_id == s.source.id and s.reader.reads == ["FAKE0001"]
    assert all(d.identity.external_key == "FAKE0001" for r in s.provider.calls for d in r.documents)


def test_missing_capability_and_missing_price_block_before_source_read(setup_pipeline):
    s = setup_pipeline
    with pytest.raises(DocumentAnalysisError):
        s.service(execution_budget=ExecutionBudget(max_estimated_cost=Decimal("1")))
    s.provider.capabilities = frozenset({("map", CONTRACT_VERSIONS["map"])})
    with pytest.raises(DocumentAnalysisError):
        execute(s)
    assert s.reader.reads == [] and s.provider.calls == []


def test_plan_commit_rechecks_association_after_read(setup_pipeline, monkeypatch):
    s = setup_pipeline
    original = s.reader.get_fulltext

    def detached(key):
        result = original(key)
        s.links.detach(s.question.id, s.source.id)
        return result

    monkeypatch.setattr(s.reader, "get_fulltext", detached)
    with pytest.raises(DocumentAnalysisError):
        execute(s)
    assert s.repo.list_by_question(s.question.id) == () and s.provider.calls == []


@pytest.mark.parametrize(
    "field,value",
    [
        ("input_tokens", -1),
        ("input_tokens", True),
        ("output_tokens", "private"),
        ("response_id", "PRIVATE_RESPONSE\nwith prose"),
    ],
)
def test_malformed_provider_metadata_cannot_complete_or_leak(
    setup_pipeline, monkeypatch, field, value
):
    s = setup_pipeline
    original = s.provider.generate_stage

    def malformed(request):
        return replace(original(request), **{field: value})

    monkeypatch.setattr(s.provider, "generate_stage", malformed)
    with pytest.raises(DocumentAnalysisError):
        execute(s)
    run = s.repo.list_by_question(s.question.id)[0]
    assert run.status == "failed" and len(s.provider.calls) == 1
    assert s.repo.get_stages(run.id)[0].result_json is None
    assert b"PRIVATE_RESPONSE" not in s.db.path.read_bytes()


def test_new_history_tables_exclude_question_prompt_raw_source_and_key(setup_pipeline):
    s = setup_pipeline
    run = execute(s)
    with s.db._session() as session:
        saved = repr(
            [session.execute(select(table)).all() for table in (executions, chunks, stages, inputs)]
        )
    for sentinel in (
        QUESTION,
        TEXT,
        "PRIVATE_SOURCE_SENTINEL",
        "synthetic-api-secret",
        "Return only the requested",
    ):
        assert sentinel not in saved
    assert run.question_sha256 and run.content_sha256


def test_cost_and_token_preflight_exact_boundaries(setup_pipeline):
    s = setup_pipeline
    s.reader.text = "Synthetic short fulltext."
    baseline = execute(s)
    stage = s.repo.get_stages(baseline.id)[0]
    price = Pricing("USD", "synthetic-v1", Decimal("1.25"), Decimal("2.5"))
    max_cost = price.cost(stage.estimated_input_tokens, 4096) + price.cost(24000, 4096)
    tokens = stage.estimated_context_tokens + 32768
    exact = ExecutionBudget(max_calls=2, max_estimated_tokens=tokens, max_estimated_cost=max_cost)
    assert execute(s, execution_budget=exact, pricing=price).status == "completed"
    before = len(s.provider.calls)
    with pytest.raises(DocumentAnalysisError):
        execute(
            s,
            execution_budget=replace(exact, max_estimated_cost=max_cost - Decimal("0.00000001")),
            pricing=price,
        )
    assert len(s.provider.calls) == before
    with pytest.raises(DocumentAnalysisError):
        execute(s, execution_budget=replace(exact, max_estimated_tokens=tokens - 1), pricing=price)
    assert len(s.provider.calls) == before
