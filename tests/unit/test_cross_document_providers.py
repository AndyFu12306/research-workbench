"""Full synthetic pipeline through both real SDKs, HTTP entirely MockTransport."""

import json

import pytest
from test_stage_providers import wire  # noqa: F401

from research_workbench.analysis.chunking import ChunkingConfig
from research_workbench.analysis.cross_document_pipeline import CrossDocumentError
from research_workbench.application.cross_document_analysis import CrossDocumentAnalysisService
from research_workbench.integrations.ai.stages import prepare_stage_call


@pytest.mark.parametrize("failure", [None, "illegal_reference", "refusal", "incomplete"])
def test_sdk_final_payload_matches_estimates_across_whole_pipeline(cross_setup, wire, failure):  # noqa: F811
    s = cross_setup
    provider = wire["instance"]
    original = provider.generate_stage
    expected_calls = []

    def generate(request):
        expected, estimate = prepare_stage_call(
            request, model=provider.model, provider=wire["provider"]
        )
        assert provider.estimate_stage(request).input_tokens == estimate.input_tokens
        expected_calls.append(expected)
        citations = (
            [request.chunks[0].chunk_id]
            if request.stage == "map"
            else [f.finding_id for child in request.children for f in child.findings][:1]
        )
        if failure == "illegal_reference" and request.stage == "cross_document_reduce":
            citations = ["f" * 64]
        wire["body"]["output"][0]["content"][0]["text"] = json.dumps(
            {
                "status": "findings",
                "findings": [
                    {
                        "text": "Synthetic SDK finding",
                        "kind": "ai_inference",
                        "category": "finding",
                        "citations": citations,
                    }
                ],
            }
        )
        if request.stage == "cross_document_reduce":
            if failure == "refusal":
                wire["body"]["output"][0]["content"] = [
                    {"type": "refusal", "refusal": "Synthetic refusal"}
                ]
            if failure == "incomplete":
                wire["body"]["status"] = "incomplete"
                wire["body"]["incomplete_details"] = {"reason": "max_output_tokens"}
        return original(request)

    provider.generate_stage = generate
    svc = CrossDocumentAnalysisService(s.db, s.reader, provider, chunking=ChunkingConfig(100, 0))
    if failure:
        with pytest.raises(CrossDocumentError):
            svc.run_analysis(s.question.id, tuple(x.id for x in s.sources))
        assert s.repo.list_by_question(s.question.id)[0].status == "failed"
    else:
        result = svc.run_analysis(s.question.id, tuple(x.id for x in s.sources))
        assert s.repo.get_history(result.id).status == "findings"
    assert len(wire["calls"]) == 10
    assert wire["calls"] == expected_calls
