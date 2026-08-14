"""The single deterministic walk of the knowledge-base tree (decision C).

Validation, generation and maintenance all read the :class:`~pkb.core.models.KbSnapshot` this
module produces rather than walking the tree themselves. Three walks would drift — GE-4's
byte-determinism and MA-8's orphan analysis need *the same* view of the tree — so there is exactly
one, here.

Three properties are the whole point and every design choice below serves them:

* **Deterministic.** Every directory listing is sorted with :func:`pkb.core.paths.sort_key` before
  anything is recorded, and the file map is re-sorted by KB-relative path at the end. The snapshot
  is therefore a pure function of the tree's *contents*, never of the order the filesystem happened
  to hand back (GE-4).
* **Total.** A degraded tree — unparseable frontmatter, undecodable bytes, a topic root smuggled
  into ``notes/`` — produces findings, never an exception (MA-14, CX-5). The only exception this
  module raises is :class:`~pkb.core.errors.KbNotFoundError`, for a root that is not a directory at
  all. An *empty* directory is a valid, empty knowledge base (GE-29).
* **Complete.** Every non-ignored file is recorded, including the assets and skills that no
  generator renders, because orphan and link analysis need them (GE-15) and a file missing from the
  walk is invisible to every agent (GE-25).

Recording and discovery are two jobs, not one, and the walk keeps them apart: it enters every
non-ignored directory to record files, but only the directories PA-5 and VA-36 allow can yield a
topic root. :data:`RECORD_ONLY_DIRS` is that seam.

Classification is delegated wholesale to :func:`pkb.core.paths.classify`, and addressing to
``topic_tag_for`` / ``agent_id_for``, so the location → role table lives in exactly one place.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from pkb.core import diagnostics, frontmatter, paths
from pkb.core.errors import (
    Finding,
    KbNotFoundError,
    NotATopicRootError,
    Severity,
    sort_findings,
)
from pkb.core.models import FileClass, FileRecord, KbSnapshot, Metadata, ParsedDocument, TopicRecord

__all__ = ["FALLBACK_TOPIC_SLUG", "RECORD_ONLY_DIRS", "scan"]

FALLBACK_TOPIC_SLUG = "untitled"
"""Stand-in segment for a topic folder whose name slugifies to nothing (see :func:`scan`)."""

_TOPIC_ROUTES = frozenset({paths.SUBTOPICS_DIR, paths.NOTES_DIR, paths.REFERENCES_DIR})
"""The structural directories a topic root may still be *reached* through.

``sub-topics/`` is the documented route (PA-4). ``notes/`` and ``references/`` are not, but VA-36
names them — together with extension folders, which are not structural at all — as the places a
smuggled ``topic.md`` is discovered anyway, warned about, and kept, "so it is never invisible".
"""

RECORD_ONLY_DIRS = paths.STRUCTURAL_DIRS - _TOPIC_ROUTES
"""Directories the walk enters to **record** files but never to **discover** a topic root.

The two concerns are separate and this constant is the seam between them. PA-5 fixes the canonical
walk as "never descending into ``references/``, ``notes/``, ``media/``, ``skills/``, or
dot-directories"; VA-36 re-opens *discovery* for the first two and for extension folders, and names
neither ``media/`` nor ``skills/``. So a ``topic.md`` under those two is not a topic root — while
the files beside it are still real files that GE-15 ("excluded assets are still visited by
orphan/link analysis") and GE-25 ("a file is never silently dropped") require the walk to record.

Treating one as a topic root is not cosmetic: it mints a ``topic.*`` tag and an agent id for a
directory VA-6 says "never participate[s] in index or tag generation", routes the root catalog to
it, and makes ``regenerate_all`` write a derived ``index.md`` *into* the human's media or skills
folder — the exact directory GE-15 lists among the topic-index exclusions.

The walk owns only half of the policy. :func:`pkb.core.paths.owning_topic_root` re-derives ownership
from the tree, so a ``topic.md`` smuggled under ``skills/`` still captures its siblings there and
:func:`pkb.core.paths.classify` reports ``skills/voice/SKILL.md`` as AUTHORED rather than SKILL. The
two must be stopped by the same rule; until they are, ``FileRecord.topic_path`` (this walk) and
``owning_topic_root`` disagree for files under a smuggled root.
"""


# --------------------------------------------------------------------------------------
# Public entry point
# --------------------------------------------------------------------------------------


def scan(kb_root: Path) -> KbSnapshot:
    """Walk ``kb_root`` once and return the snapshot every later phase reads (decision C).

    What the walk does, and the rule that asks for it:

    * skips dot-entries and ``__pycache__`` entirely, so a ``.DS_Store`` cannot change a single
      derived byte (PA-16);
    * discovers topic roots recursively in depth-first pre-order, parent before child, siblings
      case-insensitively then by codepoint (PA-5), and never treats ``kb_root`` as one (PA-2);
    * descends into ``references/``, ``notes/``, ``media/`` and ``skills/`` to **record** their
      files, but only ``references/`` and ``notes/`` can still *yield* a topic root there — VA-36
      names those two and extension folders, PA-5 and VA-6 close the other two
      (:data:`RECORD_ONLY_DIRS`);
    * classifies every file by location (PA-11 … PA-19 via :func:`pkb.core.paths.classify`) and
      parses each markdown file exactly once — assets are recorded with ``doc=None`` and never
      opened for YAML (FM-14);
    * reports rather than raises: ``FRONTMATTER_PARSE_ERROR`` (VA-39), ``UNREADABLE_FILE`` (MA-14),
      ``UNEXPECTED_ROOT_ENTRY`` (PA-1), ``MISPLACED_TOPIC_ROOT`` (VA-36) and
      ``UNADDRESSABLE_TOPIC_ROOT`` (PA-8).

    ``UNADDRESSABLE_TOPIC_ROOT`` covers the one case ``topic_tag_for`` cannot answer: a topic folder
    whose every path segment slugifies away (``KB/!!!/topic.md``). Dropping the topic would hide its
    files from every index, so the record is kept under a :data:`FALLBACK_TOPIC_SLUG` address and
    the defect is reported for a human to rename.

    Raises :class:`~pkb.core.errors.KbNotFoundError` when ``kb_root`` is missing or is not a
    directory — that is a caller or deployment error, not a content defect (CX-5).
    """
    root = _require_directory(kb_root)
    walk = _Walk(kb_root=root)
    walk.run()

    files = {
        record.path: record
        for record in sorted(walk.files, key=lambda record: paths.sort_key(record.path))
    }
    return KbSnapshot(
        root=root,
        topics=_build_topics(walk.topics, files),
        files=files,
        findings=tuple(sort_findings(walk.findings)),
    )


def _require_directory(kb_root: Path) -> Path:
    """Normalize the root, or refuse (KbNotFoundError). An empty directory is fine (GE-29)."""
    if not kb_root.is_dir():
        raise KbNotFoundError(f"knowledge base root {kb_root} does not exist or is not a directory")
    # Absolute and lexically normalized, never ``resolve()``: resolving would rewrite the caller's
    # root through symlinks (macOS ``/var`` → ``/private/var``) and make ``abs_path`` surprising.
    return Path(os.path.normpath(kb_root.absolute()))


# --------------------------------------------------------------------------------------
# The walk
# --------------------------------------------------------------------------------------


@dataclass(slots=True)
class _TopicDraft:
    """A topic root as the walk sees it — everything except what needs the finished file map."""

    path: str
    name: str
    abs_path: Path
    tag: str
    agent_id: str
    parent: str | None


@dataclass(slots=True)
class _Walk:
    """Mutable accumulator for one traversal. Nothing here is public; :func:`scan` owns it."""

    kb_root: Path
    files: list[FileRecord] = field(default_factory=list)
    topics: list[_TopicDraft] = field(default_factory=list)
    findings: list[Finding] = field(default_factory=list)
    visited: set[tuple[int, int]] = field(default_factory=set)

    def run(self) -> None:
        self._descend(self.kb_root, owner=None, at_root=True, discoverable=True)

    # -- traversal ---------------------------------------------------------------------

    def _descend(
        self, directory: Path, *, owner: str | None, at_root: bool, discoverable: bool
    ) -> None:
        """Record every file under ``directory``; discover topic roots only where PA-5 allows.

        ``discoverable`` is the one difference between the two jobs this recursion does. It is
        threaded rather than recomputed from the current directory's name because PA-5 stops the
        *descent*: once the walk is inside :data:`RECORD_ONLY_DIRS`, every descendant is equally
        undiscoverable, so ``skills/voice/sub-topics/Deep/topic.md`` is no more a topic root than
        ``skills/voice/topic.md`` is. Recording is unconditional — it has no such flag, and that
        asymmetry is the point (GE-15, GE-25).
        """
        if not self._first_visit(directory):
            return
        listing = _listing(directory)
        if at_root:
            self._check_root_entries(listing)

        for name, is_directory in listing:
            if not is_directory:
                self._record_file(directory / name, owner)

        # Directories second, so a topic root's own files are recorded before its children's.
        for name, is_directory in listing:
            if not is_directory:
                continue
            child = directory / name
            child_discoverable = discoverable and name not in RECORD_ONLY_DIRS
            child_owner = owner
            if child_discoverable and paths.is_topic_root(child):
                child_owner = self._record_topic(child, parent=owner)
            self._descend(child, owner=child_owner, at_root=False, discoverable=child_discoverable)

    def _first_visit(self, directory: Path) -> bool:
        """False for an unreadable directory or one already seen, so no loop can hang the walk.

        Symlinks no longer reach here — :func:`_listing` refuses to call one a directory — so what
        this guards is a hard-linked or bind-mounted cycle. It is a backstop, not the fix for GE-5:
        refusing the *second* visit is what split one topic's files across two addresses when a link
        did get in.
        """
        try:
            status = directory.stat()
        except OSError:
            return False
        identity = (status.st_dev, status.st_ino)
        if identity in self.visited:
            return False
        self.visited.add(identity)
        return True

    # -- files -------------------------------------------------------------------------

    def _record_file(self, path: Path, owner: str | None) -> None:
        relative = paths.rel(self.kb_root, path)
        role, file_class = paths.classify(self.kb_root, path)
        # A captured source is never opened for YAML even when it is markdown (T-14) — the same
        # ``ASSET`` exemption a non-markdown file already gets, extended to a markdown one whose
        # class says its bytes are source material rather than a knowledge file.
        document = (
            self._read(path, relative)
            if _is_markdown(path) and file_class is not FileClass.ASSET
            else None
        )
        self.files.append(
            FileRecord(
                path=relative,
                abs_path=path,
                role=role,
                file_class=file_class,
                topic_path=owner,
                doc=document,
            )
        )

    def _read(self, path: Path, relative: str) -> ParsedDocument:
        """Read and parse one markdown file, reporting a defect instead of raising (VA-39, MA-14)."""
        try:
            # ``newline=""`` keeps CRLF files byte-exact, so anything that later writes through
            # ``frontmatter.set_field`` preserves the human's line endings (FM-11).
            with path.open(encoding="utf-8", newline="") as handle:
                text = handle.read()
        except (OSError, UnicodeDecodeError) as exc:
            self.findings.append(
                Finding(
                    code="UNREADABLE_FILE",
                    severity=Severity.ERROR,
                    message=f"the file could not be read as UTF-8 text: {exc}",
                    rule_id="MA-14",
                    path=relative,
                    hint="re-save the file as UTF-8, or move it out of the knowledge base",
                )
            )
            return ParsedDocument(body="", error=str(exc))

        document = frontmatter.parse(text)
        if document.error is not None:
            self.findings.append(
                diagnostics.frontmatter_parse_error(relative, document.error, document.error_line)
            )
        return document

    # -- topics ------------------------------------------------------------------------

    def _record_topic(self, directory: Path, *, parent: str | None) -> str:
        relative = paths.rel(self.kb_root, directory)
        tag, agent_id = self._address(directory, relative)
        self.topics.append(
            _TopicDraft(
                path=relative,
                name=directory.name,
                abs_path=directory,
                tag=tag,
                agent_id=agent_id,
                parent=parent,
            )
        )
        if not _is_properly_placed(self.kb_root, relative):
            expected = (
                f"{parent}/{paths.SUBTOPICS_DIR}/{directory.name}"
                if parent
                else "a directory directly under the KB root"
            )
            self.findings.append(diagnostics.misplaced_topic_root(relative, expected))
        return relative

    def _address(self, directory: Path, relative: str) -> tuple[str, str]:
        """The topic's tag and agent id (PA-9, PA-10), degraded rather than fatal (PA-8)."""
        try:
            return (
                paths.topic_tag_for(self.kb_root, directory),
                paths.agent_id_for(self.kb_root, directory),
            )
        except NotATopicRootError as exc:
            self.findings.append(
                Finding(
                    code="UNADDRESSABLE_TOPIC_ROOT",
                    severity=Severity.WARNING,
                    message=(
                        f"this topic root has no addressable name, so it was filed under "
                        f"{FALLBACK_TOPIC_SLUG!r}: {exc}"
                    ),
                    rule_id="PA-8",
                    path=relative,
                    value=directory.name,
                    hint="rename the folder to something containing letters or digits",
                )
            )
            namespace = paths.TOPIC_TAG_NAMESPACE
            return f"{namespace}.{FALLBACK_TOPIC_SLUG}", f"{namespace}/{FALLBACK_TOPIC_SLUG}"

    # -- root --------------------------------------------------------------------------

    def _check_root_entries(self, listing: list[tuple[str, bool]]) -> None:
        """The root holds ``tags.md``, ``skills/``, ``sessions/`` and topic directories (PA-1).

        A root ``index.md`` is no longer one of them (T-37, P2): the registry is the one derived
        file above the topics, so a copy on disk — however it got there — is reported here like any
        other unrecognized entry and never touched or deleted. (A generator still writes one today;
        a later task retires it. Either way this check treats the bytes the same: a stray.)
        """
        for name, is_directory in listing:
            if is_directory:
                if name in (paths.SKILLS_DIR, paths.SESSIONS_DIR) or paths.is_topic_root(
                    self.kb_root / name
                ):
                    continue
            elif name == paths.TAGS_FILE:
                continue
            self.findings.append(diagnostics.unexpected_root_entry(name))


# --------------------------------------------------------------------------------------
# Assembly
# --------------------------------------------------------------------------------------


def _build_topics(
    drafts: list[_TopicDraft], files: dict[str, FileRecord]
) -> dict[str, TopicRecord]:
    """Finish the topic records once the file map exists (children, ``expert.md``, ``topic.md``)."""
    children: dict[str, list[str]] = {}
    for draft in drafts:
        if draft.parent is not None:
            children.setdefault(draft.parent, []).append(draft.path)

    return {
        draft.path: TopicRecord(
            path=draft.path,
            name=draft.name,
            agent_id=draft.agent_id,
            tag=draft.tag,
            parent=draft.parent,
            # Discovery order, which is already the render order: siblings sorted, parent first.
            children=tuple(children.get(draft.path, ())),
            # The same predicate ``resolve_expert`` uses, so GE-13's catalog marker can never
            # promise a prompt PA-13 refuses to hand Layer 2. Reading it off the walk instead was
            # file-only for a *directory* named expert.md but not for a symlinked one: the listing
            # calls a link a non-directory (see :func:`_listing`), so the record claimed a custom
            # expert for a path ``read_text()`` cannot open. Case-exactness is preserved — that is
            # what the helper is for (PA-17).
            has_expert=paths.has_case_exact_file(draft.abs_path, paths.EXPERT_FILE),
            meta=_topic_meta(files, draft.path),
        )
        for draft in drafts
    }


def _topic_meta(files: dict[str, FileRecord], topic_path: str) -> Metadata | None:
    """``topic.md``'s metadata, or ``None`` when it is missing or unparseable (GE-25)."""
    record = files.get(f"{topic_path}/{paths.TOPIC_FILE}")
    return record.meta if record is not None else None


# --------------------------------------------------------------------------------------
# Small helpers
# --------------------------------------------------------------------------------------


def _listing(directory: Path) -> list[tuple[str, bool]]:
    """``(name, is_directory)`` pairs, ignored entries dropped, in the one sibling order.

    Sorting here — rather than trusting ``os.scandir`` — is what makes the whole snapshot
    independent of filesystem iteration order (PA-5, PA-16, GE-4). An unreadable directory yields
    nothing rather than raising, because the walk must survive a degraded tree (MA-14).

    ``follow_symlinks=False`` matches :func:`pkb.core.paths._dir_names` and is load-bearing. A
    symlink to a directory *inside* the tree would otherwise be listed as a directory, satisfy
    ``is_topic_root`` through the link, and be recorded as a second topic root holding the same
    ``topic.md``; :meth:`_Walk._first_visit` then refuses whichever alias is reached second, so one
    topic's files are filed under the other's address and vanish from their own (GE-25) while both
    indexes are rewritten on every run (GE-5). A link is therefore *not a directory the walk owns* —
    but it is still an entry, so it is recorded as a file rather than dropped, which is what lets
    :meth:`_Walk._check_root_entries` tell the human about it (PA-1).
    """
    try:
        with os.scandir(directory) as entries:
            listing = [
                (entry.name, entry.is_dir(follow_symlinks=False))
                for entry in entries
                if not paths.is_ignored(entry.name)
            ]
    except OSError:
        return []
    listing.sort(key=lambda item: paths.sort_key(item[0]))
    return listing


def _is_markdown(path: Path) -> bool:
    """Only ``.md`` files are ever parsed; media and source files are not (FM-14)."""
    return path.name.endswith(paths.MARKDOWN_SUFFIX)


def _is_properly_placed(kb_root: Path, relative: str) -> bool:
    """True when a topic root sits at the KB root or in a parent topic's ``sub-topics/`` (VA-36).

    Anything else — a ``topic.md`` under ``notes/``, ``references/``, ``media/`` or inside an
    extension folder — is discovered all the same (PA-5) but flagged, because ``sub-topics/`` is the
    only documented route to a nested topic (PA-4).
    """
    parts = relative.split("/")
    if len(parts) == 1:
        return True
    if parts[-2] != paths.SUBTOPICS_DIR:
        return False
    # ``sub-topics/`` at the knowledge-base root has no parent topic, so joinpath() lands on
    # ``kb_root``, which is never a topic root (PA-2) — exactly the answer VA-36 wants.
    return paths.is_topic_root(kb_root.joinpath(*parts[:-2]))
