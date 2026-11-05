"""User-run real API tests. No keys, raw prompts/responses or source text in reports."""

import hashlib
import json
import os
from dataclasses import replace
from importlib.metadata import version

import pytest
from analysis_fakes import FakeZoteroClient
from deepseek_validation_support import consented_zotero_item, require_deepseek_config
from sqlalchemy import inspect

from research_workbench.analysis.contracts import PROMPT_VERSION, SCHEMA_VERSION
from research_workbench.analysis.corpus import ResearchCorpusBuilder
from research_workbench.analysis.errors import AnalysisStateError
from research_workbench.application.analysis import AnalysisService
from research_workbench.application.source_links import QuestionSourceService
from research_workbench.integrations.ai.deepseek import DeepSeekAnalysisProvider
from research_workbench.integrations.zotero import ZoteroClient
from research_workbench.persistence import (
    Database,
    ProjectRepository,
    QuestionRepository,
    QuestionSourceRepository,
)
from research_workbench.persistence.analysis_repository import AnalysisRepository

pytestmark = pytest.mark.deepseek_integration


def _check(condition, message):
    if not condition:
        pytest.fail(message, pytrace=False)


class ObservedProvider:
    """Ephemeral observations for verifying hashes/usage; never dump request/result."""

    def __init__(self, provider):
        self.provider = provider.provider
        self.model = provider.model
        self.inner = provider
        self.request = None
        self.result = None
        self.calls = 0

    def generate(self, request):
        self.calls += 1
        self.request = request
        self.result = self.inner.generate(request)
        return self.result


def _verify(db, run, observed, abstract, source_id, level):
    repo = AnalysisRepository(db)
    _check(observed.calls == 1, "Expected exactly one provider invocation")
    _check(
        run.status == "completed" and repo.get(run.id) == run, "Analysis history did not complete"
    )
    _check(
        run.provider == "deepseek" and run.model == os.environ["DEEPSEEK_MODEL"].strip(),
        "Provider identity/model was not persisted correctly",
    )
    _check(
        run.prompt_version == PROMPT_VERSION and run.schema_version == SCHEMA_VERSION,
        "Prompt/schema version mismatch",
    )
    before = repo.get(run.id)
    with pytest.raises(AnalysisStateError):
        repo.fail_run(run.id, "analysis_failed")
    with pytest.raises(AnalysisStateError):
        repo.complete_run(run.id, run.result_json)
    _check(repo.get(run.id) == before, "Terminal history changed")
    result = json.loads(run.result_json)
    points = [result["summary"]] + [
        p
        for name in (
            "main_approaches",
            "key_findings",
            "agreements",
            "disagreements",
            "research_gaps",
        )
        for p in result[name]
    ]
    _check(all(p["source_ids"] == [source_id] for p in points), "Invalid source attribution")
    manifest = repo.get_sources(run.id)
    _check(
        len(manifest) == 1
        and manifest[0].source_id == source_id
        and manifest[0].input_kind == "abstract"
        and manifest[0].ordinal == 1,
        "Source manifest did not match the one-abstract input",
    )
    content = observed.request.sources[0].content
    _check(
        manifest[0].content_sha256 == hashlib.sha256(content.encode("utf-8")).hexdigest()
        and manifest[0].input_chars == len(content),
        "Input fingerprint mismatch",
    )
    _check(
        (run.input_tokens, run.output_tokens, run.provider_response_id)
        == (
            observed.result.input_tokens,
            observed.result.output_tokens,
            observed.result.response_id,
        ),
        "Provider metadata normalization was not preserved",
    )
    columns = {c["name"] for c in inspect(db._engine).get_columns("analysis_source_inputs")}
    _check(
        columns
        == {"analysis_id", "source_id", "ordinal", "input_kind", "content_sha256", "input_chars"},
        "Unexpected raw-content storage column",
    )
    stored = db.path.read_bytes()
    _check(abstract.encode("utf-8") not in stored, "Raw abstract found in temporary database")
    _check(
        content.encode("utf-8") not in stored
        and observed.request.render_input().encode("utf-8") not in stored,
        "Raw content or complete prompt found in temporary database",
    )
    _check(
        os.environ["DEEPSEEK_API_KEY"].encode("utf-8") not in stored,
        "Credential found in temporary database",
    )
    # Only allowlisted operational metadata is printed. UUID value is not needed in a report.
    print(
        json.dumps(
            {
                "level": level,
                "provider": run.provider,
                "model": run.model,
                "sdk_version": version("openai"),
                "status": run.status,
                "input_tokens": run.input_tokens,
                "output_tokens": run.output_tokens,
                "response_id_present": run.provider_response_id is not None,
                "prompt_version": run.prompt_version,
                "schema_version": run.schema_version,
                "api": "Responses",
                "max_output_tokens": 4096,
                "max_retries": 0,
                "provider_calls": observed.calls,
                "immutable_history_verified": True,
                "manifest_verified": True,
                "security_check": "PASS",
            }
        )
    )


@pytest.fixture(scope="module")
def synthetic_validation(tmp_path_factory):
    require_deepseek_config(os.environ)
    reader = FakeZoteroClient()
    reader.item = replace(
        reader.item,
        abstract=(
            "A synthetic pilot study compared two memory methods on ten toy tasks. "
            "Method A solved six tasks and method B solved five. No repeated trials or "
            "significance tests were reported. Generalization remains untested."
        ),
    )
    with Database(tmp_path_factory.mktemp("deepseek-synthetic") / "analysis.db") as db:
        db.upgrade()
        project = ProjectRepository(db).create("Temporary synthetic validation")
        question = QuestionRepository(db).create(
            project.id, "What is supported and what remains uncertain?"
        )
        link = QuestionSourceRepository(db).attach_reference(question.id, "FAKE0001")
        with DeepSeekAnalysisProvider() as provider:
            observed = ObservedProvider(provider)
            run = AnalysisService(db, ResearchCorpusBuilder(reader), observed).run_analysis(
                question.id, "abstracts"
            )
        _check(reader.fulltext_calls == 0, "Unexpected PDF read")
        _verify(db, run, observed, reader.item.abstract, link.source_id, "A")
        return run


def test_synthetic_deepseek_analysis(synthetic_validation):
    _check(synthetic_validation.status == "completed", "Synthetic validation did not complete")


def test_zotero_abstract_deepseek_analysis(tmp_path, request):
    # Check consent BEFORE requesting the paid synthetic prerequisite fixture.
    key = consented_zotero_item(os.environ)
    require_deepseek_config(os.environ)
    # No stale flag/cache can claim A passed. Require a successful A request
    # in this invocation before the real Zotero abstract is retrieved or sent.
    prerequisite = request.getfixturevalue("synthetic_validation")
    _check(prerequisite.status == "completed", "Level A must pass before Level B")
    zotero = ZoteroClient()
    _check(zotero.health_check().status == "healthy", "Local Zotero/CLI must be healthy")
    with Database(tmp_path / "zotero abstract validation.db") as db:
        db.upgrade()
        project = ProjectRepository(db).create("Temporary consented abstract validation")
        question = QuestionRepository(db).create(
            project.id, "What does this source support and what are its limitations?"
        )
        link = QuestionSourceService(db, zotero).attach_zotero_source(question.id, key)
        abstract = link.metadata.abstract
        _check(bool(abstract and abstract.strip()), "Selected source needs a non-empty abstract")

        # Guard the test against accidental PDF/annotation calls; only current metadata is used.
        def prohibited(*args, **kwargs):
            pytest.fail("PDF/annotation reads are prohibited in this test", pytrace=False)

        zotero.get_fulltext = prohibited
        zotero.get_annotations = prohibited
        with DeepSeekAnalysisProvider() as provider:
            observed = ObservedProvider(provider)
            run = AnalysisService(db, ResearchCorpusBuilder(zotero), observed).run_analysis(
                question.id, "abstracts"
            )
        # Compare the exact abstract actually sent (not a previous metadata read if Zotero changed).
        actual_abstract = json.loads(observed.request.sources[0].content)["text"]
        _verify(db, run, observed, actual_abstract, link.source.id, "B")
