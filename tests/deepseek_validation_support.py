"""Explicit paid-test gates. These helpers never read or print secret values."""

import pytest


def require_deepseek_config(environ):
    if environ.get("RUN_DEEPSEEK_INTEGRATION") != "1":
        pytest.skip("DeepSeek NOT TESTED: RUN_DEEPSEEK_INTEGRATION=1 is required")
    if not environ.get("DEEPSEEK_API_KEY") or not environ.get("DEEPSEEK_MODEL"):
        pytest.skip("DeepSeek NOT TESTED: key/model configuration is required")


def consented_zotero_item(environ):
    if environ.get("RUN_DEEPSEEK_ZOTERO_E2E") != "1":
        pytest.skip("Zotero→DeepSeek NOT TESTED: explicit abstract-transmission consent required")
    key = environ.get("ZOTERO_TEST_ITEM_KEY", "")
    if not key:
        pytest.skip("Zotero→DeepSeek NOT TESTED: explicitly select ZOTERO_TEST_ITEM_KEY")
    return key
