"""Create the standard structure for a new topic (rules SC-1 … SC-12).

The scaffolder is the only code in Layer 1 that brings a *content* file into existence, and it
writes the smallest tree README §1.2 calls a topic: ``topic.md`` plus the two breadth summaries.
Three properties make it safe to call from an agent turn:

* **Nothing optional.** No ``expert.md``, no ``skills/``, no ``sub-topics/``, no extension folder
  (SC-4). Those are human-approved additions, and their absence is never a finding.
* **Nothing overwritten.** Every member is created with ``O_EXCL``; a member that already exists is
  reported as skipped and left byte-identical (SC-10). Re-scaffolding is therefore a repair
  operation, not a reset — which matters because there is no version control to undo it (arch D6).
* **Nothing invalid.** Every file written here passes :func:`pkb.core.validation.validate_content`
  with zero errors (SC-3): all seven required fields, ``status.draft``, and ``topic`` /
  ``source_type`` / ``type.*`` / ``topic.*`` consistent with where the file lands. A scaffolder that
  emitted invalid placeholders would poison every topic at creation and burn Layer 2's retry budget
  on files no agent asked for.

``index.md`` is not written here. It arrives through :func:`~pkb.core.generators.regenerate_all`,
which also adds the new topic's line to the root catalog, so a scaffolded topic is addressable the
moment the call returns (SC-2, SC-7).

Per decision A in the rules document the placeholders carry ``source_type: summary`` and
``type.summary`` — including ``topic.md``, which is distinguished by its location, not by a
``source_type`` of its own (VA-13 as amended).
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from pathlib import Path, PurePosixPath
from typing import Final

from pkb.core import frontmatter, paths, tags
from pkb.core.errors import (
    KbNotFoundError,
    NotATopicRootError,
    ScaffoldError,
    TopicDepthExceededError,
)
from pkb.core.generators import regenerate_all
from pkb.core.models import Metadata, ScaffoldResult

__all__ = [
    "NOTES_SUMMARY_TITLE",
    "PLACEHOLDER_SOURCE_TYPE",
    "PLACEHOLDER_STATUS_TAG",
    "PLACEHOLDER_TYPE_TAG",
    "REFERENCES_SUMMARY_TITLE",
    "member_paths",
    "scaffold_subtopic",
    "scaffold_topic",
]


# --------------------------------------------------------------------------------------
# Placeholder vocabulary (SC-2, SC-3, SC-12)
# --------------------------------------------------------------------------------------

PLACEHOLDER_SOURCE_TYPE: Final = "summary"
"""Every scaffolded file is a breadth overview (decision A: ``topic.md`` is a summary too)."""

PLACEHOLDER_TYPE_TAG: Final = f"{tags.Namespace.TYPE.value}.{PLACEHOLDER_SOURCE_TYPE}"
"""Derived from :data:`PLACEHOLDER_SOURCE_TYPE` so VA-11's bijection cannot drift here."""

PLACEHOLDER_STATUS_TAG: Final = f"{tags.Namespace.STATUS.value}.draft"
"""Everything the scaffolder writes is a proposal awaiting human approval (SC-2, SC-3)."""

NOTES_SUMMARY_TITLE: Final = "Notes summary"
REFERENCES_SUMMARY_TITLE: Final = "References summary"

_TOPIC_BODY: Final = (
    "\n"
    "# {title}\n"
    "\n"
    "Placeholder. The Topic Expert drafts the breadth map here for the human to approve: what this "
    "topic covers, how it is organised, and where a reader should start.\n"
)

_NOTES_BODY: Final = (
    "\n"
    "# " + NOTES_SUMMARY_TITLE + "\n"
    "\n"
    "Placeholder. The Topic Expert distils the rules and notable solutions from the notes in this "
    "folder, and the human approves them.\n"
)

_REFERENCES_BODY: Final = (
    "\n"
    "# " + REFERENCES_SUMMARY_TITLE + "\n"
    "\n"
    "Placeholder. The Topic Expert summarises the sources ingested into this folder, and the human "
    "approves the result.\n"
)

_NOTES_DESCRIPTION: Final = "Placeholder: distilled rules and solutions from {title} notes"
_REFERENCES_DESCRIPTION: Final = "Placeholder: overview of ingested {title} sources"

_WHITESPACE_RUN: Final = re.compile(r"\s+")


# --------------------------------------------------------------------------------------
# The member table (SC-1, SC-5)
# --------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _Spec:
    """Everything the three placeholders share — resolved once, before anything is written."""

    title: str
    description: str
    topic_name: str
    """The topic root's folder name: the display name VA-12 compares against the location."""

    topic_tag: str
    today: date


def _document(spec: _Spec, *, title: str, description: str, body: str) -> str:
    """One placeholder, in canonical frontmatter form (FM-7, FM-8, SC-3).

    The block is built through :func:`pkb.core.frontmatter.serialize` rather than a text template so
    that the scaffolder cannot drift from the canonical style the round-trip tests pin.
    """
    meta = Metadata(
        title=title,
        description=description,
        topic=spec.topic_name,
        tags=(spec.topic_tag, PLACEHOLDER_TYPE_TAG, PLACEHOLDER_STATUS_TAG),
        created=spec.today,
        updated=spec.today,
        source_type=PLACEHOLDER_SOURCE_TYPE,
    )
    return frontmatter.serialize(meta, body)


def _topic_document(spec: _Spec) -> str:
    """``<topic root>/topic.md`` — the file that makes the directory a topic root (SC-2, PA-3)."""
    return _document(
        spec,
        title=spec.title,
        description=spec.description,
        body=_TOPIC_BODY.format(title=spec.title),
    )


def _notes_summary_document(spec: _Spec) -> str:
    """``notes/summary.md`` — the breadth overview of experience (SC-1)."""
    return _document(
        spec,
        title=NOTES_SUMMARY_TITLE,
        description=_NOTES_DESCRIPTION.format(title=spec.title),
        body=_NOTES_BODY,
    )


def _references_summary_document(spec: _Spec) -> str:
    """``references/summary.md`` — the breadth overview of static knowledge (SC-1)."""
    return _document(
        spec,
        title=REFERENCES_SUMMARY_TITLE,
        description=_REFERENCES_DESCRIPTION.format(title=spec.title),
        body=_REFERENCES_BODY,
    )


@dataclass(frozen=True, slots=True)
class _Member:
    """One entry of the standard structure, addressed relative to the topic root."""

    parts: tuple[str, ...]
    """``()`` is the topic root directory itself."""

    render: Callable[[_Spec], str] | None
    """``None`` for a directory."""


_MEMBERS: Final[tuple[_Member, ...]] = (
    _Member((), None),
    _Member((paths.TOPIC_FILE,), _topic_document),
    _Member((paths.NOTES_DIR,), None),
    _Member((paths.NOTES_DIR, paths.SUMMARY_FILE), _notes_summary_document),
    _Member((paths.REFERENCES_DIR,), None),
    _Member((paths.REFERENCES_DIR, paths.SUMMARY_FILE), _references_summary_document),
)
"""The complete standard structure (SC-1), parents before children so creation order is legal.

Identical for a top-level topic and for a sub-topic — SC-5's "depth-agnostic" is this table being
the only description of what a topic is made of.
"""


# --------------------------------------------------------------------------------------
# Path resolution and refusals (SC-9, SC-11)
# --------------------------------------------------------------------------------------


def _require_kb_root(kb_root: Path) -> Path:
    if not kb_root.is_dir():
        raise KbNotFoundError(f"{kb_root} is not a directory")
    return kb_root


def _relative_topic(kb_root: Path, topic_path: Path | str) -> PurePosixPath:
    """``topic_path`` as a knowledge-base-relative POSIX path, however the caller spelled it.

    An absolute :class:`~pathlib.Path` is resolved against ``kb_root``; anything else is read as
    already relative. Escaping the tree is a caller bug rather than a content defect (CX-5), so it
    raises rather than reporting.
    """
    if isinstance(topic_path, Path) and topic_path.is_absolute():
        relative = PurePosixPath(paths.rel(kb_root, topic_path))
    else:
        relative = PurePosixPath(str(topic_path).replace("\\", "/"))
    if relative.is_absolute() or ".." in relative.parts:
        raise ScaffoldError(f"{topic_path!r} is not a knowledge-base-relative topic path")
    if not relative.parts:
        raise ScaffoldError("the knowledge base root is not a topic root (PA-2)")
    return relative


def _check_placement(kb_root: Path, relative: PurePosixPath) -> None:
    """A topic root sits at the KB root or at ``<parent topic>/sub-topics/<Name>`` (PA-4).

    Refusing anything else is what keeps :func:`pkb.core.paths.topic_tag_for` meaningful: it
    slugifies every folder name on the way down, so a ``topic.md`` under ``notes/`` would derive
    ``topic.cooking.notes.grilling`` and every file inside it would be permanently unable to carry a
    location-consistent tag (VA-15). Validation only warns about such a tree (VA-36) because it must
    not break one that already exists; creating one is a different matter.
    """
    parts = relative.parts
    if len(parts) == 1:
        return
    if len(parts) < 3 or parts[-2] != paths.SUBTOPICS_DIR:
        raise ScaffoldError(
            f"{relative.as_posix()!r} is not a legal topic location: a nested topic lives at "
            f"<parent topic>/{paths.SUBTOPICS_DIR}/<Name> (PA-4)"
        )
    parent = kb_root.joinpath(*parts[:-2])
    if not paths.is_topic_root(parent):
        raise NotATopicRootError(
            f"{PurePosixPath(*parts[:-2]).as_posix()!r} holds no {paths.TOPIC_FILE}, so it cannot "
            f"host a sub-topic (PA-3)"
        )


def _sibling_directory_names(directory: Path) -> list[str]:
    try:
        entries = sorted(directory.iterdir(), key=lambda entry: paths.sort_key(entry.name))
    except OSError:
        return []
    return [entry.name for entry in entries if entry.is_dir() and not paths.is_ignored(entry.name)]


def _check_name(kb_root: Path, relative: PurePosixPath) -> str:
    """Validate the new topic's folder name and return its slug (SC-11).

    Three refusals, all of them about addressability rather than taste: a name with no slug has no
    tag and no agent id (PA-8); a reserved or structural name would be read as part of the standard
    structure instead of as a topic (PA-19, PA-6); and a sibling sharing our slug would make
    ``topic.<slug>`` ambiguous, so :func:`pkb.core.paths.path_for_topic_tag` could not invert it.

    The comparison is made on the slug as well as the literal name, because the slug is what ends up
    in the tag — ``Notes/`` and ``notes/`` are the same topic as far as PA-9 is concerned.
    """
    name = relative.name
    slug = paths.slugify(name)
    if not slug:
        raise ScaffoldError(
            f"topic name {name!r} slugifies to the empty string, so the topic would have no tag "
            f"and no agent id (PA-8, SC-11)"
        )
    for candidate in (name, slug):
        if candidate in paths.STRUCTURAL_DIRS:
            raise ScaffoldError(
                f"topic name {name!r} is a structural directory name and cannot be a topic "
                f"(PA-6, SC-11)"
            )
        if paths.is_reserved_item_name(candidate):
            raise ScaffoldError(
                f"topic name {name!r} is reserved by the standard structure (PA-19, SC-11)"
            )

    parent_dir = kb_root.joinpath(*relative.parts[:-1])
    for sibling in _sibling_directory_names(parent_dir):
        if sibling != name and paths.slugify(sibling) == slug:
            raise ScaffoldError(
                f"topic name {name!r} slugifies to {slug!r}, which the existing sibling "
                f"{sibling!r} already claims (PA-9, SC-11)"
            )
    return slug


def _check_depth(kb_root: Path, topic_dir: Path, relative: PurePosixPath) -> str:
    """Return the new topic's tag, refusing one that would need a fifth level (SC-9).

    A hard refusal rather than a warning: every file inside an over-deep topic would be unable to
    carry a location-consistent ``topic.*`` tag (TG-3, VA-15), so creating one produces a subtree
    that can never be made valid. An over-deep tree that already exists is only warned about
    (VA-37) — Layer 1 never breaks what a human already built.
    """
    tag = paths.topic_tag_for(kb_root, topic_dir)
    depth = tag.count(".") + 1
    if depth > tags.MAX_TAG_DEPTH:
        raise TopicDepthExceededError(
            f"{relative.as_posix()!r} would need the {depth}-level tag {tag!r}; a topic tag carries "
            f"at most {tags.MAX_TAG_DEPTH} levels including the 'topic' namespace (TG-3, SC-9)"
        )
    return tag


def _one_line(value: str, *, field: str) -> str:
    """Collapse a caller-supplied string to the single trimmed line the schema requires (VA-26).

    Normalizing rather than rejecting keeps a caller that pasted a wrapped paragraph from getting a
    refusal it cannot act on; an empty result is a genuine caller bug, because no placeholder can be
    written without it (VA-4).
    """
    collapsed = _WHITESPACE_RUN.sub(" ", value).strip()
    if not collapsed:
        raise ScaffoldError(f"{field} is required and must not be blank (VA-4, SC-3)")
    return collapsed


# --------------------------------------------------------------------------------------
# Writing (SC-10)
# --------------------------------------------------------------------------------------


def _create_directory(target: Path) -> bool:
    """Create ``target``, reporting whether it was this call that made it.

    Intermediate directories are created silently: ``sub-topics/`` is the parent of a sub-topic, not
    a member of it (SC-6), and it must not appear in the member set SC-5 compares.
    """
    try:
        target.mkdir(parents=True)
    except FileExistsError:
        return False
    return True


def _create_file(target: Path, text: str) -> bool:
    """Write ``text`` only if nothing is there, reporting whether it was written (SC-10).

    ``"x"`` mode is the whole guarantee: exclusive creation is atomic, so a concurrent writer cannot
    slip between a check and a write, and the human's bytes are never at risk. A case-insensitive
    filesystem answering "exists" for a differently-cased name also lands here — reported as
    skipped, which is the honest outcome (PA-17).
    """
    try:
        with target.open("x", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
    except FileExistsError:
        return False
    return True


# --------------------------------------------------------------------------------------
# Public API
# --------------------------------------------------------------------------------------


def member_paths(kb_root: Path, topic_path: Path | str) -> list[str]:
    """The knowledge-base-relative paths a scaffolded topic consists of (SC-1, SC-5).

    Pure path arithmetic — it names the standard structure whether or not any of it exists, which is
    what lets a caller (or a test) compare the member set of a top-level topic against a
    sub-topic's modulo the prefix.
    """
    relative = _relative_topic(kb_root, topic_path)
    base = relative.as_posix()
    return [base if not member.parts else f"{base}/{'/'.join(member.parts)}" for member in _MEMBERS]


def scaffold_topic(
    kb_root: Path,
    topic_path: Path | str,
    *,
    title: str,
    description: str,
    today: date,
    regenerate: bool = True,
) -> ScaffoldResult:
    """Create the standard structure for a topic at ``topic_path`` (SC-1 … SC-12).

    Writes exactly ``topic.md``, ``notes/summary.md``, ``references/summary.md`` and the two
    directories holding them, plus the topic root itself — six paths, and nothing optional (SC-1,
    SC-4). Each placeholder carries the seven required fields, ``status.draft``, and the ``topic`` /
    ``source_type`` / ``type.*`` / ``topic.*`` values its location implies, so every one of them
    validates with zero errors (SC-3).

    ``topic_path`` may be a knowledge-base-relative POSIX string, a relative
    :class:`~pathlib.Path`, or an absolute path inside ``kb_root``. It must name either a directory
    directly under the knowledge-base root or ``<parent topic>/sub-topics/<Name>`` (PA-4);
    :func:`scaffold_subtopic` is the convenient spelling of the second form.

    ``today`` is injected rather than read from the clock so the result is reproducible (CX-2); it
    becomes both ``created`` and ``updated`` (VA-28).

    With ``regenerate`` left true the call ends with a full flush, so the new topic's ``index.md``
    exists and the root catalog lists it before the function returns (SC-7). The scaffolder itself
    writes no derived file and seeds no tags beyond the placeholders' own (SC-12).

    Nothing is ever overwritten (SC-10): a member that already exists is left byte-identical and
    named in :attr:`~pkb.core.models.ScaffoldResult.skipped`, which makes re-scaffolding a safe way
    to repair a partially created topic.

    Raises :class:`~pkb.core.errors.TopicDepthExceededError` for a topic needing a fifth tag level
    (SC-9), :class:`~pkb.core.errors.ScaffoldError` for an unusable name or an illegal location
    (SC-11, PA-4), :class:`~pkb.core.errors.NotATopicRootError` when the named parent holds no
    ``topic.md``, and :class:`~pkb.core.errors.KbNotFoundError` when ``kb_root`` is not a directory.
    There is no approval parameter: human approval happens in Layer 2 before this is called (SC-8).
    """
    root = _require_kb_root(kb_root)
    relative = _relative_topic(root, topic_path)
    _check_placement(root, relative)
    _check_name(root, relative)

    topic_dir = root.joinpath(*relative.parts)
    spec = _Spec(
        title=_one_line(title, field="title"),
        description=_one_line(description, field="description"),
        topic_name=relative.name,
        topic_tag=_check_depth(root, topic_dir, relative),
        today=today,
    )

    result = ScaffoldResult(topic_path=relative.as_posix())
    for member in _MEMBERS:
        target = topic_dir.joinpath(*member.parts)
        created = (
            _create_directory(target)
            if member.render is None
            else _create_file(target, member.render(spec))
        )
        bucket = result.created if created else result.skipped
        bucket.append(paths.rel(root, target))

    if regenerate:
        result.flush = regenerate_all(root)
    return result


def scaffold_subtopic(
    kb_root: Path,
    parent_topic_path: Path | str,
    name: str,
    *,
    title: str,
    description: str,
    today: date,
    regenerate: bool = True,
) -> ScaffoldResult:
    """Create a sub-topic at ``<parent>/sub-topics/<name>`` (SC-6, SC-5).

    The same implementation as :func:`scaffold_topic` — the standard structure is identical at every
    depth (SC-5) — with ``sub-topics/`` created on the way down if it is absent. ``sub-topics`` is a
    literal directory name (PA-4) that is elided from the topic's tag and agent id (PA-9, PA-10), so
    a sub-topic of ``Cooking`` named ``Grilling`` becomes ``topic.cooking.grilling``.

    ``name`` is a single folder name, not a path. Raises
    :class:`~pkb.core.errors.NotATopicRootError` when ``parent_topic_path`` holds no ``topic.md``,
    and otherwise refuses exactly what :func:`scaffold_topic` refuses.
    """
    root = _require_kb_root(kb_root)
    parent = _relative_topic(root, parent_topic_path)
    if not paths.is_topic_root(root.joinpath(*parent.parts)):
        raise NotATopicRootError(
            f"{parent.as_posix()!r} holds no {paths.TOPIC_FILE}, so it is not a topic root (PA-3)"
        )
    candidate = PurePosixPath(name.replace("\\", "/"))
    if len(candidate.parts) != 1 or candidate.is_absolute():
        raise ScaffoldError(f"sub-topic name {name!r} must be a single folder name, not a path")

    return scaffold_topic(
        root,
        (parent / paths.SUBTOPICS_DIR / candidate.name).as_posix(),
        title=title,
        description=description,
        today=today,
        regenerate=regenerate,
    )
