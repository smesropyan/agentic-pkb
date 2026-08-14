"""Tests for :mod:`pkb.packs` — context-pack assembly (PK-7 … PK-12, and MC-20's trigger).

A pack is the slice of the knowledge base an external agent gets instead of the whole tree, and
almost everything that can go wrong with one is *invisible at the consumer*. A pack built for the
wrong topic reads exactly like a correct one. A pack whose last file was clipped in half reads
exactly like a short topic. A pack that quietly dropped the human's own distilled rules off the end
reads like a topic with no rules. None of those failures announce themselves, which is why every
rule in this area is stated as an ordering or a visibility rule rather than a quality one, and why
the tests below pin ordered path lists byte-for-byte instead of checking that "the right sort of
thing" came back.

Everything here runs over a fixture knowledge base in ``tmp_path`` and **constructs no runtime**:
no checkpointer, no chat model, no registry, no harness import at all. That is not an economy, it
is the point of decision G — assembly was moved below the seam precisely so that the golden
ordering tests could live in the free-and-fast profile (PK-7). Two of the tests enforce that
property from the outside, in a subprocess, because a leaf module stops being a leaf silently.
"""

from __future__ import annotations

import datetime
import hashlib
import os
import re
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path

import pytest

from pkb.contracts import Pack
from pkb.core.frontmatter import serialize
from pkb.core.generators import regenerate_all
from pkb.core.models import KbSnapshot, Metadata
from pkb.core.scaffold import scaffold_subtopic, scaffold_topic
from pkb.core.scan import scan
from pkb.packs import (
    CONFLICT_TAG,
    TAG_SUBTREE_ROLE,
    UnknownTopicError,
    escalations,
    implementation_pack,
    research_pack,
)

TODAY = datetime.date(2026, 8, 7)

COOKING = "topic/cooking"
TRADING = "topic/trading"
GRILLING = "topic/cooking/grilling"
LEDGER = "topic/ledger"

REVIEW_NOTE = "McGee says 6 hours, the notes say 30 minutes. Unresolved."
"""The human's own words on the contested file. MC-20 requires them back **verbatim**."""

REPO_ROOT = Path(__file__).resolve().parents[1]

LEAF_FORBIDDEN = ("deepagents", "langgraph", "langchain", "langsmith")
"""The harness packages PK-7 names. Any top-level module starting with one of these is a violation."""

ABOVE_THE_SEAM = ("pkb.agents", "pkb.service", "pkb.server", "pkb.daemon", "pkb.sources")
"""``pkb.packs`` is a leaf: it may import ``pkb.core`` and ``pkb.contracts``, and nothing else."""


# --------------------------------------------------------------------------------------
# Fixture knowledge base — scaffolded where the standard structure matters, hand-made where
# the absence of it is the point
# --------------------------------------------------------------------------------------


def write_note(
    path: Path,
    *,
    title: str,
    topic: str,
    tags: Sequence[str],
    source_type: str,
    body: str = "Body.\n",
    review_note: str | None = None,
    newline: str = "\n",
) -> None:
    """One authored markdown file, written through Layer 1's own serializer.

    Going through :func:`pkb.core.frontmatter.serialize` rather than a text template means the
    fixture cannot drift from the canonical form the scan parses, so a golden ordering that passes
    here is a golden ordering over a knowledge base Layer 1 would accept.
    """
    meta = Metadata(
        title=title,
        description=f"{title} — fixture file",
        topic=topic,
        tags=tuple(tags),
        created=TODAY,
        updated=TODAY,
        source_type=source_type,
    )
    # `review_note` is no longer a schema field (T-12); serialized as an unknown key so the fixture
    # text stays exactly what it was when the field was still known.
    extra = {"review_note": review_note} if review_note is not None else None
    text = serialize(meta, f"\n# {title}\n\n{body}", extra=extra)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(text.replace("\n", newline).encode("utf-8"))


def make_kb(root: Path) -> Path:
    """The fixture the golden orderings are pinned against.

    ``Cooking`` is scaffolded and then populated so that every ordering slot is occupied by
    something distinguishable: two references (so their relative order is observable), a
    ``type.solution`` note, a note under ``status.conflict-review`` carrying a review note, and one
    sub-topic (so ``include_subtopics``'s default has something to leave out).

    ``Trading`` is a second scaffolded topic with nothing tagged — the sibling that must stay
    unaffected by Cooking's conflict (MC-20).

    ``Ledger`` is **hand-made**: a directory holding nothing but ``topic.md``. It is a legal topic
    root — ``topic.md`` is the only structural marker — and it has no ``notes/summary.md`` at all,
    which is the case that makes "``notes/summary.md`` first" an *ordering* rule rather than an
    ``entries[0]`` assertion (PK-10).
    """
    kb = root / "kb"
    kb.mkdir()
    scaffold_topic(kb, "Cooking", title="Cooking", description="Food and heat", today=TODAY)
    scaffold_topic(kb, "Trading", title="Trading", description="Positions", today=TODAY)
    scaffold_subtopic(
        kb, kb / "Cooking", "Grilling", title="Grilling", description="Fire", today=TODAY
    )

    write_note(
        kb / "Cooking" / "notes" / "salt.md",
        title="Salting",
        topic="Cooking",
        tags=("topic.cooking", "type.note", CONFLICT_TAG),
        source_type="note",
        review_note=REVIEW_NOTE,
    )
    write_note(
        kb / "Cooking" / "notes" / "sear.md",
        title="Searing",
        topic="Cooking",
        tags=("topic.cooking", "type.solution", "status.approved"),
        source_type="solution",
    )
    write_note(
        kb / "Cooking" / "references" / "mcgee" / "mcgee.md",
        title="On Food and Cooking",
        topic="Cooking",
        tags=("topic.cooking", "type.reference", "status.approved"),
        source_type="reference",
    )
    write_note(
        kb / "Cooking" / "references" / "ruhlman" / "ruhlman.md",
        title="Ratio",
        topic="Cooking",
        tags=("topic.cooking", "type.reference", "status.approved"),
        source_type="reference",
    )
    write_note(
        kb / "Cooking" / "sub-topics" / "Grilling" / "notes" / "fire.md",
        title="Two-zone fire",
        topic="Grilling",
        tags=("topic.cooking.grilling", "type.solution", "status.approved"),
        source_type="solution",
    )

    ledger = kb / "Ledger"
    ledger.mkdir()
    write_note(
        ledger / "topic.md",
        title="Ledger",
        topic="Ledger",
        tags=("topic.ledger", "type.summary", "status.approved"),
        source_type="summary",
        body="A topic root and nothing else.\n",
    )

    regenerate_all(kb)
    return kb


@pytest.fixture
def kb(tmp_path: Path) -> Path:
    return make_kb(tmp_path)


@pytest.fixture
def snapshot(kb: Path) -> KbSnapshot:
    return scan(kb)


def paths(pack: Pack) -> list[str]:
    return [entry.path for entry in pack.entries]


def roles(pack: Pack) -> list[str]:
    return [entry.role for entry in pack.entries]


# --------------------------------------------------------------------------------------
# PK-7 — a leaf, and provably so
# --------------------------------------------------------------------------------------


def run_python(source: str, tmp: Path, *args: str) -> str:
    """Run a snippet in a fresh interpreter and return its stdout.

    A subprocess rather than an in-process ``sys.modules`` check because the rest of the suite
    imports the harness: by the time this file runs under ``pytest tests/``, ``deepagents`` is
    already resident and an in-process assertion would pass no matter what ``pkb.packs`` imports.
    """
    script = tmp / "leaf_probe.py"
    script.write_text(source, encoding="utf-8")
    env = {**os.environ, "PYTHONPATH": str(REPO_ROOT / "src")}
    result = subprocess.run(
        [sys.executable, str(script), *args],
        capture_output=True,
        text=True,
        env=env,
        cwd=str(REPO_ROOT),
        timeout=120,
    )
    assert result.returncode == 0, f"probe failed:\n{result.stdout}\n{result.stderr}"
    return result.stdout


def test_importing_packs_loads_no_harness_module_pk7(tmp_path: Path) -> None:
    """Assembly must stay below the seam, or the golden tests need a runtime to run at all.

    Layer 2's Q10 put pack assembly in ``pkb.agents``; decision G moved it down because the only
    way to test a golden ordering above the harness is to stand up a checkpointer and a chat model,
    and a test that expensive is a test that stops being run. Leaf-ness is not something the type
    checker or the linter notices — one convenience import of ``pkb.agents.registry`` for an agent
    id would restore the whole dependency silently — so it is asserted from a fresh interpreter that
    imports ``pkb.packs`` and nothing else.
    """
    loaded = run_python(
        "import sys\nimport pkb.packs  # noqa: F401\nprint('\\n'.join(sorted(sys.modules)))\n",
        tmp_path,
    ).split()

    harness = [m for m in loaded if m.split(".")[0].startswith(LEAF_FORBIDDEN)]
    assert harness == [], f"pkb.packs dragged the harness in: {harness}"
    above = [m for m in loaded if m.startswith(ABOVE_THE_SEAM)]
    assert above == [], f"pkb.packs is no longer a leaf: {above}"


def test_a_golden_pack_builds_with_no_runtime_resident_pk7(kb: Path, tmp_path: Path) -> None:
    """The point of PK-7 is not the import graph, it is that *building a pack* needs no runtime.

    A module can be a leaf and still be useless below the seam if the caller has to construct a
    registry to name a topic. This drives both pack kinds end to end in an interpreter that has
    never imported the harness, over the same fixture the goldens use, and checks the orderings
    survive — so the free-and-fast profile decision G bought is exercised, not merely claimed.
    """
    out = run_python(
        "import sys\n"
        "from pathlib import Path\n"
        "from pkb.core.scan import scan\n"
        "from pkb.packs import implementation_pack, research_pack\n"
        "snapshot = scan(Path(sys.argv[1]))\n"
        "research = research_pack(snapshot, topics=['topic/cooking'])\n"
        "implementation = implementation_pack(snapshot, topic='topic/cooking')\n"
        "print('|'.join(e.path for e in research.entries))\n"
        "print('|'.join(e.path for e in implementation.entries))\n"
        "loaded = [m for m in sys.modules if m.split('.')[0].startswith("
        f"{LEAF_FORBIDDEN!r})]\n"
        "print('RUNTIME=' + ','.join(sorted(loaded)))\n",
        tmp_path,
        str(kb),
    ).splitlines()

    assert out[0].split("|") == [
        "tags.md",
        "Cooking/topic.md",
        "Cooking/notes/summary.md",
        "Cooking/references/summary.md",
        "Cooking/notes/salt.md",
    ]
    assert out[1].split("|")[0] == "Cooking/notes/summary.md"
    assert out[2] == "RUNTIME="


# --------------------------------------------------------------------------------------
# PK-9 — the research pack: breadth, in a fixed order
# --------------------------------------------------------------------------------------


def test_research_pack_ordering_is_golden_pk9(snapshot: KbSnapshot) -> None:
    """A research agent reads top-down and stops when its context fills, so the order *is* the
    answer to "what did it actually learn".

    The sequence encodes three separate decisions and each one is load-bearing. The tag subtree
    comes first because an agent that does not know what vocabulary exists cannot tell a gap in the
    knowledge base from a gap in its query. ``notes/summary.md`` precedes ``references/summary.md``
    because §1.7 ranks human-approved experience above ingested static knowledge, and an agent that
    reads a book's summary before the human's own conclusions will weight them the wrong way round.
    Conflict-review notes come last but come *at all*, because reasoning over contested material
    without knowing it is contested produces confident nonsense.

    Pinned as an exact ordered list rather than a set of memberships: a pack whose contents are
    right and whose order is wrong is precisely the failure this rule exists to prevent, and it is
    invisible to any assertion weaker than this one.
    """
    pack = research_pack(snapshot, topics=[COOKING, TRADING])

    assert paths(pack) == [
        "tags.md",
        "Cooking/topic.md",
        "Cooking/notes/summary.md",
        "Cooking/references/summary.md",
        "Trading/topic.md",
        "Trading/notes/summary.md",
        "Trading/references/summary.md",
        "Cooking/notes/salt.md",
    ]
    assert roles(pack) == [
        TAG_SUBTREE_ROLE,
        "topic-overview",
        "notes-summary",
        "references-summary",
        "topic-overview",
        "notes-summary",
        "references-summary",
        CONFLICT_TAG,
    ]
    assert pack.kind == "research"
    assert pack.scope == (COOKING, TRADING)


def test_research_pack_excludes_every_index_by_default_pk9(snapshot: KbSnapshot) -> None:
    """An index is a list of everything, which is the opposite of breadth.

    README says research agents do not read indexes unless asked. Leaking one in costs the agent a
    large, low-signal file at the top of a budgeted pack — exactly the context-window problem goal 2
    exists to solve — and it would arrive *ahead* of the material that answers the question.
    """
    pack = research_pack(snapshot, topics=[COOKING, TRADING, LEDGER])

    assert [p for p in paths(pack) if p.endswith("index.md")] == []


def test_include_index_adds_exactly_the_topic_indexes_pk9(snapshot: KbSnapshot) -> None:
    """``include_index`` is an addition, not a different pack.

    A caller that asks for indexes is asking for one more file per topic; if the flag also
    reshuffled the breadth entries or pulled in the *root* index (which lists every topic in the
    knowledge base, most of them out of scope) the caller could not reason about what it paid for.
    The assertion is a set difference against the default pack, so any collateral change fails.
    """
    default = research_pack(snapshot, topics=[COOKING, TRADING])
    with_index = research_pack(snapshot, topics=[COOKING, TRADING], include_index=True)

    added = [p for p in paths(with_index) if p not in set(paths(default))]
    assert added == ["Cooking/index.md", "Trading/index.md"]
    assert [p for p in paths(default) if p not in set(paths(with_index))] == []
    assert [p for p in paths(with_index) if p in set(paths(default))] == paths(default)


def test_research_pack_orders_topics_by_snapshot_not_by_caller_pk9(snapshot: KbSnapshot) -> None:
    """PK-9 fixes the ordering; ``research_pack`` lets the caller's argument order redefine it.

    The rule says "then per topic **in snapshot order**", and ``Pack.scope`` documents itself the
    same way. The implementation iterates the ``topics`` argument instead, so two callers naming
    the same two topics — a classifier that returned them alphabetically and one that returned them
    by score — get byte-different packs for the same knowledge base. That is the reproducibility
    PK-8 gives up a model call to buy, spent again at the call site.
    """
    forward = research_pack(snapshot, topics=[COOKING, TRADING])
    reversed_args = research_pack(snapshot, topics=[TRADING, COOKING])

    assert paths(reversed_args) == paths(forward)
    assert reversed_args.scope == forward.scope


def test_research_pack_needs_no_classification_when_topics_are_given_pk9(
    snapshot: KbSnapshot,
) -> None:
    """The one model call in the research path is topic *selection*; naming the topics skips it.

    ``research_pack`` takes a snapshot and a list of agent ids and returns a pack — there is no
    model parameter to pass, no runtime to construct and nothing to await. That signature is the
    rule: if selection and assembly were one call, "``notes/summary.md`` before
    ``references/summary.md``" would be a property of a prompt rather than of a function, and
    unverifiable (PK-8).
    """
    first = research_pack(snapshot, topics=[COOKING])
    second = research_pack(snapshot, topics=[COOKING])

    assert [(e.path, e.text) for e in first.entries] == [(e.path, e.text) for e in second.entries]


def test_tag_subtree_entry_is_scoped_and_cites_the_real_file_pk9(snapshot: KbSnapshot) -> None:
    """The subtree is a *slice*, not the root ``tags.md``, and it still cites ``tags.md``.

    The real file holds every namespace in the knowledge base, so shipping it whole would put the
    trading vocabulary into a cooking pack and grow with the tree forever. Shipping a slice under
    an invented path would instead leave a consumer citing a file that does not exist. Hence a
    synthesized ``text`` at the real ``path``, with ``role`` carrying the distinction.
    """
    pack = research_pack(snapshot, topics=[COOKING])
    subtree = pack.entries[0]
    whole_file = (snapshot.root / "tags.md").read_text(encoding="utf-8")

    assert subtree.path == "tags.md"
    assert subtree.role == TAG_SUBTREE_ROLE
    assert "cooking" in subtree.text
    assert "trading" not in subtree.text
    assert "trading" in whole_file
    assert subtree.bytes == len(subtree.text.encode("utf-8"))


# --------------------------------------------------------------------------------------
# PK-10 — the implementation pack: depth, human rules first
# --------------------------------------------------------------------------------------


def assert_summary_precedes_everything(pack: Pack) -> None:
    """``notes/summary.md``, if the topic has one, is ahead of every other entry (PK-10)."""
    summaries = [
        i for i, entry in enumerate(pack.entries) if entry.path.endswith("notes/summary.md")
    ]
    if summaries:
        assert summaries[0] == 0, f"notes/summary.md is not first: {paths(pack)}"


def test_implementation_pack_ordering_is_golden_pk10(snapshot: KbSnapshot) -> None:
    """An implementation agent reads top-down and stops when full, so the order is a priority list.

    Human-approved rules first, then the map of what else exists, then the ingested sources, then
    reusable solutions. Invert any two of those and the agent that ran out of context has kept the
    wrong half: a pack that led with four ingested reference files would hand back an agent that
    read a book and never read the human's conclusions about it — and it would look like a
    perfectly full, perfectly successful pack.
    """
    pack = implementation_pack(snapshot, topic=COOKING)

    assert paths(pack) == [
        "Cooking/notes/summary.md",
        "Cooking/index.md",
        "Cooking/references/mcgee/mcgee.md",
        "Cooking/references/ruhlman/ruhlman.md",
        "Cooking/notes/sear.md",
    ]
    assert roles(pack) == [
        "notes-summary",
        "topic-index",
        "reference",
        "reference",
        "type.solution",
    ]
    assert pack.kind == "implementation"
    assert pack.scope == (COOKING,)


def test_notes_summary_is_first_even_as_an_empty_placeholder_pk10(tmp_path: Path) -> None:
    """ "First, always" has to survive the file being worthless, because that is when it is tested.

    A freshly scaffolded topic's ``notes/summary.md`` is a placeholder nobody has written yet. The
    tempting optimisation — skip it, or sink it below the real content — is exactly wrong: the slot
    is what tells an implementation agent that the human's rules for this topic are empty rather
    than merely absent from the pack, and the moment the human writes them the agent already reads
    them first without anything changing.
    """
    kb = tmp_path / "kb"
    kb.mkdir()
    scaffold_topic(kb, "Baking", title="Baking", description="Flour", today=TODAY)
    write_note(
        kb / "Baking" / "notes" / "summary.md",
        title="Notes summary",
        topic="Baking",
        tags=("topic.baking", "type.summary", "status.draft"),
        source_type="summary",
        body="",
    )
    write_note(
        kb / "Baking" / "references" / "hamelman" / "hamelman.md",
        title="Bread",
        topic="Baking",
        tags=("topic.baking", "type.reference", "status.approved"),
        source_type="reference",
    )
    regenerate_all(kb)

    pack = implementation_pack(scan(kb), topic="topic/baking")

    assert pack.entries[0].path == "Baking/notes/summary.md"
    assert "Body." not in pack.entries[0].text
    assert_summary_precedes_everything(pack)


def test_a_hand_made_topic_without_a_notes_summary_still_orders_correctly_pk10(
    snapshot: KbSnapshot,
) -> None:
    """A topic root is ``topic.md`` and nothing more; the rest of the structure is a convention.

    ``Ledger`` was created by hand and has no ``notes/`` directory at all. PK-10 is an *ordering*
    rule — ``notes/summary.md`` precedes everything else — not a guarantee that one exists, so
    assembly must degrade to the next slot rather than raise, invent a placeholder, or (worst)
    return an empty pack because the first mandatory entry was missing.
    """
    pack = implementation_pack(snapshot, topic=LEDGER)

    assert paths(pack) == ["Ledger/index.md"]
    assert_summary_precedes_everything(pack)
    assert pack.scope == (LEDGER,)


def test_sub_topics_are_excluded_unless_asked_for_pk10(snapshot: KbSnapshot) -> None:
    """README asks for the full index of *the selected topic*, and Q8 kept it that way.

    A four-sub-topic tree returns an order of magnitude more than the caller asked for, which is
    the context-window problem goal 2 exists to solve — and under a budget the extra material does
    not merely bloat the pack, it evicts the selected topic's own solution notes off the end.
    Turning the flag on must add the descendants *within* the existing ordering, not append a
    second pack after the first.
    """
    default = implementation_pack(snapshot, topic=COOKING)
    deep = implementation_pack(snapshot, topic=COOKING, include_subtopics=True)

    assert [p for p in paths(default) if "sub-topics" in p] == []
    assert [p for p in paths(deep) if "sub-topics" in p] == [
        "Cooking/sub-topics/Grilling/notes/summary.md",
        "Cooking/sub-topics/Grilling/index.md",
        "Cooking/sub-topics/Grilling/notes/fire.md",
    ]
    assert deep.scope == (COOKING, GRILLING)
    assert_summary_precedes_everything(deep)


def test_a_file_selected_twice_is_read_once_at_its_highest_priority_pk10(tmp_path: Path) -> None:
    """The ordering rules overlap, and deduplication is what makes them compose.

    A ``notes/summary.md`` the human has flagged ``status.conflict-review`` qualifies for two slots:
    the breadth slot near the top and the conflict block at the bottom. Emitting it twice would
    double its cost against the budget and tell the consumer the knowledge base holds two files;
    emitting it only in the *later* slot would violate §1.7's ordering. Keeping the first
    occurrence is the rule that makes both the priority list and the conflict sweep safe to state
    independently.
    """
    kb = tmp_path / "kb"
    kb.mkdir()
    scaffold_topic(kb, "Baking", title="Baking", description="Flour", today=TODAY)
    write_note(
        kb / "Baking" / "notes" / "summary.md",
        title="Notes summary",
        topic="Baking",
        tags=("topic.baking", "type.summary", CONFLICT_TAG),
        source_type="summary",
        review_note="Two hydration rules disagree.",
    )
    regenerate_all(kb)

    pack = research_pack(scan(kb), topics=["topic/baking"])

    assert paths(pack).count("Baking/notes/summary.md") == 1
    assert paths(pack) == [
        "tags.md",
        "Baking/topic.md",
        "Baking/notes/summary.md",
        "Baking/references/summary.md",
    ]
    assert [e.path for e in pack.escalation] == ["Baking/notes/summary.md"]


# --------------------------------------------------------------------------------------
# PK-11 — the budget: a prefix, whole entries, and a named remainder
# --------------------------------------------------------------------------------------


def test_a_budget_keeps_a_whole_prefix_and_names_the_rest_pk11(snapshot: KbSnapshot) -> None:
    """A silently clipped pack is worse than a short one: the consumer cannot tell.

    Packs are the one result that can be arbitrarily large — forty ingested references produce an
    implementation pack no context window holds — so truncation is inevitable and the only question
    is whether it is visible. Three properties together make it visible: what arrived is a prefix
    of what would have arrived (so the priority order still means what it says), every entry is the
    whole file (so nothing the consumer reads is a half-truth it cannot detect), and everything
    dropped is named with a reason (so the consumer knows to ask for more rather than concluding the
    knowledge base is empty on the subject).
    """
    full = implementation_pack(snapshot, topic=COOKING)
    budget = sum(e.bytes for e in full.entries[:2]) + 5

    clipped = implementation_pack(snapshot, topic=COOKING, budget_bytes=budget)

    assert paths(clipped) == paths(full)[:2]
    assert clipped.truncated is True
    assert clipped.total_bytes <= budget
    assert [o.path for o in clipped.omitted] == paths(full)[2:]
    assert {o.reason for o in clipped.omitted} == {"budget"}
    for entry in clipped.entries:
        on_disk = (snapshot.root / entry.path).read_bytes()
        assert entry.text.encode("utf-8") == on_disk
        assert entry.bytes == len(on_disk)


def test_the_budget_never_skips_ahead_to_fit_a_smaller_later_file_pk11(
    snapshot: KbSnapshot,
) -> None:
    """Best-fit packing would silently reorder the pack, and the consumer reads top-down.

    Once ``index.md`` does not fit, the two smaller reference files that *would* fit in the
    remaining space are dropped anyway. That looks wasteful and is not: the ordering is the
    priority, so admitting a low-priority file over a high-priority one hands the consumer a pack
    whose order no longer expresses what the knowledge base thinks matters — and it has no way to
    notice, because prefixes and best-fit results are indistinguishable from the inside.
    """
    full = implementation_pack(snapshot, topic=COOKING)
    summary, index, first_reference = full.entries[0], full.entries[1], full.entries[2]
    budget = summary.bytes + index.bytes - 1
    assert first_reference.bytes < index.bytes, (
        "fixture must have a smaller entry after the big one"
    )

    clipped = implementation_pack(snapshot, topic=COOKING, budget_bytes=budget)

    assert paths(clipped) == [summary.path]
    assert [o.path for o in clipped.omitted] == paths(full)[1:]
    assert first_reference.bytes < budget - summary.bytes


def test_a_budget_that_fits_everything_omits_nothing_pk11(snapshot: KbSnapshot) -> None:
    """A budget is a ceiling, not a resize: an unpressured pack is the unbudgeted pack exactly.

    If a generous budget changed anything — dropped a trailing entry, reordered to pack tighter —
    a caller could not use ``truncated`` to decide whether to ask for more, and every consumer
    would have to re-derive completeness for itself.
    """
    full = implementation_pack(snapshot, topic=COOKING)

    generous = implementation_pack(snapshot, topic=COOKING, budget_bytes=full.total_bytes)

    assert paths(generous) == paths(full)
    assert generous.omitted == ()
    assert generous.truncated is False


def test_the_research_budget_can_drop_the_conflict_block_but_says_so_pk11(
    snapshot: KbSnapshot,
) -> None:
    """The most dangerous omission is the one at the bottom, so it is the one that must be named.

    Conflict-review notes sit last in a research pack, which makes them the first casualty of a
    budget — and an agent that never learns the material is contested is exactly the failure the
    conflict block exists to prevent. The pack cannot afford to keep it, but it can afford to say
    it dropped it, and the escalation list is unaffected by the budget precisely so the consumer
    still has to stop.
    """
    full = research_pack(snapshot, topics=[COOKING])
    budget = full.total_bytes - full.entries[-1].bytes

    clipped = research_pack(snapshot, topics=[COOKING], budget_bytes=budget)

    assert "Cooking/notes/salt.md" not in paths(clipped)
    assert [(o.path, o.reason) for o in clipped.omitted] == [("Cooking/notes/salt.md", "budget")]
    assert [e.path for e in clipped.escalation] == ["Cooking/notes/salt.md"]


def test_entry_bytes_count_the_bytes_a_consumer_receives_even_for_crlf_pk11(
    tmp_path: Path,
) -> None:
    """The byte count is a budget input, so it has to be the file's bytes and not Python's view.

    Reading with universal newlines turns every ``\\r\\n`` into ``\\n``, so a CRLF file measures
    one byte short per line — a 400-line note under-reports by 400 bytes. The budget would then
    admit entries that do not fit, and the whole point of PK-11 (a pack the consumer can trust to
    fit) fails quietly on exactly the files most likely to come from an outside source. ``newline=""``
    in the reader is what prevents it; this test is what notices if it is removed.
    """
    kb = tmp_path / "kb"
    kb.mkdir()
    scaffold_topic(kb, "Baking", title="Baking", description="Flour", today=TODAY)
    write_note(
        kb / "Baking" / "notes" / "windows.md",
        title="Imported from Windows",
        topic="Baking",
        tags=("topic.baking", "type.solution", "status.approved"),
        source_type="solution",
        body="One.\nTwo.\nThree.\n",
        newline="\r\n",
    )
    regenerate_all(kb)
    on_disk = (kb / "Baking" / "notes" / "windows.md").read_bytes()
    assert b"\r\n" in on_disk

    pack = implementation_pack(scan(kb), topic="topic/baking")
    entry = next(e for e in pack.entries if e.path.endswith("windows.md"))

    assert entry.bytes == len(entry.text.encode("utf-8"))
    assert entry.bytes == len(on_disk)
    assert entry.text.encode("utf-8") == on_disk


# --------------------------------------------------------------------------------------
# PK-12 — read-only, and derived from Layer 1's surface rather than a second walk
# --------------------------------------------------------------------------------------


def tree_fingerprint(kb: Path) -> dict[str, tuple[str, int]]:
    """Every file's content hash and mtime, so "read-only" is checked and not assumed."""
    out: dict[str, tuple[str, int]] = {}
    for path in sorted(kb.rglob("*")):
        if path.is_file():
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            out[path.relative_to(kb).as_posix()] = (digest, path.stat().st_mtime_ns)
    return out


def test_building_both_packs_leaves_the_tree_byte_identical_pk12(kb: Path) -> None:
    """Assembly is a read. Anything else makes reading the knowledge base change it.

    A pack that stamped ``last_reviewed``, appended a tag, regenerated an index or bumped an
    ``updated`` line would mean that an external agent merely *looking* at a topic produces a diff
    the human has to review and a conflict scan the daemon has to run. mtimes are compared as well
    as bytes because a rewrite with identical content is still a write: it defeats every downstream
    "has this changed" check the maintenance pass makes.
    """
    before = tree_fingerprint(kb)
    snapshot = scan(kb)

    research_pack(snapshot, topics=[COOKING, TRADING, LEDGER], include_index=True)
    implementation_pack(snapshot, topic=COOKING, include_subtopics=True)
    implementation_pack(snapshot, topic=COOKING, budget_bytes=10)

    assert tree_fingerprint(kb) == before


def test_membership_comes_from_the_snapshot_and_not_a_second_walk_pk12(kb: Path) -> None:
    """One walk, shared. A pack that re-walks the tree is reading a different knowledge base.

    Layer 1's snapshot is the single deterministic view every consumer shares (decision C); a pack
    assembled from a fresh walk would see whatever the tree looked like a few milliseconds later —
    a half-written note from a concurrent turn, an index the flush has not regenerated yet — and
    would disagree with the validation and the tag registry computed from the same request. Here a
    note appears on disk *after* the snapshot: it must not be in the pack, because it is not in the
    view the pack was asked to slice.
    """
    snapshot = scan(kb)
    write_note(
        kb / "Cooking" / "notes" / "late.md",
        title="Written after the scan",
        topic="Cooking",
        tags=("topic.cooking", "type.solution", "status.approved"),
        source_type="solution",
    )

    pack = implementation_pack(snapshot, topic=COOKING)

    assert "Cooking/notes/late.md" not in paths(pack)
    assert "Cooking/notes/late.md" in paths(implementation_pack(scan(kb), topic=COOKING))


def test_no_module_below_or_above_packs_walks_the_tree_itself_pk12() -> None:
    """Layer 1 owns the walk; a second one anywhere is a second, divergent answer.

    ``pkb.core`` classifies files, decides what a topic root is, honours the dot-prefix rules that
    keep ``.inbox`` invisible, and applies PA-5's ordering. Any ``rglob`` or ``os.walk`` outside it
    reimplements a subset of that and gets a different tree — most cheaply by picking up ``.inbox``
    staging files, which by construction are not knowledge. This greps the pack leaf and every
    Layer 3 module, which are the ones that hold a ``kb_root`` and could be tempted.
    """
    walker = re.compile(r"os\.walk\(|\.rglob\(|\.glob\(|\.iterdir\(|\.walk\(")
    scanned = [
        REPO_ROOT / "src" / "pkb" / "packs.py",
        REPO_ROOT / "src" / "pkb" / "contracts.py",
        REPO_ROOT / "src" / "pkb" / "daemon.py",
        *sorted((REPO_ROOT / "src" / "pkb" / "service").rglob("*.py")),
        *sorted((REPO_ROOT / "src" / "pkb" / "server").rglob("*.py")),
    ]
    assert len(scanned) > 5, "the grep found nothing to grep — check the paths"

    offenders = [
        f"{path.relative_to(REPO_ROOT)}:{number}: {line.strip()}"
        for path in scanned
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
        if walker.search(line)
    ]
    assert offenders == [], f"a tree walk outside pkb.core: {offenders}"


@pytest.mark.superseded
def test_assembly_schedules_no_conflict_scan_pk12(kb: Path) -> None:
    """A pack is not a change, so it must not schedule the work a change schedules.

    ``ScanRequest`` is how Layer 1 says a topic's content moved and a conflict scan is owed — and
    every one of those is a model-backed whole-topic comparison. If merely reading a topic produced
    one, an external agent polling for context would generate an unbounded stream of expensive
    scans over a knowledge base nobody edited, indistinguishable in the log from real editing. The
    changed set is derived from the tree itself rather than asserted on the pack object, so a future
    side effect that writes through some other path is caught too.

    Superseded by T-41 (Phase 5 rebuilds this): ``pkb.core.maintenance.build_scan_requests`` — the
    automatic, changed-set-to-requests builder this test called — is retired outright, not merely
    left uncalled by ``flush``, so there is nothing left in Layer 1 to assert "schedules none of"
    against. The half of this rule that still holds mechanically, "a pack does not write", is
    covered by the ``changed == []`` assertion on its own; the scan-scheduling half is Layer 2/3's
    to reassert once a replacement (built on the surviving ``scan_request_for``) exists.
    """
    before = tree_fingerprint(kb)
    snapshot = scan(kb)

    research_pack(snapshot, topics=[COOKING, TRADING], include_index=True)
    implementation_pack(snapshot, topic=COOKING, include_subtopics=True)

    after = tree_fingerprint(kb)
    changed = [path for path, stamp in after.items() if before.get(path) != stamp]
    assert changed == []


# --------------------------------------------------------------------------------------
# MC-20's trigger — escalation is computed from the tag, and it self-clears
# --------------------------------------------------------------------------------------


@pytest.mark.superseded
def test_escalations_report_the_review_note_verbatim_mc20(snapshot: KbSnapshot) -> None:
    """The caller is a program that has to stop, and the human's note is why.

    An escalation is a *successful* result with a discriminator, not an error, because a
    well-behaved agent retries errors and a retried escalation is an escalation ignored. What makes
    it actionable is the human's own ``review_note``: paraphrasing or summarising it would put a
    machine's reading of the disagreement in front of the human's, which is the one thing README
    Part 4 does not allow a conflict to do.
    """
    found = escalations(snapshot, [COOKING])

    assert [e.path for e in found] == ["Cooking/notes/salt.md"]
    assert found[0].review_note == REVIEW_NOTE
    assert found[0].agent_id == COOKING


def test_a_sibling_topic_is_unaffected_by_the_conflict_mc20(snapshot: KbSnapshot) -> None:
    """The trigger is the tag intersected with *the participating topics' subtrees*.

    A knowledge base with one contested note anywhere in it would otherwise stop every agent
    everywhere. Scoping is what keeps escalation a signal rather than a permanent global halt —
    and it is why the intersection is computed from ``files_with_tag`` and topic ownership rather
    than from a flag on the knowledge base.
    """
    assert escalations(snapshot, [TRADING]) == ()
    assert research_pack(snapshot, topics=[TRADING]).escalation == ()
    assert implementation_pack(snapshot, topic=TRADING).escalation == ()
    assert research_pack(snapshot, topics=[COOKING, TRADING]).escalation != ()


def test_escalation_clears_itself_when_the_human_resolves_the_tag_mc20(kb: Path) -> None:
    """Nothing acknowledges an escalation; resolving the tag is the acknowledgement.

    The trigger is recomputed from the tree on every request, so there is no escalation state to
    get stuck, no queue to drain and nothing for an agent to mark as read. The human moves the tag
    to ``status.approved`` and the next pack is a normal pack — which also means an agent cannot
    dismiss a conflict the human has not resolved.
    """
    assert escalations(scan(kb), [COOKING]) != ()

    write_note(
        kb / "Cooking" / "notes" / "salt.md",
        title="Salting",
        topic="Cooking",
        tags=("topic.cooking", "type.note", "status.approved"),
        source_type="note",
    )
    regenerate_all(kb)
    resolved = scan(kb)

    assert escalations(resolved, [COOKING]) == ()
    assert research_pack(resolved, topics=[COOKING]).escalation == ()
    assert "Cooking/notes/salt.md" not in paths(research_pack(resolved, topics=[COOKING]))


@pytest.mark.superseded
def test_escalation_is_computed_from_the_tag_not_from_the_pack_contents_mc20(
    snapshot: KbSnapshot,
) -> None:
    """A conflict must stop the caller even when the contested file is not in the pack.

    An implementation pack over Cooking carries neither ``salt.md`` (it is a plain note, not a
    solution) nor any hint that it exists — yet the topic it slices is contested. If the escalation
    were derived from what the pack happens to contain, the caller would proceed confidently over
    material that the knowledge base itself says is unsettled, which is precisely the confident
    nonsense MC-20 exists to prevent.
    """
    pack = implementation_pack(snapshot, topic=COOKING)

    assert "Cooking/notes/salt.md" not in paths(pack)
    assert [e.path for e in pack.escalation] == ["Cooking/notes/salt.md"]
    assert pack.escalation[0].review_note == REVIEW_NOTE


# --------------------------------------------------------------------------------------
# Resolution — an id that does not resolve is a refusal, never a guess
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize("unknown", ["topic/cookery", "topic/cooking/grillling", "cooking", ""])
def test_an_unresolvable_agent_id_is_refused_by_name_mc16(
    snapshot: KbSnapshot, unknown: str
) -> None:
    """A pack silently built for the nearest topic is indistinguishable from a correct one.

    ``topic/cookery`` is one letter from ``topic/cooking``, which is exactly why fuzzy resolution is
    forbidden: the caller gets a full, well-ordered, entirely plausible pack about the wrong subject
    and has no signal that anything went wrong. Refusing by name is recoverable — the caller can
    list topics and retry — and the message carries the id it was given, so a typo in a config file
    is visible in the log rather than inferred from the answers.
    """
    with pytest.raises(UnknownTopicError) as caught:
        implementation_pack(snapshot, topic=unknown)
    assert repr(unknown) in str(caught.value)

    with pytest.raises(UnknownTopicError) as research_caught:
        research_pack(snapshot, topics=[COOKING, unknown])
    assert repr(unknown) in str(research_caught.value)

    with pytest.raises(UnknownTopicError):
        escalations(snapshot, [unknown])


def test_the_refusal_names_the_ids_that_do_resolve_and_is_a_value_error_mc16(
    snapshot: KbSnapshot,
) -> None:
    """Below the seam there are no status codes, so the error has to carry its own repair.

    ``UnknownTopicError`` is a plain ``ValueError`` subclass because ``pkb.packs`` knows nothing
    about HTTP or MCP — the transport that has a wire to answer maps it. What the module *can* do is
    make the refusal self-service by listing the ids that exist, so the caller does not need a
    second round trip to find out what it should have asked for.
    """
    with pytest.raises(UnknownTopicError) as caught:
        research_pack(snapshot, topics=["topic/cookery"])

    message = str(caught.value)
    assert isinstance(caught.value, ValueError)
    for known in (COOKING, GRILLING, LEDGER, TRADING):
        assert known in message
