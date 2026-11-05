"""Negative runtime and persisted bindings must fail before a Cross model call."""

import json
from dataclasses import replace

import pytest
from sqlalchemy import delete, update

from research_workbench.analysis.chunking import ChunkingConfig
from research_workbench.analysis.cross_document_pipeline import CrossDocumentError
from research_workbench.analysis.stage_validation import validate_stage_output
from research_workbench.application.cross_document_analysis import CrossDocumentAnalysisService
from research_workbench.persistence.document_models import chunks, inputs, stages


def service(s):
    return CrossDocumentAnalysisService(s.db, s.reader, s.provider, chunking=ChunkingConfig(100, 0))


@pytest.mark.parametrize(
    "damage",
    [
        "reorder",
        "coordinated_reorder",
        "omit",
        "duplicate",
        "nested_document",
        "nested_cross",
        "map_child",
        "renamed_stage",
        "forged_result",
        "wrong_execution",
        "wrong_final_ordinal",
        "history_dto",
    ],
)
def test_repository_independently_rejects_illegal_runtime_topology(
    cross_setup, monkeypatch, damage
):
    s = cross_setup
    svc = service(s)
    original = svc._runs.start_cross

    def tamper(handle, prepared, request, outcomes, estimate):
        children = list(request.children)
        outcomes = list(outcomes)
        if damage == "reorder":
            children[0], children[1] = children[1], children[0]
        if damage == "coordinated_reorder":
            children[0], children[1] = children[1], children[0]
            outcomes[0], outcomes[1] = outcomes[1], outcomes[0]
        if damage == "omit":
            children.pop()
        if damage == "duplicate":
            children[1] = children[0]
        if damage == "nested_document":
            r = replace(children[0].request, children=(children[0],))
            children[0] = validate_stage_output(
                r, '{"status":"no_relevant_findings","findings":[]}'
            )
            outcomes[0] = replace(outcomes[0], result=children[0])
        if damage == "nested_cross":
            r = replace(request, documents=request.documents[:2], children=tuple(children[:2]))
            children[0] = validate_stage_output(
                r, '{"status":"no_relevant_findings","findings":[]}'
            )
            outcomes[0] = replace(outcomes[0], result=children[0])
        if damage == "map_child":
            children[0] = children[0].request.children[0]
            outcomes[0] = replace(outcomes[0], result=children[0])
        if damage == "renamed_stage":
            children[0] = replace(
                children[0], request=replace(children[0].request, stage="cross_document_reduce")
            )
        if damage == "forged_result":
            children[0] = replace(children[0], findings=())
        if damage == "wrong_execution":
            outcomes[0] = replace(outcomes[0], run=outcomes[1].run)
        if damage == "wrong_final_ordinal":
            outcomes[0] = replace(outcomes[0], final_stage_ordinal=2)
        if damage == "history_dto":
            from research_workbench.persistence.cross_document_history import CrossHistory

            children[0] = CrossHistory(99, 1, "cross-document-history-v1", "findings", "{}", ())
        original(
            handle, prepared, replace(request, children=tuple(children)), tuple(outcomes), estimate
        )

    monkeypatch.setattr(svc._runs, "start_cross", tamper)
    with pytest.raises(CrossDocumentError):
        svc.run_analysis(s.question.id, tuple(x.id for x in s.sources))
    assert len(s.provider.calls) == 9
    assert all(r.stage != "cross_document_reduce" for r in s.provider.calls)


@pytest.mark.parametrize(
    "damage",
    [
        "map_edge",
        "manifest",
        "map_result",
        "final_stage_type",
        "final_request_hash",
        "map_contract",
    ],
)
def test_all_persisted_maps_and_edges_checked_before_cross(cross_setup, damage):
    s = cross_setup

    def corrupt(request):
        if len(s.provider.calls) != 9:
            return
        run = s.repo.list_by_question(s.question.id)[0]
        b = s.repo.get_members(run.id)[0]
        with s.db._engine.begin() as c:
            if damage == "map_edge":
                c.execute(
                    delete(inputs).where(
                        inputs.c.execution_id == b.document_execution_id,
                        inputs.c.parent_ordinal == 1,
                    )
                )
            if damage == "manifest":
                c.execute(
                    update(chunks)
                    .where(chunks.c.execution_id == b.document_execution_id)
                    .values(content_sha256="f" * 64)
                )
            if damage == "map_result":
                c.execute(
                    update(stages)
                    .where(stages.c.execution_id == b.document_execution_id, stages.c.ordinal == 1)
                    .values(result_json='{"status":"no_relevant_findings","findings":[]}')
                )
            if damage == "final_stage_type":
                c.execute(
                    update(stages)
                    .where(
                        stages.c.execution_id == b.document_execution_id,
                        stages.c.ordinal == b.final_stage_ordinal,
                    )
                    .values(stage="map")
                )
            if damage == "final_request_hash":
                c.execute(
                    update(stages)
                    .where(
                        stages.c.execution_id == b.document_execution_id,
                        stages.c.ordinal == b.final_stage_ordinal,
                    )
                    .values(request_sha256="f" * 64)
                )
            if damage == "map_contract":
                c.execute(
                    update(stages)
                    .where(stages.c.execution_id == b.document_execution_id, stages.c.ordinal == 1)
                    .values(contract_version="wrong")
                )

    s.provider.callback = corrupt
    with pytest.raises(CrossDocumentError):
        service(s).run_analysis(s.question.id, tuple(x.id for x in s.sources))
    assert len(s.provider.calls) == 9


@pytest.mark.parametrize("change", ["question", "association", "identity"])
def test_t0_rechecks_preparation_changes_with_zero_calls(cross_setup, monkeypatch, change):
    s = cross_setup
    svc = service(s)
    prepare = svc._prepare

    def race(*args):
        prepared = prepare(*args)
        if change == "question":
            s.questions.update(s.question.id, description="Changed during planning")
        elif change == "association":
            s.links.detach(s.question.id, s.sources[0].id)
        else:
            from research_workbench.persistence.source_models import SourceRow

            with s.db._engine.begin() as c:
                c.execute(
                    update(SourceRow)
                    .where(SourceRow.id == s.sources[0].id)
                    .values(external_key="FAKE0099")
                )
        return prepared

    monkeypatch.setattr(svc, "_prepare", race)
    with pytest.raises(CrossDocumentError):
        svc.run_analysis(s.question.id, tuple(x.id for x in s.sources))
    assert s.provider.calls == [] and s.repo.list_by_question(s.question.id) == ()


def test_post_registration_question_detach_and_reader_edits_do_not_mix_snapshot(cross_setup):
    s = cross_setup

    def edit(request):
        if len(s.provider.calls) == 1:
            s.questions.update(
                s.question.id, title="Edited after registration", description="Changed"
            )
            for src in s.sources:
                s.links.detach(s.question.id, src.id)
            s.reader.texts["FAKE0002"] = "new source text"

    s.provider.callback = edit
    result = service(s).run_analysis(s.question.id, tuple(x.id for x in s.sources))
    assert result.status == "completed"
    assert all(
        json.loads(r.question) == {"title": "Compare?", "description": "Scope"}
        for r in s.provider.calls
    )
    assert s.repo.get_history(result.id).status == "findings"


def test_same_source_and_finding_ids_cannot_borrow_prior_execution(cross_setup, monkeypatch):
    from research_workbench.persistence.document_repository import DocumentAnalysisRepository

    s = cross_setup
    first = service(s).run_analysis(s.question.id, tuple(x.id for x in s.sources))
    old = DocumentAnalysisRepository(s.db).get(
        s.repo.get_members(first.id)[0].document_execution_id
    )
    s.provider.calls.clear()
    svc = service(s)
    original = svc._runs.start_cross

    def borrow(handle, prepared, request, outcomes, estimate):
        outcomes = (replace(outcomes[0], run=old), *outcomes[1:])
        return original(handle, prepared, request, outcomes, estimate)

    monkeypatch.setattr(svc._runs, "start_cross", borrow)
    with pytest.raises(CrossDocumentError):
        svc.run_analysis(s.question.id, tuple(x.id for x in s.sources))
    assert len(s.provider.calls) == 9
    assert s.repo.get_history(first.id).status == "findings"
