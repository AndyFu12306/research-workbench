"""A small local, read-only client over the installed zotero-cli JSON contract."""

import json
import math
import re
from collections.abc import Mapping
from http.client import HTTPException
from pathlib import Path
from urllib.error import URLError
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

from .errors import ZoteroError, ZoteroParseError, ZoteroUnavailableError
from .models import (
    ComponentHealth,
    ZoteroAnnotation,
    ZoteroFulltext,
    ZoteroHealth,
    ZoteroItem,
)
from .runner import CommandRunner

_KEY = re.compile(r"[A-Z0-9]{8}")
_API_URL = "http://localhost:23119/api/users/0/items?limit=1"


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _item_key(key: str) -> str:
    if not isinstance(key, str) or not _KEY.fullmatch(key):
        raise ValueError("item_key must be an 8-character uppercase Zotero key")
    return key


def _object(value: object) -> dict:
    if not isinstance(value, dict):
        raise ZoteroParseError("Expected a JSON object from the CLI.")
    return value


def _text(data: dict, field: str, *, required: bool = False) -> str | None:
    value = data.get(field)
    if value is None and not required:
        return None
    if not isinstance(value, str) or (required and not value.strip()):
        raise ZoteroParseError(f"Missing or invalid string field: {field}")
    return value or None


def _parse_item(data: dict) -> ZoteroItem:
    key = _text(data, "key", required=True)
    if not _KEY.fullmatch(key):
        raise ZoteroParseError("Invalid item key in CLI response.")
    creators = data.get("creators", [])
    if not isinstance(creators, list):
        raise ZoteroParseError("Invalid creators list.")
    authors = []
    for creator in creators:
        creator = _object(creator)
        if creator.get("creatorType", "author") != "author":
            continue
        name = _text(creator, "name")
        if not name:
            name = " ".join(filter(None, (_text(creator, "firstName"), _text(creator, "lastName"))))
        if name:
            authors.append(name)
    return ZoteroItem(
        item_key=key,
        title=_text(data, "title", required=True),
        authors=tuple(authors),
        abstract=_text(data, "abstractNote"),
        item_type=_text(data, "itemType", required=True),
        publication_date=_text(data, "date"),
    )


class ZoteroClient:
    """Personal-library adapter. DTOs are ephemeral; no metadata is persisted.

    CLI timeout applies to each process; a truncated fulltext requires a second
    bounded page-read process. API health has a separate short timeout.
    """

    def __init__(
        self,
        cli_path: str | Path | None = None,
        *,
        timeout: float = 30,
        api_timeout: float = 5,
        environ: Mapping[str, str] | None = None,
    ) -> None:
        if not math.isfinite(api_timeout) or api_timeout <= 0:
            raise ValueError("api_timeout must be positive and finite")
        self._runner = CommandRunner(cli_path, timeout=timeout, environ=environ)
        self._api_timeout = api_timeout

    @property
    def last_stderr(self) -> str:
        """Diagnostics from the most recent CLI process, including successful commands.

        This may contain private library information. It is never logged automatically.
        Use one client per concurrent workflow; this diagnostic field is mutable.
        """
        return self._runner.last_stderr

    def search_items(self, query: str, *, limit: int = 10) -> tuple[ZoteroItem, ...]:
        if not isinstance(query, str) or not query.strip() or "\0" in query:
            raise ValueError("query must be a non-empty string without NUL characters")
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError("limit must be an integer from 1 to 100")
        data = _object(
            self._runner.run_json(
                ["search", "--limit", str(limit), "--detail", "full", "--", query]
            )
        )
        items = data.get("items")
        if (
            not isinstance(items, list)
            or type(data.get("count")) is not int
            or data["count"] != len(items)
        ):
            raise ZoteroParseError("Invalid search items/count response.")
        result = []
        for item in items:
            item = _object(item)
            if item.get("deleted"):
                continue
            raw = _object(item.get("raw"))
            if raw.get("deleted"):
                continue
            if raw.get("key") != item.get("key"):
                raise ZoteroParseError("Search projection and raw record keys do not match.")
            normalized = _parse_item({**raw, "key": item.get("key"), "title": item.get("title")})
            result.append(normalized)
        return tuple(result)

    def get_item(self, item_key: str) -> ZoteroItem:
        key = _item_key(item_key)
        record = _object(self._runner.run_json(["get", "metadata", key]))
        data = _object(record.get("data"))
        if record.get("key") != key or data.get("key") != key:
            raise ZoteroParseError("Metadata response key does not match the requested item.")
        if data.get("deleted"):
            raise ZoteroParseError("The returned item is in Zotero's trash.")
        return _parse_item(data)

    def get_fulltext(self, item_key: str) -> ZoteroFulltext:
        key = _item_key(item_key)
        data = _object(self._runner.run_json(["get", "fulltext", key]))
        if data.get("item_key") != key or type(data.get("truncated")) is not bool:
            raise ZoteroParseError("Missing fulltext identity or truncation status.")
        text = _text(data, "text", required=True)
        if data.get("chars") != len(text):
            raise ZoteroParseError("Fulltext character count mismatch.")
        if not data["truncated"]:
            return ZoteroFulltext(key, text)
        page_count = data.get("page_count")
        if type(page_count) is not int or page_count < 1:
            raise ZoteroParseError("Truncated fulltext did not provide a valid page count.")
        pages = _object(
            self._runner.run_json(
                [
                    "read",
                    key,
                    "--start-page",
                    "1",
                    "--end-page",
                    str(page_count),
                    "--format",
                    "text",
                ]
            )
        )
        text = _text(pages, "text", required=True)
        if (
            pages.get("item_key") != key
            or pages.get("start_page") != 1
            or pages.get("end_page") != page_count
            or pages.get("chars") != len(text)
        ):
            raise ZoteroParseError("Full PDF page-read response is incomplete or mismatched.")
        sections = re.split(r"(?m)^## Page (\d+)\s*$", text)
        actual = [int(sections[i]) for i in range(1, len(sections) - 1, 2)]
        if actual != list(range(1, page_count + 1)) or not any(
            sections[i].strip() for i in range(2, len(sections), 2)
        ):
            raise ZoteroParseError(
                "Full PDF response does not cover every page with extracted text."
            )
        return ZoteroFulltext(key, text, page_count)

    def get_annotations(self, item_key: str, *, limit: int = 100) -> tuple[ZoteroAnnotation, ...]:
        """Read up to limit annotations; never imply that a capped result is exhaustive."""
        key = _item_key(item_key)
        if type(limit) is not int or not 1 <= limit <= 1000:
            raise ValueError("annotation limit must be an integer from 1 to 1000")
        records = self._runner.run_json(
            ["annotations", "list", "--item-key", key, "--format", "json", "--limit", str(limit)]
        )
        if not isinstance(records, list):
            raise ZoteroParseError("Expected an annotations array.")
        results = []
        for record in records:
            record = _object(record)
            page_index = record.get("page_index")
            if page_index is not None and (type(page_index) is not int or page_index < 0):
                raise ZoteroParseError("Invalid annotation page index.")
            results.append(
                ZoteroAnnotation(
                    annotation_key=_text(record, "annotation_key"),
                    item_key=_text(record, "item_key"),
                    attachment_key=_text(record, "attachment_key"),
                    annotation_type=_text(record, "type"),
                    text=_text(record, "text") or "",
                    comment=_text(record, "comment") or "",
                    page=_text(record, "page"),
                    page_index=page_index,
                )
            )
        return tuple(results)

    def _check_local_api(self) -> None:
        # Explicit localhost URL and no environment proxy; no cloud fallback.
        request = Request(_API_URL, headers={"Zotero-API-Version": "3"}, method="GET")
        try:
            with build_opener(ProxyHandler({}), _NoRedirect()).open(
                request, timeout=self._api_timeout
            ) as response:
                body = response.read(1_000_001)
                if response.status != 200 or len(body) > 1_000_000:
                    raise ZoteroUnavailableError(
                        "Local API returned an invalid/oversized response."
                    )
                data = json.loads(body.decode("utf-8"))
                if not isinstance(data, list) or any(
                    not isinstance(item, dict)
                    or not item.get("key")
                    or not isinstance(item.get("data"), dict)
                    for item in data
                ):
                    raise ZoteroUnavailableError("Local API did not return a Zotero items array.")
        except (URLError, OSError, ValueError, HTTPException) as exc:
            raise ZoteroUnavailableError(f"Cannot read Zotero Local API: {exc}") from exc

    def health_check(self) -> ZoteroHealth:
        try:
            self._check_local_api()
            api = ComponentHealth("healthy", "Local API returned a valid items response.")
            client = ComponentHealth("healthy", "Client availability inferred from the Local API.")
        except ZoteroUnavailableError as exc:
            api = ComponentHealth("unavailable", str(exc))
            client = ComponentHealth(
                "degraded", "Client process not independently inspected; API unavailable."
            )
        try:
            settings = _object(_object(self._runner.run_json(["config"])).get("settings"))
            if settings.get("ZOTERO_LOCAL") != "true" or settings.get("ZOTERO_BACKEND") != "api":
                raise ZoteroParseError("CLI did not confirm the requested local API configuration.")
            cli = ComponentHealth(
                "healthy", "CLI JSON contract and local API configuration confirmed."
            )
        except ZoteroError as exc:
            cli = ComponentHealth("unavailable", f"{exc.layer}: {exc}")
        status = "healthy" if api.status == cli.status == "healthy" else "degraded"
        if api.status == cli.status == "unavailable":
            status = "unavailable"
        return ZoteroHealth(status, client, api, cli)
