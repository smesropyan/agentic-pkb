"""Rules MA-1 … MA-15 — the once-per-turn flush.

Every test names the rule it covers, because a rule that changes must change a test that cites it.
The load-bearing one is :func:`test_flush_changes_only_the_updated_line_of_touched_files_ma5`: it
is the property a human with no undo is really relying on (arch D6).
"""

from __future__ import annotations

import ast
import re
from collections.abc import Callable, Mapping, Sequence
from datetime import date
from pathlib import Path

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

import pkb.core
from pkb.core import generators, maintenance, paths
from pkb.core.errors import NotATopicRootError, Severity
from pkb.core.generators import regenerate_all
from pkb.core.maintenance import (
    MAINTENANCE_ORIGIN,
    ON_DEMAND_ORIGIN,
    build_scan_requests,
    bump_updated,
    find_broken_links,
    find_orphans,
    flush,
    scan_request_for,
)
from pkb.core.models import FlushReport, KbSnapshot
from pkb.core.scaffold import scaffold_topic
from pkb.core.scan import scan
from tests.core.conftest import SAMPLE_KB_FILES, write_kb

TODAY = date(2025, 3, 4)
GOLDEN = Path(__file__).parent / "golden"
SOURCE = Path(maintenance.__file__).read_text(encoding="utf-8")
"""``maintenance.py`` alone — MA-15's no-lock assertion is about this module, not the package."""

CORE_SOURCES = sorted(Path(pkb.core.__file__).parent.rglob("*.py"))
"""Every module of ``pkb.core``, including ``generators/`` — what CX-3 and CX-4 grep over."""


def _imported_modules(path: Path) -> set[str]:
    """Every module name a source file imports, from its parsed AST (CX-1, CX-4)."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            names.add(node.module)
    return names


_ISO_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")


# --------------------------------------------------------------------------------------
# Fixture helpers
# --------------------------------------------------------------------------------------


def authored(
    title: str,
    *,
    topic: str = "Cooking",
    tag: str = "topic.cooking",
    type_tag: str = "type.note",
    source_type: str = "note",
    created: str = "2024-01-01",
    updated: str = "2024-01-02",
    body: str = "Nothing to see here.\n",
    extra: str = "",
) -> str:
    """One authored markdown file in canonical FM-7/FM-8 form."""
    return (
        "---\n"
        f'title: "{title}"\n'
        f'description: "A description of {title}"\n'
        f'topic: "{topic}"\n'
        "tags:\n"
        f"  - {tag}\n"
        f"  - {type_tag}\n"
        "  - status.draft\n"
        f"created: {created}\n"
        f"updated: {updated}\n"
        f"source_type: {source_type}\n"
        f"{extra}"
        "---\n"
        "\n"
        f"# {title}\n"
        "\n"
        f"{body}"
    )


def topic_files(name: str, *, tag: str | None = None) -> dict[str, str]:
    """The three files every topic root carries (SC-1), keyed by knowledge-base path."""
    slug = tag or f"topic.{paths.slugify(name)}"
    common = {"topic": name, "tag": slug, "type_tag": "type.summary", "source_type": "summary"}
    return {
        f"{name}/topic.md": authored(name, **common),
        f"{name}/notes/summary.md": authored("Notes summary", **common),
        f"{name}/references/summary.md": authored("References summary", **common),
    }


def kb_with(root: Path, extra: Mapping[str, str] | None = None) -> Path:
    """A one-topic knowledge base plus whatever the test needs on top."""
    return write_kb(root / "KB", {**topic_files("Cooking"), **(extra or {})})


def content_bytes(root: Path, files: Mapping[str, str]) -> dict[str, bytes]:
    """The on-disk bytes of every authored file — the baseline a mutation test compares against."""
    return {relative: (root / relative).read_bytes() for relative in files}


def changed_lines(before: bytes, after: bytes) -> list[str]:
    """The lines that differ between two versions of one file, as they read afterwards."""
    old = before.decode("utf-8").splitlines()
    new = after.decode("utf-8").splitlines()
    assert len(old) == len(new), "a stamped file must not gain or lose lines"
    return [line for original, line in zip(old, new, strict=True) if original != line]


# --------------------------------------------------------------------------------------
# MA-1, MA-2 — the six duties and their order
# --------------------------------------------------------------------------------------


@pytest.mark.superseded
def test_flush_performs_the_six_duties_in_order_ma1(
    sample_kb: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A spy records every sub-operation the flush is defined as (MA-1) and their order (MA-2)."""
    calls: list[str] = []

    def spy(label: str, attribute: str) -> None:
        real = getattr(maintenance, attribute)

        def wrapper(*args: object, **kwargs: object) -> object:
            calls.append(label)
            return real(*args, **kwargs)

        monkeypatch.setattr(maintenance, attribute, wrapper)

    spy("bump", "_bump_updated")
    spy("links", "find_broken_links")
    spy("orphans", "find_orphans")
    spy("regenerate", "regenerate_all")
    spy("requests", "build_scan_requests")

    flush(sample_kb, ["Cooking/notes/summary.md"], today=TODAY)

    assert calls == ["bump", "links", "orphans", "regenerate", "requests"]


@pytest.mark.superseded
def test_flush_regenerates_every_derived_file_ma1(sample_kb: Path) -> None:
    """Duties 1-3: the topic indexes, the root registry and the root catalog all get written."""
    report = flush(sample_kb, today=TODAY)

    assert sorted(report.derived) == sorted(
        [
            "BBQ/index.md",
            "Cooking/index.md",
            "Cooking/sub-topics/Grilling/index.md",
            "index.md",
            "tags.md",
        ]
    )
    assert (sample_kb / "Cooking/index.md").read_bytes() == (
        GOLDEN / "cooking_index.md"
    ).read_bytes()


def test_timestamps_are_bumped_before_anything_is_rendered_ma2(tmp_path: Path) -> None:
    """The flush's own order corrects arch §7: no derived file can describe the pre-bump state."""
    note = "Cooking/notes/steak.md"
    root = kb_with(tmp_path, {note: authored("Steak", updated="2024-01-02")})
    seen: list[str] = []

    def watching_scan(kb_root: Path) -> KbSnapshot:
        snapshot = scan(kb_root)
        record = snapshot.files[note]
        assert record.meta is not None
        seen.append(str(record.meta.updated))
        return snapshot

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(maintenance, "scan", watching_scan)
        flush(root, [note], today=TODAY)

    assert seen == [TODAY.isoformat()], "the walk that feeds the generators must see the new date"


def test_no_derived_file_carries_a_date_ma2(sample_kb: Path) -> None:
    """Belt and braces for the ordering bug: derived output renders no dates at all (GE-6)."""
    report = flush(sample_kb, ["Cooking/notes/summary.md"], today=TODAY)

    for relative in report.derived:
        assert not _ISO_DATE.search((sample_kb / relative).read_text(encoding="utf-8"))


# --------------------------------------------------------------------------------------
# MA-3, MA-4, MA-5 — what a flush may write
# --------------------------------------------------------------------------------------


def test_flush_over_an_untouched_tree_rewrites_no_content_file_ma3(sample_kb: Path) -> None:
    """Scan-and-stamp is forbidden: with an empty touched set every content file is untouched."""
    before = content_bytes(sample_kb, SAMPLE_KB_FILES)

    report = flush(sample_kb, today=TODAY)

    assert report.stamped == []
    assert content_bytes(sample_kb, SAMPLE_KB_FILES) == before


def test_bump_updated_rewrites_only_the_named_paths_ma3(sample_kb: Path) -> None:
    """Only the explicit set is considered, and only its ``updated`` line changes."""
    touched = "Cooking/notes/preheat-the-grill.md"
    before = content_bytes(sample_kb, SAMPLE_KB_FILES)

    stamped = bump_updated(sample_kb, [touched], today=TODAY)

    assert stamped == [touched]
    after = content_bytes(sample_kb, SAMPLE_KB_FILES)
    assert {p for p in after if after[p] != before[p]} == {touched}
    assert changed_lines(before[touched], after[touched]) == [f"updated: {TODAY.isoformat()}"]


def test_bumping_twice_on_the_same_day_is_a_no_op_ma3(tmp_path: Path) -> None:
    """The second call renders identical bytes, so it writes nothing and reports nothing."""
    note = "Cooking/notes/steak.md"
    root = kb_with(tmp_path, {note: authored("Steak")})

    assert bump_updated(root, [note], today=TODAY) == [note]
    stamp = (root / note).stat().st_mtime_ns

    assert bump_updated(root, [note], today=TODAY) == []
    assert (root / note).stat().st_mtime_ns == stamp


@pytest.mark.parametrize(
    ("relative", "content"),
    [
        ("Cooking/index.md", '---\ntitle: "x"\nsource_type: index\n---\n'),
        ("skills/voice/SKILL.md", "---\nname: voice\ndescription: how to write\n---\n"),
        ("Cooking/notes/old/media/photo.jpg", "not text\n"),
        ("Cooking/notes/plain.md", "# No frontmatter here\n"),
    ],
    ids=["derived", "skill", "asset", "no-frontmatter"],
)
def test_bump_updated_never_stamps_a_non_authored_file_ma3(
    tmp_path: Path, relative: str, content: str
) -> None:
    """``updated`` is not part of a derived, skill, media or frontmatter-less file's schema."""
    root = kb_with(tmp_path, {relative: content})
    before = (root / relative).read_bytes()

    assert bump_updated(root, [relative], today=TODAY) == []
    assert (root / relative).read_bytes() == before


def test_bump_updated_ignores_paths_outside_the_knowledge_base_ma3(
    tmp_path: Path, sample_kb: Path
) -> None:
    """A path the middleware mis-translated must not raise: the flush runs after failed turns."""
    outsider = tmp_path / "elsewhere.md"
    outsider.write_text(authored("Outside"), encoding="utf-8")

    assert bump_updated(sample_kb, [outsider, "no/such/file.md"], today=TODAY) == []
    assert outsider.read_text(encoding="utf-8") == authored("Outside")


def test_the_clock_is_injected_never_read_ma3() -> None:
    """No wall-clock call anywhere: the same tree and the same ``today`` give the same bytes."""
    assert "date.today" not in SOURCE
    assert "datetime.now" not in SOURCE
    assert "time.time" not in SOURCE


def test_stamping_preserves_line_endings_and_the_body_ma5(tmp_path: Path) -> None:
    """A CRLF file stays CRLF and a missing ``updated`` is inserted in canonical order (FM-11)."""
    note = "Cooking/notes/crlf.md"
    original = b'---\r\ntitle: "CRLF"\r\ncreated: 2024-01-01\r\n---\r\n\r\n# CRLF\r\n\r\nBody.\r\n'
    expected = (
        b'---\r\ntitle: "CRLF"\r\ncreated: 2024-01-01\r\n'
        b"updated: 2025-03-04\r\n---\r\n\r\n# CRLF\r\n\r\nBody.\r\n"
    )
    root = kb_with(tmp_path)
    (root / note).write_bytes(original)

    assert bump_updated(root, [note], today=TODAY) == [note]

    after = (root / note).read_bytes()
    assert after == expected
    assert after.count(b"\n") == after.count(b"\r\n"), "no line ending was rewritten to LF"


_UNREADABLE_FENCES: Mapping[str, bytes] = {
    "trailing-prose": b"---\nupdated: 2026-08-05\nSteak searing, part 1\n---\n\nReverse sear.\n",
    "thematic-break": b"---\nSteak searing, part 1\n---\n\nReverse sear at 250F.\n",
    "sequence": b"---\n- one\n- two\n---\n\nBody.\n",
    "half-written": b'---\ntitle: "Half"\nTODO finish this frontmatter\n---\n\n# Half\n',
    "flow-mapping": b"---\n{title: X, updated: 2026-08-05}\n---\n\nBody.\n",
}
"""Leading fences a targeted field write must refuse: four that are not mappings, and one that is
a mapping whose keys own no line of their own (FM-11)."""


@pytest.mark.parametrize("name", sorted(_UNREADABLE_FENCES), ids=sorted(_UNREADABLE_FENCES))
def test_a_flush_never_writes_into_a_fence_it_cannot_read_fm11_ma5(
    tmp_path: Path, name: str
) -> None:
    """A stamp is a *targeted* write or it is nothing (FM-11, MA-5, MA-3, §5).

    The failure this pins is not the inserted line — it is the one after it. ``set_field``'s replace
    branch used to attribute every following non-key line to the preceding key, so the *second*
    flush of the same day swallowed the human's ``Steak searing, part 1`` while rewriting
    ``updated``. There is no undo (arch D6), so §5's "never overwrite human content" makes this an
    error rather than a cosmetic defect. The guard lives in ``frontmatter`` — this test states the
    property from the flush's side, where the write is actually issued.
    """
    relative = f"Cooking/notes/{name}.md"
    root = kb_with(tmp_path)
    (root / relative).write_bytes(_UNREADABLE_FENCES[name])

    first = flush(root, [relative], today=TODAY)
    assert (root / relative).read_bytes() == _UNREADABLE_FENCES[name]
    assert first.stamped == []

    # MA-3: the same day twice is indistinguishable from once, refusal included.
    second = flush(root, [relative], today=TODAY)
    assert (root / relative).read_bytes() == _UNREADABLE_FENCES[name]
    assert second.stamped == []


def test_a_fence_that_is_a_mapping_is_still_stamped_ma5(tmp_path: Path) -> None:
    """The negative half of the guard: refusing every fence would also satisfy the test above.

    A pasted YAML snippet under a leading ``---`` *is* frontmatter by FM-1, however little the
    human meant it to be; ``updated`` is the one field MA-5 permits, and FM-10 keeps the unknown
    keys and their block style intact around it.
    """
    relative = "Cooking/notes/pasted.md"
    original = b'---\nversion: "3"\nservices:\n  web:\n    image: nginx\n---\n\nRun it.\n'
    stamped = (
        b'---\nupdated: 2025-03-04\nversion: "3"\nservices:\n  web:\n    image: nginx\n'
        b"---\n\nRun it.\n"
    )
    root = kb_with(tmp_path)
    (root / relative).write_bytes(original)

    assert flush(root, [relative], today=TODAY).stamped == [relative]

    assert (root / relative).read_bytes() == stamped
    assert flush(root, [relative], today=TODAY).stamped == []


def test_created_is_immutable_ma4(sample_kb: Path) -> None:
    """No maintenance operation may move ``created`` — it is the file's birth date, forever."""
    note = "Cooking/notes/old-idea/old-idea.md"
    created = "created: 2024-08-20"
    assert created in (sample_kb / note).read_text(encoding="utf-8")

    flush(sample_kb, [note], today=TODAY)

    assert created in (sample_kb / note).read_text(encoding="utf-8")


_TOPIC_NAME = st.sampled_from(["Cooking", "Physics", "Baking"])
_ITEM_NAME = st.sampled_from(["alpha", "beta", "gamma"])


@given(
    topics=st.lists(_TOPIC_NAME, min_size=1, max_size=3, unique=True),
    items=st.lists(_ITEM_NAME, max_size=3, unique=True),
    picks=st.lists(st.integers(min_value=0, max_value=20), max_size=5),
    day=st.dates(min_value=date(2024, 1, 1), max_value=date(2030, 12, 31)),
)
@settings(max_examples=30, deadline=None, suppress_health_check=[HealthCheck.too_slow])
def test_flush_changes_only_the_updated_line_of_touched_files_ma5(
    tmp_path_factory: pytest.TempPathFactory,
    topics: list[str],
    items: list[str],
    picks: list[int],
    day: date,
) -> None:
    """The guarantee this layer offers a human with no undo (arch D6).

    Over a generated valid knowledge base, one flush changes at most the ``updated`` line of the
    files the caller named, plus the derived files it owns outright. Nothing else — no other field,
    no tag, no body byte, no untouched file.
    """
    files: dict[str, str] = {}
    for name in topics:
        files.update(topic_files(name))
        for item in items:
            files[f"{name}/notes/{item}.md"] = authored(
                item.title(), topic=name, tag=f"topic.{paths.slugify(name)}"
            )

    root = write_kb(tmp_path_factory.mktemp("kb") / "KB", files)
    content = sorted(files)
    touched = sorted({content[pick % len(content)] for pick in picks})
    before = content_bytes(root, files)

    report = flush(root, touched, today=day)

    after = content_bytes(root, files)
    for relative, original in before.items():
        if after[relative] == original:
            continue
        assert relative in touched, f"{relative} was rewritten without being touched"
        assert changed_lines(original, after[relative]) == [f"updated: {day.isoformat()}"]
    assert set(report.stamped) <= set(touched)


# --------------------------------------------------------------------------------------
# MA-6 — Layer 1 has no opinion about status
# --------------------------------------------------------------------------------------


def test_the_only_frontmatter_field_ever_written_is_updated_ma6() -> None:
    """Static proof: every targeted field write in this module names ``updated``, nothing else."""
    written = [
        ast.literal_eval(call.args[1])
        for call in ast.walk(ast.parse(SOURCE))
        if isinstance(call, ast.Call)
        and isinstance(call.func, ast.Attribute)
        and call.func.attr in {"set_field", "remove_field"}
    ]

    assert written == ["updated"]


@pytest.mark.superseded
def test_flush_leaves_a_conflicted_files_tags_byte_identical_ma6(sample_kb: Path) -> None:
    """Conflict tagging is Layer 2's judgment; clearing it is the human's decision."""
    conflicted = "Cooking/notes/preheat-the-grill.md"
    before = (sample_kb / conflicted).read_text(encoding="utf-8")

    flush(sample_kb, [conflicted], today=TODAY)

    after = (sample_kb / conflicted).read_text(encoding="utf-8")
    assert "status.conflict-review" in after
    assert after.count("review_note:") == 1
    assert [line for line in after.splitlines() if not line.startswith("updated:")] == [
        line for line in before.splitlines() if not line.startswith("updated:")
    ]


# --------------------------------------------------------------------------------------
# MA-7 — broken links
# --------------------------------------------------------------------------------------

_LINK_KB: Mapping[str, str] = {
    **topic_files("Cooking"),
    "Cooking/notes/Heat Management.md": authored("Heat Management"),
    "Cooking/notes/steak/steak.md": authored("Steak"),
    "Cooking/notes/old/other.md": authored("Other"),
    "Cooking/references/grill-basics/grill-basics.md": authored(
        "Grill Basics", type_tag="type.reference", source_type="reference"
    ),
    "Cooking/references/grill-basics/grill-basics.pdf": "binary placeholder\n",
}


def link_kb(root: Path, body: str) -> Path:
    """The link fixture plus one note whose body is ``body``."""
    return write_kb(
        root / "KB", {**_LINK_KB, "Cooking/notes/linker.md": authored("Linker", body=body)}
    )


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        ("See the [guide](../references/missing.md).\n", ["BROKEN_LINK"]),
        ("See the [summary](summary.md).\n", []),
        ("See [the web](https://example.invalid/page).\n", []),
        ("Mail [someone](mailto:nobody@example.invalid).\n", []),
        ("See [a section](summary.md#heading).\n", []),
        ("See [above](#heading).\n", []),
        ("See [outside](../../../elsewhere.md).\n", ["LINK_ESCAPES_KB_ROOT"]),
        ("See [absolute](/etc/passwd).\n", ["LINK_ESCAPES_KB_ROOT"]),
        ("![pdf](../references/grill-basics/grill-basics.pdf)\n", []),
        ("![photo](media/missing.png)\n", ["BROKEN_LINK"]),
        ("See the [steak note](steak/).\n", []),
        ("See the [old item](old/).\n", ["BROKEN_LINK"]),
        ("See [heat](Heat%20Management.md).\n", []),
        ("See [the index](../index.md).\n", []),
        ("See [it][guide].\n\n[guide]: ../references/missing.md\n", ["BROKEN_LINK"]),
        ("```markdown\n[example](nowhere.md)\n```\n", []),
    ],
    ids=[
        "missing-relative",
        "existing-sibling",
        "http-scheme",
        "mailto-scheme",
        "fragment-stripped",
        "bare-anchor",
        "escapes-root",
        "absolute-path",
        "image-embed-ok",
        "image-embed-missing",
        "folder-hosted-item",
        "folder-without-main-file",
        "percent-encoded",
        "not-yet-generated-index",
        "reference-definition",
        "fenced-code-block",
    ],
)
def test_broken_link_scope_ma7(tmp_path: Path, body: str, expected: list[str]) -> None:
    """The link checker's whole contract, one row per documented case (MA-7)."""
    root = link_kb(tmp_path, body)

    findings = [
        finding
        for finding in find_broken_links(root, scan(root))
        if finding.path == "Cooking/notes/linker.md"
    ]

    assert [finding.code for finding in findings] == expected
    assert all(finding.severity is Severity.WARNING for finding in findings)
    assert all(finding.rule_id == "MA-7" for finding in findings)


def test_a_broken_link_finding_cites_the_source_target_and_line_ma7(tmp_path: Path) -> None:
    """The message is what Layer 2 shows an agent, so it must name the target as written."""
    root = link_kb(tmp_path, "Line one.\n\nSee the [guide](../references/missing.md).\n")

    (finding,) = [f for f in find_broken_links(root, scan(root)) if f.code == "BROKEN_LINK"]

    assert finding.path == "Cooking/notes/linker.md"
    assert finding.value == "../references/missing.md"
    assert finding.message == "link target `../references/missing.md` does not exist"
    text = (root / "Cooking/notes/linker.md").read_text(encoding="utf-8").splitlines()
    assert finding.line is not None
    assert "missing.md" in text[finding.line - 1], "the line is 1-based within the whole file"


def test_derived_files_are_not_link_sources_ma7(sample_kb: Path) -> None:
    """A generated index links to everything by construction; checking it reports nothing new."""
    flush(sample_kb, today=TODAY)
    (sample_kb / "Cooking/notes/old-idea/old-idea.md").unlink()

    findings = find_broken_links(sample_kb, scan(sample_kb))

    assert all(not finding.path.endswith("index.md") for finding in findings if finding.path)


def test_link_checking_makes_no_socket_call_ma7(tmp_path: Path) -> None:
    """An external URL is unverifiable, never broken: Layer 1 makes no network call (CX-1).

    The suite's autouse fixture raises on any socket, so reaching the network fails here.
    """
    root = link_kb(tmp_path, "See [the web](https://example.invalid/page).\n")

    assert find_broken_links(root, scan(root)) == []


# --------------------------------------------------------------------------------------
# MA-8 — orphans
# --------------------------------------------------------------------------------------


def test_an_unreferenced_asset_is_an_orphan_ma8(sample_kb: Path) -> None:
    """The §4.3 fixture's own defect, reported in the shape the topic index renders."""
    (finding,) = [f for f in find_orphans(sample_kb, scan(sample_kb)) if f.code == "ORPHAN_ASSET"]

    assert finding.path == "Cooking/notes/old-idea/media/photo.jpg"
    assert finding.message == "not referenced by `notes/old-idea/old-idea.md`"
    assert finding.severity is Severity.WARNING
    assert finding.rule_id == "MA-8"


def test_a_referenced_asset_is_not_an_orphan_ma8(sample_kb: Path) -> None:
    """``grill-basics.pdf`` is linked from its sibling main file, so it is clean (negative case)."""
    codes = {
        finding.path
        for finding in find_orphans(sample_kb, scan(sample_kb))
        if finding.code == "ORPHAN_ASSET"
    }

    assert "Cooking/references/grill-basics/grill-basics.pdf" not in codes


def test_an_item_folder_without_its_main_file_is_an_orphan_ma8(tmp_path: Path) -> None:
    """``notes/trip/`` with no ``trip.md`` holds no text at all, so nothing in it is reachable."""
    root = kb_with(tmp_path, {"Cooking/notes/trip/media/a.png": "binary\n"})

    findings = [f for f in find_orphans(root, scan(root)) if f.code == "ORPHAN_ITEM_FOLDER"]

    assert [f.path for f in findings] == ["Cooking/notes/trip"]
    assert "trip.md" in findings[0].message


def test_a_section_level_media_folder_is_not_an_item_folder_ma8(tmp_path: Path) -> None:
    """``media`` is structural (PA-6), never a folder-hosted item — VA-16 skips it for the same
    reason, and the two rules live in the same layer, so they must agree about the same directory.

    The harm was not the finding but its remedy: MA-10 renders it into the human's ``index.md``
    with the hint *add ``recipes/media/media.md``*, which would create an item literally named
    ``media`` inside a section.
    """
    root = kb_with(
        tmp_path,
        {"Cooking/recipes/media/pic.png": "binary\n", "Cooking/recipes/r.md": authored("R")},
    )

    findings = [f for f in find_orphans(root, scan(root)) if f.code == "ORPHAN_ITEM_FOLDER"]

    assert findings == []
    flush(root, today=TODAY)
    assert "## Maintenance flags" not in (root / "Cooking/index.md").read_text(encoding="utf-8")


def test_an_item_folder_holding_only_media_is_still_an_orphan_ma8(tmp_path: Path) -> None:
    """The exclusion is one segment wide: ``notes/trip/media/`` still says ``trip/`` has no text."""
    root = kb_with(tmp_path, {"Cooking/notes/trip/media/a.png": "binary\n"})

    findings = [f for f in find_orphans(root, scan(root)) if f.code == "ORPHAN_ITEM_FOLDER"]

    assert [f.path for f in findings] == ["Cooking/notes/trip"]


def test_a_stale_derived_index_is_flagged_and_never_deleted_pa12(tmp_path: Path) -> None:
    """An ``index.md`` derived by name that no generator owns is PA-12's stale-file flag.

    ``sub-topics/Optics/index.md`` outliving its ``topic.md`` is the realistic case: it is exempt
    from the content rules (class ``DERIVED``), excluded from every index (GE-15), skipped by the
    authored-only orphan predicates, never regenerated and — correctly — never deleted (PA-12,
    MA-9). Before this flag it was reported by nobody at all, so it sat there looking authoritative.
    """
    stale = "Physics/sub-topics/Optics/index.md"
    root = write_kb(
        tmp_path / "KB",
        {**topic_files("Physics"), stale: '---\ntitle: "Optics"\nsource_type: index\n---\n'},
    )

    report = flush(root, today=TODAY)

    (finding,) = [f for f in report.findings if f.code == "STALE_DERIVED_FILE"]
    assert finding.path == stale
    assert finding.rule_id == "PA-12"
    assert finding.severity is Severity.WARNING
    assert (root / stale).exists(), "PA-12: never written and never deleted by Layer 1"

    index = (root / "Physics/index.md").read_text(encoding="utf-8")
    assert f"- stale-derived-file: `{paths.rel(root / 'Physics', root / stale)}`" in index
    assert "Optics" not in index.split("## Maintenance flags")[0], "GE-15: not an indexed item"


def test_the_generated_indexes_are_never_stale_pa12(sample_kb: Path) -> None:
    """The negative half: every file the generators own must survive their own staleness check."""
    flush(sample_kb, today=TODAY)

    findings = find_orphans(sample_kb, scan(sample_kb))

    assert [f for f in findings if f.code == "STALE_DERIVED_FILE"] == []


def test_a_folder_hosted_item_with_its_main_file_is_clean_ma8(tmp_path: Path) -> None:
    """The negative case: a well-formed folder-hosted item produces no orphan finding."""
    root = kb_with(
        tmp_path,
        {
            "Cooking/notes/trip/trip.md": authored("Trip", body="![p](media/a.png)\n"),
            "Cooking/notes/trip/media/a.png": "binary\n",
        },
    )

    assert find_orphans(root, scan(root)) == []


def test_misfiled_markdown_inside_a_topic_is_an_orphan_ma8(tmp_path: Path) -> None:
    """Authored markdown in none of notes/, references/ or an extension folder is unreachable."""
    root = kb_with(tmp_path, {"Cooking/media/stray.md": authored("Stray")})

    findings = [f for f in find_orphans(root, scan(root)) if f.code == "ORPHAN_FILE"]

    assert [f.path for f in findings] == ["Cooking/media/stray.md"]


def test_markdown_outside_every_topic_is_an_orphan_ma8(tmp_path: Path) -> None:
    """A note in a directory that is not a topic root belongs to no index anywhere."""
    root = kb_with(tmp_path, {"NotATopic/stray.md": authored("Stray")})

    findings = [f for f in find_orphans(root, scan(root)) if f.code == "ORPHAN_OUTSIDE_TOPIC"]

    assert [f.path for f in findings] == ["NotATopic/stray.md"]


def test_a_note_reachable_only_through_its_index_is_not_an_orphan_ma8(sample_kb: Path) -> None:
    """Rejecting Q8's option (a): notes are legitimately reachable only via tags and the index."""
    flush(sample_kb, today=TODAY)

    orphaned = {f.path for f in find_orphans(sample_kb, scan(sample_kb))}

    assert "Cooking/notes/grill-performance-in-windy-conditions.md" not in orphaned
    assert "Cooking/recipes/ribeye-on-gas.md" not in orphaned


# --------------------------------------------------------------------------------------
# MA-9, MA-10 — flagging is non-mutating, and where the flags land
# --------------------------------------------------------------------------------------


def test_flagging_never_mutates_a_content_file_ma9(tmp_path: Path) -> None:
    """A knowledge base full of defects: the flush completes and rewrites no content byte."""
    files = {
        **topic_files("Cooking"),
        "Cooking/notes/linker.md": authored("Linker", body="[gone](../references/missing.md)\n"),
        "Cooking/notes/trip/media/a.png": "binary\n",
        "Cooking/media/stray.md": authored("Stray"),
    }
    root = write_kb(tmp_path / "KB", files)
    before = content_bytes(root, files)

    report = flush(root, today=TODAY)

    assert content_bytes(root, files) == before
    assert {f.code for f in report.findings} >= {
        "BROKEN_LINK",
        "ORPHAN_ITEM_FOLDER",
        "ORPHAN_FILE",
    }


def test_flags_land_in_the_report_and_the_owning_topic_index_ma10(tmp_path: Path) -> None:
    """Two places, and the index section self-clears once the defect is fixed."""
    note = "Cooking/notes/linker.md"
    root = kb_with(tmp_path, {note: authored("Linker", body="[gone](../references/gone.md)\n")})

    report = flush(root, today=TODAY)
    index = (root / "Cooking/index.md").read_text(encoding="utf-8")

    assert any(finding.code == "BROKEN_LINK" for finding in report.findings)
    assert "## Maintenance flags" in index
    assert (
        "- broken-link: `notes/linker.md` (link target `../references/gone.md` does not exist)"
        in index
    )

    (root / "Cooking/references/gone.md").write_text(authored("Gone"), encoding="utf-8")
    flush(root, today=TODAY)

    assert "## Maintenance flags" not in (root / "Cooking/index.md").read_text(encoding="utf-8")


def test_a_flag_is_routed_to_the_nearest_owning_topic_ma10(sample_kb: Path) -> None:
    """A defect inside a sub-topic is flagged there, not repeated in every ancestor's index."""
    note = "Cooking/sub-topics/Grilling/notes/linker.md"
    (sample_kb / note).write_text(authored("Linker", body="[gone](gone.md)\n"), encoding="utf-8")

    flush(sample_kb, today=TODAY)

    grilling = (sample_kb / "Cooking/sub-topics/Grilling/index.md").read_text(encoding="utf-8")
    cooking = (sample_kb / "Cooking/index.md").read_text(encoding="utf-8")
    assert "- broken-link: `notes/linker.md`" in grilling
    assert "linker.md" not in cooking.split("## Maintenance flags")[-1]


# --------------------------------------------------------------------------------------
# GE-5 — a full rebuild equals an incremental flush
# --------------------------------------------------------------------------------------

_DEFECTIVE_KB: Mapping[str, str] = {
    **topic_files("Cooking"),
    "Cooking/notes/linker.md": authored("Linker", body="[gone](../references/gone.md)\n"),
    "Cooking/notes/trip/trip.md": authored("Trip"),
    "Cooking/notes/trip/media/photo.jpg": "binary\n",
}
"""A valid tree carrying exactly the two defects MA-10 renders: a broken link and an orphan asset."""


def tree_bytes(root: Path) -> dict[str, bytes]:
    """Every file in the tree, keyed by KB-relative POSIX path — derived files included."""
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def test_a_full_rebuild_equals_an_incremental_flush_ge5(tmp_path: Path) -> None:
    """The same tree renders the same bytes from either entry point (GE-5, CX-4).

    ``regenerate_all`` used to take the ``## Maintenance flags`` section as a caller-supplied
    argument, so a derived file was a function of *who called* rather than of the tree: a flush
    wrote the section and the next bare rebuild — the one ``scaffold_topic`` runs, SC-7 — silently
    stripped it back out. Both directions are asserted here, because CX-4's recovery story is the
    rebuild one: delete every derived file, rebuild, and the tree must come back byte-identical.
    """
    root = write_kb(tmp_path / "KB", _DEFECTIVE_KB)

    flush(root, today=TODAY)
    after_flush = tree_bytes(root)

    flags = (root / "Cooking/index.md").read_text(encoding="utf-8").split("## Maintenance flags")
    assert len(flags) == 2, "the fixture must actually produce flags, or this test is vacuous"
    assert "- broken-link: `notes/linker.md`" in flags[1]
    assert "- orphan-asset: `notes/trip/media/photo.jpg`" in flags[1]

    assert regenerate_all(root).written == []
    assert tree_bytes(root) == after_flush

    for derived in {root / "index.md", root / "tags.md", *root.glob("**/index.md")}:
        derived.unlink()
    regenerate_all(root)

    assert tree_bytes(root) == after_flush


def test_the_tree_is_analysed_once_per_call_ge5(
    sample_kb: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Deriving the flags must not mean deriving them twice (GE-5, MA-1).

    The flush needs the findings for its own report and hands the same list on, so the generators'
    fallback stays unused there. It is the fallback that makes a *bare* rebuild render the same
    bytes, and the analysis reads every content file, so which side runs it is worth pinning.
    """
    calls: list[str] = []

    for module, label in ((maintenance, "flush"), (generators, "rebuild")):
        for name in ("find_broken_links", "find_orphans"):
            real: Callable[..., object] = getattr(module, name)

            def spy(
                *args: object,
                _real: Callable[..., object] = real,
                _tag: str = f"{label}:{name}",
                **kwargs: object,
            ) -> object:
                calls.append(_tag)
                return _real(*args, **kwargs)

            monkeypatch.setattr(module, name, spy)

    flush(sample_kb, today=TODAY)
    assert calls == ["flush:find_broken_links", "flush:find_orphans"]

    calls.clear()
    regenerate_all(sample_kb)
    assert calls == ["rebuild:find_broken_links", "rebuild:find_orphans"]

    calls.clear()
    regenerate_all(sample_kb, flags=())
    assert calls == [], "an explicit empty sequence is the opt-out, not a request to re-derive"


def test_scaffolding_a_topic_leaves_another_topics_index_byte_identical_ge5(tmp_path: Path) -> None:
    """SC-7's bare rebuild is the operational face of GE-5: creating Baking must not edit Cooking."""
    root = write_kb(tmp_path / "KB", _DEFECTIVE_KB)
    flush(root, today=TODAY)
    before = (root / "Cooking/index.md").read_bytes()

    scaffold_topic(root, "Baking", title="Baking", description="Bread and cake", today=TODAY)

    assert (root / "Cooking/index.md").read_bytes() == before


# --------------------------------------------------------------------------------------
# MA-11, MA-12 — conflict-scan requests
# --------------------------------------------------------------------------------------


@pytest.mark.superseded
def test_scan_requests_are_data_only_ma11(sample_kb: Path) -> None:
    """Layer 1 returns values; the queue, the database and the comparison are Layer 2/3's."""
    report = flush(sample_kb, ["Cooking/notes/summary.md"], today=TODAY)

    (request,) = report.scan_requests
    assert request.topic_id == "topic/cooking"
    assert request.topic_path == "Cooking"
    assert request.origin == MAINTENANCE_ORIGIN
    assert request.requested_at == TODAY
    assert "sqlite" not in SOURCE and "import socket" not in SOURCE


@pytest.mark.superseded
def test_requests_are_coalesced_per_topic_ma12(tmp_path: Path) -> None:
    """Five changed notes in one topic are one whole-topic scan, not five identical ones."""
    notes = [f"Cooking/notes/n{index}.md" for index in range(5)]
    root = kb_with(tmp_path, {note: authored(f"Note {note}") for note in notes})
    snapshot = scan(root)

    requests = build_scan_requests(snapshot, reversed(notes), requested_at=TODAY)

    assert len(requests) == 1
    assert requests[0].changed_paths == tuple(sorted(notes))


@pytest.mark.superseded
def test_only_notes_references_and_extension_folders_trigger_a_scan_ma12(tmp_path: Path) -> None:
    """``topic.md``, skills, assets and derived files state no knowledge to compare (C20)."""
    root = kb_with(
        tmp_path,
        {
            "Cooking/recipes/ribeye.md": authored("Ribeye"),
            "skills/voice/SKILL.md": "---\nname: voice\ndescription: d\n---\n",
            "Cooking/notes/trip/media/a.png": "binary\n",
        },
    )
    snapshot = scan(root)
    flush(root, today=TODAY)

    requests = build_scan_requests(
        scan(root),
        [
            "Cooking/topic.md",
            "Cooking/index.md",
            "index.md",
            "skills/voice/SKILL.md",
            "Cooking/notes/trip/media/a.png",
            "Cooking/notes/summary.md",
            "Cooking/recipes/ribeye.md",
        ],
        requested_at=TODAY,
    )

    assert snapshot.topics.keys() == {"Cooking"}
    assert [request.changed_paths for request in requests] == [
        ("Cooking/notes/summary.md", "Cooking/recipes/ribeye.md")
    ]


@pytest.mark.superseded
def test_a_request_can_carry_an_empty_changed_set_ma12(sample_kb: Path) -> None:
    """An on-demand scan addresses the topic, not a file change."""
    request = scan_request_for(
        scan(sample_kb), "Cooking", origin=ON_DEMAND_ORIGIN, requested_at=TODAY
    )

    assert request.changed_paths == ()
    assert request.origin == ON_DEMAND_ORIGIN
    with pytest.raises(NotATopicRootError):
        scan_request_for(scan(sample_kb), "NoSuchTopic", requested_at=TODAY)


@pytest.mark.superseded
def test_requests_are_one_per_topic_in_discovery_order_ma12(sample_kb: Path) -> None:
    """Two topics changed, two requests, in the tree's own deterministic order."""
    changed = ["Cooking/sub-topics/Grilling/notes/summary.md", "BBQ/notes/summary.md"]

    requests = build_scan_requests(scan(sample_kb), changed, requested_at=TODAY)

    assert [request.topic_id for request in requests] == ["topic/bbq", "topic/cooking/grilling"]


# --------------------------------------------------------------------------------------
# MA-13, MA-14, MA-15 — the report, totality, and locking
# --------------------------------------------------------------------------------------


def test_flush_report_covers_every_derived_path_ma13(sample_kb: Path) -> None:
    """``written + unchanged`` is the whole derived set, and the second flush moves it across."""
    first = flush(sample_kb, today=TODAY)
    second = flush(sample_kb, today=TODAY)

    assert isinstance(first, FlushReport)
    assert sorted(first.written) == sorted(second.unchanged)
    assert second.written == []
    assert first.derived == second.derived


def test_flush_survives_a_broken_tree_and_reports_it_ma14(tmp_path: Path) -> None:
    """One unparseable file plus one half-written note: the flush completes and diagnoses both."""
    files = {
        **topic_files("Cooking"),
        "Cooking/notes/broken.md": "---\ntitle: [unclosed\n---\n\n# Broken\n",
        "Cooking/notes/half.md": '---\ntitle: "Half"\n---\n\n# Half\n',
    }
    root = write_kb(tmp_path / "KB", files)

    report = flush(root, ["Cooking/notes/broken.md", "Cooking/notes/half.md"], today=TODAY)

    assert "FRONTMATTER_PARSE_ERROR" in {finding.code for finding in report.findings}
    assert (root / "Cooking/index.md").exists()
    assert "Half" in (root / "Cooking/index.md").read_text(encoding="utf-8")
    assert flush(root, today=TODAY).written == [], "a second flush over the same tree is a no-op"


@pytest.mark.superseded
def test_flush_over_an_empty_knowledge_base_ma14(empty_kb: Path) -> None:
    """A directory with no topics is a valid knowledge base, not a failure (GE-29)."""
    report = flush(empty_kb, ["nothing.md"], today=TODAY)

    assert report.written == ["index.md", "tags.md"]
    assert report.stamped == []
    assert report.scan_requests == []
    assert report.findings == []


def test_flush_is_safe_to_run_twice_ma14(sample_kb: Path) -> None:
    """Idempotence over the whole operation, not only over the generators."""
    note = "Cooking/notes/summary.md"
    flush(sample_kb, [note], today=TODAY)
    after_first = content_bytes(sample_kb, SAMPLE_KB_FILES)

    report = flush(sample_kb, [note], today=TODAY)

    assert report.written == []
    assert report.stamped == []
    assert content_bytes(sample_kb, SAMPLE_KB_FILES) == after_first


def test_maintenance_takes_no_lock_of_its_own_ma15() -> None:
    """The global write lock is Layer 2's; the docstring states the sole-writer contract."""
    assert re.search(r"^import (threading|asyncio|fcntl)", SOURCE, re.MULTILINE) is None
    assert "fcntl" not in SOURCE and "Lock(" not in SOURCE
    assert flush.__doc__ is not None
    assert "sole-writer" in flush.__doc__.lower()


# --------------------------------------------------------------------------------------
# Cross-cutting
# --------------------------------------------------------------------------------------


def test_no_layer_2_mount_prefix_anywhere_in_core_cx3() -> None:
    """CX-3 greps the *package*: ``/kb/`` is Layer 2's backend mount prefix and belongs there.

    Scoped to one module — which is all this assertion used to cover — the guard was decorative:
    a ``/kb/`` constant and an agent-visible-path helper could sit in any of the other sixteen
    modules with every gate green. CX-3's own test assertion says ``grep -r … pkb/core``.

    Its other clause ("every exported callable's signature includes ``kb_root``") is not asserted
    here and deliberately so: under decision C most functions take a ``KbSnapshot``, which carries
    the root, and the rest are pure helpers like ``slugify(name)``. Tightening that wording is a
    spec edit, not a code change, and an allowlist of pure helpers would assert nothing.
    """
    assert CORE_SOURCES, "the package moved: this scan must never silently pass over no files"
    for path in CORE_SOURCES:
        assert "/kb/" not in path.read_text(encoding="utf-8"), path


def test_no_shelling_out_and_no_git_anywhere_in_core_cx4() -> None:
    """CX-4, package-wide: no process, no repository, no undo machinery in Layer 1.

    Derived files rebuilt from content are the *only* recovery mechanism in the first draft (arch
    D6), which is what makes a hidden ``git`` shell-out a correctness problem and not a style one:
    it would let the layer pretend to have a history it must not rely on.

    Asserted over the *imports*, parsed, rather than over the raw source: a docstring that promises
    "no subprocess" must not fail the test that checks the promise.
    """
    forbidden = {"subprocess", "git", "os.system", "shutil", "pty", "asyncio"}
    assert CORE_SOURCES
    for path in CORE_SOURCES:
        for name in _imported_modules(path):
            assert name.split(".")[0] not in forbidden, f"{path} imports {name}"


def test_findings_are_deterministic_across_two_runs_ge4(sample_kb: Path) -> None:
    """Same tree, same findings, in the same order — nothing depends on iteration order."""
    snapshot = scan(sample_kb)
    first: Sequence[object] = [
        *find_broken_links(sample_kb, snapshot),
        *find_orphans(sample_kb, snapshot),
    ]
    second: Sequence[object] = [
        *find_broken_links(sample_kb, scan(sample_kb)),
        *find_orphans(sample_kb, scan(sample_kb)),
    ]

    assert first == second
