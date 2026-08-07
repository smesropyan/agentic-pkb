"""Link and orphan analysis over one snapshot (rules MA-7, MA-8, PA-12).

These are the ``## Maintenance flags`` of a topic's ``index.md``. They live here, below the
generators, for one reason: GE-5 requires a full rebuild to render the same bytes as an incremental
flush, so the flags section has to be a function of the **tree**, not of whoever called. That means
the generators must be able to compute it themselves — and since :mod:`pkb.core.maintenance` imports
the generators, the analysis cannot live there without an import cycle.

Everything here is a pure function of ``(kb_root, snapshot)``:

* **Non-blocking and non-mutating** (MA-9). Nothing is moved, deleted or rewritten because of a
  finding; the analysis only ever answers questions.
* **Answered from the snapshot** (decision C). Existence is decided from the walk's own file map,
  never from a fresh ``stat``, so the answer stays case-exact on a case-insensitive host (PA-17) and
  consistent with the tree every other duty of the same flush saw.
* **Deterministic** (GE-4). Both entry points return findings through
  :func:`~pkb.core.errors.sort_findings`, so the rendered section is byte-stable.

The one filesystem read left is :func:`read_text`, which the link scanner needs because a finding
cites a **line number** and the snapshot keeps only the parsed body.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from urllib.parse import unquote

from pkb.core import paths
from pkb.core.errors import Finding, Severity, sort_findings
from pkb.core.models import FileClass, FileRecord, FileRole, KbSnapshot, TopicRecord

__all__ = [
    "find_broken_links",
    "find_orphans",
    "read_text",
]


def read_text(path: Path) -> str | None:
    """The file's text, or ``None`` when it cannot be read as UTF-8 (MA-14).

    ``newline=""`` keeps CRLF files byte-exact, which matters twice: a targeted field write must
    preserve the human's line endings (FM-11), and a link's line number must count the lines the
    file actually has. A single definition on purpose — two readers disagreeing about ``newline``
    is exactly how the layer once produced a phantom broken link (MA-7).

    A file the walk already reported as unreadable is simply skipped by every caller here.
    """
    try:
        with path.open(encoding="utf-8", newline="") as handle:
            return handle.read()
    except (OSError, UnicodeDecodeError):
        return None


# --------------------------------------------------------------------------------------
# Links (MA-7)
# --------------------------------------------------------------------------------------

_INLINE_LINK = re.compile(r"!?\[[^\]]*\]\(([^()]*)\)")
"""``[text](target)`` and the image form ``![text](target)`` (MA-7)."""

_REFERENCE_DEFINITION = re.compile(r"^ {0,3}\[[^\]]+\]:\s+(\S+)")
"""A reference-style link definition, which is line-anchored by CommonMark (MA-7)."""

_FENCE = re.compile(r"^ {0,3}(?:```|~~~)")
_URL_SCHEME = re.compile(r"^[A-Za-z][A-Za-z0-9+.\-]*:")


@dataclass(frozen=True, slots=True)
class _Link:
    """One link target as written, with the 1-based line of the file it appears on."""

    target: str
    line: int


def find_broken_links(kb_root: Path, snapshot: KbSnapshot) -> list[Finding]:
    """Report link targets that do not exist or escape the knowledge base (MA-7).

    Sources are every non-derived markdown file: a generated index links to everything by
    construction, so including it would report the same defect once per index (GE-3, MA-8).

    Targets are resolved lexically against the containing file's directory, with the ``#fragment``
    stripped and percent-encoding decoded — the generators percent-encode their own link targets
    (PA-18), so a human who copies one must not be told it is broken. Anything carrying a URL
    scheme is skipped outright: Layer 1 makes no network call, so an external URL can only ever be
    unverifiable, never broken (CX-1). Wiki-links ``[[x]]`` and heading anchors are deferred (§5);
    the link kind lives in the finding code so adding them later breaks nothing.

    Existence is answered from the snapshot, never from a fresh ``stat``: that keeps the answer
    case-exact on a case-insensitive host (PA-17) and consistent with the tree every other duty of
    the flush saw. A target the flush is about to *generate* counts as existing (PA-12) — a note
    linking its topic's ``index.md`` is not broken merely because this is the first flush.
    """
    directories = _directories(snapshot)
    findings: list[Finding] = []
    for record in snapshot.files.values():
        if not _is_link_source(record):
            continue
        for link in _links_in(kb_root, record):
            finding = _check_link(kb_root, snapshot, directories, record, link)
            if finding is not None:
                findings.append(finding)
    return sort_findings(findings)


def _is_link_source(record: FileRecord) -> bool:
    """Every markdown file except the derived ones (MA-7, GE-3)."""
    return record.is_markdown and record.file_class is not FileClass.DERIVED


def _links_in(kb_root: Path, record: FileRecord) -> list[_Link]:
    """Every link in one file's body, with file-relative line numbers (MA-7).

    Fenced code blocks are skipped: their contents are literal text, so a link written as an
    example there is not a link at all and flagging it would train an agent to mangle its own
    examples.
    """
    text = read_text(kb_root / record.path)
    if text is None:
        return []
    body, offset = _body_and_offset(text, record)

    links: list[_Link] = []
    fenced = False
    for number, line in enumerate(body.splitlines(), 1):
        if _FENCE.match(line):
            fenced = not fenced
            continue
        if fenced:
            continue
        for match in _INLINE_LINK.finditer(line):
            target = _clean_target(match.group(1))
            if target:
                links.append(_Link(target=target, line=offset + number))
        definition = _REFERENCE_DEFINITION.match(line)
        if definition is not None:
            target = _clean_target(definition.group(1))
            if target:
                links.append(_Link(target=target, line=offset + number))
    return links


def _body_and_offset(text: str, record: FileRecord) -> tuple[str, int]:
    """The body to scan and how many lines precede it, so a finding cites the file's own line.

    The parser hands back the body byte-exactly, so the frontmatter block's height is the number of
    newlines in what the body is *not*. A file whose frontmatter failed to parse is scanned whole:
    it is already reported (VA-39), and its links are still worth checking (MA-14).
    """
    doc = record.doc
    if doc is None or doc.error is not None or not text.endswith(doc.body):
        return text, 0
    return doc.body, text.count("\n", 0, len(text) - len(doc.body))


def _clean_target(raw: str) -> str:
    """The target of a link, without its optional title and without angle brackets."""
    target = raw.strip()
    if target.startswith("<") and target.endswith(">"):
        return target[1:-1].strip()
    head = target.split(maxsplit=1)
    return head[0] if head else ""


def _check_link(
    kb_root: Path,
    snapshot: KbSnapshot,
    directories: frozenset[str],
    record: FileRecord,
    link: _Link,
) -> Finding | None:
    """One link, checked. ``None`` when it is fine or deliberately out of scope (MA-7)."""
    if _URL_SCHEME.match(link.target) or link.target.startswith("//"):
        return None
    without_fragment = link.target.split("#", 1)[0]
    if not without_fragment:
        return None  # a bare heading anchor: deferred (§5)

    resolved = _resolve(record.path, unquote(without_fragment))
    if resolved is None:
        return Finding(
            code="LINK_ESCAPES_KB_ROOT",
            severity=Severity.WARNING,
            message=f"link target `{link.target}` resolves outside the knowledge base",
            rule_id="MA-7",
            path=record.path,
            value=link.target,
            line=link.line,
            hint="link to a path inside the knowledge base, or use a full URL",
        )

    wants_main_file = without_fragment.endswith("/")
    if _target_exists(kb_root, snapshot, directories, resolved, wants_main_file=wants_main_file):
        return None
    return Finding(
        code="BROKEN_LINK",
        severity=Severity.WARNING,
        message=f"link target `{link.target}` does not exist",
        rule_id="MA-7",
        path=record.path,
        value=link.target,
        line=link.line,
        hint="fix the target, or create the file it names",
    )


def _resolve(source: str, target: str) -> str | None:
    """A link target as a knowledge-base-relative path, or ``None`` when it escapes the root.

    Purely lexical — ``..`` is popped rather than followed — so no symlink can smuggle a link out
    of the tree and no filesystem call is needed to answer the question.
    """
    if target.startswith("/"):
        return None
    parts: list[str] = list(PurePosixPath(source).parent.parts)
    for part in PurePosixPath(target).parts:
        if part == ".":
            continue
        if part == "..":
            if not parts:
                return None
            parts.pop()
        else:
            parts.append(part)
    return "/".join(parts)


def _target_exists(
    kb_root: Path,
    snapshot: KbSnapshot,
    directories: frozenset[str],
    resolved: str,
    *,
    wants_main_file: bool,
) -> bool:
    """Whether a resolved target names something (MA-7).

    A trailing slash means the folder-hosted item's main file, per MA-7: ``[item]/`` is a link to
    the item, and the item's text is ``[item]/[item].md``.
    """
    if not resolved:
        return True  # the knowledge-base root itself
    if wants_main_file:
        return _main_file_of(resolved) in snapshot.files
    if resolved in snapshot.files or resolved in directories:
        return True
    # Not on disk yet, but this very flush writes it (PA-12).
    return paths.is_generated(kb_root, kb_root / resolved)


def _main_file_of(directory: str) -> str:
    """``notes/steak`` → ``notes/steak/steak.md`` (PA-17, VA-16)."""
    name = PurePosixPath(directory).name
    return f"{directory}/{name}{paths.MARKDOWN_SUFFIX}"


def _directories(snapshot: KbSnapshot) -> frozenset[str]:
    """Every directory the walk saw, as knowledge-base-relative paths."""
    found: set[str] = set(snapshot.topics)
    for path in snapshot.files:
        parent = PurePosixPath(path).parent
        while str(parent) != ".":
            found.add(str(parent))
            parent = parent.parent
    return frozenset(found)


# --------------------------------------------------------------------------------------
# Orphans (MA-8) and stale derived files (PA-12)
# --------------------------------------------------------------------------------------


def find_orphans(kb_root: Path, snapshot: KbSnapshot) -> list[Finding]:
    """Report the four structural orphans and the stale derived files (MA-8, PA-12, Q8).

    "Orphan" is used once in the README and never defined. The intuitive "not listed in an index"
    reading is vacuous — the index is generated from this same walk — and "nothing links to it" is
    wrong, because a note is legitimately reachable only through its tags and its index. What is
    left is four structural defects, each with its own code so later tuning is non-breaking:

    ``ORPHAN_ITEM_FOLDER``
        a folder-hosted item directory with no ``<item>/<item>.md``, so it has no text at all.
    ``ORPHAN_ASSET``
        a file under ``media/`` or inside a reference folder that the sibling main ``.md`` never
        references — a binary no agent will ever open, because agents read the text.
    ``ORPHAN_FILE``
        authored markdown inside a topic that is in none of ``notes/``, ``references/``, or an
        extension folder, and is not one of the structurally required files.
    ``ORPHAN_OUTSIDE_TOPIC``
        authored markdown outside every topic root that is not a reserved root file.

    ``STALE_DERIVED_FILE`` (PA-12) rides along because it is the same kind of answer — a file the
    structure does not account for — and because this is the one pass that reaches it: a stale
    ``index.md`` is exempt from the content rules, excluded from every index, and skipped by the
    authored-only predicates above.

    Flagging is non-blocking and non-mutating (MA-9): nothing here moves, deletes, or rewrites.
    """
    findings: list[Finding] = []
    referenced: dict[str, frozenset[str]] = {}
    for topic in snapshot.topics.values():
        findings += _orphan_item_folders(snapshot, topic)
        findings += _orphan_assets(kb_root, snapshot, topic, referenced)
    findings += _orphan_markdown(snapshot)
    findings += _stale_derived(kb_root, snapshot)
    return sort_findings(findings)


def _orphan_item_folders(snapshot: KbSnapshot, topic: TopicRecord) -> list[Finding]:
    """Item directories with no main file (MA-8). Same defect VA-16 gates writes on.

    "Same defect" is meant literally, including the exclusions: :func:`_item_folders` skips
    ``media/`` exactly as ``validation._check_item_folders`` does, so the two never disagree about
    whether a given directory is an item.
    """
    findings: list[Finding] = []
    for folder, main in _item_folders(snapshot, topic).items():
        if main in snapshot.files:
            continue
        findings.append(
            Finding(
                code="ORPHAN_ITEM_FOLDER",
                severity=Severity.WARNING,
                message=f"the item folder has no `{_within(topic, main)}`, so it holds no text",
                rule_id="MA-8",
                path=folder,
                hint=f"add {_within(topic, main)}, or move the folder's contents into an item",
            )
        )
    return findings


def _orphan_assets(
    kb_root: Path,
    snapshot: KbSnapshot,
    topic: TopicRecord,
    referenced: dict[str, frozenset[str]],
) -> list[Finding]:
    """Assets their sibling main file never links (MA-8).

    Only the sibling main ``.md`` counts as a referrer. A photo beside a note is there *for* that
    note; if the note's text never points at it, no agent reading the knowledge base will ever
    learn it exists.
    """
    findings: list[Finding] = []
    for record in snapshot.files_in_topic(topic.path):
        if record.is_markdown:
            continue
        within = _asset_main_file(_inner_parts(topic, record.path))
        if within is None:
            continue
        main = f"{topic.path}/{within}"
        if main not in snapshot.files:
            continue  # no main file at all: ORPHAN_ITEM_FOLDER already says so
        if record.path in _referenced_paths(kb_root, snapshot, main, referenced):
            continue
        findings.append(
            Finding(
                code="ORPHAN_ASSET",
                severity=Severity.WARNING,
                message=f"not referenced by `{within}`",
                rule_id="MA-8",
                path=record.path,
                hint=f"link it from {within}, or delete it",
            )
        )
    return findings


def _orphan_markdown(snapshot: KbSnapshot) -> list[Finding]:
    """Authored markdown the standard structure does not describe (MA-8).

    Both codes fall out of one predicate: the location → role table already returns ``UNKNOWN`` for
    exactly the markdown no section of an index describes (VA-38, PA-11), and ``topic_path`` says
    whether it is inside a topic at all.
    """
    findings: list[Finding] = []
    for record in snapshot.files.values():
        if record.role is not FileRole.UNKNOWN or record.file_class is not FileClass.AUTHORED:
            continue
        if record.topic_path is None:
            findings.append(
                Finding(
                    code="ORPHAN_OUTSIDE_TOPIC",
                    severity=Severity.WARNING,
                    message="outside every topic root, so no index lists it",
                    rule_id="MA-8",
                    path=record.path,
                    hint="move it into a topic's notes/ or references/, or delete it",
                )
            )
        else:
            findings.append(
                Finding(
                    code="ORPHAN_FILE",
                    severity=Severity.WARNING,
                    message="not in notes/, references/, or an extension folder, so no index "
                    "lists it as an item",
                    rule_id="MA-8",
                    path=record.path,
                    hint="move it under notes/ or references/, or delete it",
                )
            )
    return findings


def _stale_derived(kb_root: Path, snapshot: KbSnapshot) -> list[Finding]:
    """An ``index.md`` derived by name that no generator owns (PA-12).

    PA-12 states two obligations for this file and only the first was implemented: it is *never
    written and never deleted* by Layer 1 — which holds, and must keep holding, because Layer 1
    flags and never repairs (§5, MA-9) — and it is *a stale-file flag*, which is this.

    Nothing else in the layer can see it. It is exempt from the content rules (class ``DERIVED``,
    VA-5), excluded from every index (GE-15), and invisible to the orphan predicates above, which
    all require ``AUTHORED``. The realistic case is a sub-topic whose ``topic.md`` was deleted or
    renamed: ``sub-topics/Optics/index.md`` then outlives the topic it indexed and goes on looking
    authoritative forever, with no diagnostic anywhere.

    Answered here rather than in ``validation._rules_result`` on purpose: this pass only ever sees
    files that exist, so it cannot misfire on a path VA-1 is asked about *before* it is written —
    a new ``NewTopic/index.md`` is not stale, it is merely early.
    """
    return [
        Finding(
            code="STALE_DERIVED_FILE",
            severity=Severity.WARNING,
            message="an index.md here is generated by nobody, so nothing keeps it true",
            rule_id="PA-12",
            path=record.path,
            hint="delete it by hand, or restore the topic.md that made this a topic root",
        )
        for record in snapshot.files.values()
        if record.file_class is FileClass.DERIVED
        and not paths.is_generated(kb_root, record.abs_path)
    ]


def _item_folders(snapshot: KbSnapshot, topic: TopicRecord) -> dict[str, str]:
    """Item directory → the main file the folder-hosted convention requires (VA-16, MA-8).

    A directory holding any file at any depth counts as an item folder; the snapshot is the source
    of truth, so a directory containing nothing at all is invisible here — it holds no content, so
    nothing of the human's can be orphaned by it.

    ``media`` is excluded, exactly as ``validation._check_item_folders`` excludes it: it is a
    structural directory (PA-6) holding binaries for the item that owns it, never an item itself.
    Without the guard a section-level ``recipes/media/`` was reported as an item folder missing its
    ``recipes/media/media.md`` — a false statement rendered into the human's own ``index.md``
    (MA-10), whose hint asked them to create an item literally named ``media``.
    """
    sections = {paths.NOTES_DIR, paths.REFERENCES_DIR, *topic.extension_folders}
    folders: dict[str, str] = {}
    for record in snapshot.files_in_topic(topic.path):
        parts = _inner_parts(topic, record.path)
        if len(parts) < 3 or parts[0] not in sections or parts[1] == paths.MEDIA_DIR:
            continue
        folder = f"{topic.path}/{parts[0]}/{parts[1]}"
        folders[folder] = _main_file_of(folder)
    return folders


def _asset_main_file(parts: Sequence[str]) -> str | None:
    """The topic-relative main ``.md`` that should reference an asset, or ``None`` (MA-8).

    Two shapes carry the convention: anything below a ``media/`` directory belongs to the item that
    owns that ``media/``, and anything inside ``references/<source>/`` belongs to that source's
    main file (references have no ``media/`` convention at all, VA-24).
    """
    if paths.MEDIA_DIR in parts[:-1]:
        item = list(parts[: parts.index(paths.MEDIA_DIR)])
    elif parts and parts[0] == paths.REFERENCES_DIR and len(parts) >= 3:
        item = list(parts[:2])
    else:
        return None
    if len(item) < 2:
        return None
    return _main_file_of("/".join(item))


def _referenced_paths(
    kb_root: Path, snapshot: KbSnapshot, main: str, cache: dict[str, frozenset[str]]
) -> frozenset[str]:
    """Every knowledge-base path one file links to, memoized per call (MA-7, MA-8)."""
    known = cache.get(main)
    if known is not None:
        return known
    record = snapshot.files[main]
    targets: set[str] = set()
    for link in _links_in(kb_root, record):
        if _URL_SCHEME.match(link.target):
            continue
        resolved = _resolve(record.path, unquote(link.target.split("#", 1)[0]))
        if resolved:
            targets.add(resolved)
    cache[main] = frozenset(targets)
    return cache[main]


def _inner_parts(topic: TopicRecord, path: str) -> tuple[str, ...]:
    """A file's path relative to its topic root, split into segments."""
    return tuple(path.removeprefix(f"{topic.path}/").split("/"))


def _within(topic: TopicRecord, path: str) -> str:
    """A knowledge-base path shown relative to the topic root, the frame a topic index reads in."""
    return path.removeprefix(f"{topic.path}/")
