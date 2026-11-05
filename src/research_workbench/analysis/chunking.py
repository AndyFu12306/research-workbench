"""Deterministic ephemeral slices. No normalization, I/O or page-number inference."""

import hashlib
import json
import re
from bisect import bisect_right
from dataclasses import dataclass, field

CHUNKING_VERSION = "paragraph-chars-v1"
_PARAGRAPH_BREAK = re.compile(r"(?:\r?\n|\r(?!\n))[^\S\r\n]*(?:(?:\r?\n|\r(?!\n))[^\S\r\n]*)+")


class PlanningInputError(ValueError):
    def __init__(self):
        super().__init__("Invalid or empty planning input.")


class PlanningLimitError(ValueError):
    def __init__(self):
        super().__init__("Analysis planning limit exceeded.")


def require_text(value: str) -> None:
    # Check before encoding: UnicodeEncodeError would retain the original input.
    if (
        type(value) is not str
        or not value.strip()
        or any("\ud800" <= char <= "\udfff" for char in value)
    ):
        raise PlanningInputError()


def require_int(value: int, minimum: int = 1) -> None:
    if type(value) is not int or value < minimum:
        raise PlanningInputError()


def fingerprint(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def fingerprint_parts(parts: list) -> str:
    return fingerprint(json.dumps(parts, ensure_ascii=False, separators=(",", ":")))


@dataclass(frozen=True, slots=True)
class SourceIdentity:
    provider: str = field(repr=False)
    scope: str = field(repr=False)
    external_key: str = field(repr=False)

    def __post_init__(self):
        for value in (self.provider, self.scope, self.external_key):
            require_text(value)

    @property
    def fingerprint(self) -> str:
        return fingerprint_parts([self.provider, self.scope, self.external_key])


@dataclass(frozen=True, slots=True)
class SourceDocument:
    identity: SourceIdentity
    text: str = field(repr=False)
    content_sha256: str = field(init=False)
    source_fingerprint: str = field(init=False)

    def __post_init__(self):
        if not isinstance(self.identity, SourceIdentity):
            raise PlanningInputError()
        require_text(self.text)
        digest = fingerprint(self.text)
        object.__setattr__(self, "content_sha256", digest)
        object.__setattr__(
            self, "source_fingerprint", fingerprint_parts([self.identity.fingerprint, digest])
        )


@dataclass(frozen=True, slots=True)
class ChunkingConfig:
    max_chars: int = 8_000
    overlap_chars: int = 200

    def __post_init__(self):
        require_int(self.max_chars)
        require_int(self.overlap_chars, 0)
        if self.overlap_chars >= self.max_chars:
            raise PlanningInputError()


@dataclass(frozen=True, slots=True)
class TextChunk:
    source_fingerprint: str
    ordinal: int
    start: int
    end: int
    text: str = field(repr=False)
    content_sha256: str
    chunk_id: str
    algorithm_version: str = CHUNKING_VERSION


def chunk_document(
    document: SourceDocument,
    config: ChunkingConfig | None = None,
    *,
    max_chunks: int = 100,
) -> tuple[TextChunk, ...]:
    """Prefer the last blank-line boundary that fits and advances past prior content.

    Separators belong to the preceding slice. Overlap is exactly overlap_chars
    between adjacent chunks. Long paragraphs split at Python code-point offsets;
    grapheme clusters and CRLF pairs may straddle a boundary. Nothing is removed.
    """
    if not isinstance(document, SourceDocument):
        raise PlanningInputError()
    config = config if config is not None else ChunkingConfig()
    if not isinstance(config, ChunkingConfig):
        raise PlanningInputError()
    require_int(max_chunks)
    text = document.text
    boundaries = [match.end() for match in _PARAGRAPH_BREAK.finditer(text)]
    chunks = []
    start = 0
    while start < len(text):
        if len(chunks) >= max_chunks:
            raise PlanningLimitError()
        end = min(start + config.max_chars, len(text))
        if end < len(text):
            index = bisect_right(boundaries, end) - 1
            # Prevent overlap-only chunks and guarantee progress on every iteration.
            if index >= 0 and boundaries[index] > start + config.overlap_chars:
                end = boundaries[index]
        part = text[start:end]
        digest = fingerprint(part)
        chunk_id = fingerprint_parts(
            [
                CHUNKING_VERSION,
                config.max_chars,
                config.overlap_chars,
                document.source_fingerprint,
                start,
                end,
                digest,
            ]
        )
        chunks.append(
            TextChunk(
                document.source_fingerprint, len(chunks) + 1, start, end, part, digest, chunk_id
            )
        )
        if end == len(text):
            break
        start = end - config.overlap_chars
    return tuple(chunks)
