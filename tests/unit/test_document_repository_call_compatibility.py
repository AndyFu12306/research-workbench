"""Original positional/keyword APIs must preserve parent ownership protection."""

import inspect

import pytest

from research_workbench.analysis.cross_document_pipeline import CrossConflict
from research_workbench.analysis.document_pipeline import ExecutionBudget
from research_workbench.analysis.stage_validation import validate_stage_output
from research_workbench.persistence.document_repository import DocumentAnalysisRepository


@pytest.mark.parametrize("method", ["start_stage", "complete_stage", "fail_run"])
@pytest.mark.parametrize("style", ["positional", "keyword", "mixed"])
@pytest.mark.parametrize("parent_owned", [False, True])
def test_original_call_styles_and_parent_protection(cross_setup, method, style, parent_owned):
    s = cross_setup
    repo = DocumentAnalysisRepository(s.db)
    prepared = s.prepared.documents[0]
    request, estimate = prepared.requests[0], prepared.estimates[0]
    if parent_owned:
        handle = s.repo.register(s.prepared)
        run = repo.get(handle.bindings[0].document_execution_id)
    else:
        run = repo.create_run(
            s.question.id,
            s.sources[0].id,
            prepared.plan,
            provider="fake",
            model="synthetic",
            stage_budget=s.prepared.stage_budget,
            execution_budget=ExecutionBudget(),
            pricing=None,
            estimates=prepared.estimates,
        )
    if method != "start_stage":
        if parent_owned:
            s.repo.start_document_stage(handle, s.prepared, 1, 1, request, estimate)
        else:
            repo.start_stage(run.id, 1, request, estimate)
    metadata = s.provider.generate_stage(request)
    result = validate_stage_output(request, metadata.output_json)
    if method == "start_stage":
        arguments = dict(execution_id=run.id, ordinal=1, request=request, estimate=estimate)
        options = {}
    elif method == "complete_stage":
        arguments = dict(execution_id=run.id, ordinal=1, result=result, metadata=metadata)
        options = {}
    else:
        arguments = dict(execution_id=run.id, code="interrupted")
        options = dict(interrupted=True, metadata=metadata, response_received=True)

    def invoke():
        target = getattr(repo, method)
        if style == "keyword":
            return target(**arguments, **options)
        if style == "mixed":
            remaining = {k: v for k, v in arguments.items() if k != "execution_id"}
            return target(run.id, **remaining, **options)
        return target(*arguments.values(), **options)

    before = repo.get_stages(run.id)
    if parent_owned:
        with pytest.raises(CrossConflict):
            invoke()
        assert repo.get_stages(run.id) == before
        assert repo.get(run.id) == run
        assert s.repo.get(handle.run_id).status == "running"
    else:
        returned = invoke()
        stage = repo.get_stages(run.id)[0]
        if method == "start_stage":
            assert returned is None and stage.status == "running"
        else:
            assert returned == repo.get(run.id)
            assert stage.status == ("completed" if method == "complete_stage" else "interrupted")
            assert stage.call_outcome == "response_received"
            assert (stage.input_tokens, stage.output_tokens) == (100, 40)
            assert returned.status == ("running" if method == "complete_stage" else "interrupted")


@pytest.mark.parametrize(
    "method,names",
    [
        ("start_stage", ("self", "execution_id", "ordinal", "request", "estimate")),
        ("complete_stage", ("self", "execution_id", "ordinal", "result", "metadata")),
        (
            "fail_run",
            ("self", "execution_id", "code", "interrupted", "metadata", "response_received"),
        ),
    ],
)
def test_public_signatures_remain_explicit(method, names):
    parameters = inspect.signature(getattr(DocumentAnalysisRepository, method)).parameters
    assert tuple(parameters) == names
    for name, parameter in parameters.items():
        expected = (
            inspect.Parameter.KEYWORD_ONLY
            if method == "fail_run" and name in ("interrupted", "metadata", "response_received")
            else inspect.Parameter.POSITIONAL_OR_KEYWORD
        )
        assert parameter.kind == expected
