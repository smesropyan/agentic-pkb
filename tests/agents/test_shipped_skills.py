"""The ten shipped skills, tested as package data (SK-1 … SK-18).

Skill *quality* is not testable without a model (SK-18), so nothing here asserts what a skill makes
an agent do. What is testable is everything that decides whether a skill loads at all, whether it
says the things the collaboration model depends on, and whether adopting one is safe: frontmatter
schema, name/directory agreement, the description cap, Layer 1 cleanliness, the approval-gate
section, the shared draft footer, and adoption semantics.

Two rules in the ``SK`` group are asserted elsewhere because they need a compiled graph: SK-8 (no
skill body reaches a system prompt) and SK-16 (the harness's source ordering agrees with
``pkb.core.resolve_skills``) live in the expert suite, and SK-13's "an overload cannot weaken a
standard" is a validation-middleware assertion.
"""

from __future__ import annotations

import re
import socket
from collections.abc import Iterator
from pathlib import Path

import pytest

from pkb.agents.skills import (
    DEFAULT_SKILL_NAMES,
    DRAFT_FOOTER,
    UnknownSkillError,
    adopt_skill,
    check_skill_dir,
    packaged_skills_root,
)
from pkb.core import (
    NotATopicRootError,
    regenerate_all,
    resolve_skills,
    validate_content,
)
from pkb.core.frontmatter import REQUIRED_FIELDS
from pkb.core.frontmatter import parse as parse_frontmatter
from pkb.core.scaffold import scaffold_topic
from pkb.core.tags import MAX_TAG_DEPTH, TYPE_TAGS, Namespace
from tests.agents.conftest import TODAY

SKILL_FILE = "SKILL.md"

APPROVAL_HEADING = "## The approval gate"
"""Every shipped body carries this section (SK-11). One shared heading rather than ten wordings,
so "does this skill say where it stops?" is a grep rather than a reading."""

_DEEPAGENTS_KEYS = frozenset({"name", "description", "license", "compatibility"})
"""The frontmatter keys a shipped skill may carry (SK-6). ``allowed-tools`` is deliberately absent:
its enforcement semantics are unverified on this pin and a wrong restriction is a silent capability
gap mid-task."""


def _text(name: str) -> str:
    return (packaged_skills_root() / name / SKILL_FILE).read_text(encoding="utf-8")


def _meta(name: str) -> dict[str, object]:
    raw = parse_frontmatter(_text(name)).raw
    assert raw is not None, f"{name}: no frontmatter block"
    return dict(raw)


def _body(name: str) -> str:
    return parse_frontmatter(_text(name)).body


def _flat(text: str) -> str:
    """Prose with its hard line wrapping removed, so a phrase check is not a line-break check."""
    return " ".join(text.split())


def _sentences(text: str) -> list[str]:
    return re.split(r"(?<=[.;:]) ", _flat(text))


@pytest.fixture(params=DEFAULT_SKILL_NAMES)
def skill(request: pytest.FixtureRequest) -> str:
    """Every rule below that is per-skill runs ten times, named after the skill that failed."""
    return str(request.param)


# --------------------------------------------------------------------------------------
# What ships, and where it lives
# --------------------------------------------------------------------------------------


def test_ships_exactly_the_ten_starter_skills_sk1() -> None:
    """SK-1: ten skills, one directory each, and the constant matches what is on disk.

    Eight until large-source ingestion landed. ``ingest-book`` and ``ingest-paper`` are pinned here
    for the same reason as the other eight: a directory without the constant is package data no
    expert's prompt reaches, and a constant without the directory is a name the model can ask to
    adopt and never get.
    """
    assert set(DEFAULT_SKILL_NAMES) == {
        "summarization",
        "conflict-detection",
        "tag-proposal",
        "ingestion-classification",
        "ingest-book",
        "ingest-paper",
        "sub-topic-proposal",
        "voice",
        "discovery",
        "research",
    }
    on_disk = {entry.name for entry in packaged_skills_root().iterdir() if entry.is_dir()}
    assert on_disk == set(DEFAULT_SKILL_NAMES)


def test_frontmatter_name_matches_directory_sk2(skill: str) -> None:
    """SK-2: ``name`` equals the directory name and is a legal skill name.

    deepagents matches an override by name; a mismatch means the skill silently fails to shadow
    anything, with a log line and no error.
    """
    assert _meta(skill)["name"] == skill
    assert re.fullmatch(r"[a-z0-9]+(-[a-z0-9]+)*", skill)
    assert len(skill) <= 64


def test_skills_are_mounted_package_data_not_seeded_sk3(kb: Path) -> None:
    """SK-3: the shipped skills resolve as package data and live nowhere near a knowledge base."""
    root = packaged_skills_root()
    assert root.is_dir()
    assert all((root / name / SKILL_FILE).is_file() for name in DEFAULT_SKILL_NAMES)
    assert kb not in root.parents and root != kb
    assert not (kb / "skills").exists(), "a knowledge base gets a skills/ folder only by adoption"


# --------------------------------------------------------------------------------------
# Frontmatter and description (SK-6, SK-7)
# --------------------------------------------------------------------------------------


def test_frontmatter_is_deepagents_only_and_validates_clean_sk6(skill: str, kb: Path) -> None:
    """SK-6: deepagents' schema, never the seven PKB fields, and clean under Layer 1's VA-6."""
    keys = set(_meta(skill))
    assert keys <= _DEEPAGENTS_KEYS, (
        f"{skill}: unexpected frontmatter keys {keys - _DEEPAGENTS_KEYS}"
    )
    assert {"name", "description"} <= keys
    assert not (keys & (REQUIRED_FIELDS - _DEEPAGENTS_KEYS)), (
        f"{skill}: carries PKB frontmatter, which would break deepagents' parsing"
    )
    assert validate_content(kb, f"skills/{skill}/{SKILL_FILE}", _text(skill)) == []


def test_description_is_a_trigger_clause_under_the_cap_sk7(skill: str) -> None:
    """SK-7: the description is the whole routing surface — progressive disclosure ships nothing
    else into the prompt — so it must say *when* to invoke the skill and fit deepagents' cap."""
    description = str(_meta(skill)["description"])
    assert 0 < len(description) <= 1024
    assert re.match(r"Use (when|before) ", description), description


# --------------------------------------------------------------------------------------
# What the bodies may and may not say (SK-9, SK-10, SK-11, SK-12, SK-14)
# --------------------------------------------------------------------------------------


def test_bodies_address_files_by_role_not_by_location_sk9(skill: str) -> None:
    """SK-9: no mount prefix, no knowledge-base root, no concrete topic folder.

    A shipped skill is loaded by every topic in every knowledge base. The topic-specific paths come
    from the expert's own prompt, which alone knows its topic.
    """
    text = _text(skill).lower()
    assert "/kb/" not in text
    assert "knowledgebase/" not in text
    for concrete in ("cooking/", "bbq", "grilling/", "physics"):
        assert concrete not in text, f"{skill}: names a concrete topic ({concrete!r})"


_DERIVED_MENTION_MARKERS = ("machine-generated", "never", "do not", "not read", "nothing to")
"""A body may mention a derived file only to stop an agent wasting a tool call on it (SK-10)."""


def test_no_body_instructs_a_write_to_a_derived_file_sk10(skill: str) -> None:
    """SK-10: derived files are protected by the deny list, not by prose.

    Any mention of ``index.md`` or root ``tags.md`` must be a "this is generated, leave it alone"
    aside. An instruction to maintain one would be both wrong and unenforceable — the write is
    refused at the harness level regardless of what any skill says.
    """
    for sentence in _sentences(_body(skill)):
        if "index.md" not in sentence and "tags.md" not in sentence:
            continue
        assert any(marker in sentence.lower() for marker in _DERIVED_MENTION_MARKERS), (
            f"{skill}: mentions a derived file without saying it is generated: {sentence!r}"
        )


_GATE_PHRASES = {
    "summarization": "breadth-approval gate",
    "conflict-detection": "conflict-resolution gate",
    "tag-proposal": "new-tag gate",
    "ingestion-classification": "show-before-write step",
    "ingest-book": "re-ingestion gate",
    "ingest-paper": "re-ingestion gate",
    "sub-topic-proposal": "Creating a sub-topic pauses",
    "voice": "pauses",
    "discovery": "hand-off",
    "research": "request for direction",
}
"""What each body must name as the point where it stops and the human decides (SK-11)."""


def test_every_body_names_the_approval_gate_it_terminates_at_sk11(skill: str) -> None:
    """SK-11: no shipped skill ends with the agent having finalized human-curated content."""
    body = _body(skill)
    assert APPROVAL_HEADING in body, f"{skill}: no approval-gate section"
    section = _flat(body.split(APPROVAL_HEADING, 1)[1].split(DRAFT_FOOTER)[0])
    assert len(section) > 200, f"{skill}: approval-gate section is a stub"
    assert "human" in section.lower()
    assert _GATE_PHRASES[skill] in section, f"{skill}: does not name its gate"


def test_every_body_ends_with_the_shared_draft_footer_sk12(skill: str) -> None:
    """SK-12: the human is told, in every skill, that this text is theirs to rewrite."""
    assert _text(skill).rstrip("\n").endswith(DRAFT_FOOTER)
    assert "starter draft" in DRAFT_FOOTER
    assert "adopt" in DRAFT_FOOTER


def test_restated_layer1_constraints_match_layer1_sk14() -> None:
    """SK-14: the small restated set is checked against Layer 1's own constants.

    Restatement is limited to what an agent cannot recover from in one attempt — a structurally
    wrong file burns all three retries, while a single bad field self-corrects from the hint the
    middleware forwards verbatim. Because it is a copy, it can drift, so it is pinned here.
    """
    tags = _body("tag-proposal")
    for namespace in Namespace:
        assert f"`{namespace.value}.*`" in tags
    assert f"{MAX_TAG_DEPTH} segments" in tags
    for closed_tag in sorted(TYPE_TAGS):
        assert f"`{closed_tag}`" in tags, f"tag-proposal omits the closed vocabulary {closed_tag}"

    ingestion = _body("ingestion-classification")
    for field in sorted(REQUIRED_FIELDS):
        assert field in ingestion, f"ingestion-classification omits required field {field!r}"
    assert "<item-name>/<item-name>.md" in ingestion


# --------------------------------------------------------------------------------------
# Adoption (SK-4, SK-5, SK-17)
# --------------------------------------------------------------------------------------


def test_adoption_copies_once_and_refuses_to_overwrite_sk4(kb: Path) -> None:
    """SK-4: adoption is the only way a shipped skill reaches the tree, and it never overwrites."""
    first = adopt_skill(kb, "voice")
    assert first.adopted
    assert first.path == "skills/voice/SKILL.md"
    copy = kb / first.path
    assert validate_content(kb, first.path, copy.read_text(encoding="utf-8")) == []

    human_text = copy.read_text(encoding="utf-8") + "\n\nI write in short sentences.\n"
    copy.write_text(human_text, encoding="utf-8")

    second = adopt_skill(kb, "voice")
    assert not second.adopted
    assert copy.read_text(encoding="utf-8") == human_text, "the human's rewrite was overwritten"
    assert "already exists" in second.message


def test_adoption_into_a_topic_is_the_same_act_as_an_overload_sk4(kb: Path) -> None:
    """SK-4: one mechanism covers the root skill and the topic overload."""
    result = adopt_skill(kb, "summarization", topic_path=Path("Cooking"))
    assert result.path == "Cooking/skills/summarization/SKILL.md"
    assert (kb / result.path).is_file()
    assert resolve_skills(kb, kb / "Cooking")["summarization"] == kb / result.path

    with pytest.raises(NotATopicRootError):
        adopt_skill(kb, "voice", topic_path=Path("Cooking/notes"))

    with pytest.raises(UnknownSkillError):
        adopt_skill(kb, "interviewing")


def test_adoption_announces_a_permanent_fork_sk5(kb: Path) -> None:
    """SK-5: the notice reaches the file, not only the terminal it was printed to.

    Whoever runs the command is not the person who opens the file six months later, and there is no
    version control to explain why the shipped improvements stopped arriving.
    """
    result = adopt_skill(kb, "summarization")
    body = parse_frontmatter((kb / result.path).read_text(encoding="utf-8")).body
    assert body.lstrip("\n").splitlines()[0] == result.notice
    assert "shipped" in result.notice and "never reach it" in result.notice
    assert "delet" in result.notice.lower() and "way back" in result.notice
    assert "replaces the shipped one" in result.message
    assert "skills/summarization/" in result.message


def test_an_adopted_skill_shadows_the_shipped_one_wholesale_sk5(kb: Path) -> None:
    """SK-5: override is whole-record replacement, so a gutted copy leaks no packaged text.

    This is why adoption copies the entire directory and why the fork is worth announcing: there is
    no field-level merge that would quietly keep the shipped procedure alive underneath.
    """
    result = adopt_skill(kb, "summarization")
    copy = kb / result.path
    copy.write_text("---\nname: summarization\ndescription: Use when I say so.\n---\n\nAsk me.\n")

    resolved = resolve_skills(kb, kb / "Cooking")["summarization"]
    assert resolved == copy
    assert "breadth-approval gate" not in resolved.read_text(encoding="utf-8")


def _derived_bytes(kb: Path) -> dict[str, bytes]:
    regenerate_all(kb)
    return {
        str(path.relative_to(kb)): path.read_bytes()
        for path in sorted(kb.rglob("*.md"))
        if path.name in ("index.md", "tags.md")
    }


def test_adopting_every_skill_changes_no_generated_artifact_sk17(kb: Path) -> None:
    """SK-17: skills appear in no index, contribute no tags, and mint no topic root.

    Layer 1 excludes ``skills/**`` from every artifact; Layer 2 must not defeat that by putting an
    adopted copy anywhere else.
    """
    before = _derived_bytes(kb)
    for name in DEFAULT_SKILL_NAMES:
        assert adopt_skill(kb, name).adopted
        assert adopt_skill(kb, name, topic_path=Path("Cooking")).adopted
    assert _derived_bytes(kb) == before


# --------------------------------------------------------------------------------------
# Diagnostics Layer 1 cannot see (SK-15) and the no-key guarantee (SK-18)
# --------------------------------------------------------------------------------------


def test_check_skill_dir_reports_the_two_silent_failures_sk15(kb: Path) -> None:
    """SK-15: a skill can be well-formed for Layer 1 and still never load."""
    adopt_skill(kb, "voice")
    assert check_skill_dir(kb, kb / "skills" / "voice") == []

    mismatched = kb / "skills" / "voice" / SKILL_FILE
    mismatched.write_text(
        mismatched.read_text(encoding="utf-8").replace("name: voice", "name: tone")
    )
    codes = [finding.code for finding in check_skill_dir(kb, kb / "skills" / "voice")]
    assert codes == ["SKILL_NAME_MISMATCH"]
    assert validate_content(kb, "skills/voice/SKILL.md", mismatched.read_text()) == [], (
        "Layer 1 checks presence only — that is exactly why this diagnostic exists"
    )

    inert = kb / "Cooking" / "notes" / "skills" / "discovery"
    inert.mkdir(parents=True)
    (inert / SKILL_FILE).write_text("---\nname: discovery\ndescription: Use when.\n---\n\nBody.\n")
    assert [f.code for f in check_skill_dir(kb, inert)] == ["INERT_SKILL_OVERLOAD"]

    assert check_skill_dir(kb, kb / "Cooking" / "notes") == []


@pytest.fixture
def no_network(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    def _blocked(*args: object, **kwargs: object) -> None:
        raise AssertionError("the shipped-skill suite must not open a socket (SK-18)")

    monkeypatch.setattr(socket, "socket", _blocked)
    yield


def test_shipped_skill_mechanics_need_no_key_and_no_network_sk18(
    kb: Path, no_network: None
) -> None:
    """SK-18: skill *content* quality is a live-marked concern; the mechanics run free."""
    result = adopt_skill(kb, "research")
    assert result.adopted
    assert validate_content(kb, result.path, (kb / result.path).read_text()) == []
    assert check_skill_dir(kb, kb / "skills" / "research") == []


# --------------------------------------------------------------------------------------
# Voice — a usable default, narrowed per topic
# --------------------------------------------------------------------------------------


def test_the_shipped_voice_is_a_profile_not_a_questionnaire_sk1() -> None:
    """`voice` ships an opinionated starter profile, so day-one drafts have a definite style.

    The alternative — shipping only a procedure for building a profile — leaves the first weeks of
    drafts with an accidental voice rather than a chosen one. A wrong default is visible and gets
    corrected; an absent one is invisible and does not.
    """
    body = _body("voice")
    assert "## The profile" in body, "voice ships no profile section"
    profile = body.split("## The profile", 1)[1].split("\n## ", 1)[0]
    assert len(profile) > 800, "the profile is a stub, not something an agent can write from"
    # Concrete instructions, not an interview script.
    assert profile.count("\n- **") >= 6, "the profile has no enumerated, applicable rules"
    for questionnaire in ("gather samples", "ask the human how", "interview"):
        assert questionnaire not in profile.lower(), (
            f"the profile section reads as a procedure, not a profile: {questionnaire!r}"
        )
    # It must still present itself as a guess about someone it has never met.
    assert "starter profile" in body or "starter draft" in body


def test_voice_explains_that_a_topic_copy_replaces_rather_than_merges_sk1() -> None:
    """The resolver is whole-record last-wins, and a human writing a topic voice must know it.

    `resolve_skills` returns one path per skill name, so a topic's `skills/voice/` *replaces* the
    root profile for that topic. Someone who assumes it layers writes a two-line topic voice and
    silently loses every general rule — with no error anywhere to tell them.
    """
    body = _flat(_body("voice"))
    assert "replaces this file for that topic" in body
    assert "does not merge" in body
    assert "restate" in body


def test_each_topic_resolves_its_own_voice_sk16(kb: Path) -> None:
    """Cooking and Trading are different registers, so each topic may hold its own voice (SK-16).

    Asserted through `pkb.core.resolve_skills`, which is what the expert's skill chain is built
    from: nearest copy wins, a topic without one falls back to the root, and a sub-topic may narrow
    its parent again.
    """
    scaffold_topic(
        kb,
        "Trading",
        title="Trading",
        description="Positions, theses, and post-mortems",
        today=TODAY,
    )
    adopt_skill(kb, "voice")
    adopt_skill(kb, "voice", topic_path=Path("Trading"))
    adopt_skill(kb, "voice", topic_path=Path("Cooking/sub-topics/Grilling"))

    trading = kb / "Trading" / "skills" / "voice" / SKILL_FILE
    trading.write_text(
        trading.read_text(encoding="utf-8").replace(
            "## The profile", "## The profile\n\nDates, sizes, and the reasoning at the time.\n"
        ),
        encoding="utf-8",
    )

    assert resolve_skills(kb, kb / "Trading")["voice"] == trading
    assert resolve_skills(kb, kb / "Cooking")["voice"] == kb / "skills" / "voice" / SKILL_FILE
    assert (
        resolve_skills(kb, kb / "Cooking" / "sub-topics" / "Grilling")["voice"]
        == kb / "Cooking" / "sub-topics" / "Grilling" / "skills" / "voice" / SKILL_FILE
    )
    assert "Dates, sizes" not in resolve_skills(kb, kb / "Cooking")["voice"].read_text(
        encoding="utf-8"
    ), "the Trading voice leaked into Cooking"


# --------------------------------------------------------------------------------------
# The two extraction skills — the judgement large-source ingestion needs written down
# --------------------------------------------------------------------------------------


def test_ingest_book_carries_the_source_structured_shape_sk1() -> None:
    """`ingest-book` groups arguments under the chapter that introduced them (LS-10, LS-2).

    The chapter heading is the anchor a re-ingestion matches on — chapter 3 is chapter 3 on every
    reading — so a body that described a flat list of arguments would leave reconciliation with no
    stable key and quietly reintroduce the identity problem one-file-per-source exists to avoid.
    """
    body = _flat(_body("ingest-book"))
    assert "## Across the book" in _body("ingest-book"), "no section for the ideas no chapter owns"
    assert "one bullet per argument, not one per chapter" in body, "arguments are argument-scoped"
    assert "you do not decide when the reading is finished" in body, (
        "the loop drives the reading; a body that lets the model judge its own progress is the "
        "failure this design exists to prevent"
    )


def test_ingest_book_says_an_empty_chapter_is_a_result_sk1() -> None:
    """The most common failure is a bullet per chapter, produced by an agent that feels obliged.

    A chapter holding nothing for this topic yields nothing, and the body has to say so outright —
    otherwise the file fills with near-misses and a reader cannot tell which entries are real. It
    must also keep "read and took nothing" distinct from "never opened": both leave no section, and
    only one of them means somebody still has to go back.
    """
    body = _flat(_body("ingest-book"))
    assert "A chapter that gives this topic nothing yields nothing" in body
    assert "Padding is not thoroughness" in body
    assert "Read and took nothing is not the same as never opened" in body
    assert "no folder, no stub" in body, "zero insights must leave no trace at all (LS-6)"


def test_ingest_paper_carries_the_five_part_skeleton_in_order_sk1() -> None:
    """`ingest-paper` is question · method · results · limitations · does this apply to me.

    The last part is the one that earns its place: a result is knowledge here only once someone has
    said whether it applies to this human's circumstances. Order matters as much as presence — the
    applicability judgement is made against the method and the limitations, so it comes last.
    """
    body = _body("ingest-paper")
    parts = ["## Question", "## Method", "## Results", "## Limitations", "## Does this apply to me"]
    positions = [body.find(part) for part in parts]
    assert all(position >= 0 for position in positions), dict(zip(parts, positions, strict=True))
    assert positions == sorted(positions), "the five parts are out of order"
    assert body.count("## Does this apply to me") >= 2, (
        "the applicability judgement needs its own section, not just a line in the skeleton"
    )


def test_ingest_paper_keeps_a_finding_out_of_the_humans_mouth_sk1() -> None:
    """A paper's claims are what the paper found, never what happened when someone tried it.

    README §1.3 makes notes human-authored. An extraction that writes "we tried this and it worked"
    manufactures experience the human never had, and "human content wins" stops meaning anything
    once there is no human side left to win.
    """
    body = _flat(_body("ingest-paper"))
    assert "This file is a reference" in body
    assert "is the human's to write" in body
    assert "an argument you are making, not a finding the paper reports" in body, (
        "the applicability judgement must present itself as a judgement"
    )
    assert "One study is one study" in body
