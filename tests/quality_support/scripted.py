"""Test-only scripts observed at real calls. No SDK client or real Zotero instance."""

from dataclasses import dataclass, field
from types import SimpleNamespace

from research_workbench.analysis.chunking import ChunkingConfig, SourceDocument, SourceIdentity
from research_workbench.analysis.contracts import ProviderResult
from research_workbench.analysis.cross_document_pipeline import CrossBudget, allocate_budget
from research_workbench.analysis.document_pipeline import RequestEstimate, render_document_question
from research_workbench.analysis.stage_contracts import (
    CONTRACT_VERSIONS,
    StageBudget,
    StageBudgetError,
    StageRequest,
)
from research_workbench.analysis.stage_validation import render_stage_input, validate_stage_output
from research_workbench.integrations.ai.stages import prepare_stage_call
from research_workbench.integrations.zotero.models import ZoteroFulltext

from .contracts import Observation, require
from .loading import decode, digest, encode, text_hash
from .validation import chunks_for, identifier, validate_candidate, validate_case


@dataclass(frozen=True, slots=True)
class ObservedRun:
    run_alias: str
    observations: tuple[Observation, ...] = field(repr=False)
    mapping: tuple[tuple[str, str, str], ...]
    config_json: str = field(repr=False)
    terminal: str
    case_sha256: str
    candidate_sha256: str
    metadata_json: str = field(default="{}", repr=False)


class SyntheticReader:
    def __init__(self, case):
        self.sources = {s.identity[2]: s for s in case.sources}
        self.reads = []

    def health_check(self):
        return SimpleNamespace(status="healthy")

    def get_fulltext(self, key):
        self.reads.append(key)
        return ZoteroFulltext(key, self.sources[key].text, 1)


class ObservingProvider:
    provider = "fake"
    model = "synthetic-quality-v1"
    capabilities = frozenset(CONTRACT_VERSIONS.items())

    def __init__(self, case, candidate, *, transform=None):
        validate_case(case)
        validate_candidate(case, candidate)
        self.case, self.candidate, self.transform = case, candidate, transform
        # Freeze observation provenance before execution, not when attaching a score.
        self.case_sha256, self.candidate_sha256 = digest(case), digest(candidate)
        self.observations, self.mapping, self.requests, self.results = [], [], [], []
        self.alias_to_id = {}
        self.id_to_alias = {}

    def estimate_stage(self, request):
        _, estimate = prepare_stage_call(request, model=self.model, provider="openai")
        return RequestEstimate(
            estimate.input_tokens, estimate.context_tokens, estimate.estimator_version
        )

    def stage_alias(self, request):
        if request.stage == "cross_document_reduce":
            return "cross"
        identity = request.documents[0].identity
        source = next(
            s.alias
            for s in self.case.sources
            if s.identity == (identity.provider, identity.scope, identity.external_key)
        )
        return (
            f"{source}/map/{request.chunks[0].ordinal}"
            if request.stage == "map"
            else f"{source}/document"
        )

    def generate_stage(self, request):
        alias = self.stage_alias(request)
        script = next(s for s in self.candidate.stages if s.alias == alias)
        local = dict(self.alias_to_id)
        if request.stage == "map":
            source = alias.split("/")[0]
            local[f"{source}/chunk/{request.chunks[0].ordinal}"] = request.chunks[0].chunk_id
        output = {
            "status": "findings" if script.findings else "no_relevant_findings",
            "findings": [
                {
                    "text": f.text,
                    "kind": f.kind,
                    "category": f.category,
                    "citations": [local[r] for r in f.citations],
                }
                for f in script.findings
            ],
        }
        if self.transform:
            output = self.transform(alias, output)
        raw = encode(output)
        # Actual return is validated independently of the script's expected prose/labels.
        result = validate_stage_output(request, raw)
        projected = []
        for ordinal, f in enumerate(result.findings, 1):
            finding_alias = f"{alias}/f{ordinal}"
            self.alias_to_id[finding_alias] = f.finding_id
            self.id_to_alias[(alias, f.finding_id)] = finding_alias
            self.mapping.append((alias, finding_alias, f.finding_id))
            projected.append(
                {
                    "alias": finding_alias,
                    "finding_id": f.finding_id,
                    "text": f.text,
                    "kind": f.kind,
                    "category": f.category,
                    "citations": list(f.citations),
                    "chunks": [
                        {
                            "identity": [
                                c.identity.provider,
                                c.identity.scope,
                                c.identity.external_key,
                            ],
                            "source_fingerprint": c.source_fingerprint,
                            "chunk_id": c.chunk_id,
                            "start": c.start,
                            "end": c.end,
                            "content_sha256": c.content_sha256,
                        }
                        for c in f.chunks
                    ],
                }
            )
        projection = {
            "stage": alias,
            "status": result.status,
            "findings": projected,
            "inputs": [
                {
                    "stage": self.stage_alias(c.request),
                    "findings": [f.finding_id for f in c.findings],
                }
                for c in request.children
            ],
        }
        payload, _ = prepare_stage_call(request, model=self.model, provider="openai")
        actual_input = {
            "rendered": render_stage_input(request),
            "final_payload": payload,
            "budget": request.budget,
            "chunking": request.chunking,
            "documents": [
                (
                    d.identity.provider,
                    d.identity.scope,
                    d.identity.external_key,
                    d.source_fingerprint,
                )
                for d in request.documents
            ],
        }
        self.observations.append(
            Observation(
                alias,
                digest(actual_input),
                text_hash(raw),
                text_hash(result.output_json),
                encode(projection),
            )
        )
        self.requests.append(request)
        self.results.append(result)
        return ProviderResult(raw, f"synthetic-{len(self.observations)}", 100, 40)

    def snapshot(self, run_alias, terminal):
        identifier(run_alias)
        config = {
            "max_chars": self.case.max_chars,
            "overlap_chars": self.case.overlap_chars,
            "max_calls": self.candidate.max_calls,
            "stage_input_limit": self.candidate.stage_input_limit,
            "contracts": CONTRACT_VERSIONS,
            "provider": self.provider,
            "model": self.model,
        }
        from research_workbench.analysis.stage_contracts import PROMPT_VERSIONS

        config["prompts"] = PROMPT_VERSIONS
        config["actual_stage_configs"] = [
            {
                "stage": self.stage_alias(r),
                "budget": r.budget,
                "chunking": r.chunking,
                "contract": r.contract_version,
            }
            for r in self.requests
        ]
        return ObservedRun(
            run_alias,
            tuple(self.observations),
            tuple(self.mapping),
            encode(config),
            terminal,
            self.case_sha256,
            self.candidate_sha256,
        )


def run_memory(case, candidate, run_alias, *, transform=None):
    """Small no-DB execution using the same chunker, estimator, validator and allocation."""
    provider = ObservingProvider(case, candidate, transform=transform)
    documents = tuple(SourceDocument(SourceIdentity(*s.identity), s.text) for s in case.sources)
    chunks = chunks_for(case)
    budget = StageBudget(max_input_tokens=candidate.stage_input_limit)
    question = render_document_question(case.question.title, case.question.description)
    map_requests = tuple(
        tuple(
            StageRequest(
                "map",
                CONTRACT_VERSIONS["map"],
                question,
                (doc,),
                budget,
                (c,),
                ChunkingConfig(case.max_chars, case.overlap_chars),
            )
            for c in chunks[s.alias]
        )
        for s, doc in zip(case.sources, documents, strict=True)
    )
    estimates = tuple(tuple(provider.estimate_stage(r) for r in group) for group in map_requests)
    try:
        allocate_budget(estimates, budget, CrossBudget(max_calls=candidate.max_calls))
    except ValueError:
        return provider.snapshot(run_alias, "preflight_rejected")
    reduced = []
    for doc, group in zip(documents, map_requests, strict=True):
        children = tuple(
            validate_stage_output(r, provider.generate_stage(r).output_json) for r in group
        )
        request = StageRequest(
            "document_reduce",
            CONTRACT_VERSIONS["document_reduce"],
            question,
            (doc,),
            budget,
            chunking=ChunkingConfig(case.max_chars, case.overlap_chars),
            children=children,
        )
        provider.estimate_stage(request)
        reduced.append(validate_stage_output(request, provider.generate_stage(request).output_json))
    request = StageRequest(
        "cross_document_reduce",
        CONTRACT_VERSIONS["cross_document_reduce"],
        question,
        documents,
        budget,
        chunking=ChunkingConfig(case.max_chars, case.overlap_chars),
        children=tuple(reduced),
    )
    try:
        provider.estimate_stage(request)
    except StageBudgetError:
        return provider.snapshot(run_alias, "cross_rejected")
    provider.generate_stage(request)
    return provider.snapshot(run_alias, "completed")


def actual_findings(run):
    return {
        f["alias"]: (o.stage, f)
        for o in run.observations
        for f in decode(o.projection_json)["findings"]
    }


def check_history(provider, repository, documents, run_id):
    """Cross strict history reader plus every persisted child stage must agree."""
    history = decode(repository.get_history(run_id).result_json)
    cross_result = next(r for r in provider.results if r.request.stage == "cross_document_reduce")
    require(
        [f["finding_id"] for f in history["findings"]]
        == [f.finding_id for f in cross_result.findings]
    )
    check_child_history(provider, repository, documents, run_id)
    return digest(history)


def check_child_history(provider, repository, documents, run_id):
    """Also usable after Cross rejection, when no final Cross result exists."""
    from research_workbench.persistence.document_repository import history_result

    for member in repository.get_members(run_id):
        source = provider.case.sources[member.ordinal - 1]
        for stage in documents.get_stages(member.document_execution_id):
            alias = (
                f"{source.alias}/map/{stage.ordinal}"
                if stage.stage == "map"
                else f"{source.alias}/document"
            )
            index = next(i for i, o in enumerate(provider.observations) if o.stage == alias)
            result = provider.results[index]
            ordinals = tuple(range(1, len(result.request.children) + 1))
            require(decode(stage.result_json) == decode(history_result(result, ordinals)))
