"""Entirely synthetic cross-document support, no private library or API data."""

from types import SimpleNamespace

from test_document_analysis import FakeStageProvider

from research_workbench.analysis.chunking import ChunkingConfig, SourceDocument, SourceIdentity
from research_workbench.analysis.cross_document_pipeline import (
    CrossBudget,
    FrozenSelection,
    PreparedCross,
    allocate_budget,
)
from research_workbench.analysis.document_pipeline import render_document_question
from research_workbench.application.document_analysis import DocumentAnalysisService
from research_workbench.integrations.zotero.models import ZoteroFulltext
from research_workbench.persistence.cross_document_repository import CrossDocumentRepository
from research_workbench.persistence.models import utc_now


class CrossReader:
    def __init__(self):
        self.texts = {"FAKE0001": "a" * 180, "FAKE0002": "b" * 260, "FAKE0003": "c" * 80}
        self.reads = []

    def health_check(self):
        return SimpleNamespace(status="healthy")

    def get_fulltext(self, key):
        self.reads.append(key)
        return ZoteroFulltext(key, self.texts[key], 1)


def make_cross(database, projects, questions, links):
    q = questions.create(
        projects.create("Synthetic cross project").id, "Compare?", description="Scope"
    )
    sources = tuple(
        links.attach_reference(q.id, key).source for key in ("FAKE0001", "FAKE0002", "FAKE0003")
    )
    reader, provider = CrossReader(), FakeStageProvider()
    service = DocumentAnalysisService(database, reader, provider, chunking=ChunkingConfig(100, 0))
    question = render_document_question(q.title, q.description)
    documents = tuple(
        SourceDocument(
            SourceIdentity(s.provider, s.scope, s.external_key), reader.texts[s.external_key]
        )
        for s in sources
    )
    selection = FrozenSelection(
        q.id,
        tuple(s.id for s in sources),
        question,
        documents,
        tuple(utc_now() for _ in sources),
        provider.provider,
        provider.model,
    )
    prepared_docs = tuple(service.prepare_document(question, d) for d in documents)
    budget = CrossBudget()
    allocation = allocate_budget(
        tuple(p.estimates for p in prepared_docs), service._stage_budget, budget
    )
    prepared = PreparedCross(
        selection, prepared_docs, allocation, service._stage_budget, budget, None
    )
    return SimpleNamespace(
        db=database,
        question=q,
        sources=sources,
        reader=reader,
        provider=provider,
        prepared=prepared,
        repo=CrossDocumentRepository(database),
        links=links,
        questions=questions,
    )
