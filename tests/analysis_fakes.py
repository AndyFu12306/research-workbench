"""Synthetic sources and deterministic provider shared by opt-in tests too."""

import json
from types import SimpleNamespace

from research_workbench.analysis.contracts import ProviderResult
from research_workbench.integrations.zotero import ZoteroFulltext, ZoteroItem


def valid_output(label="S1"):
    point = {
        "text": "The supplied evidence suggests a limited common approach.",
        "source_labels": [label],
    }
    return {
        "summary": point,
        "main_approaches": [point],
        "key_findings": [point],
        "agreements": [],
        "disagreements": [],
        "research_gaps": [point],
        "next_questions": ["Which result needs replication?"],
    }


class FakeAnalysisProvider:
    provider = "fake"
    model = "offline-test"

    def __init__(self):
        self.calls = 0
        self.request = None
        self.failure = None
        self.output = valid_output()

    def generate(self, request):
        self.calls += 1
        self.request = request
        if self.failure:
            raise self.failure
        return ProviderResult(json.dumps(self.output), "resp_synthetic", 100, 50)


class FakeZoteroClient:
    def __init__(self):
        self.item = ZoteroItem(
            "FAKE0001",
            "SYNTHETIC_METADATA_ONLY",
            ("Synthetic Author",),
            "PRIVATE_SOURCE_SENTINEL abstract body",
            "journalArticle",
            "2026",
        )
        self.fulltext = ZoteroFulltext("FAKE0001", "PRIVATE_FULLTEXT_SENTINEL research body")
        self.health = "healthy"
        self.error = None
        self.fulltext_error = None
        self.fulltext_calls = 0

    def health_check(self):
        return SimpleNamespace(status=self.health)

    def get_item(self, key):
        if self.error:
            raise self.error
        return self.item

    def get_fulltext(self, key):
        self.fulltext_calls += 1
        if self.fulltext_error:
            raise self.fulltext_error
        return self.fulltext
