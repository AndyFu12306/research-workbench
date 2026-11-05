"""Synthetic sentinels only. Histories must contain no runtime inputs or raw candidates."""

import logging

import pytest
from sqlalchemy import select
from test_cross_document_analysis import execute
from test_cross_document_gates import service

from research_workbench.analysis.cross_document_pipeline import CrossDocumentError
from research_workbench.persistence.cross_document_models import (
    cross_inputs,
    cross_stages,
    members,
    runs,
)
from research_workbench.persistence.document_models import chunks, executions, inputs, stages


@pytest.mark.parametrize("invalid", [False, True])
def test_sources_questions_and_raw_responses_never_enter_analysis_history_or_logs(
    cross_setup, caplog, invalid
):
    s = cross_setup
    s.reader.texts["FAKE0001"] = "SOURCE_PRIVATE_SENTINEL unique fulltext"
    s.questions.update(s.question.id, description="QUESTION_PRIVATE_SENTINEL")
    if invalid:
        s.provider.invalid_at = 4
    caplog.set_level(logging.DEBUG)
    if invalid:
        with pytest.raises(CrossDocumentError) as e:
            execute(s)
        assert e.value.__suppress_context__
    else:
        run = execute(s)
        assert "SOURCE_PRIVATE_SENTINEL" not in repr(s.repo.get_history(run.id))
    with s.db._engine.connect() as c:
        persisted = repr(
            [
                c.execute(select(table)).all()
                for table in (
                    runs,
                    members,
                    cross_stages,
                    cross_inputs,
                    executions,
                    chunks,
                    stages,
                    inputs,
                )
            ]
        )
    for private in ("SOURCE_PRIVATE_SENTINEL", "QUESTION_PRIVATE_SENTINEL", "RAW_INVALID_SENTINEL"):
        assert private not in persisted + caplog.text
    p = service(s)._prepare(s.question.id, tuple(x.id for x in s.sources))
    assert "SOURCE_PRIVATE_SENTINEL" not in repr(p) + repr(p.selection) + repr(p.documents)


def test_reader_exception_and_raw_database_parameters_are_suppressed(
    cross_setup, monkeypatch, caplog
):
    s = cross_setup

    def fail(_):
        raise RuntimeError("PRIVATE_READER_SENTINEL synthetic-credential")

    monkeypatch.setattr(s.reader, "get_fulltext", fail)
    with pytest.raises(CrossDocumentError) as e:
        execute(s)
    assert e.value.__suppress_context__
    assert "PRIVATE_READER_SENTINEL" not in str(e.value) + repr(e.value) + caplog.text
    assert s.provider.calls == [] and s.repo.list_by_question(s.question.id) == ()
