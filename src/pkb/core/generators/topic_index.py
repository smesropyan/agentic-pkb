"""Topic ``index.md`` — the depth index (GE-14 … GE-18, GE-25 … GE-27, MA-10, §4.3).

Where the root catalog answers *which topic*, this file answers *which file inside it*. It is read
by a Topic Expert before it opens anything, so every bullet has to carry enough to decide without
opening: the title, the one-line description, and the tags that let an agent select the
``type.solution`` or ``status.conflict-review`` files without reading twelve notes (Q3).

Three boundaries are load-bearing:

* **It does not recurse into ``sub-topics/``** (GE-16). Each immediate sub-topic is one line linking
  to its own index. Thirty notes under ``sub-topics/Grilling/notes/`` add exactly one line here, and
  a thirty-first adds none — which is the only reason a deep tree stays readable at every level.
* **Bodies are never read** (GE-14). Everything rendered comes from frontmatter and from the path.
* **No file is silently dropped** (GE-25). A markdown file in a place the structure does not
  describe still gets a bullet, under ``Other``; a dropped file is invisible to every depth agent,
  which is strictly worse than an ugly index.

Item bullets sort by relative POSIX path, never by title (GE-27), so editing a title changes one
line in place and renaming a file moves exactly one line.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from pathlib import Path
from urllib.parse import quote

from pkb.core import paths, tags
from pkb.core.errors import Finding, NotATopicRootError, sort_findings
from pkb.core.generators import base, derive
from pkb.core.models import FileClass, FileRecord, FileRole, KbSnapshot, Metadata, TopicRecord

__all__ = [
    "NO_REVIEW_NOTE",
    "SOURCE_TYPE",
    "generate_topic_index",
    "render_topic_index",
    "topic_index_findings",
]

SOURCE_TYPE = "index"
"""Derived-reserved ``source_type`` for a topic index (FM-6, Q2)."""

TITLE_SUFFIX = f"{base.EM_DASH}Index"
DESCRIPTION_TEMPLATE = "Canonical index of the {title} topic"

NO_REVIEW_NOTE = "*(no review note)*"
"""What every ``status.conflict-review`` row renders — ``review_note`` is no longer a schema field
(T-12), so :func:`_needs_review` cannot glean one from ``Metadata``."""

CONFLICT_TAG = "status.conflict-review"

BREADTH = "Breadth"
NEEDS_REVIEW = "Needs review"
SUBTOPICS = "Sub-topics"
NOTES = "Notes"
REFERENCES = "References"
OTHER = "Other"
TAG_SUBTREE = "Tag subtree"
CROSS_TOPIC_MAPPINGS = "Cross-topic mappings"
MAINTENANCE_FLAGS = "Maintenance flags"

_BREADTH_ROLES = frozenset(
    {
        FileRole.NOTES_SUMMARY,
        FileRole.REFERENCES_SUMMARY,
        FileRole.EXTENSION_SUMMARY,
    }
)
"""The ``summary.md`` files. ``topic.md`` leads the section separately — it is the topic itself."""


# --------------------------------------------------------------------------------------
# Rendering
# --------------------------------------------------------------------------------------


def render_topic_index(
    snapshot: KbSnapshot, topic_path: str | Path, flags: Sequence[Finding] = ()
) -> str:
    """Render one topic's index (GE-14 … GE-18, MA-10). Pure: no I/O, no clock (GE-9).

    ``flags`` are the maintenance findings this topic owns; the section is omitted when empty and
    therefore self-clears on the next flush once the defect is fixed (MA-10). Layer 1 never writes a
    flag into a content file, so this derived section is the only place they surface in the tree.

    Raises :class:`~pkb.core.errors.NotATopicRootError` when ``topic_path`` names no topic root —
    including the knowledge-base root, which is never one (PA-2).
    """
    topic = _topic(snapshot, topic_path)
    records = _indexable(snapshot, topic)
    title = base.inline(topic.title)

    blocks: list[str] = []
    blocks += base.section(BREADTH, _breadth(snapshot, topic, records))
    blocks += base.section(NEEDS_REVIEW, _needs_review(snapshot, topic, records))
    blocks += base.section(SUBTOPICS, _subtopics(snapshot, topic))
    blocks += base.section(NOTES, _items(snapshot, topic, records, FileRole.NOTE))
    blocks += base.section(REFERENCES, _items(snapshot, topic, records, FileRole.REFERENCE))
    for folder in topic.extension_folders:
        blocks += base.section(
            _folder_heading(folder), _extension_items(snapshot, topic, records, folder)
        )
    blocks += base.section(OTHER, _items(snapshot, topic, records, FileRole.UNKNOWN))
    blocks += base.section(TAG_SUBTREE, _tag_subtree(snapshot, topic))
    blocks += base.section(CROSS_TOPIC_MAPPINGS, _mappings(snapshot, topic))
    blocks += base.section(MAINTENANCE_FLAGS, _flags(topic, flags))

    meta = Metadata(
        title=f"{title}{TITLE_SUFFIX}",
        description=DESCRIPTION_TEMPLATE.format(title=title),
        topic=topic.name,
        source_type=SOURCE_TYPE,
    )
    return base.document(meta, f"{title}{TITLE_SUFFIX}", blocks)


def generate_topic_index(
    kb_root: Path,
    snapshot: KbSnapshot,
    topic_path: str | Path,
    flags: Sequence[Finding] = (),
) -> bool:
    """Render and write ``<topic root>/index.md``; True when the bytes changed (GE-8, GE-9)."""
    topic = _topic(snapshot, topic_path)
    text = render_topic_index(snapshot, topic.path, flags)
    return base.write_derived(kb_root / topic.path / paths.INDEX_FILE, text)


# --------------------------------------------------------------------------------------
# Which files this index describes (GE-14, GE-15)
# --------------------------------------------------------------------------------------


def _topic(snapshot: KbSnapshot, topic_path: str | Path) -> TopicRecord:
    """Resolve a topic root, accepting either a KB-relative string or an absolute path (PA-2)."""
    key = topic_path if isinstance(topic_path, str) else paths.rel(snapshot.root, topic_path)
    record = snapshot.topics.get(key)
    if record is None:
        raise NotATopicRootError(f"{key!r} is not a topic root in this knowledge base (PA-2, PA-3)")
    return record


def _indexable(snapshot: KbSnapshot, topic: TopicRecord) -> list[FileRecord]:
    """The topic's own authored markdown, in relative-path order (GE-14, GE-15, GE-27).

    Excluded per GE-15: derived files (including a stale ``notes/x/index.md``), everything under
    ``skills/``, ``expert.md``, non-markdown assets, and the ``sub-topics/`` subtree — the last of
    which is represented by one line per sub-topic instead (GE-16). Excluded assets are still in the
    snapshot, so orphan and link analysis continue to see them.
    """
    records = [
        record
        for record in snapshot.files_in_topic(topic.path)
        if record.file_class is FileClass.AUTHORED
        and record.is_markdown
        and not _relative(topic, record).startswith(f"{paths.SUBTOPICS_DIR}/")
    ]
    return sorted(records, key=lambda record: paths.sort_key(_relative(topic, record)))


def _relative(topic: TopicRecord, record: FileRecord) -> str:
    """The file's path relative to the topic root — what a link target and a sort key use."""
    return record.path.removeprefix(f"{topic.path}/")


# --------------------------------------------------------------------------------------
# Sections
# --------------------------------------------------------------------------------------


def _breadth(snapshot: KbSnapshot, topic: TopicRecord, records: Iterable[FileRecord]) -> list[str]:
    """``topic.md`` first, then the ``summary.md`` breadth files in path order (§4.3).

    ``topic.md`` leads regardless of path order because it is the topic's own overview; everything
    else in the section is sorted by relative path like any other bullet (GE-27).
    """
    lines = [_topic_bullet(snapshot, topic)]
    lines += [
        _bullet(snapshot, topic, record, with_tags=False)
        for record in records
        if record.role in _BREADTH_ROLES
    ]
    return lines


def _topic_bullet(snapshot: KbSnapshot, topic: TopicRecord) -> str:
    """The ``topic.md`` line, rendered from the topic record so it survives a missing file (GE-25)."""
    root = snapshot.root / topic.path
    target = paths.link_target(root, root / paths.TOPIC_FILE)
    description = (
        base.MISSING_TOPIC_METADATA
        if topic.meta is None
        else _description_of(topic.meta.description)
    )
    return base.item_bullet(base.inline(topic.title), target, description)


def _needs_review(
    snapshot: KbSnapshot, topic: TopicRecord, records: Iterable[FileRecord]
) -> list[str]:
    """Every ``status.conflict-review`` file (§4.3, Part 4).

    This is derived from *current* state, not from a conflict history — Layer 1 keeps no record that
    a conflict ever happened (GE-28), so resolving one and reflushing empties the section entirely.

    ``review_note`` is no longer a schema field (T-12); ``Metadata`` cannot carry it, so every row
    reads as :data:`NO_REVIEW_NOTE` until the conflict-review workflow itself is redesigned.
    """
    lines: list[str] = []
    for record in records:
        meta = record.meta
        if meta is None or CONFLICT_TAG not in meta.tags:
            continue
        lines.append(
            base.item_bullet(
                _title_of(topic, record), _link(snapshot, topic, record), NO_REVIEW_NOTE
            )
        )
    return lines


def _subtopics(snapshot: KbSnapshot, topic: TopicRecord) -> list[str]:
    """One line per immediate sub-topic, linking to its own index (GE-16)."""
    lines: list[str] = []
    for child_path in topic.children:
        child = snapshot.topics[child_path]
        target = paths.link_target(
            snapshot.root / topic.path, snapshot.root / child.path / paths.INDEX_FILE
        )
        description = (
            base.MISSING_TOPIC_METADATA
            if child.meta is None
            else _description_of(child.meta.description)
        )
        lines.append(base.item_bullet(base.inline(child.title), target, description))
    return lines


def _items(
    snapshot: KbSnapshot, topic: TopicRecord, records: Iterable[FileRecord], role: FileRole
) -> list[str]:
    """Item bullets for one role, already in relative-path order (GE-27)."""
    return [
        _bullet(snapshot, topic, record, with_tags=True)
        for record in records
        if record.role is role
    ]


def _extension_items(
    snapshot: KbSnapshot, topic: TopicRecord, records: Iterable[FileRecord], folder: str
) -> list[str]:
    """Item bullets for one extension folder (PA-7, GE-14)."""
    prefix = f"{folder}/"
    return [
        _bullet(snapshot, topic, record, with_tags=True)
        for record in records
        if record.role is FileRole.EXTENSION_ITEM and _relative(topic, record).startswith(prefix)
    ]


def _folder_heading(folder: str) -> str:
    """An extension folder's section heading: the folder name, first letter upper-cased (§4.3).

    ``recipes/`` renders as ``## Recipes``. The folder name is the human's word for the section, so
    it is used as written apart from the leading capital a heading wants, and it goes through
    :func:`base.inline` like every other injected string (GE-26).

    A directory named entirely with whitespace inlines to the empty string, and ``## `` would then
    carry trailing whitespace — which GE-7 forbids unconditionally, degraded tree or not. Such a
    folder is legal and reachable: PA-7 makes any non-structural directory under a topic root an
    extension folder, and VA-38 deliberately never flags one, so this generator is the only place
    that can absorb it (GE-25). The fallback is the percent-encoded name, which is exactly what
    :func:`paths.link_target` puts in this section's own bullets (PA-18) — so the heading names the
    folder its links point into, it can never be whitespace, and two such folders stay
    distinguishable, which a generic ``*(unnamed folder)*`` literal would not achieve.
    """
    heading = base.inline(folder) or quote(folder, safe="")
    return heading[:1].upper() + heading[1:]


def _tag_subtree(snapshot: KbSnapshot, topic: TopicRecord) -> list[str]:
    """The branch of the **global** tag tree rooted at this topic's tag (GE-17).

    Global, not local: a file filed elsewhere that carries ``topic.cooking.sous-vide`` still shows
    up here, which is the point — the subtree describes the topic's vocabulary, not its folder. Only
    ``topic.*`` is rendered; ``type.*``/``status.*``/``domain.*`` live in the registry (§5).
    """
    tree = tags.build_tag_tree(snapshot)
    node = tree.subtree(topic.tag) or tags.TagNode(topic.tag)
    return tags.render_tag_tree([node], annotations=derive.topic_annotations(snapshot, topic.tag))


def _mappings(snapshot: KbSnapshot, topic: TopicRecord) -> list[str]:
    """This topic's cross-topic mappings, local tag always on the left (GE-18)."""
    pairs, _ = derive.cross_topic_pairs(snapshot)
    return [
        tags.render_mapping_line(left, right)
        for left, right in derive.pairs_for_topic(pairs, topic.tag)
    ]


def _flags(topic: TopicRecord, flags: Sequence[Finding]) -> list[str]:
    """The maintenance findings this topic owns, one line each (MA-10).

    Paths are shown relative to the topic root, like every other path in this file, so a reader is
    never asked to translate between two frames of reference.
    """
    lines: list[str] = []
    for finding in sort_findings(flags):
        label = finding.code.lower().replace("_", "-")
        message = base.inline(finding.message)
        if finding.path is None:
            lines.append(f"- {label}: {message}")
            continue
        shown = _strip_topic(topic, finding.path)
        lines.append(f"- {label}: `{shown}` ({message})")
    return list(dict.fromkeys(lines))


def _strip_topic(topic: TopicRecord, path: str) -> str:
    prefix = f"{topic.path}/"
    return path[len(prefix) :] if path.startswith(prefix) else path


# --------------------------------------------------------------------------------------
# Bullets (GE-25, GE-26, GE-27)
# --------------------------------------------------------------------------------------


def _bullet(
    snapshot: KbSnapshot, topic: TopicRecord, record: FileRecord, *, with_tags: bool
) -> str:
    """One item bullet, total over a degraded file (GE-25, GE-26, GE-27)."""
    meta = record.meta
    description = _description_of(meta.description if meta else None)
    suffix = _tag_suffix(meta) if with_tags else ""
    return base.item_bullet(
        _title_of(topic, record), _link(snapshot, topic, record), description, suffix
    )


def _link(snapshot: KbSnapshot, topic: TopicRecord, record: FileRecord) -> str:
    return paths.link_target(snapshot.root / topic.path, record.abs_path)


def _title_of(topic: TopicRecord, record: FileRecord) -> str:
    """The file's title, falling back to its stem so a file is never rendered nameless (GE-25)."""
    meta = record.meta
    if meta and meta.title:
        return base.inline(meta.title)
    return base.inline(Path(_relative(topic, record)).stem)


def _description_of(description: str | None) -> str:
    """The description, or the literal placeholder for a missing/empty one (GE-25)."""
    return base.inline(description) if description else base.NO_DESCRIPTION


def _tag_suffix(meta: Metadata | None) -> str:
    """`` · tags: `a` `b` `` — the file's tags, sorted, backticked, space-separated (GE-27).

    Sorted rather than rendered in frontmatter order so the line is independent of how the author
    happened to list them, and deduplicated because a repeated tag carries no meaning (VA-10 reports
    it) while a doubled bullet entry reads as two different tags.
    """
    if meta is None or not meta.tags:
        return ""
    unique = sorted(dict.fromkeys(meta.tags), key=tags.tag_sort_key)
    return base.TAGS_PREFIX + " ".join(f"`{tag}`" for tag in unique)


# --------------------------------------------------------------------------------------
# Diagnostics (GE-25)
# --------------------------------------------------------------------------------------


def topic_index_findings(snapshot: KbSnapshot, topic_path: str | Path) -> list[Finding]:
    """One diagnostic per degraded entry this index renders (GE-25). Pure.

    The topic's own ``topic.md`` is diagnosed by the root catalog (it renders the same entry), so
    only the topic's items are reported here — otherwise a single missing ``description`` would
    produce two findings for one fix.
    """
    topic = _topic(snapshot, topic_path)
    findings: list[Finding] = []
    for record in _indexable(snapshot, topic):
        if record.role is FileRole.TOPIC_OVERVIEW:
            continue
        meta = record.meta
        if meta is None or not meta.description:
            findings.append(base.missing_description(record.path))
    return findings
