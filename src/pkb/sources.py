"""Source acquisition, extraction and staging for large-source ingestion (LS-7, LS-8, LS-9).

A source arrives as a **path or a URL**. Before any topic has earned a copy of it (LS-1 gives the
copy only on *gainful* ingestion, and gainfulness is not known until the reading is done) it has to
exist somewhere, so :func:`stage` puts it in ``<kb>/.inbox/``. That directory is dot-prefixed, which
is the whole trick: Layer 1's ``PA-16`` skips dot-prefixed names, so nothing staged here is scanned,
validated, indexed, or registered as a tag. Layer 1 needs no change, and neither does the permission
model — an expert is confined to its own subtree (RT-15) and could not write here anyway.

Binary sources are extracted to text and **both are kept** (LS-7): the extraction is what the
ingestion loop reads, the original is what a topic gets a copy of. The extraction stays in
``.inbox`` as a cache, so a re-ingestion (LS-5) re-reads it without paying for extraction twice.

**Structure is the product, not the text.** ``LS-10`` organises the source file by the source's own
structure — chapter 3 is chapter 3 on every re-reading, which is what gives reconciliation a stable
key for free. So :func:`extract` returns :class:`Section` objects carrying the document's own
headings, and records in :attr:`ExtractedSource.structure_method` *how* it found them. A caller
deserves to know whether it is holding the document's own table of contents or a guess made from
font sizes: the first is a key it can match on across passes, the second is a hint.

**Failure is loud, and the cases are distinguished.** No library raises on a scanned PDF — every one
returns empty text — so a naive pipeline produces a confident summary of nothing, which is exactly
the silent-success failure this whole feature exists to prevent. :func:`extract` therefore raises
:class:`ScannedSourceError` (no text, images present — needs OCR), :class:`EmptySourceError` (no
text, no images — genuinely blank), :class:`EncryptedSourceError`, or :class:`GarbledTextError`,
rather than returning forty pages of silence that reads like success.

This module is a **leaf**: standard library plus ``pypdf``, ``trafilatura`` and ``httpx``, and one
import of :func:`pkb.core.paths.slugify` so that staging directory names are slugified by the single
implementation that already exists rather than a second one (§0.2). It imports nothing from
``pkb.agents``, so it is testable without the harness and usable from any layer above Layer 1.
"""

from __future__ import annotations

import codecs
import hashlib
import json
import re
import shutil
import unicodedata
import zipfile
from collections import Counter
from collections.abc import Callable, Iterable, Iterator, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from enum import StrEnum
from html import entities as _html_entities
from ipaddress import ip_address
from pathlib import Path, PurePosixPath
from typing import Any, Final
from urllib.parse import unquote, urlparse
from xml.etree import ElementTree as ET

import httpx
import trafilatura
from pypdf import PdfReader
from pypdf.errors import DependencyError, PdfReadError
from trafilatura.metadata import extract_metadata as _extract_html_metadata

from pkb.core.paths import slugify

__all__ = [
    "INBOX_DIR",
    "EmptySourceError",
    "EncryptedSourceError",
    "ExtractedSource",
    "ExtractionError",
    "FetchError",
    "Fetched",
    "Fetcher",
    "GarbledTextError",
    "MalformedSourceError",
    "ScannedSourceError",
    "Section",
    "SourceError",
    "SourceNotFoundError",
    "StagedSource",
    "StructureMethod",
    "UnsupportedSourceError",
    "canonical_origin",
    "check_fetchable",
    "detect_kind",
    "extract",
    "find_staged",
    "inbox_root",
    "is_url",
    "load_extraction",
    "render_markdown",
    "save_extraction",
    "slug_for",
    "stage",
]


# --------------------------------------------------------------------------------------
# Errors — the distinction is the point (LS-7)
# --------------------------------------------------------------------------------------


class SourceError(Exception):
    """Base class for every failure in acquiring, staging or extracting a source."""


class SourceNotFoundError(SourceError):
    """The named path — the source, or the knowledge-base root — does not exist."""


class UnsupportedSourceError(SourceError):
    """The file is of a kind this module cannot turn into structured text."""


class FetchError(SourceError):
    """A URL could not be retrieved."""


class ExtractionError(SourceError):
    """A source was found and understood but yielded no usable text.

    Every subclass names *which* way it failed. That naming is the rule LS-7 asks for: a caller that
    cannot tell "scanned, needs OCR" from "genuinely blank" from "encrypted" has no way to decide
    what to do next, and the tempting default — treat empty as done — poisons the knowledge base
    quietly.
    """


class EncryptedSourceError(ExtractionError):
    """The document is encrypted and the empty password did not open it."""


class ScannedSourceError(ExtractionError):
    """No extractable text, but the pages carry images — a scan, needing OCR.

    This is the case that must never be mistaken for success: forty pages of images produce zero
    characters from every PDF library there is, and none of them raise.
    """


class EmptySourceError(ExtractionError):
    """No extractable text and no images either — the document is genuinely blank."""


class GarbledTextError(ExtractionError):
    """The extracted text is mostly unmappable glyphs — a broken font encoding.

    Text survives extraction but means nothing: the font has no usable ``ToUnicode`` map and every
    glyph lands in a Private Use Area codepoint. It reads as text to every downstream check and as
    noise to a human.
    """


class MalformedSourceError(ExtractionError):
    """The container is structurally broken — a truncated PDF, an EPUB missing its OPF."""


# --------------------------------------------------------------------------------------
# The shapes
# --------------------------------------------------------------------------------------


class StructureMethod(StrEnum):
    """How the sections were found — the document's own structure, or an inference.

    LS-10 makes the source's own structure the anchor re-ingestion matches on, so this distinction
    is load-bearing rather than diagnostic: a title recovered from a PDF outline is stable across
    passes, and a title guessed from a font size is stable only until the guess changes.
    """

    PDF_OUTLINE = "pdf-outline"
    """The PDF's own bookmark tree, with nesting and page destinations."""

    PDF_FONT_SIZE = "pdf-font-size"
    """Inferred: runs set noticeably larger than the body text were treated as headings."""

    EPUB_NAV = "epub-nav"
    """The EPUB3 navigation document (``<nav epub:type="toc">``)."""

    EPUB_NCX = "epub-ncx"
    """The EPUB2 NCX ``navMap``."""

    EPUB_SPINE = "epub-spine"
    """No table of contents at all: one section per spine document, titled from the document."""

    HTML_HEADINGS = "html-headings"
    """The page's own ``<h1>``…``<h6>`` structure, via trafilatura's markdown output."""

    MARKDOWN_HEADINGS = "markdown-headings"
    """The file's own ATX (``#``) headings."""

    WHOLE_DOCUMENT = "whole-document"
    """No structure was recoverable: the source is one unsplit section."""

    @property
    def is_intrinsic(self) -> bool:
        """True when the structure is the document's own rather than inferred from layout."""
        return self not in (StructureMethod.PDF_FONT_SIZE, StructureMethod.WHOLE_DOCUMENT)


@dataclass(frozen=True, slots=True)
class Section:
    """One chapter, one paper section, one article — the unit the ingestion loop windows on.

    ``level`` is 1 for a chapter or top-level section and 2+ for a subsection. ``title`` is the key
    LS-10 reconciles on, so it is taken from the source wherever the source supplies one — with its
    whitespace collapsed to single spaces, which is the one liberty taken with it.

    That normalisation is load-bearing rather than tidy. A title is written into the source file as
    ``## <title>``, into the reading record as ``**<title>**``, and compared against both to decide
    what a later pass has already read. All three break on an embedded newline, and an embedded
    newline is ordinary real-world data: a pretty-printed EPUB ``navLabel`` wraps its text, and PDF
    bookmarks carry whatever the typesetter put in them. Left alone, such a section grew a duplicate
    heading block in the file on every pass and a duplicate reading-record entry on every write,
    while ``validate_tree`` reported nothing. Normalising once, here, fixes every path at the source
    rather than at each of the three places that assume one line.
    """

    title: str
    level: int
    text: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "title", _WHITESPACE_RUN.sub(" ", self.title).strip())

    @property
    def char_count(self) -> int:
        """Characters of extracted text — what a windowed reader (LS-9) budgets against."""
        return len(self.text)

    @property
    def is_empty(self) -> bool:
        """True when the section has a heading but no body — a real and reportable outcome."""
        return not self.text.strip()


@dataclass(frozen=True, slots=True)
class ExtractedSource:
    """A source turned into titled, ordered, nested sections, with its provenance.

    ``origin`` is the path or URL the source came from, not where it happens to be staged: it is
    what the source file's provenance block records, and it survives the source being copied into a
    topic (LS-1).
    """

    title: str
    kind: str
    """``"pdf"`` | ``"epub"`` | ``"html"`` | ``"text"``."""

    origin: str
    sections: tuple[Section, ...]
    structure_method: StructureMethod
    author: str | None = None
    published: str | None = None
    """The source's own date string, as the source states it — not normalised or invented."""

    site: str | None = None
    """Sitename for a web page, publisher for a book."""

    page_count: int | None = None
    warnings: tuple[str, ...] = ()
    """Non-fatal honesty: pages that yielded nothing, outline entries that pointed nowhere."""

    @property
    def char_count(self) -> int:
        return sum(section.char_count for section in self.sections)

    @property
    def text(self) -> str:
        """The whole extraction as markdown — the readable view of the same data."""
        return render_markdown(self.sections)

    def with_origin(self, origin: str) -> ExtractedSource:
        """A copy that remembers where the source really came from.

        :func:`extract` is handed a file on disk, so it can only report that file as the origin.
        When :func:`stage` fetched that file from a URL, the URL is the origin worth recording.
        """
        return _replace_origin(self, origin)


@dataclass(frozen=True, slots=True)
class StagedSource:
    """What ``<kb>/.inbox/<slug>/`` holds after :func:`stage` (LS-8, LS-9).

    Four files, and each earns its place: ``source.json`` is the manifest that makes a re-stage of
    the same origin a cache hit rather than a second download; ``original`` is the bytes exactly as
    they arrived, which is what LS-1 copies into a topic; ``extraction_path``
    (``<slug>.extracted.json``) is the structured extraction the loop reads; ``text_path``
    (``<slug>.extracted.md``) is the same extraction rendered as markdown, so a human or an agent
    looking into ``.inbox`` can read it without a JSON parser. The markdown is written from the JSON
    on every write, so the two cannot drift.

    ``original`` keeps the name it arrived with and both derived files are marked ``.extracted.``,
    which is what keeps a markdown source's original from being overwritten by its own extraction —
    see :data:`_TEXT_SUFFIX`.
    """

    origin: str
    slug: str
    root: Path
    original: Path
    extraction_path: Path
    text_path: Path
    extracted: ExtractedSource
    from_cache: bool
    """True when this call reused an earlier extraction instead of redoing the work (LS-9)."""

    digest: str = ""
    """SHA-256 of the original, so a cache hit can tell "same document" from "same path" (LS-5)."""


@dataclass(frozen=True, slots=True)
class Fetched:
    """The result of retrieving a URL, kept small so a test can supply one without a network."""

    body: bytes
    content_type: str = ""
    final_url: str = ""


Fetcher = Callable[[str], Fetched]
"""How :func:`stage` retrieves a URL. Injectable so the test suite never touches the network."""


# --------------------------------------------------------------------------------------
# Constants
# --------------------------------------------------------------------------------------

INBOX_DIR: Final = ".inbox"
"""Staging directory name (LS-8). Dot-prefixed, so Layer 1's PA-16 walk never sees inside it."""

_MANIFEST_NAME: Final = "source.json"
_EXTRACTION_SUFFIX: Final = ".extracted.json"
_TEXT_SUFFIX: Final = ".extracted.md"
"""Rendered-markdown suffix.

Both derived files carry ``.extracted.``, and the original keeps whatever name it arrived with. That
asymmetry is load-bearing rather than cosmetic: the earlier layout wrote the rendered markdown to
``<slug>.md``, which is *also* what a markdown source is called once it is copied into staging, so
staging a ``.md`` or ``.markdown`` file silently overwrote the preserved original with the
re-rendered text — and the manifest went on calling it the original. LS-8's "keeping both" then held
for a PDF and quietly failed for the one kind of source where the two are easiest to confuse. Naming
the *derived* files defensively fixes it for every suffix at once, rather than special-casing the
one that was noticed.
"""
_SCHEMA_VERSION: Final = 2
"""Bumped when the on-disk layout changes; a manifest at another version is not a cache hit.

Version 1 wrote the rendered markdown over the original for markdown sources, so its ``original``
cannot be trusted to be the original. Re-staging costs one extraction and restores the invariant.
"""

_BOM_ENCODINGS: Final = (
    (codecs.BOM_UTF32_LE, "utf-32"),
    (codecs.BOM_UTF32_BE, "utf-32"),
    (codecs.BOM_UTF8, "utf-8-sig"),
    (codecs.BOM_UTF16_LE, "utf-16"),
    (codecs.BOM_UTF16_BE, "utf-16"),
)
"""Byte-order marks, longest first — UTF-32 LE starts with the UTF-16 LE mark, so order decides."""

_MAX_HEADING_LEVEL: Final = 6

# Provisional. The ratio was measured against a different extraction library's output, so treat the
# number as a starting point rather than a finding: raise it if a legitimate source with heavy
# symbol fonts trips it, lower it if garbled text gets through. What is *not* provisional is that
# the guard must exist — unmapped glyphs read as text to every downstream check.
_PUA_RATIO_LIMIT: Final = 0.10

# A run set this much larger than the body text is a heading. Also provisional: 1.15 separates a
# 24pt chapter title from 11pt body comfortably and does not fire on emphasised body text.
_HEADING_SIZE_RATIO: Final = 1.15
_MAX_HEADING_CHARS: Final = 200

_FRONT_MATTER_TITLE: Final = "(front matter)"

_ATX_HEADING = re.compile(r"^(#{1,6})\s+(.*?)\s*$")
_FENCE = re.compile(r"^\s*(```|~~~)")
_WHITESPACE_RUN = re.compile(r"\s+")
_NAMED_ENTITY = re.compile(r"&([a-zA-Z][a-zA-Z0-9]{1,31});")
_XML_DECLARED_ENCODING = re.compile(r"""<\?xml[^>]*?encoding=["']([\w.\-]+)["'][^>]*\?>""")
_XML_ENTITIES: Final = frozenset({"amp", "lt", "gt", "quot", "apos"})

_TEXT_SUFFIXES: Final = frozenset({".txt", ".text", ".md", ".markdown", ".rst", ".org"})
_HTML_SUFFIXES: Final = frozenset({".html", ".htm", ".xhtml"})

_CONTENT_TYPE_SUFFIX: Final = {
    "application/pdf": ".pdf",
    "application/x-pdf": ".pdf",
    "application/epub+zip": ".epub",
    "text/html": ".html",
    "application/xhtml+xml": ".html",
    "text/markdown": ".md",
    "text/plain": ".txt",
}

_EPUB_MIMETYPE: Final = "application/epub+zip"
_NCX_MEDIA_TYPE: Final = "application/x-dtbncx+xml"
_EPUB_OPS_NS: Final = "{http://www.idpf.org/2007/ops}"

_BLOCK_TAGS: Final = frozenset(
    {
        "address",
        "article",
        "aside",
        "blockquote",
        "br",
        "caption",
        "dd",
        "div",
        "dl",
        "dt",
        "figcaption",
        "figure",
        "footer",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "header",
        "hr",
        "li",
        "main",
        "nav",
        "ol",
        "p",
        "pre",
        "section",
        "table",
        "tbody",
        "td",
        "tfoot",
        "th",
        "thead",
        "tr",
        "ul",
    }
)
_PARAGRAPH_TAGS: Final = frozenset(
    {"blockquote", "div", "h1", "h2", "h3", "h4", "h5", "h6", "p", "pre", "section", "table"}
)
_SKIPPED_TAGS: Final = frozenset({"head", "script", "style", "svg"})


# --------------------------------------------------------------------------------------
# Public API
# --------------------------------------------------------------------------------------


def inbox_root(kb_root: Path) -> Path:
    """``<kb>/.inbox`` (LS-8). Not created — :func:`stage` creates it when it needs it."""
    return kb_root / INBOX_DIR


def stage(
    kb_root: Path,
    origin: str | Path,
    *,
    fetch: Fetcher | None = None,
    refresh: bool = False,
) -> StagedSource:
    """Put a source in ``<kb>/.inbox/`` and extract it, keeping both (LS-7, LS-8).

    ``origin`` is a filesystem path or a URL. A URL is retrieved through ``fetch`` — defaulting to
    httpx, injectable so tests never reach the network — and the retrieved bytes become the
    original; a path is copied, so the human's file is never the thing the loop mutates.

    Re-staging the same origin is a **cache hit** (LS-9): the extraction on disk is reloaded and no
    work is repeated, which is what makes re-ingestion (LS-5) affordable. Pass ``refresh=True`` to
    force the extraction again, for instance after upgrading an extractor.

    Extraction happens **here**, before any topic has seen the source, which is what makes LS-7's
    "extraction quality is visible rather than assumed" true in practice: a scanned PDF fails at
    staging, loudly, rather than reaching an expert as an empty string.

    This function writes under ``kb_root``. That is deliberate and outside RT-18's corollary, which
    constrains ``pkb.agents``: staging is harness code in a leaf module, not a model-issued write,
    and the destination is invisible to every Layer 1 walk.
    """
    if not kb_root.is_dir():
        raise SourceNotFoundError(f"knowledge-base root does not exist: {kb_root}")

    recorded = canonical_origin(origin)
    staging = _staging_dir(kb_root, recorded)

    if not refresh:
        cached = _load_staged(staging, recorded)
        if cached is not None and not _origin_has_changed(recorded, cached):
            return cached

    slug = staging.name
    pending = staging.with_name(f".{staging.name}.staging")
    _clear(pending)
    pending.mkdir(parents=True, exist_ok=True)
    try:
        original = _acquire(recorded, pending, slug, fetch=fetch)
        extracted = extract(original, origin=recorded)
        staged = _write_staged(pending, slug, recorded, original, extracted, from_cache=False)
    except BaseException:
        _clear(pending)
        raise
    # Everything succeeded, so the new staging replaces the old one in one move. Writing in place
    # would leave a directory holding edition two's bytes beside edition one's extraction whenever
    # a re-stage failed — a manifest whose two halves describe different documents, served forever
    # after as a clean cache hit.
    _clear(staging)
    pending.rename(staging)
    return _rebase(staged, staging)


def find_staged(kb_root: Path, origin: str | Path) -> StagedSource | None:
    """The staged form of ``origin``, or ``None`` — the LS-11 "is this already here?" question.

    Answers from the manifest without re-extracting or re-fetching anything, so it is cheap enough
    to ask before deciding whether to offer a re-ingestion rather than assuming one.
    """
    if not kb_root.is_dir():
        return None
    recorded = canonical_origin(origin)
    staging = _staging_dir(kb_root, recorded)
    return _load_staged(staging, recorded)


def canonical_origin(origin: str | Path) -> str:
    """The recorded identity of a source: one string per document, however it was spelled.

    ``stage`` records this rather than the caller's string, because the recorded origin is what
    every later question about the source is answered from — is this already staged, is this already
    ingested, which reference folder does it belong to. Left uncanonicalised, ``~/book.pdf``,
    ``./book.pdf`` and ``/home/me/books/../books/book.pdf`` are three sources: the same book is read
    three times, lands in three reference folders, and reconciliation never runs because no pass has
    anything to reconcile against.

    Paths resolve through symlinks and ``..`` and are made absolute; a path that does not exist
    still normalises, so the caller gets :class:`SourceNotFoundError` from the acquisition rather
    than a confusing failure here. URLs lower-case the scheme and host — which are defined to be
    case-insensitive — and keep the path, query and fragment exactly as given, because those are
    case-sensitive and a server may well distinguish them.
    """
    text = str(origin)
    if _is_url(text):
        parsed = urlparse(text)
        host = parsed.netloc.lower()
        return parsed._replace(scheme=parsed.scheme.lower(), netloc=host).geturl()
    return str(Path(text).expanduser().resolve())


def _origin_has_changed(origin: str, cached: StagedSource) -> bool:
    """True when the file at ``origin`` is no longer the document that was staged (LS-5, LS-9).

    The cache is what makes re-ingestion affordable, and it is also what makes a re-ingestion read
    the wrong bytes: the most common reason a human re-ingests a local source is that they changed
    it — corrected it, added a chapter, replaced a draft — and an origin-keyed cache hands back the
    superseded text with a reading dated today. Comparing the digest costs one read of a file that
    is about to be read anyway on a miss, and nothing on the hit path that matters.

    A URL cannot be checked without re-fetching, so it is never invalidated here; ``refresh=True``
    is the way to force one, and :func:`pkb.agents.ingestion.ingest_source_tool` exposes it.
    """
    if _is_url(origin):
        return False
    source = Path(origin)
    if not source.is_file():
        # The human moved or deleted it. The staged copy is now the only one there is, so serving it
        # is strictly better than failing — LS-8's whole reason for keeping the original.
        return False
    return _digest(source) != cached.digest


def _digest(path: Path) -> str:
    reader = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            reader.update(block)
    return reader.hexdigest()


def _clear(path: Path) -> None:
    if path.is_dir():
        shutil.rmtree(path)
    elif path.exists():
        path.unlink()


def _rebase(staged: StagedSource, root: Path) -> StagedSource:
    """The same staged source, with its paths pointing at where the directory ended up."""
    return replace(
        staged,
        root=root,
        original=root / staged.original.name,
        extraction_path=root / staged.extraction_path.name,
        text_path=root / staged.text_path.name,
    )


def detect_kind(path: Path) -> str:
    """``"pdf"`` | ``"epub"`` | ``"html"`` | ``"text"`` for ``path``.

    Magic bytes win over the file extension: a source downloaded from a URL frequently arrives with
    a name that says nothing, and a mislabelled ``.pdf`` that is really HTML should be read as HTML
    rather than reported as a broken PDF.
    """
    if not path.is_file():
        raise SourceNotFoundError(f"source does not exist: {path}")

    with path.open("rb") as handle:
        head = handle.read(4096)
    if head.startswith(b"%PDF-"):
        return "pdf"
    if head.startswith(b"PK\x03\x04") and _is_epub_zip(path):
        return "epub"

    suffix = path.suffix.lower()
    if suffix == ".pdf":
        # The extension claims PDF but the magic bytes disagree; nothing downstream will work.
        raise MalformedSourceError(
            f"{path.name} is named as a PDF but does not start with the PDF header"
        )
    if suffix == ".epub":
        raise MalformedSourceError(f"{path.name} is named as an EPUB but is not a readable zip")
    if suffix in _HTML_SUFFIXES:
        return "html"
    if suffix in _TEXT_SUFFIXES:
        return "text"

    stripped = head.lstrip()[:512].lower()
    if stripped.startswith((b"<!doctype html", b"<html", b"<?xml")) or b"<body" in stripped:
        return "html"
    if _looks_like_text(head):
        return "text"
    raise UnsupportedSourceError(
        f"{path.name}: no extractor for this file — supported kinds are pdf, epub, html and text"
    )


def extract(path: Path, *, origin: str | None = None) -> ExtractedSource:
    """Turn a file into titled, ordered sections, or raise saying exactly what went wrong.

    ``origin`` overrides the recorded provenance; :func:`stage` passes the URL a staged copy came
    from, so the extraction remembers the source rather than the staging path.
    """
    kind = detect_kind(path)
    recorded_origin = origin if origin is not None else str(path)
    if kind == "pdf":
        return _extract_pdf(path, recorded_origin)
    if kind == "epub":
        return _extract_epub(path, recorded_origin)
    if kind == "html":
        return _extract_html(path, recorded_origin)
    return _extract_text(path, recorded_origin)


def render_markdown(sections: Iterable[Section]) -> str:
    """Sections as markdown — the readable view of an extraction, and its cached text form.

    Deterministic and lossless for the heading tree, which is what lets ``.inbox``'s markdown be
    regenerated from the JSON on every write instead of maintained beside it.
    """
    blocks: list[str] = []
    for section in sections:
        hashes = "#" * max(1, min(section.level, _MAX_HEADING_LEVEL))
        body = section.text.strip("\n")
        blocks.append(
            f"{hashes} {section.title}\n\n{body}\n" if body else f"{hashes} {section.title}\n"
        )
    return "\n".join(blocks)


def save_extraction(source: ExtractedSource, path: Path) -> None:
    """Write the extraction cache (LS-9), sorted and indented so a diff between passes is readable."""
    payload: dict[str, Any] = {
        "schema": _SCHEMA_VERSION,
        "title": source.title,
        "kind": source.kind,
        "origin": source.origin,
        "structure_method": str(source.structure_method),
        "author": source.author,
        "published": source.published,
        "site": source.site,
        "page_count": source.page_count,
        "warnings": list(source.warnings),
        "sections": [{"title": s.title, "level": s.level, "text": s.text} for s in source.sections],
    }
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def load_extraction(path: Path) -> ExtractedSource:
    """Read back what :func:`save_extraction` wrote."""
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise MalformedSourceError(f"unreadable extraction cache at {path}: {exc}") from exc
    if not isinstance(payload, dict) or payload.get("schema") != _SCHEMA_VERSION:
        raise MalformedSourceError(f"extraction cache at {path} is not schema {_SCHEMA_VERSION}")
    sections = tuple(
        Section(title=str(s["title"]), level=int(s["level"]), text=str(s["text"]))
        for s in payload.get("sections", [])
    )
    return ExtractedSource(
        title=str(payload["title"]),
        kind=str(payload["kind"]),
        origin=str(payload["origin"]),
        sections=sections,
        structure_method=StructureMethod(payload["structure_method"]),
        author=payload.get("author"),
        published=payload.get("published"),
        site=payload.get("site"),
        page_count=payload.get("page_count"),
        warnings=tuple(payload.get("warnings", ())),
    )


# --------------------------------------------------------------------------------------
# Staging internals
# --------------------------------------------------------------------------------------


def _staging_dir(kb_root: Path, origin: str) -> Path:
    """The directory this origin stages into, disambiguating slug collisions by the manifest.

    Two different sources whose names slugify to the same string must not share a directory: the
    second would overwrite the first's original and its extraction, and nothing anywhere would
    record that the first source had been replaced. The manifest's recorded origin is what tells
    them apart, so the answer is stable across calls and across processes.
    """
    base = slug_for(origin)
    inbox = inbox_root(kb_root)
    for attempt in range(1, 100):
        slug = base if attempt == 1 else f"{base}-{attempt}"
        candidate = inbox / slug
        recorded = _manifest_origin(candidate)
        if recorded is None or recorded == origin:
            return candidate
    raise SourceError(f"too many staged sources named {base!r} in {inbox}")


def slug_for(origin: str) -> str:
    """The staging-directory name an origin prefers, before any collision is considered.

    Public because the runtime needs the same answer to find a topic's reference folder *before*
    staging anything — asking "have I read this?" must not cost a download.
    """
    if _is_url(origin):
        parsed = urlparse(origin)
        name = PurePosixPath(unquote(parsed.path)).name
        base = PurePosixPath(name).stem or parsed.netloc or "source"
    else:
        base = Path(origin).stem or "source"
    return slugify(base) or "source"


def _is_url(origin: str) -> bool:
    return urlparse(origin).scheme in ("http", "https")


def _manifest_origin(staging: Path) -> str | None:
    manifest = staging / _MANIFEST_NAME
    if not manifest.is_file():
        return None
    try:
        payload = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    origin = payload.get("origin") if isinstance(payload, dict) else None
    return str(origin) if isinstance(origin, str) else None


def _load_staged(staging: Path, origin: str) -> StagedSource | None:
    manifest_path = staging / _MANIFEST_NAME
    if not manifest_path.is_file():
        return None
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict) or payload.get("origin") != origin:
        return None
    if payload.get("schema") != _SCHEMA_VERSION:
        # A staging directory written by another layout is not a cache hit. Re-staging re-reads the
        # origin and costs one extraction; trusting a manifest whose field meanings have changed
        # costs correctness, and silently — which is how the v1 markdown bug survived a test suite.
        return None

    original = staging / str(payload.get("original", ""))
    extraction_path = staging / str(payload.get("extraction", ""))
    text_path = staging / str(payload.get("text", ""))
    if not original.is_file() or not extraction_path.is_file():
        return None
    return StagedSource(
        origin=origin,
        slug=staging.name,
        root=staging,
        original=original,
        extraction_path=extraction_path,
        text_path=text_path,
        extracted=load_extraction(extraction_path),
        from_cache=True,
        digest=str(payload.get("digest", "")),
    )


def _write_staged(
    staging: Path,
    slug: str,
    origin: str,
    original: Path,
    extracted: ExtractedSource,
    *,
    from_cache: bool,
) -> StagedSource:
    extraction_path = staging / f"{slug}{_EXTRACTION_SUFFIX}"
    text_path = staging / f"{slug}{_TEXT_SUFFIX}"
    save_extraction(extracted, extraction_path)
    text_path.write_text(render_markdown(extracted.sections), encoding="utf-8")
    manifest = {
        "schema": _SCHEMA_VERSION,
        "origin": origin,
        "slug": slug,
        "original": original.name,
        "extraction": extraction_path.name,
        "text": text_path.name,
        "kind": extracted.kind,
        "digest": _digest(original),
        "staged": datetime.now(UTC).isoformat(timespec="seconds"),
    }
    (staging / _MANIFEST_NAME).write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    return StagedSource(
        origin=origin,
        slug=slug,
        root=staging,
        original=original,
        extraction_path=extraction_path,
        text_path=text_path,
        extracted=extracted,
        from_cache=from_cache,
        digest=str(manifest["digest"]),
    )


def _acquire(origin: str, staging: Path, slug: str, *, fetch: Fetcher | None) -> Path:
    """Put the source bytes into the staging directory and return where they landed."""
    if _is_url(origin):
        fetched = (fetch or _http_fetch)(origin)
        suffix = _suffix_for_fetch(fetched, origin)
        destination = staging / _original_name(slug, suffix)
        destination.write_bytes(fetched.body)
        return destination

    source = Path(origin).expanduser()
    if not source.is_file():
        raise SourceNotFoundError(f"source does not exist: {source}")
    destination = staging / _original_name(slug, source.suffix.lower())
    destination.write_bytes(source.read_bytes())
    return destination


def _original_name(slug: str, suffix: str) -> str:
    """``<slug><suffix>``, stepped aside when that is a name this directory already uses.

    The derived files were made defensive by construction — both carry ``.extracted.`` — but the
    manifest's name is a fixed literal, and a source called ``source.json`` slugs to ``source`` and
    lands on exactly it. So did every stem that slugifies to nothing: ``!!!.json``, ``Source.JSON``.
    The manifest is written last, so the preserved original was overwritten by the manifest and then
    served as the original forever, ``_load_staged`` being satisfied that a file by that name exists.

    One line of defence for a whole class rather than a check per name: if the original would take a
    name this module writes, it becomes ``<slug>.original<suffix>``.
    """
    name = f"{slug}{suffix}"
    reserved = {_MANIFEST_NAME, f"{slug}{_EXTRACTION_SUFFIX}", f"{slug}{_TEXT_SUFFIX}"}
    return f"{slug}.original{suffix}" if name in reserved else name


def _suffix_for_fetch(fetched: Fetched, origin: str) -> str:
    media_type = fetched.content_type.split(";", 1)[0].strip().lower()
    if media_type in _CONTENT_TYPE_SUFFIX:
        return _CONTENT_TYPE_SUFFIX[media_type]
    if fetched.body.startswith(b"%PDF-"):
        return ".pdf"
    url_suffix = PurePosixPath(unquote(urlparse(fetched.final_url or origin).path)).suffix.lower()
    if url_suffix in _CONTENT_TYPE_SUFFIX.values():
        return url_suffix
    return ".html"


def is_url(origin: str) -> bool:
    """True when this origin is fetched rather than read from disk. See :func:`check_fetchable`."""
    return _is_url(origin)


def check_fetchable(url: str) -> None:
    """Refuse a URL that points back inside the machine — the cloud-metadata class (LS-7).

    ``origin`` is chosen by the *model*, and a fetch is a read the knowledge base then keeps: a
    prompt-injected turn asking for ``http://169.254.169.254/latest/meta-data/iam/…`` would put
    credentials in front of the model and copy them into a topic. Loopback, link-local, private and
    reserved ranges are refused, and so is any scheme that is not HTTP.

    Deliberately a check on the literal host rather than a resolve-and-compare: DNS can answer
    differently between this check and the request, so treating a name as safe because it resolved
    to a public address once is a guarantee that does not hold. What this does buy is that the
    obvious spellings are refused with a clear message, and it composes with
    :attr:`~pkb.agents.runtime.RuntimeConfig.allow_url_sources` for a deployment that wants no
    network reads at all.
    """
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise FetchError(f"{url}: only http and https sources can be fetched")
    host = (parsed.hostname or "").strip("[]")
    if not host:
        raise FetchError(f"{url}: no host to fetch from")
    if host.lower() in _BLOCKED_HOSTNAMES:
        raise FetchError(f"{url}: refusing to fetch from this machine")
    try:
        address = ip_address(host)
    except ValueError:
        return
    if not address.is_global or address.is_link_local:
        raise FetchError(
            f"{url}: {host} is a private, loopback or link-local address — refusing to fetch a "
            f"source from inside the network this runs on"
        )


_BLOCKED_HOSTNAMES: Final = frozenset({"localhost", "metadata", "metadata.google.internal"})


def _http_fetch(url: str) -> Fetched:
    """The default :data:`Fetcher`. Never called by the test suite — no network, no API key."""
    check_fetchable(url)
    try:
        response = httpx.get(url, follow_redirects=True, timeout=30.0)
        response.raise_for_status()
    except httpx.HTTPError as exc:
        raise FetchError(f"could not retrieve {url}: {exc}") from exc
    return Fetched(
        body=response.content,
        content_type=response.headers.get("content-type", ""),
        final_url=str(response.url),
    )


# --------------------------------------------------------------------------------------
# Guards — LS-7's "fail loudly, and distinguish the cases"
# --------------------------------------------------------------------------------------


def _is_private_use(char: str) -> bool:
    code = ord(char)
    return 0xE000 <= code <= 0xF8FF or 0xF0000 <= code <= 0xFFFFD or 0x100000 <= code <= 0x10FFFD


def _guard_text(text: str, *, origin: str, has_images: bool) -> None:
    """Raise the error that names what went wrong, or return.

    Order matters, and not in the obvious direction: **garbling is tested first**. A private-use
    codepoint is not alphanumeric, so a wholly unmapped document has zero alphanumeric characters
    and an emptiness-first check would call it blank or scanned — sending the caller to OCR a
    document whose actual problem is a missing font map. Testing garbling first costs nothing on a
    genuinely empty document, which has no visible characters to form a ratio from.
    """
    visible = [char for char in text if not char.isspace()]
    private_use = sum(1 for char in visible if _is_private_use(char))
    if visible and private_use / len(visible) > _PUA_RATIO_LIMIT:
        percent = 100 * private_use / len(visible)
        raise GarbledTextError(
            f"{origin}: {percent:.0f}% of the extracted characters are unmappable glyphs — the "
            f"fonts carry no usable character map, so the text is noise rather than text"
        )

    if not any(char.isalnum() for char in text):
        if has_images:
            raise ScannedSourceError(
                f"{origin}: no extractable text, but the pages carry images — this looks like a "
                f"scan and needs OCR before it can be ingested"
            )
        raise EmptySourceError(
            f"{origin}: no extractable text and no images — the document is empty"
        )


def _looks_like_text(head: bytes) -> bool:
    if not head:
        return False
    if b"\x00" in head:
        return False
    try:
        head.decode("utf-8")
    except UnicodeDecodeError:
        # A truncated multi-byte character at the 4096-byte boundary is not evidence of binary.
        try:
            head[:-4].decode("utf-8")
        except UnicodeDecodeError:
            return False
    return True


def _read_text_file(path: Path) -> str:
    """Decode a text source, honouring its byte-order mark and refusing what decoded to noise.

    Three things here are each a defect that shipped:

    * ``utf-8-sig`` must be tried **before** ``utf-8``. ``utf-8`` never fails on BOM-prefixed
      content — it decodes the mark to ``U+FEFF`` and keeps going — so listing it first meant the
      BOM survived into the text, the first ATX heading no longer matched ``^#``, and a markdown
      book saved by any Windows editor silently lost its opening chapter and its title.
    * A **UTF-16** file must be decoded as UTF-16. cp1252 has almost no undefined bytes, so it
      "succeeds" on UTF-16 input and yields ``ÿþ#\\x00 \\x00D\\x00…`` — not empty, so
      :func:`_guard_text` passes it, and the expert is asked to find arguments in mojibake. The BOM
      is what distinguishes it, and it is checked first.
    * Text that still holds a **NUL** after decoding is not text. ``_looks_like_text`` already knew
      that, but :func:`detect_kind` short-circuits on a ``.txt`` or ``.md`` extension and never
      reaches it — so identical bytes were refused when the file had no extension and accepted when
      it was called ``notes.txt``. Refusing here closes the gap for every path.
    """
    raw = path.read_bytes()
    for mark, encoding in _BOM_ENCODINGS:
        if raw.startswith(mark):
            return _guard_decoded(raw.decode(encoding, errors="replace"), path)
    for encoding in ("utf-8", "cp1252", "latin-1"):
        try:
            return _guard_decoded(raw.decode(encoding), path)
        except UnicodeDecodeError:
            continue
    return _guard_decoded(raw.decode("utf-8", errors="replace"), path)


def _guard_decoded(text: str, path: Path) -> str:
    if "\x00" in text:
        raise MalformedSourceError(
            f"{path.name} is named as text but holds NUL bytes — it is binary, or text in an "
            f"encoding with no byte-order mark that nothing here can identify"
        )
    return text


# --------------------------------------------------------------------------------------
# PDF
# --------------------------------------------------------------------------------------


def _extract_pdf(path: Path, origin: str) -> ExtractedSource:
    """PDF via pypdf: the document's own outline first, font sizes only as a fallback.

    ``reader.outline`` is present on most real documents and gives correctly nested titles with page
    destinations — the document's own structure, and therefore the stable key LS-10 wants. When it
    is absent the headings are reconstructed from effective font size, which is
    ``abs(font_size * tm[3])``: the raw ``font_size`` operand ignores the text matrix's vertical
    scale, so a document that sets 1pt type and scales it by 24 looks like body text without it.
    """
    reader = _open_pdf(path, origin)
    pages: list[str] = []
    page_runs: list[list[_Run]] = []
    has_images = False
    for page in reader.pages:
        runs: list[_Run] = []
        try:
            text = page.extract_text(visitor_text=_run_collector(runs)) or ""
        except (PdfReadError, DependencyError) as exc:
            raise MalformedSourceError(f"{origin}: page text could not be read: {exc}") from exc
        pages.append(text)
        page_runs.append(runs)
        has_images = has_images or _page_has_image(page)

    doc_text, page_starts = _join_pages(pages)
    _guard_text(doc_text, origin=origin, has_images=has_images)

    warnings: list[str] = []
    blank = sum(1 for text in pages if not any(char.isalnum() for char in text))
    if blank:
        warnings.append(f"{blank} of {len(pages)} pages yielded no text")

    anchors, method = _pdf_outline_anchors(reader, pages, page_starts, warnings)
    if not anchors:
        anchors = _pdf_font_anchors(pages, page_runs, page_starts)
        method = StructureMethod.PDF_FONT_SIZE

    sections = _slice_sections(doc_text, anchors)
    if not sections:
        sections = (Section(title=_pdf_title(reader, path), level=1, text=doc_text.strip()),)
        method = StructureMethod.WHOLE_DOCUMENT

    info = reader.metadata
    return ExtractedSource(
        title=_pdf_title(reader, path),
        kind="pdf",
        origin=origin,
        sections=sections,
        structure_method=method,
        author=_clean_meta(getattr(info, "author", None)),
        published=_clean_meta(_pdf_date(info)),
        site=_clean_meta(getattr(info, "producer", None)),
        page_count=len(pages),
        warnings=tuple(warnings),
    )


def _open_pdf(path: Path, origin: str) -> PdfReader:
    try:
        reader = PdfReader(path)
    except PdfReadError as exc:
        raise MalformedSourceError(f"{origin}: not a readable PDF: {exc}") from exc
    if reader.is_encrypted:
        try:
            opened = reader.decrypt("")
        except (PdfReadError, DependencyError, NotImplementedError) as exc:
            raise EncryptedSourceError(f"{origin}: the PDF is encrypted ({exc})") from exc
        if not opened:
            raise EncryptedSourceError(
                f"{origin}: the PDF is encrypted and the empty password does not open it"
            )
    return reader


def _pdf_title(reader: PdfReader, path: Path) -> str:
    title = _clean_meta(getattr(reader.metadata, "title", None))
    return title or path.stem


def _pdf_date(info: Any) -> str | None:
    for attribute in ("creation_date", "modification_date"):
        value = getattr(info, attribute, None)
        if isinstance(value, datetime):
            return value.date().isoformat()
    raw = getattr(info, "creation_date_raw", None)
    return str(raw) if raw else None


def _clean_meta(value: Any) -> str | None:
    """A PDF metadata or outline string as one clean line, or ``None``.

    Whitespace is collapsed rather than merely stripped: a bookmark title or a ``/Title`` holding an
    embedded newline reaches the source file's heading, its frontmatter ``description`` and the
    reading record, and each of those is a single line by construction. Layer 1 has a rule for the
    frontmatter case (VA-26 ``MULTILINE_DESCRIPTION``) which turned an ordinary PDF into a refused
    write, and none for the other two, which silently grew duplicate blocks.
    """
    if value is None:
        return None
    text = _WHITESPACE_RUN.sub(" ", str(value)).strip()
    return text or None


def _page_has_image(page: Any) -> bool:
    """True when the page carries an image XObject.

    Deliberately structural rather than ``page.images``: enumerating the resource dictionary needs
    no image codec, so the scanned-vs-blank distinction never depends on an optional dependency
    being installed — which is exactly the sort of silent downgrade this guard exists to prevent.
    """
    try:
        resources = page.get("/Resources")
        if resources is None:
            return False
        xobjects = resources.get_object().get("/XObject")
        if xobjects is None:
            return False
        container = xobjects.get_object()
        for key in container:
            if container[key].get_object().get("/Subtype") == "/Image":
                return True
    except Exception:  # a broken resource tree is not evidence of a scan
        return False
    return False


@dataclass(frozen=True, slots=True)
class _Run:
    """One ``Tj`` operand with the effective size it was drawn at."""

    text: str
    size: float
    line: float


def _run_collector(sink: list[_Run]) -> Callable[[str, Any, Any, Any, Any], None]:
    def visitor(text: str, cm: Any, tm: Any, font_dict: Any, font_size: Any) -> None:
        del cm, font_dict
        stripped = text.strip()
        if not stripped or font_size is None or tm is None:
            return
        try:
            # LS-7's measured rule: the drawn size is the operand times the text matrix's vertical
            # scale. Reading font_size alone misreads any document that scales its type.
            size = abs(float(font_size) * float(tm[3]))
            line = float(tm[5])
        except (TypeError, ValueError, IndexError):
            return
        sink.append(_Run(text=stripped, size=round(size, 1), line=line))

    return visitor


def _join_pages(pages: Sequence[str]) -> tuple[str, list[int]]:
    """One document string plus the offset each page starts at."""
    parts: list[str] = []
    starts: list[int] = []
    offset = 0
    for index, text in enumerate(pages):
        if index:
            parts.append("\n\n")
            offset += 2
        starts.append(offset)
        parts.append(text)
        offset += len(text)
    return "".join(parts), starts


@dataclass(frozen=True, slots=True)
class _Anchor:
    """A heading and the character offset in the document text where its section starts."""

    title: str
    level: int
    offset: int


def _pdf_outline_anchors(
    reader: PdfReader,
    pages: Sequence[str],
    page_starts: Sequence[int],
    warnings: list[str],
) -> tuple[list[_Anchor], StructureMethod]:
    entries = list(_walk_outline(reader.outline, 1))
    if not entries:
        return [], StructureMethod.PDF_OUTLINE

    anchors: list[_Anchor] = []
    unresolved = 0
    for title, level, destination in entries:
        try:
            page_number = reader.get_destination_page_number(destination)
        except Exception:  # a dangling destination is a data problem, not a crash
            page_number = None
        if page_number is None or not 0 <= page_number < len(pages):
            unresolved += 1
            continue
        base = page_starts[page_number]
        # Several chapters can share one page, so the page number alone cannot separate them.
        # Locating the title inside the page text splits them where they actually begin.
        within = _find_title(pages[page_number], title)
        anchors.append(_Anchor(title=title, level=level, offset=base + (within or 0)))
    if unresolved:
        warnings.append(f"{unresolved} outline entries pointed at no resolvable page")

    anchors, collided = _drop_colliding_anchors(anchors)
    if collided:
        warnings.append(
            f"{len(collided)} outline entries could not be located in the page text and were "
            f"folded into the section above them: {', '.join(collided)}"
        )
    return anchors, StructureMethod.PDF_OUTLINE


def _drop_colliding_anchors(anchors: Sequence[_Anchor]) -> tuple[list[_Anchor], list[str]]:
    """Keep the first of any anchors resolving to the same offset, and name the ones dropped.

    Two anchors at one offset cannot both own the text between them: the slice for the earlier one
    is empty, so the *parent* chapter loses its body to the child entry that happened to fall in the
    same place. That is the wrong way round — the chapter is the key LS-10 reconciles on, and losing
    its text to an unlocatable sub-entry would be a silent loss dressed up as structure. The
    dropped titles go into ``warnings`` instead, so the reading is checkable rather than merely
    plausible.
    """
    kept: list[_Anchor] = []
    dropped: list[str] = []
    for anchor in anchors:
        if kept and kept[-1].offset == anchor.offset:
            dropped.append(anchor.title)
            continue
        kept.append(anchor)
    return kept, dropped


def _walk_outline(items: Any, level: int) -> Iterator[tuple[str, int, Any]]:
    """Flatten pypdf's nested outline, where a nested list is one level deeper."""
    for item in items:
        if isinstance(item, list):
            yield from _walk_outline(item, level + 1)
            continue
        title = _clean_meta(getattr(item, "title", None))
        if title:
            yield title, level, item


def _find_title(page_text: str, title: str) -> int | None:
    """Offset of ``title`` within one page's text, or ``None`` when it is not there.

    Whitespace-insensitive, because extracted text breaks lines where the layout did and an outline
    title is written as one string. ``None`` and ``0`` are deliberately different answers: "the
    heading starts the page" and "the heading is not on this page at all" send the caller down
    different paths, and collapsing them is how a chapter silently loses its body.
    """
    if not page_text or not title:
        return None
    heading = _find_as_heading(page_text, title)
    if heading is not None:
        return heading

    wanted = _WHITESPACE_RUN.sub("", title).casefold()
    if not wanted:
        return None
    compact: list[str] = []
    positions: list[int] = []
    for index, char in enumerate(page_text):
        if not char.isspace():
            compact.append(char.casefold())
            positions.append(index)
    found = "".join(compact).find(wanted)
    return positions[found] if found >= 0 else None


def _find_as_heading(page_text: str, title: str) -> int | None:
    """Offset of ``title`` where it stands as a **heading**, not merely as a substring.

    A bare ``page_text.find(title)`` was the first thing tried, and it is wrong in a way that only
    shows up on real books: outline titles are routinely short — a bare chapter number, a Roman
    numeral, a single word — and several chapters share a page. "6" then matches inside chapter 5's
    prose at "6 months", the section is cut there, chapter 5 keeps one sentence, and chapter 6 is
    handed the tail of chapter 5. The expert is asked what chapter 6 argues while reading chapter 5,
    and on the next pass reconciles it against the wrong chapter's bullets. Nothing detects it:
    ``_drop_colliding_anchors`` only fires on exactly equal offsets, so ``warnings`` comes back
    empty and the reading looks clean.

    Requiring the match to begin a line is what a heading actually looks like in extracted PDF text,
    and it costs nothing when the title is long enough to be unambiguous. The whitespace-insensitive
    fallback in :func:`_find_title` still runs when no line starts with the title, so a heading
    broken across lines by the layout is found as before.
    """
    for match in re.finditer(re.escape(title), page_text):
        start = match.start()
        if start == 0 or page_text[start - 1] == "\n":
            return start
    return None


def _pdf_font_anchors(
    pages: Sequence[str],
    page_runs: Sequence[Sequence[_Run]],
    page_starts: Sequence[int],
) -> list[_Anchor]:
    """Headings inferred from effective font size, when the PDF carries no outline.

    The body size is whichever size the most *characters* are set in — counting runs instead would
    let a title page full of one-word runs redefine the body. Anything meaningfully larger and short
    enough to be a title becomes a heading, and distinct heading sizes become levels largest-first.
    """
    weights: Counter[float] = Counter()
    for runs in page_runs:
        for run in runs:
            weights[run.size] += len(run.text)
    if not weights:
        return []
    body_size = weights.most_common(1)[0][0]

    candidates: list[tuple[int, float, str]] = []  # (offset, size, title)
    for page_index, runs in enumerate(page_runs):
        cursor = 0
        merged: list[tuple[int, float, str]] = []
        previous_end = -1
        previous_line: float | None = None
        for run in runs:
            found = pages[page_index].find(run.text, cursor)
            offset = found if found >= 0 else cursor
            end = offset + len(run.text)
            cursor = end
            if run.size <= body_size * _HEADING_SIZE_RATIO:
                previous_end = -1
                previous_line = None
                continue
            # A title split across several Tj operands on one baseline is one heading, not several:
            # merge when the previous run was a heading of the same size, on the same line, and the
            # gap between them is only the whitespace the layout inserted.
            same_line = previous_line is not None and abs(previous_line - run.line) < 0.5
            if merged and merged[-1][1] == run.size and same_line and offset - previous_end <= 4:
                head_offset, head_size, head_text = merged[-1]
                merged[-1] = (head_offset, head_size, f"{head_text} {run.text}".strip())
            else:
                merged.append((page_starts[page_index] + offset, run.size, run.text))
            previous_end = end
            previous_line = run.line
        candidates.extend(item for item in merged if len(item[2]) <= _MAX_HEADING_CHARS)

    if not candidates:
        return []
    sizes = sorted({size for _, size, _ in candidates}, reverse=True)
    levels = {size: min(rank + 1, _MAX_HEADING_LEVEL) for rank, size in enumerate(sizes)}
    return [
        _Anchor(title=title, level=levels[size], offset=offset)
        for offset, size, title in candidates
    ]


# --------------------------------------------------------------------------------------
# Shared: anchors -> sections
# --------------------------------------------------------------------------------------


def _slice_sections(doc_text: str, anchors: Sequence[_Anchor]) -> tuple[Section, ...]:
    """Cut ``doc_text`` at the anchors, keeping everything — including what precedes the first.

    Anchors are forced into non-decreasing order first. An anchor that resolves behind its
    predecessor would otherwise produce a negative-length slice, silently dropping text — the
    failure mode this whole module is built to make impossible.
    """
    if not anchors:
        return ()

    ordered: list[_Anchor] = []
    highwater = 0
    for anchor in anchors:
        offset = max(anchor.offset, highwater)
        offset = min(offset, len(doc_text))
        ordered.append(_Anchor(title=anchor.title, level=anchor.level, offset=offset))
        highwater = offset

    sections: list[Section] = []
    preamble = doc_text[: ordered[0].offset].strip()
    if preamble:
        sections.append(Section(title=_FRONT_MATTER_TITLE, level=1, text=preamble))

    for index, anchor in enumerate(ordered):
        end = ordered[index + 1].offset if index + 1 < len(ordered) else len(doc_text)
        body = _strip_leading_title(doc_text[anchor.offset : end], anchor.title)
        sections.append(Section(title=anchor.title, level=anchor.level, text=body.strip()))
    return tuple(sections)


def _strip_leading_title(body: str, title: str) -> str:
    """Drop the heading from the top of its own slice — it is the section's title, not its body.

    Whitespace-insensitive, because an extractor breaks a heading across lines wherever the layout
    did while the outline states it as one string.
    """
    wanted = _WHITESPACE_RUN.sub("", title)
    if not wanted:
        return body
    matched = 0
    for index, char in enumerate(body):
        if matched == len(wanted):
            return body[index:]
        if char.isspace():
            continue
        if char != wanted[matched]:
            return body
        matched += 1
    return "" if matched == len(wanted) else body


def _normalise_levels(sections: Sequence[Section]) -> tuple[Section, ...]:
    """Shift levels so the shallowest heading is level 1 (a page whose top heading is ``##``)."""
    if not sections:
        return ()
    shallowest = min(section.level for section in sections)
    if shallowest <= 1:
        return tuple(sections)
    return tuple(
        Section(title=s.title, level=s.level - shallowest + 1, text=s.text) for s in sections
    )


# --------------------------------------------------------------------------------------
# EPUB — stdlib zipfile + ElementTree, deliberately not EbookLib (AGPL, and unnecessary)
# --------------------------------------------------------------------------------------


@dataclass(slots=True)
class _EpubPackage:
    opf_dir: str
    manifest: dict[str, tuple[str, str, str]] = field(default_factory=dict)
    """id -> (zip path, media type, properties)."""

    spine: list[str] = field(default_factory=list)
    """Zip paths, in reading order."""

    toc_id: str | None = None
    title: str = ""
    author: str | None = None
    published: str | None = None
    publisher: str | None = None


def _extract_epub(path: Path, origin: str) -> ExtractedSource:
    """EPUB via ``zipfile`` and ``ElementTree``.

    Two traps are paid for here rather than rediscovered. First, ElementTree's ``{*}`` namespace
    wildcard works in ``findall`` but silently matches nothing in ``iter()``, so every lookup below
    goes through ``findall``/``find``. Second, hrefs are relative to the *containing* document —
    the OPF for manifest items, the navigation document or NCX for table-of-contents entries — so
    every one is resolved against its own base rather than the zip root.

    The single-file layout is the one that breaks naive readers: a whole book can be one XHTML
    document with the chapters as ``#anchors``. Each href is therefore resolved to a file *and* an
    anchor, and the text is sliced between consecutive anchor offsets.
    """
    try:
        with zipfile.ZipFile(path) as archive:
            package = _read_epub_package(archive, origin)
            documents = _read_epub_documents(archive, package)
            entries = _read_epub_toc(archive, package)
    except zipfile.BadZipFile as exc:
        raise MalformedSourceError(f"{origin}: not a readable EPUB container: {exc}") from exc
    except KeyError as exc:
        raise MalformedSourceError(f"{origin}: EPUB is missing {exc}") from exc

    doc_text, doc_starts = _join_epub_documents(documents)
    _guard_text(doc_text, origin=origin, has_images=False)

    anchors: list[_Anchor] = []
    warnings: list[str] = []
    method = StructureMethod.EPUB_SPINE
    if entries:
        method = entries[0][3]
        for title, level, target, _ in entries:
            file_path, _, anchor = target.partition("#")
            if file_path not in doc_starts:
                warnings.append(f"contents entry {title!r} points outside the spine")
                continue
            # The single-file layout: the chapter is an anchor into a document the spine lists
            # once, so its start is the document's offset plus the anchored element's own.
            within = documents[file_path].ids.get(anchor, 0) if anchor else 0
            anchors.append(_Anchor(title=title, level=level, offset=doc_starts[file_path] + within))
        anchors, collided = _drop_colliding_anchors(anchors)
        if collided:
            warnings.append(f"contents entries sharing one position: {', '.join(collided)}")

    if anchors:
        sections = _slice_sections(doc_text, anchors)
    else:
        method = StructureMethod.EPUB_SPINE
        sections = tuple(
            Section(title=documents[name].title, level=1, text=documents[name].text.strip())
            for name in package.spine
            if name in documents and documents[name].text.strip()
        )

    if not sections:
        sections = (Section(title=package.title or path.stem, level=1, text=doc_text.strip()),)
        method = StructureMethod.WHOLE_DOCUMENT

    return ExtractedSource(
        title=package.title or path.stem,
        kind="epub",
        origin=origin,
        sections=_normalise_levels(sections),
        structure_method=method,
        author=package.author,
        published=package.published,
        site=package.publisher,
        page_count=None,
        warnings=tuple(warnings),
    )


def _read_epub_package(archive: zipfile.ZipFile, origin: str) -> _EpubPackage:
    container = _parse_xml(archive.read("META-INF/container.xml"), "META-INF/container.xml")
    rootfile = container.find(".//{*}rootfile")
    full_path = rootfile.get("full-path") if rootfile is not None else None
    if not full_path:
        raise MalformedSourceError(f"{origin}: container.xml names no OPF root file")

    opf = _parse_xml(archive.read(full_path), full_path)
    opf_dir = _dirname(full_path)
    package = _EpubPackage(opf_dir=opf_dir)

    for item in opf.findall("./{*}manifest/{*}item"):
        item_id = item.get("id")
        href = item.get("href")
        if not item_id or not href:
            continue
        package.manifest[item_id] = (
            _join_zip_path(opf_dir, unquote(href)),
            item.get("media-type", ""),
            item.get("properties", ""),
        )

    spine = opf.find("./{*}spine")
    if spine is not None:
        package.toc_id = spine.get("toc")
        for itemref in spine.findall("./{*}itemref"):
            idref = itemref.get("idref")
            entry = package.manifest.get(idref or "")
            if entry is not None:
                package.spine.append(entry[0])

    metadata = opf.find("./{*}metadata")
    if metadata is not None:
        package.title = _first_text(metadata, "title") or ""
        package.author = _first_text(metadata, "creator")
        package.published = _first_text(metadata, "date")
        package.publisher = _first_text(metadata, "publisher")
    return package


def _first_text(metadata: ET.Element, local_name: str) -> str | None:
    for element in metadata.findall(f"./{{*}}{local_name}"):
        text = (element.text or "").strip()
        if text:
            return text
    return None


@dataclass(frozen=True, slots=True)
class _EpubDocument:
    title: str
    text: str
    ids: dict[str, int]
    """Element ``id`` -> character offset in ``text`` — what makes the single-file layout sliceable."""


def _read_epub_documents(
    archive: zipfile.ZipFile, package: _EpubPackage
) -> dict[str, _EpubDocument]:
    names = set(archive.namelist())
    documents: dict[str, _EpubDocument] = {}
    for zip_path in package.spine:
        if zip_path in documents or zip_path not in names:
            continue
        documents[zip_path] = _render_xhtml(archive.read(zip_path), zip_path)
    return documents


def _join_epub_documents(documents: dict[str, _EpubDocument]) -> tuple[str, dict[str, int]]:
    parts: list[str] = []
    starts: dict[str, int] = {}
    offset = 0
    for index, (name, document) in enumerate(documents.items()):
        if index:
            parts.append("\n\n")
            offset += 2
        starts[name] = offset
        parts.append(document.text)
        offset += len(document.text)
    return "".join(parts), starts


def _read_epub_toc(
    archive: zipfile.ZipFile, package: _EpubPackage
) -> list[tuple[str, int, str, StructureMethod]]:
    """Table of contents entries as ``(title, level, zip path with optional #anchor, method)``."""
    names = set(archive.namelist())

    nav_path = next(
        (path for path, _, props in package.manifest.values() if "nav" in props.split()), None
    )
    if nav_path and nav_path in names:
        entries = _parse_epub3_nav(archive.read(nav_path), nav_path)
        if entries:
            return [(t, level, href, StructureMethod.EPUB_NAV) for t, level, href in entries]

    ncx_path = None
    if package.toc_id and package.toc_id in package.manifest:
        ncx_path = package.manifest[package.toc_id][0]
    if ncx_path is None:
        ncx_path = next(
            (p for p, media, _ in package.manifest.values() if media == _NCX_MEDIA_TYPE), None
        )
    if ncx_path and ncx_path in names:
        entries = _parse_ncx(archive.read(ncx_path), ncx_path)
        return [(t, level, href, StructureMethod.EPUB_NCX) for t, level, href in entries]
    return []


def _parse_epub3_nav(payload: bytes, nav_path: str) -> list[tuple[str, int, str]]:
    root = _parse_xml(payload, nav_path)
    navs = root.findall(".//{*}nav")
    toc = next((n for n in navs if n.get(f"{_EPUB_OPS_NS}type") == "toc"), None)
    if toc is None:
        toc = navs[0] if navs else None
    if toc is None:
        return []
    entries: list[tuple[str, int, str]] = []
    for ordered_list in toc.findall("./{*}ol"):
        _walk_nav_list(ordered_list, 1, _dirname(nav_path), entries)
    return entries


def _walk_nav_list(
    ordered_list: ET.Element, level: int, base_dir: str, out: list[tuple[str, int, str]]
) -> None:
    for item in ordered_list.findall("./{*}li"):
        anchor = item.find("./{*}a")
        href = anchor.get("href") if anchor is not None else None
        if anchor is not None and href:
            title = _element_text(anchor)
            if title:
                out.append((title, level, _resolve_href(base_dir, href)))
        for nested in item.findall("./{*}ol"):
            _walk_nav_list(nested, level + 1, base_dir, out)


def _parse_ncx(payload: bytes, ncx_path: str) -> list[tuple[str, int, str]]:
    root = _parse_xml(payload, ncx_path)
    nav_map = root.find("./{*}navMap")
    if nav_map is None:
        return []
    entries: list[tuple[str, int, str]] = []
    # NCX ``content/@src`` is relative to the NCX document, which in practice is the OPF directory —
    # resolving against the zip root instead is the trap that makes every chapter href miss.
    _walk_nav_points(nav_map, 1, _dirname(ncx_path), entries)
    return entries


def _walk_nav_points(
    parent: ET.Element, level: int, base_dir: str, out: list[tuple[str, int, str]]
) -> None:
    for point in parent.findall("./{*}navPoint"):
        label = point.find("./{*}navLabel/{*}text")
        content = point.find("./{*}content")
        src = content.get("src") if content is not None else None
        # `_element_text`, not `label.text`: an NCX label may hold markup, and a pretty-printed one
        # wraps its text across lines. `Section` normalises too, but the title is also compared and
        # resolved before it ever becomes one.
        title = _element_text(label) if label is not None else ""
        if title and src:
            out.append((title, level, _resolve_href(base_dir, src)))
        _walk_nav_points(point, level + 1, base_dir, out)


def _resolve_href(base_dir: str, href: str) -> str:
    target, _, anchor = unquote(href).partition("#")
    resolved = _join_zip_path(base_dir, target)
    return f"{resolved}#{anchor}" if anchor else resolved


def _join_zip_path(base: str, href: str) -> str:
    """Normalise a zip-internal path. Written out rather than borrowed from ``os.path`` because zip
    entries are always POSIX regardless of the host filesystem."""
    parts: list[str] = []
    for segment in (base.split("/") if base else []) + href.split("/"):
        if segment in ("", "."):
            continue
        if segment == "..":
            if parts:
                parts.pop()
            continue
        parts.append(segment)
    return "/".join(parts)


def _dirname(zip_path: str) -> str:
    head, _, _ = zip_path.rpartition("/")
    return head


def _parse_xml(payload: bytes, where: str) -> ET.Element:
    try:
        return ET.fromstring(_expand_entities(payload))
    except ET.ParseError as exc:
        raise MalformedSourceError(f"{where} is not well-formed XML: {exc}") from exc


def _expand_entities(payload: bytes) -> bytes:
    """Replace HTML named entities XML does not define — ``&nbsp;`` is the usual EPUB offender.

    The decode has to honour the document's **own** declaration. ``payload.decode("utf-8",
    errors="replace")`` discarded it, which ElementTree would otherwise have read: a chapter
    declared ``ISO-8859-1`` had every accented byte turned into ``U+FFFD``, re-encoded, and read
    back as ``Kierkegaardï¿½s rï¿½sumï¿½`` — silent, permanent (Layer 1 never deletes), and applied
    to every non-English book. A UTF-16 chapter, which both OPS 2.0.1 and EPUB 3 permit, was
    destroyed outright and reported as ``not well-formed XML``, sending whoever debugged it at the
    wrong file.

    Re-encoding to UTF-8 without the declaration is what keeps the round trip honest: the returned
    bytes say nothing about their encoding, so ElementTree's default — UTF-8 — is now correct.
    """
    text = _decode_xml(payload)

    def substitute(match: re.Match[str]) -> str:
        name = match.group(1)
        if name in _XML_ENTITIES:
            return match.group(0)
        replacement = _html_entities.html5.get(f"{name};")
        return replacement if replacement is not None else match.group(0)

    expanded = _NAMED_ENTITY.sub(substitute, text)
    # The declaration described the bytes that arrived; these bytes are UTF-8. Leaving it in place
    # would make ElementTree decode UTF-8 as latin-1 — the same corruption, one step later.
    return _XML_DECLARED_ENCODING.sub("", expanded, count=1).encode("utf-8")


def _decode_xml(payload: bytes) -> str:
    """Decode an XML part the way an XML parser must: BOM, then declaration, then the UTF-8 default."""
    for mark, encoding in _BOM_ENCODINGS:
        if payload.startswith(mark):
            return payload.decode(encoding, errors="replace")
    declared = _XML_DECLARED_ENCODING.search(payload[:200].decode("ascii", errors="replace"))
    if declared:
        try:
            return payload.decode(declared.group(1))
        except (LookupError, UnicodeDecodeError):
            pass
    return payload.decode("utf-8", errors="replace")


class _TextBuilder:
    """Accumulates text while answering "what offset will the next character land at?".

    Whitespace is normalised as it is written rather than afterwards, because an id recorded against
    the raw text would point somewhere else once the text was tidied — and a chapter anchor off by
    the length of the source's indentation slices the book in the wrong place.
    """

    __slots__ = ("_length", "_parts", "_pending")

    def __init__(self) -> None:
        self._parts: list[str] = []
        self._length = 0
        self._pending = ""

    def write(self, text: str) -> None:
        if not text:
            return
        collapsed = _WHITESPACE_RUN.sub(" ", text)
        body = collapsed.strip()
        if not body:
            if self._parts:
                self._pending = self._pending or " "
            return
        if collapsed[0].isspace() and self._parts:
            self._pending = self._pending or " "
        self._flush()
        self._parts.append(body)
        self._length += len(body)
        if collapsed[-1].isspace():
            self._pending = " "

    def newline(self, count: int = 1) -> None:
        if not self._parts:
            return
        if len(self._pending.replace(" ", "")) < count:
            self._pending = "\n" * count

    def tell(self) -> int:
        return self._length + (len(self._pending) if self._parts else 0)

    def getvalue(self) -> str:
        return "".join(self._parts)

    def _flush(self) -> None:
        if self._pending and self._parts:
            self._parts.append(self._pending)
            self._length += len(self._pending)
        self._pending = ""


def _local(tag: object) -> str:
    name = str(tag)
    return name.rpartition("}")[2].lower()


def _render_xhtml(payload: bytes, zip_path: str) -> _EpubDocument:
    root = _parse_xml(payload, zip_path)
    builder = _TextBuilder()
    ids: dict[str, int] = {}
    headings: list[str] = []
    title = ""

    for element in root.findall("./{*}head/{*}title"):
        title = (element.text or "").strip()
        break

    _render_element(root, builder, ids, headings)
    text = builder.getvalue()
    ids = {key: min(offset, len(text)) for key, offset in ids.items()}
    return _EpubDocument(
        title=title or (headings[0] if headings else PurePosixPath(zip_path).stem),
        text=text,
        ids=ids,
    )


def _render_element(
    element: ET.Element,
    builder: _TextBuilder,
    ids: dict[str, int],
    headings: list[str],
) -> None:
    tag = _local(element.tag)
    if tag in _SKIPPED_TAGS:
        return

    is_block = tag in _BLOCK_TAGS
    if is_block:
        builder.newline(2 if tag in _PARAGRAPH_TAGS else 1)

    # After the separator, never before it: an anchor recorded ahead of the blank line that a block
    # element opens with points one or two characters early, and the chapter it names then begins
    # with the tail of the chapter above it.
    identifier = element.get("id")
    if identifier:
        ids.setdefault(identifier, builder.tell())

    before = builder.tell()
    builder.write(element.text or "")
    for child in element:
        _render_element(child, builder, ids, headings)
        builder.write(child.tail or "")
    if tag in {"h1", "h2", "h3", "h4", "h5", "h6"}:
        headings.append(builder.getvalue()[before:].strip())
    if is_block:
        builder.newline(2 if tag in _PARAGRAPH_TAGS else 1)


# --------------------------------------------------------------------------------------
# HTML and URLs — trafilatura
# --------------------------------------------------------------------------------------


def _extract_html(path: Path, origin: str) -> ExtractedSource:
    """HTML via trafilatura, in **markdown** output.

    ``output_format="markdown"`` is not a preference: trafilatura's default plain-text output drops
    every heading, and with the headings gone there is no structure for LS-10 to organise by and
    nothing for a re-ingestion to match on. The metadata pass supplies title, date and sitename,
    which the source map records as provenance.
    """
    raw = _read_text_file(path)
    url = origin if _is_url(origin) else None
    body = trafilatura.extract(
        raw,
        url=url,
        output_format="markdown",
        include_comments=False,
        include_tables=True,
        favor_recall=True,
    )
    metadata = _extract_html_metadata(raw, default_url=url)

    title = _clean_meta(getattr(metadata, "title", None)) or path.stem
    if not body or not body.strip():
        raise EmptySourceError(
            f"{origin}: the page carries no extractable article text — trafilatura found only "
            f"navigation, boilerplate or markup"
        )
    _guard_text(body, origin=origin, has_images=False)

    sections = _sections_from_markdown(body)
    method = StructureMethod.HTML_HEADINGS
    if not sections:
        sections = (Section(title=title, level=1, text=body.strip()),)
        method = StructureMethod.WHOLE_DOCUMENT

    return ExtractedSource(
        title=title,
        kind="html",
        origin=origin,
        sections=_normalise_levels(sections),
        structure_method=method,
        author=_clean_meta(getattr(metadata, "author", None)),
        published=_clean_meta(getattr(metadata, "date", None)),
        site=_clean_meta(getattr(metadata, "sitename", None)),
        page_count=None,
        warnings=(),
    )


# --------------------------------------------------------------------------------------
# Plain text and markdown
# --------------------------------------------------------------------------------------


def _extract_text(path: Path, origin: str) -> ExtractedSource:
    """Text and markdown are read as they are (LS-7): no extraction step, only sectioning."""
    body = _read_text_file(path)
    _guard_text(body, origin=origin, has_images=False)

    sections = _sections_from_markdown(body)
    method = StructureMethod.MARKDOWN_HEADINGS
    title = path.stem
    if sections and sections[0].level == 1 and sections[0].title != _FRONT_MATTER_TITLE:
        title = sections[0].title
    if not sections:
        sections = (Section(title=title, level=1, text=body.strip()),)
        method = StructureMethod.WHOLE_DOCUMENT

    return ExtractedSource(
        title=title,
        kind="text",
        origin=origin,
        sections=_normalise_levels(sections),
        structure_method=method,
    )


def _sections_from_markdown(text: str) -> tuple[Section, ...]:
    """Split markdown at its ATX headings, keeping any text that precedes the first one.

    Fenced code blocks are tracked so a ``#`` comment inside one never becomes a chapter. Setext
    headings are deliberately not supported: distinguishing ``---`` under a line from a horizontal
    rule or a frontmatter fence needs guesswork, and a wrong guess invents a chapter.
    """
    lines = text.splitlines()
    heads: list[tuple[int, int, str]] = []
    in_fence = False
    for index, line in enumerate(lines):
        if _FENCE.match(line):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        match = _ATX_HEADING.match(line)
        if match:
            title = match.group(2).strip().rstrip("#").strip()
            if title:
                heads.append((index, len(match.group(1)), title))
    if not heads:
        return ()

    sections: list[Section] = []
    preamble = "\n".join(lines[: heads[0][0]]).strip()
    if preamble:
        sections.append(Section(title=_FRONT_MATTER_TITLE, level=1, text=preamble))
    for position, (line_index, level, title) in enumerate(heads):
        end = heads[position + 1][0] if position + 1 < len(heads) else len(lines)
        body = "\n".join(lines[line_index + 1 : end]).strip()
        sections.append(Section(title=title, level=level, text=body))
    return tuple(sections)


# --------------------------------------------------------------------------------------
# Small helpers
# --------------------------------------------------------------------------------------


def _is_epub_zip(path: Path) -> bool:
    try:
        with zipfile.ZipFile(path) as archive:
            names = archive.namelist()
            if "META-INF/container.xml" not in names:
                return False
            if "mimetype" in names:
                return archive.read("mimetype").strip().decode("ascii", "replace") == _EPUB_MIMETYPE
            return True
    except (zipfile.BadZipFile, OSError, KeyError):
        return False


def _element_text(element: ET.Element) -> str:
    parts = [element.text or ""]
    for child in element:
        parts.append(_element_text(child))
        parts.append(child.tail or "")
    return unicodedata.normalize("NFC", _WHITESPACE_RUN.sub(" ", "".join(parts)).strip())


def _replace_origin(source: ExtractedSource, origin: str) -> ExtractedSource:
    return ExtractedSource(
        title=source.title,
        kind=source.kind,
        origin=origin,
        sections=source.sections,
        structure_method=source.structure_method,
        author=source.author,
        published=source.published,
        site=source.site,
        page_count=source.page_count,
        warnings=source.warnings,
    )
