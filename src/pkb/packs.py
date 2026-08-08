"""Context packs — a deterministic, read-only slice of the knowledge base (PK-7 … PK-12).

A pack is what an external agent gets instead of the whole tree: a research agent asking "what do I
know about referral programmes" gets breadth, an implementation agent working inside one topic gets
depth. README Part 4 names the two shapes; this module builds them.

**A leaf, deliberately.** It imports ``pkb.core`` and ``pkb.contracts`` and nothing else — no
harness, no transport, no service. Layer 2's Q10 put assembly in ``pkb.agents`` when the consumer was
a Topic Expert; the consumer is now MCP, the types have to cross the seam under I2 anyway, and
leaving assembly above the harness would mean the only way to test a golden pack is to stand up a
runtime with a checkpointer and a chat model (decision G).

Three properties, each of which is a rule rather than a preference:

* **No model runs here (PK-8).** An implementation pack makes zero graph runs and a research pack
  makes at most one — the *classification* that chooses topics, which lives in Layer 2 and is skipped
  entirely when the caller names its topics. That is what makes "``notes/summary.md`` is always
  first" a property a golden test pins rather than a hope about a prompt.
* **The ordering is the contract, not an implementation detail (PK-9, PK-10).** An implementation
  agent reads top-down, so human rules come first: ``notes/summary.md``, then the topic's own index,
  then the depth files, then solution notes. A research agent gets the tag subtree, then breadth per
  topic, with **experience before static knowledge** — ``notes/summary.md`` ahead of
  ``references/summary.md``, §1.7's general rule applied to ordering.
* **Truncation is visible and lands on an entry boundary (PK-11).** Packs are the one result that can
  be arbitrarily large; a topic with forty ingested references produces an implementation pack no
  context window holds. A silently clipped pack is worse than a short one, because the consumer
  reasons over what arrived with no way to know what did not — so every omission is named, with its
  reason, and no file is ever cut in half.

Membership comes from Layer 1's derived surface — the snapshot, ``files_with_tag``, ``build_tag_tree``
— never from a second tree walk (PK-12). Assembly writes nothing, tags nothing, stamps nothing and
enqueues nothing: building a pack twice leaves the tree byte-identical, mtimes included.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator, Sequence
from typing import TYPE_CHECKING, Final

from pkb.contracts import Escalation, Pack, PackEntry, PackOmission
from pkb.core import (
    FileRecord,
    FileRole,
    KbSnapshot,
    build_tag_tree,
    files_with_tag,
)
from pkb.core.paths import TAGS_FILE
from pkb.core.tags import render_tag_tree

if TYPE_CHECKING:  # pragma: no cover - typing only
    from pkb.core.models import TopicRecord

__all__ = [
    "CONFLICT_TAG",
    "TAG_SUBTREE_ROLE",
    "UnknownTopicError",
    "implementation_pack",
    "research_pack",
]

CONFLICT_TAG: Final = "status.conflict-review"
"""README §1.7's review flag. A file carrying it inside a pack's scope is an escalation (MC-20)."""

TAG_SUBTREE_ROLE: Final = "tag-subtree"
"""The one synthesized entry: the slice of the root ``tags.md`` covering this pack's scope.

Synthesized rather than read, because README Part 4 asks for *"the relevant subtrees of the root
``tags.md``"* and the file itself holds every namespace in the knowledge base. Its ``path`` is still
``tags.md``, because that is where the content comes from and a consumer citing it should cite the
real file — the ``role`` is what says it is a slice.
"""

_BUDGET_REASON: Final = "budget"

_RESEARCH_ROLES: Final = (
    FileRole.TOPIC_OVERVIEW,
    FileRole.NOTES_SUMMARY,
    FileRole.REFERENCES_SUMMARY,
)
"""§1.7's ordering inside one topic: the overview, then experience, then static knowledge."""

_SOLUTION_TAG: Final = "type.solution"


class UnknownTopicError(ValueError):
    """No topic answers to this agent id.

    A plain ``ValueError`` subclass rather than a ``PkbAgentError``: this module is below the seam
    and knows nothing about status codes. The caller that has a wire to answer maps it.
    """


# --------------------------------------------------------------------------------------
# Research — breadth-first (PK-9)
# --------------------------------------------------------------------------------------


def research_pack(
    snapshot: KbSnapshot,
    *,
    topics: Sequence[str],
    include_index: bool = False,
    budget_bytes: int | None = None,
) -> Pack:
    """Breadth across the named topics, in the order a research agent should read it (PK-9).

    ``topics`` are **agent ids** (``topic/cooking/grilling``), already chosen — by the caller, or by
    the one classification call that lives in Layer 2. This function never chooses and never calls a
    model, which is what keeps the ordering golden-testable.

    The ordering, fixed:

    1. the **tag subtree** covering the scope — the slice of the root ``tags.md`` a research agent
       needs to know what vocabulary exists before it reads anything;
    2. then, per topic in snapshot order: ``topic.md``, ``notes/summary.md``,
       ``references/summary.md``;
    3. then every note in scope carrying ``status.conflict-review``, because a research agent that
       reasons over contested material without knowing it is contested produces confident nonsense.

    ``index.md`` is **excluded** unless ``include_index`` — README says research agents do not read
    indexes unless explicitly asked, and an index is a list of everything, which is the opposite of
    breadth.
    """
    records = _in_snapshot_order(snapshot, topics)
    ordered: list[PackEntry] = []

    subtree = _tag_subtree_entry(snapshot, records)
    if subtree is not None:
        ordered.append(subtree)

    for record in records:
        for role in _RESEARCH_ROLES:
            ordered.extend(_entries_for_role(snapshot, record, role))
        if include_index:
            ordered.extend(_entries_for_role(snapshot, record, FileRole.TOPIC_INDEX))

    ordered.extend(_conflict_entries(snapshot, records))

    return _assemble("research", records, ordered, snapshot, budget_bytes)


# --------------------------------------------------------------------------------------
# Implementation — depth-first (PK-10)
# --------------------------------------------------------------------------------------


def implementation_pack(
    snapshot: KbSnapshot,
    *,
    topic: str,
    include_subtopics: bool = False,
    budget_bytes: int | None = None,
) -> Pack:
    """Depth inside one topic, human rules first (PK-10).

    The ordering, fixed:

    1. ``notes/summary.md`` — **always first, even when it is an empty placeholder**. An
       implementation agent reads top-down and stops when its context fills; the human's own
       distilled rules are the one thing that must never be the part that fell off the end.
    2. the topic's full ``index.md`` — README asks for it by name, and it is how the agent learns
       what else is available without a second call;
    3. ``references/<src>/<src>.md`` depth files — the ingested static knowledge;
    4. notes tagged ``type.solution`` — reusable approaches.

    ``include_subtopics`` defaults **False**: README says the full index of *the selected topic*, and
    a ``topic/cooking`` request over a four-sub-topic tree would otherwise return an order of
    magnitude more than was asked for — the context-window problem goal 2 exists to solve (Q8).
    """
    record = _topic_record(snapshot, topic)
    scope = [record, *_descendants(snapshot, record)] if include_subtopics else [record]

    ordered: list[PackEntry] = []
    for owner in scope:
        ordered.extend(_entries_for_role(snapshot, owner, FileRole.NOTES_SUMMARY))
    for owner in scope:
        ordered.extend(_entries_for_role(snapshot, owner, FileRole.TOPIC_INDEX))
    for owner in scope:
        ordered.extend(_entries_for_role(snapshot, owner, FileRole.REFERENCE))
    ordered.extend(_solution_entries(snapshot, scope))

    return _assemble("implementation", scope, ordered, snapshot, budget_bytes)


# --------------------------------------------------------------------------------------
# Selection helpers — every one of them reads the snapshot, never the tree (PK-12)
# --------------------------------------------------------------------------------------


def _topic_record(snapshot: KbSnapshot, agent_id: str) -> TopicRecord:
    """The topic an agent id names, or a refusal naming the id (RG-9, MC-16).

    Never a nearest-match guess: an id that does not resolve is an error naming the id, because a
    pack silently built for the wrong topic is indistinguishable from a correct one at the consumer.

    Resolved off the **snapshot** rather than through ``topic_path_for_agent_id``, which re-resolves
    against the tree: the snapshot already carries every topic's ``agent_id``, and PK-12 says
    membership comes from Layer 1's derived surface rather than from a second walk of the disk.
    """
    record = next((t for t in snapshot.topics.values() if t.agent_id == agent_id), None)
    if record is None:
        raise UnknownTopicError(
            f"no topic answers to the agent id {agent_id!r} — "
            f"expected one of: {', '.join(sorted(t.agent_id for t in snapshot.topics.values()))}"
        )
    return record


def _in_snapshot_order(snapshot: KbSnapshot, agent_ids: Sequence[str]) -> list[TopicRecord]:
    """The named topics, in the tree's own order — not the caller's (PK-9).

    Every id is resolved first, so an unknown one still raises rather than being silently dropped by
    the reordering. The order matters because the pack is golden-tested: two callers naming the same
    two topics differently — an alphabetical classifier and a score-ranked one — would otherwise get
    byte-different packs for identical content, and the ordering rule would be untestable.
    """
    wanted = {_topic_record(snapshot, agent_id).path for agent_id in agent_ids}
    return [record for path, record in snapshot.topics.items() if path in wanted]


def _descendants(snapshot: KbSnapshot, record: TopicRecord) -> list[TopicRecord]:
    """Every topic below this one, in snapshot order (which is depth-first pre-order, PA-5)."""
    prefix = f"{record.path}/"
    return [topic for path, topic in snapshot.topics.items() if path.startswith(prefix)]


def _entries_for_role(
    snapshot: KbSnapshot, record: TopicRecord, role: FileRole
) -> Iterator[PackEntry]:
    """Every file of one role owned by one topic, in snapshot order."""
    for file_record in snapshot.files_in_topic(record.path):
        if file_record.role is role:
            entry = _entry(file_record, role.value)
            if entry is not None:
                yield entry


def _conflict_entries(snapshot: KbSnapshot, records: Sequence[TopicRecord]) -> Iterator[PackEntry]:
    """Files under review inside the scope, deduplicated, in snapshot order (README Part 4)."""
    for path in _scoped_conflicts(snapshot, records):
        file_record = snapshot.files.get(path)
        if file_record is not None:
            entry = _entry(file_record, CONFLICT_TAG)
            if entry is not None:
                yield entry


def _solution_entries(snapshot: KbSnapshot, records: Sequence[TopicRecord]) -> Iterator[PackEntry]:
    """``type.solution`` notes inside the scope — reusable approaches (README Part 4)."""
    tagged = set(files_with_tag(snapshot, _SOLUTION_TAG))
    owners = {record.path for record in records}
    for path, file_record in snapshot.files.items():
        if path in tagged and file_record.topic_path in owners:
            entry = _entry(file_record, _SOLUTION_TAG)
            if entry is not None:
                yield entry


def _tag_subtree_entry(snapshot: KbSnapshot, records: Sequence[TopicRecord]) -> PackEntry | None:
    """The slice of the root ``tags.md`` covering this scope, rendered by Layer 1 (README Part 4).

    ``render_tag_tree`` is Layer 1's own renderer, so the vocabulary a research agent reads here is
    formatted exactly as the registry it will see in the tree — one answer to "what tags exist",
    not two (GE-23).
    """
    tree = build_tag_tree(snapshot)
    nodes = [node for node in (tree.subtree(record.tag) for record in records) if node is not None]
    if not nodes:
        return None
    text = "\n".join(render_tag_tree(nodes)) + "\n"
    return PackEntry(
        path=TAGS_FILE,
        role=TAG_SUBTREE_ROLE,
        text=text,
        bytes=len(text.encode("utf-8")),
    )


def _entry(record: FileRecord, role: str) -> PackEntry | None:
    """One file as a pack entry, or ``None`` when it cannot be read.

    ``newline=""`` because the byte count is a budget input (PK-11): universal-newline translation
    silently shortens a CRLF file, so the pack would be sized against text that is not what a
    consumer receives. Reading through ``abs_path`` — a path the snapshot already named — is *not* a
    second tree walk and is what PK-12 permits: membership is derived, the bytes are fetched.

    A file the snapshot knows about and the filesystem will not hand over is skipped rather than
    raised on: a pack that fails entirely because one file was removed mid-assembly is a worse
    answer than a pack that is one file short, and the omission is invisible either way — which is
    why this is the only silent drop in the module and it is bounded by "the tree changed under us".
    """
    try:
        with record.abs_path.open(encoding="utf-8", newline="") as handle:
            text = handle.read()
    except (OSError, UnicodeDecodeError):
        return None
    return PackEntry(path=record.path, role=role, text=text, bytes=len(text.encode("utf-8")))


def _scoped_conflicts(snapshot: KbSnapshot, records: Sequence[TopicRecord]) -> list[str]:
    """Conflict-flagged paths inside the scope's topic subtrees, in snapshot order (MC-20).

    Deterministic and computed from the tag — never from what a model said it read — and it
    self-clears the moment the human moves the tag back to ``status.approved``.
    """
    flagged = set(files_with_tag(snapshot, CONFLICT_TAG))
    scope = {record.path for record in records}
    return [
        path
        for path, record in snapshot.files.items()
        if path in flagged and record.topic_path is not None and record.topic_path in scope
    ]


def escalations(snapshot: KbSnapshot, topics: Iterable[str]) -> tuple[Escalation, ...]:
    """Every conflict-flagged file inside these topics, with its ``review_note`` (MC-20).

    Exposed separately from the packs because all four MCP tools need it, including the two that
    build no pack: any tool whose scope touches contested material has to stop, and stopping is a
    successful result with a discriminator rather than an error (a well-behaved agent retries
    errors, and a retried escalation is an escalation ignored).
    """
    records = [_topic_record(snapshot, agent_id) for agent_id in topics]
    out: list[Escalation] = []
    for path in _scoped_conflicts(snapshot, records):
        file_record = snapshot.files[path]
        meta = file_record.doc.meta if file_record.doc else None
        owner = next(
            (r.agent_id for r in records if r.path == file_record.topic_path),
            "",
        )
        out.append(
            Escalation(
                path=path,
                review_note=(getattr(meta, "review_note", None) or "") if meta else "",
                agent_id=owner,
            )
        )
    return tuple(out)


# --------------------------------------------------------------------------------------
# Assembly — dedupe, budget, escalate (PK-11)
# --------------------------------------------------------------------------------------


def _assemble(
    kind: str,
    records: Sequence[TopicRecord],
    ordered: Sequence[PackEntry],
    snapshot: KbSnapshot,
    budget_bytes: int | None,
) -> Pack:
    """Deduplicate, apply the budget at an entry boundary, attach escalations.

    Deduplication keeps the **first** occurrence, which is what makes the ordering rules compose: a
    file that is both the topic's ``notes/summary.md`` and tagged ``type.solution`` is read where the
    higher-priority rule put it, once.
    """
    seen: set[str] = set()
    unique: list[PackEntry] = []
    for entry in ordered:
        if entry.path in seen:
            continue
        seen.add(entry.path)
        unique.append(entry)

    kept, omitted = _apply_budget(unique, budget_bytes)
    return Pack(
        kind=kind,  # type: ignore[arg-type]
        scope=tuple(record.agent_id for record in records),
        entries=tuple(kept),
        omitted=tuple(omitted),
        escalation=escalations(snapshot, [record.agent_id for record in records]),
    )


def _apply_budget(
    entries: Sequence[PackEntry], budget_bytes: int | None
) -> tuple[list[PackEntry], list[PackOmission]]:
    """Take a **prefix** of the ordering that fits, and name everything it did not (PK-11).

    A prefix rather than a best-fit packing: the ordering *is* the priority, so skipping a large
    early file to fit two small later ones would silently reorder the pack — and the consumer, which
    reads top-down and trusts the order, would never know. Once one entry does not fit, every
    remaining entry is omitted for the same reason, including any that would have.
    """
    if budget_bytes is None or budget_bytes <= 0:
        return list(entries), []

    kept: list[PackEntry] = []
    omitted: list[PackOmission] = []
    used = 0
    for entry in entries:
        if omitted or used + entry.bytes > budget_bytes:
            omitted.append(
                PackOmission(
                    path=entry.path, role=entry.role, reason=_BUDGET_REASON, bytes=entry.bytes
                )
            )
            continue
        kept.append(entry)
        used += entry.bytes
    return kept, omitted
