"""The approval table (RT-21 … RT-35).

Table-driven and graph-free by design: RT-21 exists precisely so that "what gates" can be answered
without a model, a runtime or a compiled agent. Every test here reads a real knowledge base built by
Layer 1's own scaffolder and asks :func:`pkb.agents.gates.requires_approval` a question.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date
from pathlib import Path
from typing import Any

import pytest
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
from pkb.agents.paths import KB_MOUNT
from pkb.core import Metadata, errors_only, validate_content
from pkb.core.frontmatter import serialize
from pkb.core.models import KbSnapshot
from pkb.core.scan import scan
from pkb.core.tags import STATUS_TAGS
from tests.agents.conftest import TODAY

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
    """There is no version control and no undo, so every delete stops for a human (RT-30, D6)."""
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
