"""The approval table (RT-21 … RT-35).

Table-driven and graph-free by design: RT-21 exists precisely so that "what gates" can be answered
without a model, a runtime or a compiled agent. Every test here reads a real knowledge base built by
Layer 1's own scaffolder and asks :func:`pkb.agents.gates.requires_approval` a question.

**One section is not graph-free, deliberately.** "The gate did not fire" has two shapes, and only
one of them is a question about a path. The other is a question about *when* the tree was last
looked at: an agent that writes a valid ``sub-topics/Braising/topic.md`` — an action no rule gates,
because it is not one of *Cooking's* breadth files — has minted a topic root that the cached
snapshot has never heard of, and its next write to ``Braising/notes/summary.md`` used to land
unapproved. A predicate called with one snapshot cannot express that, because the escalation *is*
the two calls and the cache between them. So ``§ escalations`` at the bottom of this module compiles
a real gated agent over a real filesystem backend with a caching snapshot — the production wiring of
:func:`~pkb.agents.gates.build_interrupt_on` — and asserts the interrupt, and the bytes on disk.
Every test there was watched failing, with zero interrupts, against the code that preceded it.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date
from pathlib import Path
from typing import Any

import pytest
from deepagents import create_deep_agent
from deepagents.backends import CompositeBackend, FilesystemBackend, StateBackend
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.prebuilt.tool_node import ToolCallRequest

from pkb.agents.gates import (
    GATE_DECISIONS,
    GATED_TOOLS,
    WRITE_DECISIONS,
    GateEnv,
    GateReason,
    allowed_decisions,
    build_interrupt_on,
    describe_write,
    new_tags,
    proposed_content,
    requires_approval,
)
from pkb.agents.paths import KB_MOUNT, canonical_kb_path
from pkb.agents.permissions import is_denied_derived
from pkb.core import Metadata, errors_only, validate_content
from pkb.core.frontmatter import serialize
from pkb.core.models import KbSnapshot
from pkb.core.scaffold import scaffold_subtopic
from pkb.core.scan import scan
from pkb.core.tags import STATUS_TAGS
from tests.agents.conftest import TODAY, call, calls, says, scripted

BODY = "\n# Steak\n\nSear it hot.\n"
OTHER_BODY = "\n# Steak\n\nSear it hot, then rest it for ten minutes.\n"


def doc(
    *,
    title: str,
    description: str,
    tags: Sequence[str],
    source_type: str,
    body: str = BODY,
    topic: str = "Cooking",
    review_note: str | None = None,
    last_reviewed: date | None = None,
) -> str:
    """A well-formed PKB document, serialized by Layer 1 so the fixture cannot drift from FM-*."""
    meta = Metadata(
        title=title,
        description=description,
        topic=topic,
        tags=tuple(tags),
        created=TODAY,
        updated=TODAY,
        source_type=source_type,
        review_note=review_note,
        last_reviewed=last_reviewed,
    )
    return serialize(meta, body)


def note(
    name: str,
    *,
    status: str = "status.draft",
    tags: Sequence[str] = ("topic.cooking", "type.note"),
    body: str = BODY,
    review_note: str | None = None,
    last_reviewed: date | None = None,
) -> str:
    return doc(
        title=name,
        description=f"A note about {name} and how it behaves in practice.",
        tags=[*tags, status],
        source_type="note",
        body=body,
        review_note=review_note,
        last_reviewed=last_reviewed,
    )


def write(kb: Path, rel_path: str, text: str) -> None:
    target = kb / rel_path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")


@pytest.fixture
def gated_kb(kb: Path) -> Path:
    """The two-topic fixture plus the files the gate rows need to already exist."""
    write(kb, "Cooking/notes/steak.md", note("steak"))
    write(
        kb,
        "Cooking/notes/flagged.md",
        note(
            "flagged",
            status="status.conflict-review",
            review_note="Contradicts the Kenji reference on resting time.",
        ),
    )
    write(
        kb,
        "Cooking/references/kenji/kenji.md",
        doc(
            title="Kenji on searing",
            description="Reference notes drawn from Kenji's writing on searing steak.",
            tags=["topic.cooking", "type.reference", "status.approved"],
            source_type="reference",
        ),
    )
    # An extension folder that already exists, so RT-28 can tell minting from writing into one.
    write(kb, "Cooking/experiments/one.md", note("one"))
    return kb


@pytest.fixture
def snapshot(gated_kb: Path) -> KbSnapshot:
    return scan(gated_kb)


def gate(
    snapshot: KbSnapshot,
    tool: str,
    rel_path: str | None,
    **args: Any,
) -> GateReason | None:
    return requires_approval(tool, rel_path, args, snapshot)


# --------------------------------------------------------------------------------------
# RT-21 — the table
# --------------------------------------------------------------------------------------


def test_gate_table_rt21(snapshot: KbSnapshot) -> None:
    """~20 (tool, path, args) rows → expected `GateReason`. No graph, no model (RT-21)."""
    new_note = note("grill")
    approved_note = note("approved", status="status.approved")
    unseen_tag_note = note(
        "sous-vide", tags=("topic.cooking", "topic.cooking.sous-vide", "type.note")
    )
    known_subtopic_note = note("charcoal", tags=("topic.cooking.grilling", "type.note"))
    reference = doc(
        title="Serious Eats on grilling",
        description="Reference notes drawn from Serious Eats on charcoal grilling technique.",
        tags=["topic.cooking", "type.reference", "status.approved"],
        source_type="reference",
    )
    resolved = note(
        "flagged",
        status="status.approved",
        last_reviewed=TODAY,
    )
    reflagged = note(
        "steak",
        status="status.conflict-review",
        review_note="Contradicts the Kenji reference on resting time.",
    )

    rows: list[tuple[str, str, str | None, dict[str, Any], GateReason | None]] = [
        # --- no gate: capture must be frictionless (RT-31) -------------------------------
        ("plain note", "write_file", "Cooking/notes/grill.md", {"content": new_note}, None),
        (
            "reference depth file",
            "write_file",
            "Cooking/references/serious-eats/serious-eats.md",
            {"content": reference},
            None,
        ),
        ("read_file is never gated", "read_file", "Cooking/notes/steak.md", {}, None),
        ("agent scratch is not KB", "write_file", None, {"content": new_note}, None),
        (
            "second file in an existing extension folder",
            "write_file",
            "Cooking/experiments/two.md",
            {"content": new_note},
            None,
        ),
        (
            "re-used sub-topic tag",
            "write_file",
            "Cooking/notes/charcoal.md",
            {"content": known_subtopic_note},
            None,
        ),
        (
            "adding the conflict flag (RT-26 exemption)",
            "write_file",
            "Cooking/notes/steak.md",
            {"content": reflagged},
            None,
        ),
        (
            "derived file: I3 refuses it, no gate",
            "write_file",
            "Cooking/index.md",
            {"content": "x"},
            None,
        ),
        ("root index is derived too", "write_file", "index.md", {"content": "x"}, None),
        # --- gated ------------------------------------------------------------------------
        (
            "topic.md",
            "write_file",
            "Cooking/topic.md",
            {
                "content": doc(
                    title="Cooking",
                    description="Home cooking: technique, equipment, and recipes.",
                    tags=["topic.cooking", "type.summary", "status.draft"],
                    source_type="summary",
                )
            },
            GateReason.BREADTH_APPROVAL,
        ),
        (
            "notes/summary.md",
            "write_file",
            "Cooking/notes/summary.md",
            {"content": new_note},
            GateReason.BREADTH_APPROVAL,
        ),
        (
            "references/summary.md",
            "write_file",
            "Cooking/references/summary.md",
            {"content": new_note},
            GateReason.BREADTH_APPROVAL,
        ),
        (
            "sub-topic breadth file",
            "write_file",
            "Cooking/sub-topics/Grilling/notes/summary.md",
            {"content": new_note},
            GateReason.BREADTH_APPROVAL,
        ),
        (
            "expert.md",
            "write_file",
            "Cooking/expert.md",
            {"content": "You are the Cooking expert."},
            GateReason.EXPERT_OVERLOAD,
        ),
        (
            "topic skill overload",
            "write_file",
            "Cooking/skills/voice/SKILL.md",
            {"content": "---\nname: voice\ndescription: d\n---\n"},
            GateReason.SKILL_OVERLOAD,
        ),
        (
            "adopted KB-root skill",
            "write_file",
            "skills/voice/SKILL.md",
            {"content": "---\nname: voice\ndescription: d\n---\n"},
            GateReason.SKILL_OVERLOAD,
        ),
        (
            "minting an extension folder",
            "write_file",
            "Cooking/recipes/pasta.md",
            {"content": new_note},
            GateReason.EXTENSION_FOLDER,
        ),
        (
            "an unseen topic.* tag",
            "write_file",
            "Cooking/notes/sous-vide.md",
            {"content": unseen_tag_note},
            GateReason.NEW_TAG,
        ),
        (
            "a new note claiming status.approved",
            "write_file",
            "Cooking/notes/approved.md",
            {"content": approved_note},
            GateReason.STATUS_APPROVED,
        ),
        (
            "clearing a conflict",
            "write_file",
            "Cooking/notes/flagged.md",
            {"content": resolved},
            GateReason.CONFLICT_RESOLUTION,
        ),
        (
            "rewriting a note body",
            "write_file",
            "Cooking/notes/steak.md",
            {"content": note("steak", body=OTHER_BODY)},
            GateReason.HUMAN_CONTENT_EDIT,
        ),
        (
            "deleting a note",
            "delete",
            "Cooking/notes/steak.md",
            {},
            GateReason.DELETE,
        ),
        ("create_topic", "create_topic", None, {"name": "Physics"}, GateReason.TOPIC_CREATION),
        (
            "create_subtopic",
            "create_subtopic",
            None,
            {"parent": "Cooking", "name": "Baking"},
            GateReason.TOPIC_CREATION,
        ),
    ]

    actual = {label: gate(snapshot, tool, path, **args) for label, tool, path, args, _ in rows}
    expected = {label: want for label, _, _, _, want in rows}
    assert actual == expected


# --------------------------------------------------------------------------------------
# One test per gate rule
# --------------------------------------------------------------------------------------


def test_breadth_files_gate_and_a_plain_note_does_not_rt23(snapshot: KbSnapshot) -> None:
    """The three compact approval surfaces gate; `notes/grill.md` does not (RT-23)."""
    for rel_path in (
        "Cooking/topic.md",
        "Cooking/notes/summary.md",
        "Cooking/references/summary.md",
    ):
        assert (
            gate(snapshot, "write_file", rel_path, content=note("x")) is GateReason.BREADTH_APPROVAL
        ), rel_path
    assert gate(snapshot, "write_file", "Cooking/notes/grill.md", content=note("grill")) is None


def test_body_edit_gates_and_frontmatter_edit_does_not_rt24(snapshot: KbSnapshot) -> None:
    """A body change to an existing note needs a human; a frontmatter-only change does not (RT-24)."""
    body_edit = gate(
        snapshot,
        "edit_file",
        "Cooking/notes/steak.md",
        old_string="Sear it hot.",
        new_string="Sear it hot, then rest it.",
    )
    assert body_edit is GateReason.HUMAN_CONTENT_EDIT

    frontmatter_edit = gate(
        snapshot,
        "edit_file",
        "Cooking/notes/steak.md",
        old_string="updated: 2026-08-06",
        new_string="updated: 2026-08-07",
    )
    assert frontmatter_edit is None


def test_reference_depth_file_is_exempt_from_body_gate_rt24(gated_kb: Path) -> None:
    """RT-24 covers `notes/` and extension folders only — references are AI-owned (Q4/C6)."""
    snap = scan(gated_kb)
    assert (
        gate(
            snap,
            "edit_file",
            "Cooking/references/kenji/kenji.md",
            old_string="Sear it hot.",
            new_string="Sear it very hot.",
        )
        is None
    )


def test_unseen_topic_tag_gates_once_and_names_it_rt25(snapshot: KbSnapshot) -> None:
    """Re-using a tag does not gate; introducing one does, and the human is told which (RT-25)."""
    reused = note("known", tags=("topic.cooking", "type.note"))
    assert gate(snapshot, "write_file", "Cooking/notes/known.md", content=reused) is None

    fresh = note("sous-vide", tags=("topic.cooking.sous-vide", "type.note"))
    assert gate(snapshot, "write_file", "Cooking/notes/sv.md", content=fresh) is GateReason.NEW_TAG
    assert new_tags(fresh, snapshot) == ("topic.cooking.sous-vide",)

    description = describe_write(
        GateReason.NEW_TAG,
        "write_file",
        "Cooking/notes/sv.md",
        {"content": fresh},
        snapshot,
    )
    assert "topic.cooking.sous-vide" in description


def test_closed_namespaces_never_count_as_new_tags_rt25(snapshot: KbSnapshot) -> None:
    """Only `topic.*`/`domain.*` are proposable; `type.*`/`status.*` are closed (RT-25)."""
    assert new_tags(note("x", status="status.conflict-review"), snapshot) == ()


def test_conflict_flag_is_ungated_but_resolution_gates_rt26(snapshot: KbSnapshot) -> None:
    """Tagging a conflict runs unattended; clearing one does not (RT-26)."""
    flagging = note(
        "steak",
        status="status.conflict-review",
        review_note="Contradicts the Kenji reference.",
    )
    assert gate(snapshot, "write_file", "Cooking/notes/steak.md", content=flagging) is None

    resolving = note("flagged", status="status.approved", last_reviewed=TODAY)
    assert (
        gate(snapshot, "write_file", "Cooking/notes/flagged.md", content=resolving)
        is GateReason.CONFLICT_RESOLUTION
    )


def test_status_approved_gates_on_curated_classes_only_rt27(snapshot: KbSnapshot) -> None:
    """A note claiming `status.approved` gates; the same note as a draft lands; references are
    exempt (RT-27, Q4)."""
    approved = note("claim", status="status.approved")
    assert (
        gate(snapshot, "write_file", "Cooking/notes/claim.md", content=approved)
        is GateReason.STATUS_APPROVED
    )
    assert gate(snapshot, "write_file", "Cooking/notes/claim.md", content=note("claim")) is None

    reference = doc(
        title="Serious Eats",
        description="Reference notes drawn from Serious Eats on grilling technique.",
        tags=["topic.cooking", "type.reference", "status.approved"],
        source_type="reference",
    )
    assert (
        gate(
            snapshot,
            "write_file",
            "Cooking/references/serious-eats/serious-eats.md",
            content=reference,
        )
        is None
    )


def test_first_file_in_a_new_extension_folder_gates_rt28(snapshot: KbSnapshot) -> None:
    """Minting `recipes/` gates; the second file in an existing folder does not (RT-28)."""
    assert (
        gate(snapshot, "write_file", "Cooking/recipes/pasta.md", content=note("pasta"))
        is GateReason.EXTENSION_FOLDER
    )
    assert gate(snapshot, "write_file", "Cooking/experiments/two.md", content=note("two")) is None


def test_expert_and_skill_overloads_gate_rt29(snapshot: KbSnapshot) -> None:
    """`expert.md` and `skills/**` are human-created, AI-assisted (RT-29)."""
    assert (
        gate(snapshot, "write_file", "Cooking/expert.md", content="You are a cook.")
        is GateReason.EXPERT_OVERLOAD
    )
    assert (
        gate(
            snapshot,
            "write_file",
            "Cooking/skills/voice/SKILL.md",
            content="---\nname: voice\n---\n",
        )
        is GateReason.SKILL_OVERLOAD
    )


def test_every_kb_delete_gates_with_approve_reject_rt30(snapshot: KbSnapshot) -> None:
    """There is no version control and no undo, so every delete stops for a human (RT-30, D6).

    This is one of RT-30's three clauses. The other two — "approving removes it, rejecting leaves
    it" — are effects on the tree, which this module cannot assert: it is table-driven and graph-free
    by design (see the module docstring), and a predicate that answers "does this gate?" says nothing
    about what the human's answer then does. They live in
    ``test_approval.py::test_a_kb_delete_gates_and_the_decision_is_final_rt30``, which drives a real
    ``build_expert`` graph so the flush and ``describe_write``'s delete branch are the real ones.
    """
    assert gate(snapshot, "delete", "Cooking/notes/steak.md") is GateReason.DELETE
    assert allowed_decisions("delete") == ("approve", "reject")
    assert GATE_DECISIONS[GateReason.DELETE] == ("approve", "reject")


def test_ingestion_and_reads_never_gate_rt31(snapshot: KbSnapshot) -> None:
    """Filing a note, filing a reference and every read complete with zero interrupts (RT-31)."""
    assert gate(snapshot, "write_file", "Cooking/notes/plain.md", content=note("plain")) is None
    reference = doc(
        title="Serious Eats",
        description="Reference notes drawn from Serious Eats on charcoal grilling technique.",
        tags=["topic.cooking", "type.reference", "status.approved"],
        source_type="reference",
    )
    assert (
        gate(
            snapshot,
            "write_file",
            "Cooking/references/serious-eats/serious-eats.md",
            content=reference,
        )
        is None
    )
    for read_tool in ("read_file", "ls", "grep", "glob"):
        assert gate(snapshot, read_tool, "Cooking/notes/steak.md") is None, read_tool


def test_respond_is_never_allowed_on_a_write_gate_rt32(snapshot: KbSnapshot) -> None:
    """`respond` reports success with the tool skipped — the model would believe a phantom write."""
    for reason, decisions in GATE_DECISIONS.items():
        assert "respond" not in decisions, reason

    env = GateEnv(snapshot=lambda: snapshot)
    for tool, config in build_interrupt_on(env).items():
        assert "respond" not in config["allowed_decisions"], tool

    assert WRITE_DECISIONS == ("approve", "edit", "reject")
    for tool in ("write_file", "edit_file", "create_topic", "create_subtopic"):
        assert allowed_decisions(tool) == WRITE_DECISIONS, tool


def test_no_interrupt_on_entry_is_false_rt33(snapshot: KbSnapshot) -> None:
    """`False` means auto-approve. The AI never resolves its own gate (RT-33)."""
    env = GateEnv(snapshot=lambda: snapshot)
    config = build_interrupt_on(env)
    assert set(config) == GATED_TOOLS
    for tool, entry in config.items():
        assert entry is not False, tool
        assert isinstance(entry, dict), tool
        assert entry["allowed_decisions"], tool
        assert callable(entry["when"]), tool
        assert callable(entry["description"]), tool


def test_description_diffs_an_existing_file_and_shows_a_new_one_whole_rt34(
    snapshot: KbSnapshot,
) -> None:
    """Layer 2 renders the diff text; Layer 3 renders the modal (RT-34)."""
    revised = note("summary", body="\n# Notes summary\n\nRest steak for ten minutes.\n")
    existing = describe_write(
        GateReason.BREADTH_APPROVAL,
        "write_file",
        "Cooking/notes/summary.md",
        {"content": revised},
        snapshot,
    )
    assert "--- Cooking/notes/summary.md (current)" in existing
    assert "+++ Cooking/notes/summary.md (proposed)" in existing
    assert "@@" in existing
    assert "(existing file)" in existing

    fresh = note("brand-new")
    created = describe_write(
        GateReason.STATUS_APPROVED,
        "write_file",
        "Cooking/notes/brand-new.md",
        {"content": fresh},
        snapshot,
    )
    assert "(new file)" in created
    assert fresh.rstrip("\n") in created
    assert "@@" not in created


def test_description_labels_a_draft_that_fails_validation_rt35(snapshot: KbSnapshot) -> None:
    """HITL fires before `wrap_tool_call` (D-17), so the human must be told the draft is invalid."""
    invalid = "\n# Notes summary\n\nNo frontmatter at all.\n"
    findings = errors_only(validate_content(snapshot.root, "Cooking/notes/summary.md", invalid))
    assert findings, "fixture must actually be invalid for this test to mean anything"

    description = describe_write(
        GateReason.BREADTH_APPROVAL,
        "write_file",
        "Cooking/notes/summary.md",
        {"content": invalid},
        snapshot,
    )
    assert "fails validation" in description
    for finding in findings:
        assert finding.code in description, finding.code
        assert finding.rule_id in description, finding.rule_id


def test_a_valid_draft_carries_no_validation_label_rt35(snapshot: KbSnapshot) -> None:
    """A clean proposal must not be labelled — a label the human learns to ignore is worthless."""
    clean = doc(
        title="Notes summary",
        description="Distilled rules and notable solutions from the Cooking notes.",
        tags=["topic.cooking", "type.summary", "status.draft"],
        source_type="summary",
        body="\n# Notes summary\n\nRest steak for ten minutes.\n",
    )
    description = describe_write(
        GateReason.BREADTH_APPROVAL,
        "write_file",
        "Cooking/notes/summary.md",
        {"content": clean},
        snapshot,
    )
    assert "fails validation" not in description


# --------------------------------------------------------------------------------------
# Harness wiring
# --------------------------------------------------------------------------------------


def _request(tool: str, **args: Any) -> ToolCallRequest:
    return ToolCallRequest(
        tool_call={"name": tool, "args": args, "id": "tc-1", "type": "tool_call"},
        tool=None,
        state={},
        runtime=None,  # type: ignore[arg-type]
    )


def test_one_entry_per_tool_decides_per_path_rt22(snapshot: KbSnapshot) -> None:
    """A `when` predicate on one entry per tool, not a second permissions list (RT-22)."""
    env = GateEnv(snapshot=lambda: snapshot)
    config = build_interrupt_on(env)
    assert sorted(config) == sorted(GATED_TOOLS)

    when = config["write_file"]["when"]
    plain = f"{KB_MOUNT}Cooking/notes/x.md"
    breadth = f"{KB_MOUNT}Cooking/notes/summary.md"
    assert when(_request("write_file", file_path=plain, content=note("x"))) is False
    assert when(_request("write_file", file_path=breadth, content=note("s"))) is True

    # RT-9/D-3: the un-normalised form a model actually emits must reach the same verdict.
    unslashed = breadth.removeprefix("/")
    assert when(_request("write_file", file_path=unslashed, content=note("s"))) is True
    # Not a knowledge-base path at all, and not a string — neither may crash the predicate.
    assert when(_request("write_file", file_path="/scratch/x.md", content="x")) is False
    assert when(_request("write_file", file_path=None, content="x")) is False


def test_description_factory_renders_the_gate_it_fired_for_rt34(snapshot: KbSnapshot) -> None:
    """The wired factory produces the same text `describe_write` does (RT-34)."""
    env = GateEnv(snapshot=lambda: snapshot)
    factory = build_interrupt_on(env)["write_file"]["description"]
    tool_call: Mapping[str, Any] = {
        "name": "write_file",
        "args": {
            "file_path": f"{KB_MOUNT}Cooking/notes/summary.md",
            "content": note("summary"),
        },
        "id": "tc-2",
        "type": "tool_call",
    }
    text = factory(tool_call, {}, None)
    assert text.startswith("Approval required: breadth-approval")
    assert "Cooking/notes/summary.md" in text


def test_edit_file_result_is_simulated_with_the_backends_own_function_mw10(
    snapshot: KbSnapshot,
) -> None:
    """The gate judges the resulting file, not the fragment — via the function `edit` itself uses."""
    current = (snapshot.root / "Cooking/notes/steak.md").read_text(encoding="utf-8")
    result = proposed_content(
        "edit_file",
        "Cooking/notes/steak.md",
        {"old_string": "Sear it hot.", "new_string": "Sear it hot, then rest."},
        snapshot.root,
    )
    assert result == current.replace("Sear it hot.", "Sear it hot, then rest.")

    absent = proposed_content(
        "edit_file",
        "Cooking/notes/steak.md",
        {"old_string": "nowhere in the file", "new_string": "x"},
        snapshot.root,
    )
    assert absent is None


def test_status_tag_constants_come_from_layer_one_vocabulary_rt26(snapshot: KbSnapshot) -> None:
    """The two status literals this module names must stay members of Layer 1's closed set."""
    from pkb.agents.gates import APPROVED_TAG, CONFLICT_TAG

    assert {APPROVED_TAG, CONFLICT_TAG} <= STATUS_TAGS
    assert snapshot.root.exists()


def test_allowed_decisions_refuses_an_ungated_tool_rt32() -> None:
    """Asking for the decisions of a tool that never gates is a bug, not an empty tuple."""
    with pytest.raises(KeyError):
        allowed_decisions("read_file")


# --------------------------------------------------------------------------------------
# A topic root the snapshot has not seen yet (RT-23, RT-24, RT-27, RT-28, RT-29)
# --------------------------------------------------------------------------------------

BRAISING = "Cooking/sub-topics/Braising"


@pytest.fixture
def stale(gated_kb: Path) -> KbSnapshot:
    """A snapshot taken *before* `Cooking/sub-topics/Braising` appeared on disk.

    This is `PkbRuntime.snapshot()`'s cache, which holds for the rest of a run — and the window an
    agent opens for itself, with no human and no concurrency, by writing a `sub-topics/<X>/topic.md`
    that no rule gates. Everything below asks the gate table a question about a topic root that is
    real on disk and absent from the dictionary.
    """
    before = scan(gated_kb)
    scaffold_subtopic(
        gated_kb,
        "Cooking",
        "Braising",
        title="Braising",
        description="Low, slow and in liquid: pot roast, short ribs, and stew",
        today=TODAY,
    )
    write(gated_kb, f"{BRAISING}/notes/pot-roast.md", note("pot roast"))
    write(gated_kb, f"{BRAISING}/experiments/one.md", note("one"))
    return before


def test_a_topic_root_missing_from_the_snapshot_still_gates_rt23(
    stale: KbSnapshot, gated_kb: Path
) -> None:
    """Every gate that needs the owning topic keeps working when the scan has not caught up.

    Before the fix all six of these answered `None`: `_owning_topic` resolved the root from disk and
    then required a `TopicRecord` the cached snapshot did not hold, so `inner` was `None` and each
    rule below it was skipped rather than evaluated. The escalation that reaches this state without
    a human is asserted end to end in `§ escalations` (RT-23, RT-24, RT-27, RT-28, RT-29).
    """
    assert BRAISING not in stale.topics, "the fixture must actually be stale"
    assert (gated_kb / BRAISING / "topic.md").exists(), "…and the root must actually be on disk"

    approved_summary = note("summary", status="status.approved")
    rows: list[tuple[str, str, dict[str, Any], GateReason]] = [
        ("topic.md", f"{BRAISING}/topic.md", {"content": note("x")}, GateReason.BREADTH_APPROVAL),
        (
            "notes/summary.md carrying status.approved",
            f"{BRAISING}/notes/summary.md",
            {"content": approved_summary},
            GateReason.BREADTH_APPROVAL,
        ),
        (
            "references/summary.md",
            f"{BRAISING}/references/summary.md",
            {"content": note("x")},
            GateReason.BREADTH_APPROVAL,
        ),
        (
            "expert.md",
            f"{BRAISING}/expert.md",
            {"content": "You are the Braising expert."},
            GateReason.EXPERT_OVERLOAD,
        ),
        (
            "skills/**",
            f"{BRAISING}/skills/voice/SKILL.md",
            {"content": "---\nname: voice\ndescription: d\n---\n"},
            GateReason.SKILL_OVERLOAD,
        ),
        (
            "minting an extension folder",
            f"{BRAISING}/recipes/pot-au-feu.md",
            {"content": note("pot-au-feu")},
            GateReason.EXTENSION_FOLDER,
        ),
        (
            "a new note claiming status.approved",
            f"{BRAISING}/notes/claim.md",
            {"content": note("claim", status="status.approved")},
            GateReason.STATUS_APPROVED,
        ),
        (
            "rewriting a note body",
            f"{BRAISING}/notes/pot-roast.md",
            {"content": note("pot roast", body=OTHER_BODY)},
            GateReason.HUMAN_CONTENT_EDIT,
        ),
    ]
    actual = {label: gate(stale, "write_file", path, **args) for label, path, args, _ in rows}
    assert actual == {label: want for label, _, _, want in rows}


def test_an_unseen_topic_roots_existing_extension_folder_does_not_regate_rt28(
    stale: KbSnapshot,
) -> None:
    """RT-28's second clause survives the degraded path: the folder list comes from disk.

    Answering "no extension folders" would gate safely but wrongly — every later write into a folder
    the human already approved would ask again, which is how RT-28's "writing into an existing
    extension folder does not [gate]" turns into interrupt fatigue. `_owning_topic` reads the list
    with the same Layer 1 call `scan` makes, so a stale snapshot and a fresh one agree.
    """
    assert gate(stale, "write_file", f"{BRAISING}/experiments/two.md", content=note("two")) is None


@pytest.mark.parametrize(
    "spelling",
    ["index.md", "INDEX.md", "Cooking/index.md", "Cooking/INDEX.md", "Cooking/tags.md", "TAGS.md"],
)
def test_nothing_the_tool_layer_denies_is_ever_gated_rt35(
    snapshot: KbSnapshot, spelling: str
) -> None:
    """The early return must ask the permission rules, not Layer 1 (RT-11, RT-12, RT-35).

    Gating a write the tool body denies a moment later is the wasted round trip RT-35 exists to
    prevent, one layer over — so the predicate here has to be the same one `_check_fs_permission`
    will apply, and `DERIVED_DENY_GLOBS` is wider than `pkb.core.is_derived_name` on two axes: case,
    and a per-topic `tags.md` (which Layer 1 excludes from the derived set but rejects after the
    fact). `delete` is the spelling that shows it: it gates unconditionally the moment the early
    return declines, so asking Layer 1 puts a human in front of a `delete("/kb/Cooking/tags.md")`
    that RT-14 refuses anyway.

    Widening `pkb.core.is_derived_name` to compensate is not the fix (PA-17) — it answers a
    different question, about which files a generator owns.
    """
    assert is_denied_derived(spelling), "fixture must name something the deny globs cover"
    assert gate(snapshot, "write_file", spelling, content=note("x")) is None
    assert gate(snapshot, "delete", spelling) is None


# --------------------------------------------------------------------------------------
# Path spelling (RT-23, RT-24, RT-29) — the disk decides which file this is
# --------------------------------------------------------------------------------------


@pytest.fixture
def case_insensitive_fs(gated_kb: Path) -> bool:
    """True when this filesystem resolves two spellings of one name to one file.

    APFS, NTFS and exFAT do; ext4 does not. On a case-**sensitive** host a folded spelling names a
    genuinely different file, so there is nothing to canonicalise and nothing to protect — the tests
    guarded on this are a no-op there rather than a false green.
    """
    return (gated_kb / "COOKING").exists()


@pytest.mark.parametrize(
    ("spelling", "canonical", "expected"),
    [
        ("Cooking/TOPIC.md", "Cooking/topic.md", GateReason.BREADTH_APPROVAL),
        ("COOKING/topic.md", "Cooking/topic.md", GateReason.BREADTH_APPROVAL),
        ("Cooking/notes/SUMMARY.md", "Cooking/notes/summary.md", GateReason.BREADTH_APPROVAL),
        (
            "Cooking/sub-topics/grilling/notes/summary.md",
            "Cooking/sub-topics/Grilling/notes/summary.md",
            GateReason.BREADTH_APPROVAL,
        ),
        (
            "Cooking/SUB-TOPICS/Grilling/topic.md",
            "Cooking/sub-topics/Grilling/topic.md",
            GateReason.BREADTH_APPROVAL,
        ),
    ],
)
def test_every_spelling_of_a_protected_path_reaches_the_same_gate_rt23(
    snapshot: KbSnapshot,
    case_insensitive_fs: bool,
    spelling: str,
    canonical: str,
    expected: GateReason,
) -> None:
    """A write the filesystem sends to `notes/summary.md` gates as `notes/summary.md`.

    Every rule under a topic root is an exact-string question — `snapshot.topics[key]`,
    `inner == (TOPIC_FILE,)` — and on this filesystem the model's spelling and the human's are one
    inode. Before the canonicaliser each of these returned `None`, and the write landed on the
    human's approved bytes with no interrupt. `Cooking/TOPIC.md` is in the list on purpose: it needs
    no ancestor walk at all, so it proves the fix is not confined to `_owning_topic` (RT-23).
    """
    if not case_insensitive_fs:
        pytest.skip("case-sensitive filesystem: the folded spelling is a different file")
    assert canonical_kb_path(snapshot.root, spelling) == canonical
    assert gate(snapshot, "write_file", spelling, content=note("x")) is expected
    assert gate(snapshot, "write_file", canonical, content=note("x")) is expected


def test_a_folded_note_body_edit_still_gates_rt24(
    snapshot: KbSnapshot, case_insensitive_fs: bool
) -> None:
    """The body gate keys off `_is_curated(inner)`, which keys off the spelling too (RT-24)."""
    if not case_insensitive_fs:
        pytest.skip("case-sensitive filesystem: the folded spelling is a different file")
    assert (
        gate(
            snapshot,
            "edit_file",
            "Cooking/NOTES/steak.md",
            old_string="Sear it hot.",
            new_string="Sear it hot, then rest it.",
        )
        is GateReason.HUMAN_CONTENT_EDIT
    )


def test_a_new_file_keeps_the_spelling_its_author_chose_rt23(snapshot: KbSnapshot) -> None:
    """Canonicalisation re-spells what is on disk and invents nothing.

    A segment with nothing behind it passes through verbatim, or filing `Cooking/notes/Steak-2.md`
    into a knowledge base that happens to hold `steak-2.md`'s lowercase cousin would silently become
    a write to a different file. The walk stops at the first absent segment (RT-31).
    """
    assert (
        canonical_kb_path(snapshot.root, "Cooking/notes/Brand-New.md")
        == "Cooking/notes/Brand-New.md"
    )
    assert (
        canonical_kb_path(snapshot.root, "Cooking/Recipes/Pasta/Carbonara.md")
        == "Cooking/Recipes/Pasta/Carbonara.md"
    )
    assert gate(snapshot, "write_file", "Cooking/notes/Brand-New.md", content=note("x")) is None


def test_a_path_that_cannot_be_resolved_gates_rather_than_skipping_rt23(
    snapshot: KbSnapshot, gated_kb: Path
) -> None:
    """A check that cannot be evaluated is not a check that passed.

    When a directory can be traversed but not listed, the target exists and no entry can be shown to
    be it. Every rule below that point is a question about a specific file, so none of them can be
    answered — and answering `None` would be the same silent skip this whole section exists to
    close. The human decides instead (RT-23 … RT-29).
    """
    blocked = gated_kb / "Cooking" / "notes"
    original = blocked.stat().st_mode
    blocked.chmod(0o111)  # traverse, but do not list
    try:
        if canonical_kb_path(gated_kb, "Cooking/notes/steak.md") is not None:
            pytest.skip("this user can list an unreadable directory (running as root?)")
        assert (
            gate(snapshot, "write_file", "Cooking/notes/steak.md", content=note("steak"))
            is GateReason.UNRESOLVED_PATH
        )
        assert "respond" not in allowed_decisions("write_file")
    finally:
        blocked.chmod(original)


def test_the_description_names_the_file_the_write_will_land_on_rt34(
    snapshot: KbSnapshot, case_insensitive_fs: bool
) -> None:
    """A diff of the right bytes under the wrong name is the one thing an approval must not be."""
    if not case_insensitive_fs:
        pytest.skip("case-sensitive filesystem: the folded spelling is a different file")
    description = describe_write(
        GateReason.BREADTH_APPROVAL,
        "write_file",
        "Cooking/sub-topics/grilling/notes/summary.md",
        {"content": note("summary")},
        snapshot,
    )
    path_lines = [
        line
        for line in description.splitlines()
        if line.startswith(("Path:", "--- ", "+++ ")) and "summary.md" in line
    ]
    assert path_lines
    for line in path_lines:
        assert "Cooking/sub-topics/Grilling/notes/summary.md" in line, line


# --------------------------------------------------------------------------------------
# MW-10 — the edit simulation is the write, byte for byte
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize("newline", ["\n", "\r\n", "\r"], ids=["lf", "crlf", "cr"])
def test_an_edit_reaches_the_same_gate_in_every_newline_spelling_mw10(
    snapshot: KbSnapshot, newline: str
) -> None:
    """`FilesystemBackend.edit` normalises its arguments before matching, so the simulation must.

    Calling the same function is not making the same call. The backend maps CRLF and bare CR to LF
    (`backends/filesystem.py:561-562`) because real callers send them; passing the raw strings here
    made `perform_string_replacement` find nothing, return its error `str`, and `proposed_content`
    answer `None` — which every consumer reads as "deepagents will error on this, forward it". One
    wrong `None` disabled `validate_content` (MW-13) and all four content-derived gates at once, so
    a body rewrite of an approved human note landed from a single tool call with no interrupt
    (RT-24) and an unknown five-segment tag landed unvalidated (RT-25).
    """
    body_edit = gate(
        snapshot,
        "edit_file",
        "Cooking/notes/steak.md",
        old_string=f"Sear it hot.{newline}",
        new_string=f"Sear it hot, then rest it for ten minutes.{newline}",
    )
    assert body_edit is GateReason.HUMAN_CONTENT_EDIT

    tag_edit = gate(
        snapshot,
        "edit_file",
        "Cooking/notes/steak.md",
        old_string=f"  - topic.cooking{newline}",
        new_string=f"  - topic.cooking.sous-vide{newline}",
    )
    assert tag_edit is GateReason.NEW_TAG


def test_the_simulated_edit_is_byte_identical_across_newline_spellings_mw10(
    snapshot: KbSnapshot,
) -> None:
    """The three spellings are one call to the backend, so they must be one proposal here."""
    proposals = {
        newline: proposed_content(
            "edit_file",
            "Cooking/notes/steak.md",
            {
                "old_string": f"Sear it hot.{newline}",
                "new_string": f"Rest it.{newline}",
            },
            snapshot.root,
        )
        for newline in ("\n", "\r\n", "\r")
    }
    assert proposals["\n"] is not None
    assert len(set(proposals.values())) == 1
    assert "Rest it." in str(proposals["\n"])
    assert "\r" not in str(proposals["\n"])


# --------------------------------------------------------------------------------------
# § escalations — a compiled agent, because the defect is two calls and the cache between them
# --------------------------------------------------------------------------------------


class CachingSnapshot:
    """`PkbRuntime.snapshot()`'s caching policy, which is what makes the escalation reachable.

    The runtime scans once and holds the result until a flush, so everything an agent does to the
    tree during a run is invisible to the gate predicate that runs after it. Rescanning per call is
    not the fix — it costs a full walk per tool call and still leaves the out-of-band hand-edit
    window open — so the gate table has to keep answering with a snapshot it cannot trust.
    """

    def __init__(self, kb_root: Path) -> None:
        self.kb_root = kb_root
        self._cached: KbSnapshot | None = None

    def __call__(self) -> KbSnapshot:
        if self._cached is None:
            self._cached = scan(self.kb_root)
        return self._cached


def gated_agent(kb: Path, model: Any) -> tuple[Any, CachingSnapshot]:
    """A real compiled agent wired with the production `interrupt_on` mapping.

    `create_deep_agent` directly rather than `build_expert`: what is under test is the gate table
    against the harness's own HITL middleware and filesystem backend, and it must not pass or fail
    because a sibling module's prompt, permission list or middleware stack changed.
    """
    snapshot_fn = CachingSnapshot(kb)
    backend = CompositeBackend(
        default=StateBackend(),
        routes={KB_MOUNT: FilesystemBackend(root_dir=str(kb), virtual_mode=True)},
    )
    agent = create_deep_agent(
        model=model,
        system_prompt="test agent",
        backend=backend,
        interrupt_on=build_interrupt_on(GateEnv(snapshot=snapshot_fn)),
        checkpointer=InMemorySaver(),
    )
    return agent, snapshot_fn


def sole_interrupt(agent: Any, config: dict[str, Any]) -> dict[str, Any]:
    """The one action the thread is waiting on, as the harness stores it."""
    state = agent.get_state(config)
    assert len(state.interrupts) == 1, state.interrupts
    requests = state.interrupts[0].value["action_requests"]
    assert len(requests) == 1, requests
    return dict(requests[0])


def test_the_agent_only_escalation_interrupts_rt23(gated_kb: Path) -> None:
    """The headline case: an agent mints a topic root, then writes its breadth file.

    Neither call needs a human to set it up. Writing `Cooking/sub-topics/Braising/topic.md` gates on
    nothing — it is not one of *Cooking's* breadth files, `sub-topics` is a structural directory, and
    the tag is one the knowledge base already uses — and the moment it lands, `Braising` is a topic
    root the cached snapshot has never heard of. The next write, to that root's `notes/summary.md`
    and carrying `status.approved`, used to land with **zero** interrupts: one of the three compact
    approval surfaces overwritten, and a curated file self-approved, inside one run (RT-23, RT-27).
    """
    topic_md = doc(
        title="Braising",
        description="Low, slow and in liquid: pot roast, short ribs, and stew.",
        tags=["topic.cooking", "type.summary", "status.draft"],
        source_type="summary",
        body="\n# Braising\n\nLow and slow.\n",
    )
    breadth = doc(
        title="Braising notes",
        description="Distilled rules and notable solutions from the Braising notes.",
        tags=["topic.cooking", "type.summary", "status.approved"],
        source_type="summary",
        body="\n# Notes summary\n\nBrown first, then liquid.\n",
    )
    model = scripted(
        calls(
            call(
                "write_file",
                {"file_path": f"{KB_MOUNT}{BRAISING}/topic.md", "content": topic_md},
                "tc-mint",
            )
        ),
        calls(
            call(
                "write_file",
                {"file_path": f"{KB_MOUNT}{BRAISING}/notes/summary.md", "content": breadth},
                "tc-breadth",
            )
        ),
        says("done"),
    )
    agent, cached = gated_agent(gated_kb, model)
    config: dict[str, Any] = {"configurable": {"thread_id": "T-escalation"}}
    agent.invoke({"messages": [{"role": "user", "content": "go"}]}, config)

    # The escalation really happened: the root is on disk and absent from the snapshot in use.
    assert (gated_kb / BRAISING / "topic.md").exists()
    assert BRAISING not in cached().topics

    action = sole_interrupt(agent, config)
    assert action["name"] == "write_file"
    assert action["args"]["file_path"] == f"{KB_MOUNT}{BRAISING}/notes/summary.md"
    assert action["description"].startswith("Approval required: breadth-approval")
    assert not (gated_kb / BRAISING / "notes" / "summary.md").exists()


def test_a_case_folded_breadth_write_interrupts_and_the_bytes_survive_rt23(
    gated_kb: Path, case_insensitive_fs: bool
) -> None:
    """Capitalising one filename used to overwrite the human's approved `topic.md`.

    `Cooking/TOPIC.md` is the vector that proves the fix has to be canonicalisation and not a
    better topic lookup: there is no ancestor walk to get wrong here and no `TopicRecord` involved.
    The rule is `inner == (TOPIC_FILE,)`, an exact tuple compare, and `("TOPIC.md",)` is not it — so
    the top-level topic's breadth file, one of the three compact approval surfaces, was writable
    with zero interrupts. Models re-spell filenames all the time; no adversary is required.
    """
    if not case_insensitive_fs:
        pytest.skip("case-sensitive filesystem: the folded spelling is a different file")
    target = gated_kb / "Cooking" / "topic.md"
    human = doc(
        title="Cooking",
        description="Home cooking: technique, equipment, and recipes.",
        tags=["topic.cooking", "type.summary", "status.approved"],
        source_type="summary",
        body="\n# Cooking\n\nTechnique first, equipment second.\n",
    )
    target.write_text(human, encoding="utf-8")
    before = sorted(entry.name for entry in (gated_kb / "Cooking").iterdir())

    model = scripted(
        calls(
            call(
                "write_file",
                {
                    "file_path": f"{KB_MOUNT}Cooking/TOPIC.md",
                    "content": note("agent rewrite", body="\n# Cooking\n\nEquipment first.\n"),
                },
                "tc-folded",
            )
        ),
        says("done"),
    )
    agent, _ = gated_agent(gated_kb, model)
    config: dict[str, Any] = {"configurable": {"thread_id": "T-folded"}}
    agent.invoke({"messages": [{"role": "user", "content": "go"}]}, config)

    action = sole_interrupt(agent, config)
    assert action["description"].startswith("Approval required: breadth-approval")
    assert target.read_text(encoding="utf-8") == human
    assert sorted(entry.name for entry in (gated_kb / "Cooking").iterdir()) == before


def test_a_crlf_spelled_edit_of_a_human_note_interrupts_rt24(gated_kb: Path) -> None:
    """One tool call, spelled with Windows line endings, used to rewrite an approved note's body.

    The backend normalises `old_string` before matching and the simulation did not, so the gate
    asked "does this change the body?" about a replacement that found nothing — got `None` — and
    read that as "deepagents will refuse this anyway". deepagents then performed the edit. The note
    here carries `status.approved`, which is the class RT-24 exists for: the AI does not change a
    note's factual content unattended, whatever line endings it quotes.
    """
    target = gated_kb / "Cooking" / "notes" / "steak.md"
    human = note("steak", status="status.approved")
    target.write_text(human, encoding="utf-8")

    model = scripted(
        calls(
            call(
                "edit_file",
                {
                    "file_path": f"{KB_MOUNT}Cooking/notes/steak.md",
                    "old_string": "Sear it hot.\r\n",
                    "new_string": "Do not sear it at all.\r\n",
                },
                "tc-crlf",
            )
        ),
        says("done"),
    )
    agent, _ = gated_agent(gated_kb, model)
    config: dict[str, Any] = {"configurable": {"thread_id": "T-crlf"}}
    agent.invoke({"messages": [{"role": "user", "content": "go"}]}, config)

    action = sole_interrupt(agent, config)
    assert action["description"].startswith("Approval required: human-content-edit")
    assert target.read_text(encoding="utf-8") == human
