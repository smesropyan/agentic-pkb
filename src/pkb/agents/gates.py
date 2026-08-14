"""The approval table — the single answer to "does this action need a human?" (RT-21 … RT-35).

Everything that decides *whether* a human must see an action, and *what* they see, lives here and
only here. :func:`requires_approval` is a pure function of ``(tool, kb-relative path, args)`` plus
one :class:`~pkb.core.models.KbSnapshot`, so the whole gate policy is table-testable without
constructing a graph, a model, or a runtime (RT-21). :func:`build_interrupt_on` is the only place
that turns it into harness configuration, and it wires the same predicate as the ``when`` clause of
**one** ``interrupt_on`` entry per tool — never a second permissions list, which over-fires on bulk
tools (RT-22).

Two properties with teeth:

* **``respond`` is never allowed on a KB write gate** (RT-32). ``respond`` yields
  ``status="success"`` with the tool *skipped*, which tells the model the write landed when nothing
  was written (``human_in_the_loop.py:317-333``). And **no entry is ever ``False``** (RT-33):
  ``False`` means auto-approve, and the AI never resolves its own gate.
* **The description runs :func:`pkb.core.validate_content` on the proposal** (RT-35). HITL fires in
  ``after_model``, strictly *before* ``KbValidationMiddleware``'s ``wrap_tool_call`` (D-17), so
  without this a human can approve content the validator refuses a moment later — burning one of
  the three attempts (MW-14) on something they already said yes to. Labelling the draft lets them
  reject or edit instead.

**Amended 2026-08-07 (large-source ingestion).** RT-31's "no gate on a reference depth file" splits.
The first write of a ``references/<src>/<src>.md`` is still un-gated, and so is every later write
that only *adds* to it — that is LS-12's line, and it is what lets a re-ingestion's new arguments
land unattended. A write that would **remove or alter** a line already in one stops for a human
(:attr:`GateReason.REFERENCE_REWRITE`), because it replaces an extraction they have already read and
there is no undo. The test is a diff, not an authorship claim, which is why the same rule covers a
model's ``write_file``, a human-driven ``edit_file`` and the ingestion loop's own write alike.

Paths arriving at :func:`requires_approval` are already KB-relative POSIX strings. Normalisation is
``pkb.agents.paths.to_kb_relative``'s job (RT-8/RT-9) and the ``/kb/`` mount prefix is spelled in
exactly one module, which is not this one; :class:`GateEnv` carries the normaliser as a field so
that stays true and so the gate table can be exercised without a mount at all.
"""

from __future__ import annotations

import difflib
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, Final

from deepagents.backends.utils import perform_string_replacement

from pkb.agents.paths import canonical_kb_path
from pkb.agents.paths import to_kb_relative as _to_kb_relative
from pkb.agents.permissions import is_denied_derived
from pkb.contracts import DecisionType
from pkb.core import (
    Namespace,
    Tag,
    build_tag_tree,
    errors_only,
    has_errors,
    render_findings,
    validate_content,
)
from pkb.core.frontmatter import parse
from pkb.core.models import KbSnapshot
from pkb.core.paths import (
    EXPERT_FILE,
    NOTES_DIR,
    REFERENCES_DIR,
    SKILLS_DIR,
    STRUCTURAL_DIRS,
    SUMMARY_FILE,
    TOPIC_FILE,
    extension_folders,
    owning_topic_root,
    rel,
)

if TYPE_CHECKING:  # pragma: no cover - typing only
    from langchain_core.messages import ToolCall
    from langgraph.prebuilt.tool_node import ToolCallRequest

__all__ = [
    "APPROVED_TAG",
    "CONFLICT_TAG",
    "DELETE_DECISIONS",
    "GATED_TOOLS",
    "GATE_DECISIONS",
    "WRITE_DECISIONS",
    "GateEnv",
    "GateReason",
    "allowed_decisions",
    "build_interrupt_on",
    "describe_write",
    "new_tags",
    "proposed_content",
    "requires_approval",
]


# --------------------------------------------------------------------------------------
# Vocabulary
# --------------------------------------------------------------------------------------

WRITE_FILE_TOOL: Final = "write_file"
EDIT_FILE_TOOL: Final = "edit_file"
DELETE_TOOL: Final = "delete"
CREATE_TOPIC_TOOL: Final = "create_topic"
CREATE_SUBTOPIC_TOOL: Final = "create_subtopic"

APPROVED_TAG: Final = "status.approved"
"""A member of Layer 1's closed ``status.*`` vocabulary — pinned by a test, not restated policy."""

UNREADABLE: Final = "\x00<unreadable>"
"""Stands in for the current content of a file that exists and cannot be decoded — see `_read`.

A sentinel rather than `None` because the difference decides whether the gate table protects the
file at all, and a sentinel rather than `""` because an empty string is a *readable* empty file,
which a proposed body legitimately extends. The NUL makes it unconstructible as real file content.
"""

CONFLICT_TAG: Final = "status.conflict-review"
"""Ditto. README §1.7's review flag; adding it is deliberately ungated (RT-26)."""

_PROPOSABLE_NAMESPACES: Final = frozenset({Namespace.TOPIC, Namespace.DOMAIN})
"""The two open namespaces a new tag can appear in. ``type.*`` and ``status.*`` are closed
vocabularies Layer 1 already rejects extensions to, so gating them would be dead weight (RT-25)."""


class GateReason(StrEnum):
    """Why a gate fired, as the stable slug that reaches a client (`ActionView.reason`).

    Members are listed in **evaluation order**: a single write can match several rules, and
    :func:`requires_approval` returns the first match reading down this list. The order is chosen
    so the human reads the most consequential framing of the action — "you are resolving a
    conflict" beats "you are introducing status.approved", which beats "you edited a body".
    """

    TOPIC_CREATION = "topic-creation"
    """``create_topic``/``create_subtopic``: scaffolding a new topic root (LB-7, EX-12)."""

    DELETE = "delete"
    """Any delete under the KB. There is no version control and no undo (RT-30, D6)."""

    UNRESOLVED_PATH = "unresolved-path"
    """The target exists on disk but :func:`pkb.agents.paths.canonical_kb_path` cannot say *as
    what* — an unreadable directory, or a rename racing the walk. Every rule below this line is a
    question about a specific file, so none of them can be answered; a check that cannot be
    evaluated is not a check that passed, and the human decides instead (RT-23 … RT-29)."""

    CONFLICT_RESOLUTION = "conflict-resolution"
    """Clearing ``status.conflict-review``. Adding it is exempt — README §1.7 instructs the AI to
    tag, and gating that would block every background scan on a human (RT-26)."""

    BREADTH_APPROVAL = "breadth-approval"
    """One of the three compact approval surfaces: ``topic.md``, ``notes/summary.md``,
    ``references/summary.md`` (RT-23)."""

    EXPERT_OVERLOAD = "expert-overload"
    """``<topic>/expert.md`` — human-created, AI-assisted (RT-29)."""

    SKILL_OVERLOAD = "skill-overload"
    """Anything under a ``skills/`` directory, topic-level or KB-root (RT-29)."""

    EXTENSION_FOLDER = "extension-folder"
    """Minting a new non-structural directory directly under a topic root (RT-28)."""

    REFERENCE_REWRITE = "reference-rewrite"
    """A write to an existing ``references/<src>/<src>.md`` that would **remove or alter** text
    already in it (RT-31, amended 2026-08-07 for large-source ingestion).

    RT-31 put no gate on reference depth files, and that was right while a source was written once
    and never touched again. Re-ingestion (LS-5) makes a second reading of the same source routine,
    and an un-gated write then overwrites an extraction the human has already read and relied on.
    So the rule splits, exactly where LS-12 draws the destructiveness line: the **first** write of a
    source file stays un-gated, and every later write is un-gated too *for as long as it only adds*
    — a new argument, a new chapter section, another reading's record all land unattended, because
    nothing is lost. A write that deletes or edits a line that is already there stops for a human,
    with the whole reconciled file as one proposal rather than one gate per argument.

    Without the split, "human content wins" holds for notes and quietly fails for everything derived
    from a source, which is most of what a knowledge base accumulates.
    """

    NEW_TAG = "new-tag"
    """The frontmatter introduces a ``topic.*``/``domain.*`` tag no file in the KB carries. Layer 1
    keeps no approved-tag list, so this gate is the only mechanical backing for README §1.5's
    "Do not create ad-hoc tags" (RT-25)."""

    STATUS_APPROVED = "status-approved"
    """The write introduces ``status.approved`` on a file class the human curates. Reference depth
    files are exempt (RT-27, Q4)."""

    HUMAN_CONTENT_EDIT = "human-content-edit"
    """The **body** of an existing authored note or extension item changed. The AI does not change
    a note's factual content unattended (RT-24)."""


WRITE_DECISIONS: Final[tuple[DecisionType, ...]] = ("approve", "edit", "reject")
"""Every write gate, verbatim. ``respond`` is absent by rule, not by omission (RT-32): it reports
success to the model while skipping the tool, so the agent would believe a file it never wrote is
on disk. ``edit`` is what lets a human fix a draft in place instead of round-tripping."""

DELETE_DECISIONS: Final[tuple[DecisionType, ...]] = ("approve", "reject")
"""A delete cannot be usefully "edited" into a different delete — approve it or do not (RT-30)."""

GATE_DECISIONS: Final[Mapping[GateReason, tuple[DecisionType, ...]]] = MappingProxyType(
    {
        GateReason.TOPIC_CREATION: WRITE_DECISIONS,
        GateReason.DELETE: DELETE_DECISIONS,
        GateReason.UNRESOLVED_PATH: WRITE_DECISIONS,
        GateReason.CONFLICT_RESOLUTION: WRITE_DECISIONS,
        GateReason.BREADTH_APPROVAL: WRITE_DECISIONS,
        GateReason.EXPERT_OVERLOAD: WRITE_DECISIONS,
        GateReason.SKILL_OVERLOAD: WRITE_DECISIONS,
        GateReason.EXTENSION_FOLDER: WRITE_DECISIONS,
        GateReason.REFERENCE_REWRITE: WRITE_DECISIONS,
        GateReason.NEW_TAG: WRITE_DECISIONS,
        GateReason.STATUS_APPROVED: WRITE_DECISIONS,
        GateReason.HUMAN_CONTENT_EDIT: WRITE_DECISIONS,
    }
)
"""The approval table: what a human may answer, per reason. Server-side truth — a client may narrow
its UI (Telegram drops ``edit``) but never widen it (RT-32)."""

_FILE_TOOLS: Final = frozenset({WRITE_FILE_TOOL, EDIT_FILE_TOOL, DELETE_TOOL})
_TOPIC_TOOLS: Final = frozenset({CREATE_TOPIC_TOOL, CREATE_SUBTOPIC_TOOL})

_FILE_WRITE_REASONS: Final = frozenset(GateReason) - {GateReason.DELETE, GateReason.TOPIC_CREATION}

_REASONS_BY_TOOL: Final[Mapping[str, frozenset[GateReason]]] = MappingProxyType(
    {
        WRITE_FILE_TOOL: _FILE_WRITE_REASONS,
        EDIT_FILE_TOOL: _FILE_WRITE_REASONS,
        DELETE_TOOL: frozenset({GateReason.DELETE}),
        CREATE_TOPIC_TOOL: frozenset({GateReason.TOPIC_CREATION}),
        CREATE_SUBTOPIC_TOOL: frozenset({GateReason.TOPIC_CREATION}),
    }
)

GATED_TOOLS: Final[frozenset[str]] = frozenset(_REASONS_BY_TOOL)
"""The tools that can gate. Everything else — reads, ``grep``, ``task`` — never interrupts
(RT-31)."""


def allowed_decisions(tool: str) -> tuple[DecisionType, ...]:
    """The decisions a human may make on ``tool``, derived from :data:`GATE_DECISIONS` (RT-32).

    ``interrupt_on`` carries one ``allowed_decisions`` list per *tool*, while the table is keyed by
    *reason*, so this collapses the reasons a tool can raise into one answer — and raises if they
    ever disagree, because silently picking one would publish a decision set the table does not
    sanction.
    """
    reasons = _REASONS_BY_TOOL.get(tool)
    if reasons is None:
        msg = f"{tool!r} is not a gated tool; allowed_decisions is undefined for it"
        raise KeyError(msg)
    distinct = {GATE_DECISIONS[reason] for reason in reasons}
    if len(distinct) != 1:
        msg = f"gate table disagrees on allowed_decisions for {tool!r}: {sorted(distinct)}"
        raise ValueError(msg)
    return distinct.pop()


# --------------------------------------------------------------------------------------
# The predicate (RT-21)
# --------------------------------------------------------------------------------------


def requires_approval(
    tool: str,
    rel_path: str | None,
    args: Mapping[str, Any],
    snapshot: KbSnapshot,
) -> GateReason | None:
    """The gate table: does this action need a human, and under which rule (RT-21)?

    ``rel_path`` is the KB-relative POSIX path the action targets, already normalised by
    ``pkb.agents.paths.to_kb_relative``, or ``None`` when the action targets no KB path — agent
    scratch on the ``StateBackend`` route, or a tool like ``create_topic`` that takes no path.
    ``snapshot`` supplies the current tree; ``snapshot.root`` is the knowledge-base root.

    Returns the first matching :class:`GateReason` in member-declaration order, or ``None`` when
    the action may proceed unattended. Filing a plain note, writing a **new** reference depth file,
    adding to an existing one, and every read return ``None`` — capture must be frictionless
    (RT-31). What does *not* return ``None``, since the 2026-08-07 amendment, is a write that would
    remove or alter text already in a source file: see :attr:`GateReason.REFERENCE_REWRITE`.

    **Two ways this function used to answer "no gate" without deciding anything**, both of which
    put an unapproved write onto one of the three compact approval surfaces, and both of which are
    closed here rather than in each rule below:

    * The spelling was not the disk's. Every rule under a topic root is an exact-string question —
      ``snapshot.topics[key]``, ``inner == (TOPIC_FILE,)`` — and on a case-insensitive filesystem
      ``Cooking/TOPIC.md`` and ``Cooking/sub-topics/grilling/notes/summary.md`` are the human's
      files under a spelling no dictionary holds, so every one of those questions answered "no".
      :func:`pkb.agents.paths.canonical_kb_path` re-spells first (RT-23 … RT-29).
    * The owning topic root was on disk but not in ``snapshot``. See :func:`_owning_topic`: the
      answer is now derived from disk, not skipped.

    Where the path itself cannot be resolved, the answer is :attr:`GateReason.UNRESOLVED_PATH` and
    not ``None``. A gate is a claim that a human is not needed; it must never be made by default.
    """
    if tool in _TOPIC_TOOLS:
        return GateReason.TOPIC_CREATION
    if tool not in _FILE_TOOLS or rel_path is None:
        return None

    if is_denied_derived(rel_path):
        # I3 refuses these at the tool layer whatever the prompt says (RT-11, RT-14). Gating them
        # too would ask a human to approve a write that is then denied anyway — the same wasted
        # round trip RT-35 exists to prevent, one layer over. The predicate must be the permission
        # rules' own (RT-11/RT-12), which are wider than `pkb.core.is_derived_name` on case and on
        # a per-topic `tags.md`; asking Layer 1 here would gate writes the tool layer denies. It
        # runs *above* the canonicaliser because it is already case-insensitive, so it is correct
        # on the raw spelling and must not depend on the disk resolving one.
        return None

    if tool == DELETE_TOOL:
        # A delete gates whatever it names, so its spelling changes nothing; resolving it is work
        # that could only turn one gate into a different gate.
        return GateReason.DELETE

    canonical = canonical_kb_path(snapshot.root, rel_path)
    if canonical is None:
        return GateReason.UNRESOLVED_PATH
    rel_path = canonical

    target = snapshot.root / rel_path
    proposed = proposed_content(tool, rel_path, args, snapshot.root)
    current = _read(target)
    topic = _owning_topic(snapshot, rel_path)
    inner = _within_topic(rel_path, topic)

    if current is not None and proposed is not None and _clears_conflict(current, proposed):
        return GateReason.CONFLICT_RESOLUTION
    if inner is not None and _is_breadth_file(inner):
        return GateReason.BREADTH_APPROVAL
    if inner == (EXPERT_FILE,):
        return GateReason.EXPERT_OVERLOAD
    if _is_skill_path(rel_path, inner):
        return GateReason.SKILL_OVERLOAD
    if topic is not None and inner is not None and _mints_extension_folder(topic, inner):
        return GateReason.EXTENSION_FOLDER
    if (
        current is not None
        and proposed is not None
        and _is_reference_depth_file(inner)
        and not _extends_body(current, proposed)
    ):
        return GateReason.REFERENCE_REWRITE
    if proposed is not None and new_tags(proposed, snapshot):
        return GateReason.NEW_TAG
    if proposed is not None and _is_curated(inner) and _introduces_approved(current, proposed):
        return GateReason.STATUS_APPROVED
    if (
        current is not None
        and proposed is not None
        and _is_curated(inner)
        and _changes_body(current, proposed)
    ):
        return GateReason.HUMAN_CONTENT_EDIT
    return None


def proposed_content(
    tool: str, rel_path: str, args: Mapping[str, Any], kb_root: Path
) -> str | None:
    """The full text the action would leave on disk, or ``None`` when it cannot be determined.

    ``write_file`` carries its content verbatim. ``edit_file`` carries none — its args are
    ``file_path``/``old_string``/``new_string``/``replace_all`` (D-4) — so the resulting file is
    simulated with :func:`deepagents.backends.utils.perform_string_replacement`, **the exact
    function** ``FilesystemBackend.edit`` calls, so the simulation cannot diverge from what the
    tool would actually write (MW-10). That function returns a ``str`` for its own errors (zero
    occurrences, a non-unique match); that is deepagents' business, so it becomes ``None`` here and
    the content-based gates simply do not fire.

    **The same function is not the same call.** ``FilesystemBackend.edit`` normalizes ``old_string``
    and ``new_string`` before it matches (``backends/filesystem.py:561-562``, added because real
    callers do send CRLF), and the file it matches against was read in text mode, so it is LF-only.
    Passing the raw arguments here made the simulation miss where the write hits: the simulation
    returned ``None``, and ``None`` is read by every consumer as "deepagents will error on this,
    forward it" — which disabled ``validate_content`` (MW-13) and the four content-derived gates
    (RT-24 … RT-27) on one tool call, for nothing more exotic than a model quoting Windows line
    endings. The inference "``None`` means the write will fail" is only sound while the simulation
    cannot diverge, so the normalization is what keeps that inference true.
    """
    if tool == WRITE_FILE_TOOL:
        content = args.get("content")
        return content if isinstance(content, str) else None
    if tool != EDIT_FILE_TOOL:
        return None
    current = _read(kb_root / rel_path)
    if current is None:
        return None
    result = perform_string_replacement(
        current,
        _normalize_newlines(args.get("old_string", "")),
        _normalize_newlines(args.get("new_string", "")),
        bool(args.get("replace_all", False)),
    )
    return None if isinstance(result, str) else result[0]


def _normalize_newlines(value: object) -> str:
    """CRLF/CR → LF, exactly as ``FilesystemBackend.edit`` does before matching (MW-10).

    Kept byte-for-byte identical to ``backends/filesystem.py:561-562``, including the order of the
    two replacements — ``\\r\\n`` first, so a CRLF does not become two newlines.
    """
    return str(value).replace("\r\n", "\n").replace("\r", "\n")


def new_tags(proposed: str, snapshot: KbSnapshot) -> tuple[str, ...]:
    """``topic.*``/``domain.*`` tags in ``proposed`` that no file in the KB already carries (RT-25).

    The oracle is :func:`pkb.core.build_tag_tree`, whose ``tags`` property is the ancestor closure
    of the tags in use — so ``topic.cooking.grilling`` counts as known when only
    ``topic.cooking.grilling.charcoal`` is on disk, exactly as the registry renders it. Layer 2
    keeps no tag list of its own; Layer 1 deliberately keeps no allowlist either (TG-9/VA-40), and
    this gate is what makes the README's absolute rule mechanical rather than prose.

    Only the two open namespaces are considered: ``type.*`` and ``status.*`` are closed vocabularies
    Layer 1 already rejects additions to, so a gate there would fire on a write that is refused
    anyway.
    """
    meta = parse(proposed).meta
    if meta is None:
        return ()
    known = set(build_tag_tree(snapshot).tags)
    return tuple(
        raw
        for raw in meta.tags
        if Tag.parse(raw).namespace in _PROPOSABLE_NAMESPACES and raw not in known
    )


# --------------------------------------------------------------------------------------
# Rule helpers — each one is a single row of the gate table
# --------------------------------------------------------------------------------------


def _read(path: Path) -> str | None:
    """Current bytes of a KB file, ``None`` when there is no file, :data:`UNREADABLE` otherwise.

    Three states, not two. Every rule below reads ``current is None`` as "nothing here to protect",
    so folding "exists but is not valid UTF-8" into ``None`` disarmed the entire table for exactly
    the files most likely to hold something a human wrote by hand: a note saved by an editor whose
    default encoding is not UTF-8 stopped gating its own overwrite. :data:`UNREADABLE` is a
    non-empty string that no proposed content can extend, so every content-diff rule fires and the
    write stops for a human — which is the right answer when we cannot see what we would destroy.
    """
    try:
        return path.read_text(encoding="utf-8")
    except (FileNotFoundError, NotADirectoryError):
        return None
    except (OSError, UnicodeDecodeError):
        return UNREADABLE


@dataclass(frozen=True, slots=True)
class _OwningTopic:
    """The only two facts the gate table needs about the topic root that owns a path.

    Deliberately *not* a :class:`~pkb.core.models.TopicRecord`: the record carries an ``agent_id``,
    a ``tag`` and a parent chain that only a full scan can compute, and the whole point of
    :func:`_owning_topic` is to keep answering when the scan has not caught up. A type that can
    only hold what is knowable cannot be handed on somewhere that needs the rest.
    """

    path: str
    """KB-relative POSIX path of the topic root, as the disk spells it."""

    extension_folders: tuple[str, ...]
    """Directory names directly under the root that are neither structural nor ignored (PA-7)."""


def _owning_topic(snapshot: KbSnapshot, rel_path: str) -> _OwningTopic | None:
    """The topic root that owns ``rel_path``, via Layer 1's PA-15 walk — never a prefix match.

    **Why the snapshot is not consulted for existence (RT-23 … RT-29).** This used to resolve the
    root by walking the *disk* and then demand a matching :class:`TopicRecord` from the *cached*
    snapshot; a root the snapshot had not seen made this return ``None``, ``_within_topic`` return
    ``None``, and six gates — breadth approval, ``expert.md``, ``skills/**``, extension folders,
    ``status.approved`` and human-content edits — evaluate to "no gate" without evaluating
    anything. That window does not need a human or a race to open: an agent writes a valid
    ``sub-topics/Braising/topic.md``, which gates on nothing because it is not one of *Cooking's*
    breadth files, and by its next tool call ``Braising`` is a topic root on disk. The write after
    that landed on ``Braising/notes/summary.md`` carrying ``status.approved``, unapproved and
    unannounced.

    Both facts below are therefore taken from the same place ``owning_topic_root`` already looked —
    the disk — and the snapshot is used only as a cache for the second one. ``extension_folders``
    is read with Layer 1's own :func:`~pkb.core.paths.extension_folders`, the identical call
    ``scan`` makes (``scan.py:359``), so a fresh snapshot and a stale one give the same answer; a
    hardcoded ``()`` would have been safe in the RT-28 direction but would re-gate every later
    write into a folder the human already approved.
    """
    root = owning_topic_root(snapshot.root, snapshot.root / rel_path)
    if root is None:
        return None
    key = rel(snapshot.root, root)
    record = snapshot.topics.get(key)
    if record is not None:
        return _OwningTopic(path=key, extension_folders=record.extension_folders)
    return _OwningTopic(path=key, extension_folders=tuple(extension_folders(root)))


def _within_topic(rel_path: str, topic: _OwningTopic | None) -> tuple[str, ...] | None:
    """``rel_path``'s segments *below* its topic root, or ``None`` when no topic owns it."""
    if topic is None:
        return None
    if rel_path == topic.path:
        return ()
    prefix = f"{topic.path}/"
    if not rel_path.startswith(prefix):
        return None
    return tuple(rel_path[len(prefix) :].split("/"))


def _is_breadth_file(inner: tuple[str, ...]) -> bool:
    """The three compact approval surfaces README §1.3 marks human-approved (RT-23)."""
    return inner in {
        (TOPIC_FILE,),
        (NOTES_DIR, SUMMARY_FILE),
        (REFERENCES_DIR, SUMMARY_FILE),
    }


def _is_skill_path(rel_path: str, inner: tuple[str, ...] | None) -> bool:
    """Anything under a ``skills/`` directory (RT-29).

    Covers both ``<topic>/skills/**`` — the topic overload README §2.4 classifies as human-created —
    and the KB-root ``skills/**`` an ``adopt_skill`` copy lands in. They are the same file class
    (VA-6) with the same owner, and the root copy is owned by no topic, so it needs the second arm.
    """
    if inner is not None:
        return len(inner) > 1 and inner[0] == SKILLS_DIR
    parts = rel_path.split("/")
    return len(parts) > 1 and parts[0] == SKILLS_DIR


def _mints_extension_folder(topic: _OwningTopic, inner: tuple[str, ...]) -> bool:
    """True when this write creates the *first* file of a new extension folder (RT-28).

    An extension folder is any directory directly under a topic root that is not in
    :data:`pkb.core.paths.STRUCTURAL_DIRS` (PA-7). ``_OwningTopic.extension_folders`` is Layer 1's
    list of the ones that already exist, so "not in it" means the agent is minting one — which
    README §1.2 makes a human decision. Writing a second file into an existing folder does not gate.
    """
    return (
        len(inner) > 1
        and inner[0] not in STRUCTURAL_DIRS
        and inner[0] not in topic.extension_folders
    )


def _is_reference_depth_file(inner: tuple[str, ...] | None) -> bool:
    """Is this the one file per source — ``references/<src>/<src>.md`` (README §1.2, RT-31)?

    The folder-hosted form is the shape README's tree diagram shows and the only one the ingestion
    loop writes. The flat ``references/<name>.md`` form is included too, because Layer 1 tolerates it
    (VA-25 is a *warning*: a URL-only reference has nothing to put in a folder), and a rewrite of one
    replaces exactly the same kind of text. ``references/summary.md`` is deliberately excluded — it
    is a breadth file with its own, stronger gate (RT-23), which fires earlier.
    """
    if inner is None or not inner or inner[0] != REFERENCES_DIR:
        return False
    if len(inner) == 2:
        return inner[1] != SUMMARY_FILE
    return len(inner) == 3 and inner[2] == f"{inner[1]}.md"


def _extends_body(current: str, proposed: str) -> bool:
    """Does the proposal keep every existing body line, in order (RT-31 as amended, LS-12)?

    This is the destructiveness test, and it is deliberately a *diff* question rather than a
    "who wrote it" question. Additive writes — a new argument under an existing chapter, a new
    chapter, another pass appended to the reading record — land unattended because nothing is lost;
    a write that removes or edits a line replaces text the human may have read, and arch D6 leaves
    no undo, so it stops for them.

    Frontmatter is excluded on purpose: a tag, a ``review_note`` or an ``updated`` stamp is how the
    agent does its ordinary maintenance job, and the two frontmatter changes that *do* matter
    already have their own gates (RT-25's new tag, RT-26's conflict resolution). This mirrors
    :func:`_changes_body`, which draws the same line for notes.
    """
    before = parse(current).body.splitlines()
    after = parse(proposed).body.splitlines()
    opcodes = difflib.SequenceMatcher(a=before, b=after, autojunk=False).get_opcodes()
    return all(tag in {"equal", "insert"} for tag, *_ in opcodes)


def _is_curated(inner: tuple[str, ...] | None) -> bool:
    """Is this a file class the human curates — notes and extension-folder content (RT-24, RT-27)?

    Reference depth files are deliberately excluded: README §1.3 makes them AI-generated with human
    curation happening one level up, at ``references/summary.md``, which has its own gate (Q4/C6).
    They are not therefore un-gated — a *rewrite* of one is caught by
    :attr:`GateReason.REFERENCE_REWRITE` a few lines earlier, under RT-31 rather than under RT-24,
    which is the split the large-source-ingestion amendment introduced. The three breadth files are
    curated too but are caught earlier by :data:`GateReason.BREADTH_APPROVAL`, so they never reach
    here.
    """
    if not inner or len(inner) < 2:
        return False
    head = inner[0]
    return head == NOTES_DIR or head not in STRUCTURAL_DIRS


def _tags_of(text: str) -> frozenset[str]:
    meta = parse(text).meta
    return frozenset(meta.tags) if meta else frozenset()


def _clears_conflict(current: str, proposed: str) -> bool:
    """Is this the edit that resolves a flagged conflict (RT-26)?

    Fires on the *removal* of ``status.conflict-review`` alone, because the human must see any way
    an agent decides a conflict is over, however the write got there. Adding the flag is the
    deliberate exemption and is handled by simply not matching here.
    """
    return CONFLICT_TAG in _tags_of(current) and CONFLICT_TAG not in _tags_of(proposed)


def _introduces_approved(current: str | None, proposed: str) -> bool:
    """Does this write *introduce* ``status.approved`` (RT-27)?

    "Introduce" is the operative word: re-writing a file that is already approved is not the agent
    approving anything. A brand-new file tagged ``status.approved`` is.
    """
    if APPROVED_TAG not in _tags_of(proposed):
        return False
    return current is None or APPROVED_TAG not in _tags_of(current)


def _changes_body(current: str, proposed: str) -> bool:
    """Does the prose body change, ignoring frontmatter (RT-24)?

    Frontmatter-only edits — a tag, a ``review_note``, an ``updated`` stamp — are how the agent
    does its ordinary maintenance job and must stay unattended. Splitting is Layer 1's
    :func:`pkb.core.frontmatter.parse`, which never raises: an unparseable file yields the whole
    text as its body, so a malformed file is compared conservatively rather than skipped.
    """
    return parse(current).body != parse(proposed).body


# --------------------------------------------------------------------------------------
# The description factory (RT-34, RT-35)
# --------------------------------------------------------------------------------------

_DIFF_CONTEXT = 3


def describe_write(
    reason: GateReason | None,
    tool: str,
    rel_path: str | None,
    args: Mapping[str, Any],
    snapshot: KbSnapshot,
) -> str:
    """Render what the human is actually deciding about (RT-34, RT-35).

    ``ActionRequest`` carries only ``name``/``args``/``description``, and I2 forbids Layer 3 from
    reading the KB through the harness — so the diff has to be rendered *here*, once, for every
    channel. An existing file gets a unified ``---``/``+++`` diff of current versus proposed; a new
    file gets the full proposal, because a diff against nothing is just the content with ``+`` in
    front of every line.

    The proposal is also run through :func:`pkb.core.validate_content` and a failing draft is
    labelled (RT-35). HITL fires in ``after_model``, strictly before ``KbValidationMiddleware``'s
    ``wrap_tool_call`` (D-17), so without this the human approves first and *then* watches the
    validator refuse — spending one of three attempts (MW-14) on content they endorsed. With the
    label they can reject or edit instead.

    The path is canonicalised the same way :func:`requires_approval` canonicalises it, so the human
    reads the file the write will land on rather than the spelling the model happened to emit — and
    so ``validate_content`` is asked about that same file. Naming the target ``grilling`` for a
    write the filesystem sends to the approved ``Grilling/notes/summary.md`` would be a diff of the
    right bytes under the wrong name, which is the one thing an approval must never be.
    """
    slug = reason.value if reason is not None else "review"
    lines = [f"Approval required: {slug}", f"Tool: {tool}"]
    if rel_path is None:
        lines.append("")
        lines.extend(_render_args(args))
        return "\n".join(lines)

    rel_path = canonical_kb_path(snapshot.root, rel_path) or rel_path
    target = snapshot.root / rel_path
    current = _read(target)

    if tool == DELETE_TOOL:
        lines.append(f"Path: {rel_path} (delete — permanent, there is no undo)")
        if current is not None:
            lines += ["", "Current content:", "", current.rstrip("\n")]
        return "\n".join(lines)

    proposed = proposed_content(tool, rel_path, args, snapshot.root)
    if proposed is None:
        lines.append(f"Path: {rel_path}")
        lines.append("")
        lines.extend(_render_args(args))
        return "\n".join(lines)

    if current is None:
        lines.append(f"Path: {rel_path} (new file)")
        lines += ["", "Proposed content:", "", proposed.rstrip("\n")]
    else:
        lines.append(f"Path: {rel_path} (existing file)")
        lines += ["", _unified_diff(rel_path, current, proposed)]

    if reason is GateReason.NEW_TAG:
        introduced = ", ".join(new_tags(proposed, snapshot))
        if introduced:
            lines += ["", f"New tags not yet used anywhere in the knowledge base: {introduced}"]

    warning = _validation_label(snapshot.root, rel_path, proposed)
    if warning:
        lines += ["", warning]
    return "\n".join(lines)


def _render_args(args: Mapping[str, Any]) -> list[str]:
    return [f"{key}: {value}" for key, value in args.items()]


def _unified_diff(rel_path: str, current: str, proposed: str) -> str:
    """A unified diff with the ``---``/``+++`` headers a human reads as a diff (RT-34)."""
    return "".join(
        difflib.unified_diff(
            current.splitlines(keepends=True),
            proposed.splitlines(keepends=True),
            fromfile=f"{rel_path} (current)",
            tofile=f"{rel_path} (proposed)",
            n=_DIFF_CONTEXT,
        )
    ).rstrip("\n")


def _validation_label(kb_root: Path, rel_path: str, proposed: str) -> str:
    """The RT-35 label, or ``""`` when the draft validates clean.

    Only error-severity findings appear: warnings never block a write (MW-12), so surfacing them in
    an approval would teach the human to ignore the label.
    """
    try:
        findings = validate_content(kb_root, rel_path, proposed)
    except ValueError:
        # validate_content raises only for a path outside the KB, which cannot happen for a
        # normalised KB-relative path — but a gate is the wrong place to turn a caller bug into a
        # crashed run.
        return ""
    if not has_errors(findings):
        return ""
    return "This draft currently fails validation:\n" + render_findings(errors_only(findings))


# --------------------------------------------------------------------------------------
# Harness wiring (RT-22, RT-32, RT-33)
# --------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class GateEnv:
    """Everything :func:`build_interrupt_on` needs that the gate table itself must not know.

    ``snapshot`` is a *callable* so the caller decides the caching policy: the predicate runs once
    per gated tool call in ``after_model``, and a fresh :func:`pkb.core.scan.scan` per call would
    walk the tree several times a turn.

    ``to_kb_relative`` defaults to :func:`pkb.agents.paths.to_kb_relative`, which is the only
    function allowed to know the ``/kb/`` mount prefix (RT-8) and the only one that normalizes the
    raw model string before testing it (RT-9/D-3). It is a *field* rather than a hard-wired import
    so the table tests can drive the whole wiring without a mount, and so the gate module itself
    never has to name the prefix.
    """

    snapshot: Callable[[], KbSnapshot]
    to_kb_relative: Callable[[object], str | None] = _to_kb_relative


def build_interrupt_on(env: GateEnv) -> dict[str, Any]:
    """The ``interrupt_on=`` mapping for a KB agent: one entry per gated tool (RT-22, RT-32, RT-33).

    One entry per tool with a ``when`` predicate, never a per-path second permissions list: a
    single ``interrupt_on["write_file"]`` entry cannot distinguish paths on its own, and
    permission-derived (``mode="interrupt"``) rules over-fire on bulk tools because their patterns
    are unanchored (RT-22).

    No value is ``False`` and no ``allowed_decisions`` contains ``respond`` — both by construction
    here, so the audit RT-33 asks for is a property of this function rather than of each call site.
    """
    return {
        tool: {
            "allowed_decisions": list(allowed_decisions(tool)),
            "when": _when_for(env),
            "description": _description_for(env),
        }
        for tool in sorted(GATED_TOOLS)
    }


def _rel_path_of(env: GateEnv, args: Mapping[str, Any]) -> str | None:
    """``args["file_path"]`` as a KB-relative path, or ``None``.

    The raw value goes through unchecked: a model can put ``None``, a number or a nested object
    there, and deciding what is and is not a knowledge-base path is exactly the one job
    ``to_kb_relative`` owns (RT-9/RT-10). A type guard here would be a second, driftable copy of
    that policy.
    """
    return env.to_kb_relative(args.get("file_path"))


def _when_for(env: GateEnv) -> Callable[[ToolCallRequest], bool]:
    def when(request: ToolCallRequest) -> bool:
        call = request.tool_call
        args = call.get("args") or {}
        snapshot = env.snapshot()
        return requires_approval(call["name"], _rel_path_of(env, args), args, snapshot) is not None

    return when


def _description_for(env: GateEnv) -> Callable[[ToolCall, Any, Any], str]:
    def description(tool_call: ToolCall, state: Any, runtime: Any) -> str:
        del state, runtime  # the gate reads the tree, never the conversation
        args = tool_call.get("args") or {}
        snapshot = env.snapshot()
        rel_path = _rel_path_of(env, args)
        reason = requires_approval(tool_call["name"], rel_path, args, snapshot)
        return describe_write(reason, tool_call["name"], rel_path, args, snapshot)

    return description
