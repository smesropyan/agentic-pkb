"""Layer 1 — the mechanical knowledge-base core.

Plain Python over a directory tree: no LLM, no network, no database, no subprocess, and no imports
from the agent or transport layers (invariant I1, rule CX-1). Its whole test suite runs against
``tmp_path``.

Rules are numbered in ``docs/superpowers/specs/2026-08-06-pkb-core-layer1-rules.md`` (``FM-*``,
``PA-*``, ``TG-*``, ``VA-*``, ``GE-*``, ``SC-*``, ``MA-*``, ``CX-*``). Code and tests cite those ids;
changing behaviour means changing the rule first.

This module re-exports the surface Layer 2 needs, so ``pkb.agents`` never reaches into a submodule
for ordinary work:

**Reading the tree** — ``pkb.core.scan.scan`` produces the one :class:`KbSnapshot` every consumer
shares (decision C). It is deliberately *not* re-exported here: binding the name ``scan`` on this
package would shadow the ``pkb.core.scan`` submodule, so ``pkb.core.scan.frontmatter`` would stop
resolving and every string-based patch of it would fail. Import it as
``from pkb.core.scan import scan``. :func:`build_tag_tree` and :func:`files_with_tag` answer the tag
queries a context pack is built from.

**Gating a write** — :func:`validate_content` is pure over ``(path, proposed text)`` and correct for
a path that does not exist yet, which is what makes it usable from ``wrap_tool_call`` *before* the
write lands. It returns findings; :func:`has_errors` decides whether the write is refused and
:func:`render_findings` produces the text the agent reads.

**After a turn** — :func:`flush` performs the six maintenance duties once, on both the success and
the failure path. It writes exactly two kinds of bytes: derived files, wholesale, and an ``updated``
line on paths the caller explicitly names. Everything else it merely reports.

**Creating a topic** — :func:`scaffold_topic` and :func:`scaffold_subtopic` write the standard
structure and nothing optional. They contain no approval gate: approval happens in Layer 2 before
the call (SC-8).

**Addressing** — :func:`agent_id_for`, :func:`topic_path_for_agent_id`, :func:`resolve_expert` and
:func:`resolve_skills` are the pure path logic behind the agent registry (PA-10, PA-13, PA-14).
:func:`is_derived_name` is the single source of truth for invariant I3's deny list — Layer 2 builds
its ``FilesystemPermission`` from it rather than restating the globs.
"""

from pkb.core.errors import (
    Finding,
    KbNotFoundError,
    NotATopicRootError,
    PkbError,
    ScaffoldError,
    Severity,
    TopicDepthExceededError,
    errors_only,
    has_errors,
    render_findings,
    sort_findings,
)
from pkb.core.generators import regenerate_all
from pkb.core.maintenance import build_scan_requests, find_broken_links, find_orphans, flush
from pkb.core.models import (
    FileClass,
    FileRecord,
    FileRole,
    FlushReport,
    KbSnapshot,
    Metadata,
    ParsedDocument,
    ScaffoldResult,
    ScanRequest,
    TopicRecord,
)
from pkb.core.paths import (
    agent_id_for,
    is_derived_name,
    is_generated,
    resolve_expert,
    resolve_skills,
    topic_path_for_agent_id,
    topic_tag_for,
)
from pkb.core.scaffold import scaffold_subtopic, scaffold_topic
from pkb.core.tags import Namespace, Tag, TagTree, build_tag_tree, files_with_tag
from pkb.core.validation import validate_content, validate_file, validate_tree

__all__ = [
    "FileClass",
    "FileRecord",
    "FileRole",
    "Finding",
    "FlushReport",
    "KbNotFoundError",
    "KbSnapshot",
    "Metadata",
    "Namespace",
    "NotATopicRootError",
    "ParsedDocument",
    "PkbError",
    "ScaffoldError",
    "ScaffoldResult",
    "ScanRequest",
    "Severity",
    "Tag",
    "TagTree",
    "TopicDepthExceededError",
    "TopicRecord",
    "agent_id_for",
    "build_scan_requests",
    "build_tag_tree",
    "errors_only",
    "files_with_tag",
    "find_broken_links",
    "find_orphans",
    "flush",
    "has_errors",
    "is_derived_name",
    "is_generated",
    "regenerate_all",
    "render_findings",
    "resolve_expert",
    "resolve_skills",
    "scaffold_subtopic",
    "scaffold_topic",
    "sort_findings",
    "topic_path_for_agent_id",
    "topic_tag_for",
    "validate_content",
    "validate_file",
    "validate_tree",
]
