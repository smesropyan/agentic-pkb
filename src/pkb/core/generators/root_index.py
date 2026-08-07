"""Root ``index.md`` — the Librarian's routing view (GE-10 … GE-13, §4.2).

One line per topic root at every depth, nested one level under its parent, and nothing else. The
constraint that shapes every decision here is GE-12: this file is *context*, read in full by an
agent deciding where to route a question. So it carries no per-file listing, no counts, no tags and
no dates — a note added to ``Cooking/notes/`` must leave these bytes untouched (GE-10), or the
Librarian's context churns on every edit anywhere in the tree.

Sub-topics are listed (Q9): ``topic/cooking/grilling`` is an independently addressable agent, and a
Librarian cannot route to an agent it cannot see.
"""

from __future__ import annotations

from pathlib import Path

from pkb.core import paths
from pkb.core.errors import Finding
from pkb.core.generators import base
from pkb.core.models import KbSnapshot, Metadata, TopicRecord

__all__ = [
    "CUSTOM_EXPERT_MARKER",
    "DESCRIPTION",
    "NO_TOPICS",
    "SOURCE_TYPE",
    "TITLE",
    "generate_root_index",
    "render_root_index",
    "root_index_findings",
]

TITLE = "PKB Topic Catalog"
DESCRIPTION = f"Every PKB topic with its description{base.EM_DASH}the Librarian's routing view"
SOURCE_TYPE = "catalog"
"""Derived-reserved ``source_type`` for the root catalog (FM-6, Q2)."""

TOPICS_SECTION = "Topics"
NO_TOPICS = "_No topics yet._"
"""What an empty KB renders instead of an empty section (GE-29)."""

CUSTOM_EXPERT_MARKER = " *(custom expert)*"
"""Appended to a topic that owns an ``expert.md`` (GE-13)."""

INDENT = "    "
"""4 spaces per nesting level (GE-7)."""


def render_root_index(snapshot: KbSnapshot) -> str:
    """Render the root catalog (GE-10 … GE-13, GE-25). Pure: no I/O, no clock (GE-9).

    Entries follow ``snapshot.topics`` order, which is depth-first pre-order with siblings sorted
    case-insensitively then by codepoint (PA-5) — the same order this file must render in (GE-10),
    so no re-sorting is needed and none is possible to get wrong.
    """
    entries = [_entry(snapshot, topic) for topic in snapshot.topics.values()]
    body = entries if entries else [NO_TOPICS]
    meta = Metadata(title=TITLE, description=DESCRIPTION, source_type=SOURCE_TYPE)
    return base.document(meta, TITLE, base.section(TOPICS_SECTION, body))


def _entry(snapshot: KbSnapshot, topic: TopicRecord) -> str:
    """One catalog line: title, link, agent id, description, expert marker (§4.2).

    Built literally rather than through :func:`base.item_bullet` because the agent id sits between
    the link and the em dash — this is the only bullet shape in the KB that carries one, and it is
    what makes the file a *routing* view rather than a table of contents.
    """
    target = paths.link_target(snapshot.root, snapshot.root / topic.path / paths.TOPIC_FILE)
    marker = CUSTOM_EXPERT_MARKER if topic.has_expert else ""
    line = (
        f"- [{base.inline(topic.title)}]({target}) `{topic.agent_id}`"
        f"{base.EM_DASH}{_description(topic)}{marker}"
    )
    return f"{INDENT * _level(snapshot, topic)}{line}"


def _description(topic: TopicRecord) -> str:
    """The topic's description, degraded rather than dropped (GE-25)."""
    if topic.meta is None:
        return base.MISSING_TOPIC_METADATA
    description = topic.meta.description
    return base.inline(description) if description else base.NO_DESCRIPTION


def _level(snapshot: KbSnapshot, topic: TopicRecord) -> int:
    """Nesting depth from the parent chain, not from the tag.

    A topic whose folder name slugifies to nothing carries a degraded fallback tag (``scan``'s
    ``UNADDRESSABLE_TOPIC_ROOT``), so ``TopicRecord.depth`` would under-report its nesting and the
    catalog would render it at the wrong indent.
    """
    level = 0
    parent = topic.parent
    while parent is not None and parent in snapshot.topics:
        level += 1
        parent = snapshot.topics[parent].parent
    return level


def root_index_findings(snapshot: KbSnapshot) -> list[Finding]:
    """Diagnostics for every degraded catalog entry (GE-25). Pure; one finding per bad topic.

    The finding's path is the topic's ``topic.md`` rather than its folder, so it names the file an
    agent has to edit — and so the same defect reported from the topic index deduplicates against
    this one.
    """
    findings: list[Finding] = []
    for topic in snapshot.topics.values():
        path = f"{topic.path}/{paths.TOPIC_FILE}"
        if topic.meta is None:
            findings.append(base.missing_topic_metadata(path))
        elif not topic.meta.description:
            findings.append(base.missing_description(path))
    return findings


def generate_root_index(kb_root: Path, snapshot: KbSnapshot) -> bool:
    """Render and write ``<kb>/index.md``; True when the bytes changed (GE-8, GE-9)."""
    return base.write_derived(kb_root / paths.INDEX_FILE, render_root_index(snapshot))
