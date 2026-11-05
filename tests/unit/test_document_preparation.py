"""Preparation never registers history or calls the provider."""

from test_document_analysis import setup_pipeline  # noqa: F401

from research_workbench.analysis.chunking import SourceDocument, SourceIdentity
from research_workbench.analysis.document_pipeline import render_document_question


def test_prepare_then_register_then_execute(setup_pipeline):  # noqa: F811
    s = setup_pipeline
    service = s.service()
    doc = SourceDocument(SourceIdentity("zotero", "personal", "FAKE0001"), s.reader.text)
    prepared = service.prepare_document(render_document_question(s.question.title, ""), doc)
    assert s.repo.list_by_question(s.question.id) == ()
    assert s.provider.calls == [] and s.reader.reads == []
    run = service.register_document(s.question.id, s.source.id, prepared)
    outcome = service.execute_registered(run, prepared)
    assert outcome.run.status == "completed"
    assert outcome.final_stage_ordinal == len(prepared.requests) + 1
    assert outcome.result.request.stage == "document_reduce"
