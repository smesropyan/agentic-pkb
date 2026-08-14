"""Topic ``index.md`` — the depth index (GE-14 … GE-18, GE-25 … GE-27, MA-10, T-16, T-38, §4.3).

Where the registry answers *which topic*, this file answers *which file inside it*. It is read by a
Topic Expert before it opens anything, so every bullet has to carry enough to decide without
opening: the title, the one-line description, and the tags that let an agent select the
``type.solution`` files without reading twelve notes (Q3).

Four boundaries are load-bearing:

* **It does not recurse into ``sub-topics/``** (GE-16). Each immediate sub-topic is one line linking
  to its own index. Thirty notes under ``sub-topics/Grilling/notes/`` add exactly one line here, and
  a thirty-first adds none — which is the only reason a deep tree stays readable at every level.
* **Item bodies are never read** (GE-14) — with one deliberate, narrow exception. The three breadth
  files (``topic.md``, ``notes/summary.md``, ``references/summary.md``) may each carry their own
  ``## Approaches`` section, and T-38 is explicit that regenerating the approach entries is "a lift
  rather than a judgment": the generator reads only that one heading's own lines, out of the
  ``ParsedDocument.body`` the walk already parsed (no second file open), checks each line's shape,
  and copies the well-formed ones verbatim. Everything else in this file still comes from
  frontmatter and from the path.
* **No file is silently dropped** (GE-25). A markdown file in a place the structure does not
  describe still gets a bullet, under ``Other``; a dropped file is invisible to every depth agent,
  which is strictly worse than an ugly index.
* **The skills catalog and the approach entries repeat no other level's** (T-16, T-35). Only a
  ``skills/*/SKILL.md`` whose owning topic root is exactly this one appears in ``## Skills`` — never
  a parent's, a sub-topic's, or a shipped one — and only this topic's own breadth files feed
  ``## Approaches``.

Item bullets sort by relative POSIX path, never by title (GE-27), so editing a title changes one
line in place and renaming a file moves exactly one line.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from pathlib import Path

from pkb.core import paths, tags
from pkb.core.errors import Finding, NotATopicRootError, Severity, sort_findings
from pkb.core.generators import base, derive
from pkb.core.models import FileClass, FileRecord, FileRole, KbSnapshot, Metadata, TopicRecord

__all__ = [
    "SOURCE_TYPE",
    "generate_topic_index",
    "render_topic_index",
    "topic_index_findings",
]

SOURCE_TYPE = "index"
"""Derived-reserved ``source_type`` for a topic index (FM-6, Q2)."""

TITLE_SUFFIX = f"{base.EM_DASH}Index"
DESCRIPTION_TEMPLATE = "Canonical index of the {title} topic"

BREADTH = "Breadth"
SKILLS = "Skills"
APPROACHES = "Approaches"
SUBTOPICS = "Sub-topics"
NOTES = "Notes"
REFERENCES = "References"
OTHER = "Other"
TAG_SUBTREE = "Tag subtree"
CROSS_TOPIC_MAPPINGS = "Cross-topic mappings"
MAINTENANCE_FLAGS = "Maintenance flags"

_BREADTH_ROLES = frozenset({FileRole.NOTES_SUMMARY, FileRole.REFERENCES_SUMMARY})
"""The ``summary.md`` files. ``topic.md`` leads the section separately — it is the topic itself."""

_BREADTH_FILE_NAMES: tuple[str, ...] = (
    paths.TOPIC_FILE,
    f"{paths.NOTES_DIR}/{paths.SUMMARY_FILE}",
    f"{paths.REFERENCES_DIR}/{paths.SUMMARY_FILE}",
)
"""The three files T-38 allows a ``## Approaches`` section in, topic-relative, breadth order."""

_APPROACHES_HEADING = "## Approaches"
_APPROACH_LINE = re.compile(r"^- [^:]+: [^\s#]+#.+$")
"""``- <name>: <kb-path>#<heading>`` (P1). A line under ``## Approaches`` that fails this shape is
never guessed at — it becomes a :func:`_malformed_approach` finding instead (T-38)."""


# --------------------------------------------------------------------------------------
# Rendering
# --------------------------------------------------------------------------------------


def render_topic_index(
    snapshot: KbSnapshot, topic_path: str | Path, flags: Sequence[Finding] = ()
) -> str:
    """Render one topic's index (GE-14 … GE-18, MA-10, T-16, T-38). Pure: no I/O, no clock (GE-9).

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
    blocks += base.section(SKILLS, _skills(snapshot, topic))
    blocks += base.section(APPROACHES, _approaches(snapshot, topic))
    blocks += base.section(SUBTOPICS, _subtopics(snapshot, topic))
    blocks += base.section(NOTES, _items(snapshot, topic, records, FileRole.NOTE))
    blocks += base.section(REFERENCES, _items(snapshot, topic, records, FileRole.REFERENCE))
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


def _skills(snapshot: KbSnapshot, topic: TopicRecord) -> list[str]:
    """This topic's own ``skills/*/SKILL.md`` catalog: ``name``/``description``, nothing inherited
    and nothing shadowed in (T-16, T-25). Root skills and a parent's or sub-topic's own overload
    live in *their* catalogs — the registry for the root, that other topic's own ``index.md`` here —
    and are never repeated in this one (T-35).
    """
    lines: list[str] = []
    for name, description in _topic_skill_entries(snapshot, topic):
        gloss = base.inline(description) if description else ""
        lines.append(f"{tags.BULLET}`{name}`" + (f"{tags.TAG_DEF_SEP}{gloss}" if gloss else ""))
    return lines


def _topic_skill_entries(snapshot: KbSnapshot, topic: TopicRecord) -> list[derive.SkillEntry]:
    """This topic's own skill entries, sorted by name — the read side of :func:`_skills`.

    ``snapshot.files_in_topic(topic.path)`` already scopes to files whose *owning topic root* is
    exactly this one (no ``include_subtopics``), which is what keeps a parent's and a sub-topic's
    own skill folders out of each other's catalog.
    """
    entries: list[derive.SkillEntry] = []
    for record in snapshot.files_in_topic(topic.path):
        if record.role is not FileRole.SKILL:
            continue
        parts = _relative(topic, record).split("/")
        if len(parts) != 3 or parts[0] != paths.SKILLS_DIR or parts[2] != paths.SKILL_FILE:
            continue
        entry = derive.read_skill_entry(record.doc)
        if entry is not None:
            entries.append(entry)
    return sorted(entries, key=lambda entry: tags.tag_sort_key(entry[0]))


def _approaches(snapshot: KbSnapshot, topic: TopicRecord) -> list[str]:
    """Every well-formed ``## Approaches`` line the topic's own breadth files carry (T-38, P1).

    A breadth file names an approach together with where it sits, so this is a lift rather than a
    judgment: find the section, keep the lines whose shape matches, and note which breadth file each
    one came from — a malformed line is dropped here and reported instead by
    :func:`_malformed_approaches`, never guessed at.
    """
    lines: list[str] = []
    for relative in _BREADTH_FILE_NAMES:
        for entry in _approach_section_lines(snapshot, topic, relative):
            if _APPROACH_LINE.match(entry):
                lines.append(f"{entry} (from `{relative}`)")
    return lines


def _malformed_approaches(snapshot: KbSnapshot, topic: TopicRecord) -> list[Finding]:
    """One ``MALFORMED_APPROACH_ENTRY`` finding per ``## Approaches`` line that does not match
    ``- <name>: <kb-path>#<heading>`` (T-38) — the totality counterpart to :func:`_approaches`
    dropping the same line rather than copying it."""
    findings: list[Finding] = []
    for relative in _BREADTH_FILE_NAMES:
        for entry in _approach_section_lines(snapshot, topic, relative):
            if not _APPROACH_LINE.match(entry):
                findings.append(_malformed_approach(f"{topic.path}/{relative}", entry))
    return findings


def _approach_section_lines(
    snapshot: KbSnapshot, topic: TopicRecord, breadth_relative: str
) -> list[str]:
    """The non-blank lines of one breadth file's own ``## Approaches`` section, if it has one.

    Reads ``ParsedDocument.body`` — already parsed by the walk, so this costs no second file open
    (GE-9) — rather than the filesystem; a breadth file that does not exist or failed to parse
    contributes nothing, same as a missing ``description`` degrades rather than aborts (GE-25).
    """
    record = snapshot.files.get(f"{topic.path}/{breadth_relative}")
    if record is None or record.doc is None:
        return []
    lines = record.doc.body.splitlines()
    try:
        start = lines.index(_APPROACHES_HEADING) + 1
    except ValueError:
        return []
    section: list[str] = []
    for line in lines[start:]:
        if line.startswith("## "):
            break
        stripped = line.strip()
        if stripped:
            section.append(stripped)
    return section


def _malformed_approach(path: str, raw: str) -> Finding:
    return Finding(
        code="MALFORMED_APPROACH_ENTRY",
        severity=Severity.WARNING,
        message=(
            f"'## Approaches' line {raw!r} does not match '- <name>: <kb-path>#<heading>', so it "
            "was not copied into the topic index"
        ),
        rule_id="T-38",
        path=path,
        hint="use the shape '- <name>: <kb-path>#<heading>', e.g. "
        "'- Reverse sear: Cooking/recipes/ribeye-on-gas.md#Reverse sear'",
    )


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


def _tag_subtree(snapshot: KbSnapshot, topic: TopicRecord) -> list[str]:
    """The branch of the **global** tag tree rooted at this topic's tag (GE-17, T-16).

    Global, not local: a file filed elsewhere that carries ``topic.cooking.sous-vide`` still shows
    up here, which is the point — the subtree describes the topic's vocabulary, not its folder. Only
    ``topic.*`` is rendered; ``type.*``/``domain.*`` live in the registry (§5).

    Annotated the same way the registry annotates its own namespace section — same source function,
    :func:`~pkb.core.generators.derive.topic_node_annotations` — so a topic-backed node's lifted
    description and ``*(custom expert)*`` marker read identically whichever file shows them (T-16's
    own "same renderer for the tag subtree").
    """
    tree = tags.build_tag_tree(snapshot)
    node = tree.subtree(topic.tag) or tags.TagNode(topic.tag)
    return tags.render_tag_tree([node], annotations=derive.topic_node_annotations(snapshot))


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
    """One diagnostic per degraded entry this index renders (GE-25, T-38). Pure.

    The topic's own ``topic.md`` is diagnosed by the registry (it renders the same entry — T-37), so
    only the topic's items are reported here — otherwise a single missing ``description`` would
    produce two findings for one fix. A malformed ``## Approaches`` line has no such twin anywhere
    else, so :func:`_malformed_approaches` is folded straight in.
    """
    topic = _topic(snapshot, topic_path)
    findings: list[Finding] = []
    for record in _indexable(snapshot, topic):
        if record.role is FileRole.TOPIC_OVERVIEW:
            continue
        meta = record.meta
        if meta is None or not meta.description:
            findings.append(base.missing_description(record.path))
    findings += _malformed_approaches(snapshot, topic)
    return findings
