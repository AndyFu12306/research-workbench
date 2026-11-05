"""Measured synthetic bounds. Warnings are observations, never billing guarantees."""

import json
import time
import tracemalloc
from contextlib import contextmanager

import pytest
from sqlalchemy import event

from research_workbench.analysis.chunking import ChunkingConfig
from research_workbench.analysis.contracts import ProviderResult
from research_workbench.analysis.cross_document_pipeline import CrossBudget, CrossDocumentError
from research_workbench.analysis.stage_contracts import StageBudget
from research_workbench.application.cross_document_analysis import CrossDocumentAnalysisService


def prepare_five(s, counts, width, *, chinese=False):
    sources = list(s.sources)
    for key in ("FAKE0004", "FAKE0005"):
        sources.append(s.links.attach_reference(s.question.id, key).source)
    for i, (source, count) in enumerate(zip(sources, counts, strict=True)):
        unit = "中文😀" if chinese else chr(65 + i)
        s.reader.texts[source.external_key] = (str(i) + unit * (count * width))[: count * width]
    return tuple(x.id for x in sources)


@contextmanager
def measure_maximum(s, monkeypatch, profile):
    """Additional observations for maximal findings and maximal closure cases."""
    from research_workbench.analysis import planning, stage_validation
    from research_workbench.persistence import document_repository

    counters = {"sql": 0, "chunking": 0}
    for module in (planning, stage_validation, document_repository):
        original = module.chunk_document

        def counted(*args, _original=original, **kwargs):
            counters["chunking"] += 1
            return _original(*args, **kwargs)

        monkeypatch.setattr(module, "chunk_document", counted)

    def sql(*args):
        counters["sql"] += 1

    event.listen(s.db._engine, "before_cursor_execute", sql)
    tracemalloc.start()
    start = time.perf_counter()
    try:
        yield
    finally:
        elapsed = time.perf_counter() - start
        _, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        event.remove(s.db._engine, "before_cursor_execute", sql)
        print(
            "MAX_PERFORMANCE",
            json.dumps(
                {
                    "profile": profile,
                    "seconds": round(elapsed, 3),
                    "peak_mib": round(peak / (1024 * 1024), 3),
                    **counters,
                    "alert_60s_or_512mib": elapsed > 60 or peak > 512 * 1024 * 1024,
                }
            ),
        )


@pytest.mark.parametrize(
    "profile,counts,chinese,empty",
    [
        ("balanced-empty", (20, 20, 20, 20, 20), False, True),
        ("skewed-sparse", (96, 1, 1, 1, 1), False, False),
        ("unicode-empty", (20, 20, 20, 20, 20), True, True),
    ],
)
def test_100_chunk_capacity_profile(cross_setup, monkeypatch, profile, counts, chinese, empty):
    s = cross_setup
    selected = prepare_five(s, counts, 1000, chinese=chinese)
    s.provider.empty = set(range(1, 107)) if empty else set()
    counters = {"sql": 0, "chunking": 0}

    def sql(*args):
        counters["sql"] += 1

    from research_workbench.analysis import planning, stage_validation
    from research_workbench.persistence import document_repository

    for module in (planning, stage_validation, document_repository):
        original = module.chunk_document

        def counted(*args, _original=original, **kwargs):
            counters["chunking"] += 1
            return _original(*args, **kwargs)

        monkeypatch.setattr(module, "chunk_document", counted)

    svc = CrossDocumentAnalysisService(
        s.db,
        s.reader,
        s.provider,
        chunking=ChunkingConfig(1000, 0),
        cross_budget=CrossBudget(max_estimated_tokens=10_000_000),
    )
    event.listen(s.db._engine, "before_cursor_execute", sql)
    tracemalloc.start()
    start = time.perf_counter()
    try:
        if profile == "skewed-sparse":
            with pytest.raises(CrossDocumentError) as caught:
                svc.run_analysis(s.question.id, selected)
            assert caught.value.code == "budget_exceeded"
            run = s.repo.list_by_question(s.question.id)[0]
            assert run.status == "failed" and len(s.provider.calls) == 96
            from research_workbench.persistence.document_repository import (
                DocumentAnalysisRepository,
            )

            repo = DocumentAnalysisRepository(s.db)
            member = s.repo.get_members(run.id)[0]
            history = repo.get_stages(member.document_execution_id)
            assert all(stage.status == "completed" for stage in history[:-1])
            assert history[-1].status == "not_started"
            assert all(request.stage == "map" for request in s.provider.calls)
        else:
            run = svc.run_analysis(s.question.id, selected)
            assert s.repo.get_history(run.id).status == (
                "no_relevant_findings" if empty else "findings"
            )
            assert run.status == "completed" and len(s.provider.calls) == 106
        elapsed = time.perf_counter() - start
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
        event.remove(s.db._engine, "before_cursor_execute", sql)
    assert len(s.reader.reads) == 5
    print(
        "CAPACITY",
        json.dumps(
            {
                "profile": profile,
                "status": run.status,
                "seconds": round(elapsed, 3),
                "peak_mib": round(peak / (1024 * 1024), 3),
                **counters,
                "alert_60s_or_512mib": elapsed > 60 or peak > 512 * 1024 * 1024,
            }
        ),
    )


def test_maximum_document_findings_cross_capacity_rejects_without_truncation(
    cross_setup, monkeypatch
):
    s = cross_setup
    selected = prepare_five(s, (1, 1, 1, 1, 1), 100)
    original = s.provider.generate_stage

    def generate(request):
        result = original(request)
        if request.stage == "document_reduce":
            data = json.loads(result.output_json)
            finding = data["findings"][0]
            data["findings"] = [{**finding, "text": str(i) + "x" * 1998} for i in range(12)]
            return ProviderResult(
                json.dumps(data), result.response_id, result.input_tokens, result.output_tokens
            )
        return result

    s.provider.generate_stage = generate
    svc = CrossDocumentAnalysisService(s.db, s.reader, s.provider, chunking=ChunkingConfig(100, 0))
    with (
        measure_maximum(s, monkeypatch, "60-large-document-findings"),
        pytest.raises(CrossDocumentError) as e,
    ):
        svc.run_analysis(s.question.id, selected)
    assert e.value.code == "budget_exceeded"
    assert len(s.provider.calls) == 10 and all(
        r.stage != "cross_document_reduce" for r in s.provider.calls
    )
    from research_workbench.persistence.document_repository import DocumentAnalysisRepository

    repo = DocumentAnalysisRepository(s.db)
    assert all(
        repo.get(m.document_execution_id).status == "completed"
        for m in s.repo.get_members(s.repo.list_by_question(s.question.id)[0].id)
    )


def test_maximum_citation_closure_history_can_exceed_provider_json_limit(cross_setup, monkeypatch):
    s = cross_setup
    selected = prepare_five(s, (20, 20, 20, 20, 20), 100)
    original = s.provider.generate_stage

    def generate(request):
        result = original(request)
        if request.stage == "map":
            return result
        if request.stage == "document_reduce":
            citations = [child.findings[0].finding_id for child in request.children]
        else:
            citations = [f.finding_id for child in request.children for f in child.findings[:4]]
        assert len(citations) == 20
        output = {
            "status": "findings",
            "findings": [
                {
                    "text": f"Generated bounded synthesis {i}",
                    "kind": "ai_inference",
                    "category": "finding",
                    "citations": citations,
                }
                for i in range(12)
            ],
        }
        return ProviderResult(
            json.dumps(output), result.response_id, result.input_tokens, result.output_tokens
        )

    s.provider.generate_stage = generate
    svc = CrossDocumentAnalysisService(
        s.db,
        s.reader,
        s.provider,
        chunking=ChunkingConfig(100, 0),
        stage_budget=StageBudget(context_tokens=210000, max_input_tokens=200000),
        cross_budget=CrossBudget(max_estimated_tokens=10_000_000),
    )
    with measure_maximum(s, monkeypatch, "12-findings-20-citations-100-chunks"):
        run = svc.run_analysis(s.question.id, selected)
        history = s.repo.get_history(run.id)
    data = json.loads(history.result_json)
    assert len(history.result_json) > 100000
    assert len(data["findings"]) == 12
    assert all(len(f["citations"]) == 20 and len(f["chunk_refs"]) == 100 for f in data["findings"])
    assert history.result_json == s.repo.get_history(run.id).result_json
    print("MAX_HISTORY", len(history.result_json), "characters")
