"""The once-per-turn maintenance flush (rules MA-1 … MA-15).

Layer 2's ``after_agent`` middleware calls :func:`flush` on **both** the success and the failure
path of every agent turn, which is what shapes everything in this module:

* **Total over a broken tree.** A half-written note, an unparseable frontmatter block, an
  unreadable file — each becomes a finding and none stops the flush (MA-14). The flush is the
  mechanism that puts a tree back in order after a failed run, so it cannot itself require the
  tree to be in order.
* **Minimal-touch.** The only frontmatter field Layer 1 ever writes is ``updated``, only on paths
  the caller explicitly names, only through :func:`pkb.core.frontmatter.set_field` (MA-3, MA-5).
  ``created`` is immutable (MA-4) and ``status.*`` / ``review_note`` belong to Layer 2's judgment,
  never to a mechanical pass (MA-6). There is no version control (arch D6), so a rewrite nobody
  asked for is unrecoverable.
* **Flag, never repair.** Broken links and orphans are reported, in the returned
  :class:`~pkb.core.models.FlushReport` and in the owning topic's derived ``index.md`` (MA-10).
  Nothing is moved, deleted, or rewritten because of a finding (MA-9).

The internal order corrects arch §7 (contradiction C7): timestamps are bumped **before** anything
is rendered, so a derived file can never describe the pre-bump state (MA-2). Link and orphan
analysis also runs before rendering, because its findings are the ``## Maintenance flags`` section
of the topic index that the same call is about to write.

The analysis itself is :mod:`pkb.core.analysis`, re-exported below. It is not merely a duty of the
flush: GE-5 requires a full rebuild to render the same bytes as an incremental flush, so
:func:`~pkb.core.generators.regenerate_all` derives the same flags for itself when no caller hands
it any. This module passes the ones it already computed, so the work is done once per turn.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import date
from pathlib import Path

from pkb.core import frontmatter, paths

# ``find_broken_links`` and ``find_orphans`` are duties of the flush (MA-7, MA-8) and §6 documents
# them here, so they stay part of this module's surface. They are *implemented* one layer down
# because the generators need them as well — GE-5 makes the ``## Maintenance flags`` section a
# function of the tree, not of the caller — and this module imports the generators.
from pkb.core.analysis import find_broken_links, find_orphans, read_text
from pkb.core.errors import Finding, NotATopicRootError, Severity, sort_findings
from pkb.core.generators import regenerate_all, write_derived
from pkb.core.models import FileClass, FileRole, FlushReport, KbSnapshot, ScanRequest
from pkb.core.scan import scan

__all__ = [
    "MAINTENANCE_ORIGIN",
    "ON_DEMAND_ORIGIN",
    "build_scan_requests",
    "bump_updated",
    "find_broken_links",
    "find_orphans",
    "flush",
    "scan_request_for",
]

MAINTENANCE_ORIGIN = "maintenance"
"""``ScanRequest.origin`` for a request the flush raised itself (MA-12)."""

ON_DEMAND_ORIGIN = "on-demand"
"""``ScanRequest.origin`` for a request Layer 2 raises for a whole topic (MA-12)."""

_SCAN_TRIGGER_ROLES: frozenset[FileRole] = frozenset(
    {
        FileRole.NOTE,
        FileRole.NOTES_SUMMARY,
        FileRole.REFERENCE,
        FileRole.REFERENCES_SUMMARY,
        FileRole.EXTENSION_ITEM,
        FileRole.EXTENSION_SUMMARY,
    }
)
"""Files under ``notes/``, ``references/`` and extension folders — the conflict-scan triggers.

``topic.md``, ``expert.md``, skills, assets and derived files are excluded: a scan compares the
knowledge a topic states, and those files either state none or are rebuilt from the ones that do.
"""


# --------------------------------------------------------------------------------------
# The flush (MA-1, MA-2)
# --------------------------------------------------------------------------------------


def flush(kb_root: Path, touched_paths: Iterable[Path | str] = (), *, today: date) -> FlushReport:
    """Run the six duties of one maintenance turn and report what happened (MA-1).

    In order, and the order is the rule (MA-2):

    1. bump ``updated`` on the touched content files (MA-3);
    2. walk the tree once (decision C);
    3. find broken links and orphans (MA-7, MA-8);
    4. regenerate every derived file, routing those findings into the owning topic's
       ``## Maintenance flags`` section (MA-10);
    5. build the coalesced conflict-scan requests (MA-12);
    6. return everything as data — no queue, no database, no model call (MA-11).

    ``touched_paths`` is the middleware's record of what the turn wrote; it may hold absolute paths
    or knowledge-base-relative POSIX strings, and anything outside the tree is ignored. Scanning
    the tree for recently-modified files instead is forbidden (MA-3): it would dirty every file
    the human edited by hand and destroy idempotence.

    **Sole-writer contract.** This function acquires no lock of its own (MA-15); it assumes it is
    the only writer for its duration, and Layer 2 takes the global knowledge-base write lock around
    it. It is safe to call twice and safe to call after a partial or failed agent run (MA-14): a
    second consecutive flush over an unchanged tree writes nothing at all.
    """
    touched = _normalize_all(kb_root, touched_paths)
    stamped, stamp_findings = _bump_updated(kb_root, touched, today)

    snapshot = scan(kb_root)
    # Computed here and handed over rather than left to the generators, which would otherwise
    # derive the identical list themselves (GE-5): the flush needs them for its own report, and
    # the analysis reads every content file, so once per turn is the whole point.
    flags = [*find_broken_links(kb_root, snapshot), *find_orphans(kb_root, snapshot)]

    report = regenerate_all(kb_root, snapshot=snapshot, flags=flags)
    report.stamped = stamped
    report.scan_requests = build_scan_requests(
        snapshot, touched, origin=MAINTENANCE_ORIGIN, requested_at=today
    )
    # The generators deliberately do not fold in the walk's own findings so that a caller reporting
    # both does not report either twice; folding them in here is what makes the flush's report the
    # complete picture of the turn (MA-13, MA-14).
    report.findings = sort_findings(
        dict.fromkeys([*report.findings, *snapshot.findings, *flags, *stamp_findings])
    )
    return report


# --------------------------------------------------------------------------------------
# Timestamps (MA-3, MA-4, MA-5, MA-6)
# --------------------------------------------------------------------------------------


def bump_updated(kb_root: Path, paths: Iterable[Path | str], *, today: date) -> list[str]:
    """Set ``updated: today`` on the named content files, and nothing else (MA-3, MA-5).

    Returns the knowledge-base-relative paths actually rewritten, sorted. A path is skipped when it
    is outside the tree, missing, not markdown (FM-14), or not an authored file — a derived
    ``index.md``, a deepagents ``SKILL.md`` and a media file are never stamped, because ``updated``
    is not part of their schema.

    Three properties make this safe to run after a failed turn:

    * **Explicit set only.** Only the paths the caller names are considered; the tree is never
      scanned for recently-modified files (MA-3).
    * **One field.** The write goes through :func:`pkb.core.frontmatter.set_field`, which rewrites
      the target key's lines and leaves every other byte — including the whole body and any
      ``status.*`` tag or review note — untouched (MA-5, MA-6, FM-11).
    * **Idempotent.** A file already stamped ``today`` renders identical bytes, so it is not
      written and not reported. Two flushes on the same day are indistinguishable from one (MA-3).

    ``created`` is never read and never written (MA-4).

    Two silences are deliberate and both mean "no bytes changed", which is what this return value
    promises. A file whose frontmatter block Layer 1 cannot **read** — no block at all, an
    unterminated one, or one whose keys do not each own a line — is returned unchanged by
    ``set_field`` and therefore never gains one (FM-11); the defect itself is reported by the walk
    (VA-39), which is the right channel for it (MA-9). And a caller-supplied path that differs from
    the on-disk name only by case is refused rather than silently stamping the other file, which
    surfaces as ``UPDATED_WRITE_FAILED`` (PA-17).
    """
    return _bump_updated(kb_root, paths, today)[0]


def _bump_updated(
    kb_root: Path, touched: Iterable[Path | str], today: date
) -> tuple[list[str], list[Finding]]:
    """:func:`bump_updated` plus the write failures its return type has nowhere to put."""
    stamped: list[str] = []
    findings: list[Finding] = []
    for relative in _normalize_all(kb_root, touched):
        try:
            if _stamp(kb_root, relative, today):
                stamped.append(relative)
        except OSError as exc:
            findings.append(
                Finding(
                    code="UPDATED_WRITE_FAILED",
                    severity=Severity.ERROR,
                    message=f"the updated timestamp could not be written: {exc}",
                    rule_id="MA-3",
                    path=relative,
                    field="updated",
                    hint="check the file is writable",
                )
            )
    return sorted(stamped, key=paths.sort_key), findings


def _stamp(kb_root: Path, relative: str, today: date) -> bool:
    """Rewrite one file's ``updated`` line; True when its bytes changed (MA-3)."""
    target = kb_root / relative
    if not target.is_file() or not relative.endswith(paths.MARKDOWN_SUFFIX):
        return False
    _, file_class = paths.classify(kb_root, target)
    if file_class is not FileClass.AUTHORED:
        return False

    text = read_text(target)
    if text is None:
        return False
    # The one field Layer 1 may write, at the one call site that writes it (MA-5, MA-6).
    stamped = frontmatter.set_field(text, "updated", today)
    if stamped == text:
        return False
    # Same atomic replace-and-skip-identical primitive the generators use; nothing about it is
    # specific to derived files, and one implementation cannot drift from itself (GE-8).
    return write_derived(target, stamped)


# --------------------------------------------------------------------------------------
# Conflict-scan requests (MA-11, MA-12)
# --------------------------------------------------------------------------------------


def build_scan_requests(
    snapshot: KbSnapshot,
    changed: Iterable[Path | str],
    *,
    origin: str = MAINTENANCE_ORIGIN,
    requested_at: date,
) -> list[ScanRequest]:
    """The conflict scans this turn's changes call for, coalesced per topic (MA-12).

    Data only (MA-11): Layer 1 opens no queue, no database and no model client, and writes no
    machine state into the tree. Persisting these is Layer 2/3's job, and the semantic comparison
    itself is a Topic Expert skill.

    One request per topic, never one per file — the scan is a whole-topic comparison, so five
    changed notes in one topic must not run the same expensive scan five times. Triggers are
    creates *and* modifies (contradiction C20: an edited note can newly contradict a reference)
    under ``notes/``, ``references/`` and extension folders; derived files, ``topic.md``, skills
    and assets never trigger one. Requests come back in topic-discovery order and each request's
    paths are sorted, so the result is a deterministic function of the changed set.
    """
    grouped: dict[str, list[str]] = {}
    for relative in _normalize_all(snapshot.root, changed):
        record = snapshot.files.get(relative)
        if record is None or record.topic_path is None:
            continue
        if record.file_class is not FileClass.AUTHORED or record.role not in _SCAN_TRIGGER_ROLES:
            continue
        grouped.setdefault(record.topic_path, []).append(relative)

    return [
        scan_request_for(
            snapshot, topic_path, grouped[topic_path], origin=origin, requested_at=requested_at
        )
        for topic_path in snapshot.topics
        if topic_path in grouped
    ]


def scan_request_for(
    snapshot: KbSnapshot,
    topic_path: str,
    changed: Iterable[str] = (),
    *,
    origin: str = ON_DEMAND_ORIGIN,
    requested_at: date,
) -> ScanRequest:
    """One request for one topic, with or without a changed set (MA-12).

    An empty ``changed`` is legitimate and is how Layer 2 asks for a whole-topic re-scan that no
    file change triggered — the request addresses the topic, not the files.

    Raises :class:`~pkb.core.errors.NotATopicRootError` for a path that names no topic root.
    """
    topic = snapshot.topics.get(topic_path)
    if topic is None:
        raise NotATopicRootError(f"{topic_path!r} is not a topic root in this knowledge base")
    return ScanRequest(
        topic_id=topic.agent_id,
        topic_path=topic.path,
        changed_paths=tuple(sorted(dict.fromkeys(changed), key=paths.sort_key)),
        origin=origin,
        requested_at=requested_at,
    )


# --------------------------------------------------------------------------------------
# Path plumbing
# --------------------------------------------------------------------------------------


def _normalize_all(kb_root: Path, values: Iterable[Path | str]) -> list[str]:
    """Caller-supplied paths as knowledge-base-relative POSIX strings, deduplicated in order.

    Absolute paths, relative paths and POSIX strings are all accepted, because the caller is
    middleware translating from an agent's own view of the tree (arch I3). Anything outside the
    knowledge base is dropped rather than raising: the flush runs after a failed turn and must not
    fail on the wreckage of one.
    """
    if isinstance(values, str | Path):  # a lone path is not a sequence of paths
        values = [values]
    seen: dict[str, None] = {}
    for value in values:
        relative = _kb_relative(kb_root, value)
        if relative is not None:
            seen[relative] = None
    return list(seen)


def _kb_relative(kb_root: Path, value: Path | str) -> str | None:
    """One path as a knowledge-base-relative POSIX string, or ``None`` when it is outside."""
    candidate = Path(value)
    if not candidate.is_absolute():
        candidate = kb_root / candidate
    try:
        relative = paths.rel(kb_root, candidate)
    except ValueError:
        return None
    return None if relative == "." else relative
