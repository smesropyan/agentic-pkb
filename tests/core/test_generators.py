"""The two derived files: content, byte format, determinism and totality (GE-1 … GE-32, T-22 … T-37).

The golden files in ``tests/core/golden/`` are the contract. ``tags.md`` and ``empty_tags.md`` were
regenerated against the new registry renderer by Phase 1's Task 6 and their comparison
(:func:`test_goldens_match_ge31`, parametrized per file) is live again — the rest
(``cooking_index.md``, ``bbq_index.md``, ``grilling_index.md``, ``minimal_topic_index.md``,
``flagged_cooking_index.md``, ``degraded_topic_index.md``) are ``topic_index.py`` goldens Task 6
does not touch and stay :data:`_LIVE_GOLDENS`-excluded (superseded) until Task 7 rebuilds that
generator and regenerates them in turn. ``root_index.md``, ``empty_root_index.md`` and
``degraded_root_index.md`` are gone along with ``root_index.py`` — there is no root ``index.md``
generator left to compare against one (T-37).

Regenerate the live goldens with::

    PYTHONPATH=. uv run python tests/core/test_generators.py --update-golden

The switch lives on the module's script entry point rather than on ``pytest`` because
``pytest_addoption`` is only honoured from a ``conftest.py``, and this phase does not own
``tests/core/conftest.py``. Compare and update run through the same :func:`render_goldens`, so the
two can never disagree about what a golden contains — but :func:`_update_goldens` writes only
:data:`_LIVE_GOLDENS`: writing every key in :func:`render_goldens`' dict would silently regenerate
the topic-index goldens Task 7 still owns, ahead of the rework that is supposed to change them.

Re-run the live goldens against DESIGN §1.6 after any change to them: they are quoted text, and a
golden that drifts from the document it was copied from is worse than no golden at all.
"""

from __future__ import annotations

import ast
import inspect
import os
import re
import sys
import tempfile
import time
from collections.abc import Iterable, Mapping
from pathlib import Path

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

import pkb.core
from pkb.core import paths, tags
from pkb.core.errors import Finding, NotATopicRootError, Severity
from pkb.core.generators import (
    base,
    derive,
    flags_for_topic,
    generate_topic_index,
    regenerate_all,
    render_root_tags,
    render_topic_index,
    root_tags_findings,
    topic_index_findings,
)
from pkb.core.generators.tags_registry import MAPPINGS_HEADING
from pkb.core.models import FileClass
from pkb.core.scan import scan
from tests.core.conftest import SAMPLE_KB_FILES, reversed_directory_order, write_kb

GOLDEN = Path(__file__).parent / "golden"

# --------------------------------------------------------------------------------------
# Fixture knowledge bases beyond the shared §4.1 sample
# --------------------------------------------------------------------------------------


def _note(title: str, description: str, tag: str, *, body: str = "Body.\n") -> str:
    return (
        "---\n"
        f'title: "{title}"\n'
        f'description: "{description}"\n'
        'topic: "Physics"\n'
        "tags:\n"
        f"  - {tag}\n"
        "  - type.note\n"
        "  - status.draft\n"
        "created: 2024-05-01\n"
        "updated: 2024-05-01\n"
        "source_type: note\n"
        "---\n\n"
        f"# {title}\n\n{body}"
    )


def _summary(title: str, description: str, tag: str) -> str:
    return (
        "---\n"
        f'title: "{title}"\n'
        f'description: "{description}"\n'
        'topic: "Physics"\n'
        "tags:\n"
        f"  - {tag}\n"
        "  - type.summary\n"
        "  - status.approved\n"
        "created: 2024-05-01\n"
        "updated: 2024-05-01\n"
        "source_type: summary\n"
        "---\n\n"
        f"# {title}\n\nNothing yet.\n"
    )


MINIMAL_KB_FILES: Mapping[str, str] = {
    "Physics/topic.md": _summary("Physics", "Motion, energy, and matter", "topic.physics"),
    "Physics/notes/summary.md": _summary(
        "Notes summary", "Distilled rules from physics reading", "topic.physics"
    ),
    "Physics/notes/free-fall.md": _note(
        "Free fall", "Everything falls at the same rate in a vacuum", "topic.physics.mechanics"
    ),
    "Physics/references/summary.md": _summary(
        "References summary", "Overview of ingested physics sources", "topic.physics"
    ),
    "Physics/references/lectures/lectures.md": (
        "---\n"
        'title: "Lectures on Physics"\n'
        'description: "Feynman\'s three-volume lecture series"\n'
        'topic: "Physics"\n'
        "tags:\n"
        "  - topic.physics\n"
        "  - type.reference\n"
        "  - status.approved\n"
        "created: 2024-05-01\n"
        "updated: 2024-05-01\n"
        "source_type: reference\n"
        "---\n\n"
        "# Lectures on Physics\n\nThe classic undergraduate series.\n"
    ),
}
"""A topic with no sub-topics, no extension folder, no conflict and no flags (§4.3, minimal)."""

DEGRADED_KB_FILES: Mapping[str, str] = {
    # Unparseable frontmatter: the topic still gets a catalog line and an index (GE-25).
    "Physics/topic.md": "---\ntitle: [unclosed\n---\n\n# Physics\n",
    "Physics/notes/summary.md": (
        "---\n"
        'title: "Notes summary"\n'
        'topic: "Physics"\n'
        "tags:\n"
        "  - topic.physics\n"
        "  - type.summary\n"
        "  - status.draft\n"
        "created: 2024-05-01\n"
        "updated: 2024-05-01\n"
        "source_type: summary\n"
        "---\n\n"
        "# Notes summary\n\nNo description above.\n"
    ),
    # A directory with no topic.md is not a topic and renders nothing (GE-25).
    "NotATopic/stray.md": "# Stray\n",
}

SPACE_FOLDER = " "
NBSP_FOLDER = "\u00a0"
"""Folder names that :func:`base.inline` collapses to the empty string.

The NBSP is written as an escape, like every significant glyph in this package: a fixture whose
point is an invisible byte is unreviewable when spelled literally.
"""

UNNAMEABLE_FOLDER_KB_FILES: Mapping[str, str] = {
    **MINIMAL_KB_FILES,
    f"Physics/{SPACE_FOLDER}/alpha.md": _note(
        "Alpha", "Filed under a space-named folder", "topic.physics"
    ),
    f"Physics/{NBSP_FOLDER}/beta.md": _note(
        "Beta", "Filed under an NBSP-named folder", "topic.physics"
    ),
}
"""Two extension folders whose names collapse to nothing under ``inline`` (GE-7, PA-7, VA-38).

PA-7 makes any non-structural directory under a topic root an extension folder and VA-38 never
flags one, so nothing upstream rejects these; the generator is the only place that can absorb them.
"""

_BROKEN_LINK_NOTE = (
    "---\n"
    'title: "Wind shelter"\n'
    'description: "A windbreak beside the grill"\n'
    'topic: "Cooking"\n'
    "tags:\n"
    "  - topic.cooking.grilling\n"
    "  - type.note\n"
    "  - status.draft\n"
    "created: 2024-11-20\n"
    "updated: 2024-11-20\n"
    "source_type: note\n"
    "---\n\n"
    "# Wind shelter\n\nSee the [missing guide](../references/missing.md).\n"
)

FLAGGED_KB_FILES: Mapping[str, str] = {
    **SAMPLE_KB_FILES,
    "Cooking/notes/wind-shelter.md": _BROKEN_LINK_NOTE,
}
"""The sample KB plus a note whose body links a file that does not exist (GE-31)."""

ORPHAN_FLAG = Finding(
    code="ORPHAN_ASSET",
    severity=Severity.WARNING,
    message="not referenced by `notes/old-idea/old-idea.md`",
    rule_id="MA-8",
    path="Cooking/notes/old-idea/media/photo.jpg",
)
"""The flag §4.3's golden renders. Its shape is the contract ``maintenance.py`` must produce."""

BROKEN_LINK_FLAG = Finding(
    code="BROKEN_LINK",
    severity=Severity.WARNING,
    message="link target `../references/missing.md` does not exist",
    rule_id="MA-7",
    path="Cooking/notes/wind-shelter.md",
    line=15,
)


# --------------------------------------------------------------------------------------
# Golden machinery (GE-31)
# --------------------------------------------------------------------------------------


def _mkdir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


def render_goldens(workdir: Path) -> dict[str, str]:
    """Render every golden artifact. One implementation for comparing and for updating."""
    sample = scan(write_kb(workdir / "sample", SAMPLE_KB_FILES))
    empty = scan(_mkdir(workdir / "empty"))
    minimal = scan(write_kb(workdir / "minimal", MINIMAL_KB_FILES))
    flagged = scan(write_kb(workdir / "flagged", FLAGGED_KB_FILES))
    degraded = scan(write_kb(workdir / "degraded", DEGRADED_KB_FILES))
    return {
        "tags.md": render_root_tags(sample),
        "cooking_index.md": render_topic_index(sample, "Cooking", [ORPHAN_FLAG]),
        "bbq_index.md": render_topic_index(sample, "BBQ"),
        "grilling_index.md": render_topic_index(sample, "Cooking/sub-topics/Grilling"),
        "empty_tags.md": render_root_tags(empty),
        "minimal_topic_index.md": render_topic_index(minimal, "Physics"),
        "flagged_cooking_index.md": render_topic_index(
            flagged, "Cooking", [BROKEN_LINK_FLAG, ORPHAN_FLAG]
        ),
        "degraded_topic_index.md": render_topic_index(degraded, "Physics"),
    }


GOLDEN_NAMES = tuple(
    sorted(
        {
            "tags.md",
            "cooking_index.md",
            "bbq_index.md",
            "grilling_index.md",
            "empty_tags.md",
            "minimal_topic_index.md",
            "flagged_cooking_index.md",
            "degraded_topic_index.md",
        }
    )
)

_LIVE_GOLDENS = frozenset({"tags.md", "empty_tags.md"})
"""Task 6 regenerates these two against the new registry renderer, so their comparison is live
again; the rest are topic-index goldens Task 7 rebuilds when it reworks ``topic_index.py``."""


def _read_golden(name: str) -> str:
    """Read bytes and decode explicitly — never through newline translation (GE-7)."""
    return (GOLDEN / name).read_bytes().decode("utf-8")


@pytest.mark.parametrize(
    "name",
    [
        name if name in _LIVE_GOLDENS else pytest.param(name, marks=pytest.mark.superseded)
        for name in GOLDEN_NAMES
    ],
    ids=GOLDEN_NAMES,
)
def test_goldens_match_ge31(name: str, tmp_path: Path) -> None:
    """Full-file string equality against every golden; one drifting space fails it (GE-31)."""
    assert render_goldens(tmp_path)[name] == _read_golden(name)


# --------------------------------------------------------------------------------------
# GE-1, GE-2, GE-3 — who writes derived files, and what they read
# --------------------------------------------------------------------------------------

_WRITE_CALLS = frozenset({"write_text", "write_bytes", "replace", "rename", "unlink", "mkstemp"})
_DERIVED_NAMES = frozenset(
    {"INDEX_FILE", "TAGS_FILE", "is_generated", "is_derived_name", "write_derived"}
)
_DERIVED_LITERALS = frozenset({"index.md", "tags.md"})


def test_only_the_generators_write_derived_paths_ge1() -> None:
    """No module outside ``pkb.core.generators`` writes a path a generator owns (GE-1).

    An AST check rather than a naming convention: it names the offending function, and it keeps
    working when ``scaffold`` and ``maintenance`` land, both of which legitimately write *content*
    files. A function is flagged only when it both writes and mentions a derived path.
    """
    core = Path(pkb.core.__file__).parent
    offenders: list[str] = []
    for module in sorted(core.glob("*.py")):  # non-recursive: generators/ is the exempt package
        tree = ast.parse(module.read_text(encoding="utf-8"))
        for function in ast.walk(tree):
            if not isinstance(function, ast.FunctionDef | ast.AsyncFunctionDef):
                continue
            nodes = list(ast.walk(function))
            names = {node.attr for node in nodes if isinstance(node, ast.Attribute)}
            names |= {node.id for node in nodes if isinstance(node, ast.Name)}
            names |= {
                node.value
                for node in nodes
                if isinstance(node, ast.Constant) and isinstance(node.value, str)
            }
            if names & _WRITE_CALLS and names & (_DERIVED_NAMES | _DERIVED_LITERALS):
                offenders.append(f"{module.name}:{function.name}")
    assert offenders == []


@pytest.mark.superseded
def test_exactly_three_generators_exist_ge1() -> None:
    """Three ``render_*``/``generate_*`` pairs, one per derived artifact (GE-1)."""
    from pkb.core import generators

    rendered = {name for name in dir(generators) if name.startswith("render_")}
    generated = {name for name in dir(generators) if name.startswith("generate_")}
    assert rendered == {"render_root_index", "render_root_tags", "render_topic_index"}
    assert generated == {"generate_root_index", "generate_root_tags", "generate_topic_index"}


def test_derived_files_are_replaced_wholesale_ge2(tmp_path: Path) -> None:
    """Garbage in a derived file is never read, merged or preserved (GE-2)."""
    kb = write_kb(tmp_path / "KB", SAMPLE_KB_FILES)
    stale = kb / "BBQ" / "index.md"
    stale.write_text("# hand edited\n\nkeep me please\n", encoding="utf-8")

    regenerate_all(kb)

    assert stale.read_bytes().decode("utf-8") == _read_golden("bbq_index.md")


def test_derived_files_are_never_evidence_ge3(tmp_path: Path) -> None:
    """Deleting every derived file changes nothing about the next render (GE-3)."""
    kb = write_kb(tmp_path / "KB", SAMPLE_KB_FILES)
    regenerate_all(kb)
    with_derived = (kb / "tags.md").read_bytes()

    # A tag that exists *only* inside the registry must not survive the next flush.
    (kb / "tags.md").write_text(
        (kb / "tags.md").read_text(encoding="utf-8") + "- `topic.invented`\n", encoding="utf-8"
    )
    regenerate_all(kb)
    assert (kb / "tags.md").read_bytes() == with_derived

    for derived in [kb / "index.md", kb / "tags.md", *kb.glob("**/index.md")]:
        derived.unlink(missing_ok=True)
    regenerate_all(kb)
    assert (kb / "tags.md").read_bytes() == with_derived
    assert "topic.invented" not in with_derived.decode("utf-8")


def test_derived_files_are_excluded_from_the_index_ge3(tmp_path: Path) -> None:
    """A generated ``index.md`` never appears as an item in any index (GE-3, GE-15)."""
    kb = write_kb(tmp_path / "KB", SAMPLE_KB_FILES)
    regenerate_all(kb)
    text = (kb / "Cooking" / "index.md").read_text(encoding="utf-8")
    assert "](index.md)" not in text


# --------------------------------------------------------------------------------------
# GE-4, GE-5, GE-6 — determinism
# --------------------------------------------------------------------------------------


def _render_all(kb: Path) -> str:
    snapshot = scan(kb)
    parts = [render_root_tags(snapshot)]
    parts += [render_topic_index(snapshot, path) for path in snapshot.topics]
    return "\n".join(parts)


@pytest.mark.superseded
def test_generation_is_byte_deterministic_ge4(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Same tree, four hostile conditions, one identical byte string (GE-4, PA-18)."""
    here = write_kb(tmp_path / "a" / "KB", SAMPLE_KB_FILES)
    elsewhere = write_kb(tmp_path / "b" / "deeper" / "Knowledge Base", SAMPLE_KB_FILES)
    baseline = _render_all(here)

    assert _render_all(elsewhere) == baseline  # independent of the absolute KB root

    with reversed_directory_order():
        assert _render_all(here) == baseline  # independent of filesystem iteration order

    for zone in ("UTC", "Pacific/Auckland"):
        monkeypatch.setenv("TZ", zone)
        time.tzset()
        assert _render_all(here) == baseline  # independent of the timezone
    monkeypatch.delenv("TZ", raising=False)
    time.tzset()


@pytest.mark.superseded
def test_regeneration_is_idempotent_ge5(tmp_path: Path) -> None:
    """A second consecutive flush writes zero files (GE-5, GE-8)."""
    kb = write_kb(tmp_path / "KB", SAMPLE_KB_FILES)
    first = regenerate_all(kb)
    second = regenerate_all(kb)

    assert len(first.written) == 5  # root index + root tags + three topic indexes
    assert second.written == []
    assert sorted(second.unchanged) == sorted(first.written)


def _markdown_bytes(kb: Path) -> dict[str, bytes]:
    """Every markdown file in the tree, keyed by its **KB-relative** path (GE-5, GE-3, CX-4).

    Keyed by path and not by ``path.name``: the sample KB holds 21 markdown files under 11 distinct
    basenames (4x ``index.md``, 6x ``summary.md``, 3x ``topic.md``), so a basename-keyed dict
    silently compares one arbitrary survivor per collision — and ``Path.glob`` is scandir-ordered,
    so *which* survivor is filesystem-dependent. A missing file is then a key-set difference rather
    than an invisible one, which is what makes this a real check of CX-4's "deleting all derived
    files and running ``regenerate_all`` restores them byte-identically".
    """
    return {
        path.relative_to(kb).as_posix(): path.read_bytes() for path in sorted(kb.glob("**/*.md"))
    }


@pytest.mark.superseded
def test_a_rebuild_from_nothing_equals_a_rebuild_over_the_derived_files_ge5_ge3(
    tmp_path: Path,
) -> None:
    """Derived files are output, never input: deleting them changes nothing (GE-3, GE-5).

    The other half of GE-5 — that a full rebuild equals an *incremental flush* — needs the flush
    itself and lives in ``tests/core/test_maintenance.py``:
    ``test_a_full_rebuild_equals_an_incremental_flush_ge5``. Both halves matter, because the flush
    renders a section (``## Maintenance flags``) that a bare rebuild has to reproduce byte for byte
    or whichever ran last silently wins.
    """
    kb = write_kb(tmp_path / "KB", SAMPLE_KB_FILES)
    regenerate_all(kb)
    intact = _markdown_bytes(kb)
    assert len(intact) == 21  # every file is compared, not one per basename

    for derived in [kb / "index.md", kb / "tags.md", *kb.glob("**/index.md")]:
        derived.unlink(missing_ok=True)
    regenerate_all(kb)

    assert _markdown_bytes(kb) == intact


_ISO_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")


def test_derived_files_carry_no_clock_or_count_ge6(tmp_path: Path) -> None:
    """No timestamp, run id, host, version or count in any derived file (GE-6, Q17)."""
    kb = write_kb(tmp_path / "KB", SAMPLE_KB_FILES)
    regenerate_all(kb)

    for derived in _derived_files(kb):
        text = derived.read_text(encoding="utf-8")
        assert not _ISO_DATE.search(text), derived
        assert "generated at" not in text.lower()

    # There is no clock to mock: the entry point takes none, which is why two runs agree.
    assert "today" not in inspect.signature(regenerate_all).parameters


# --------------------------------------------------------------------------------------
# GE-7, GE-8, GE-9 — bytes, writing, purity
# --------------------------------------------------------------------------------------


def _derived_files(kb: Path) -> list[Path]:
    return sorted(path for path in kb.glob("**/*.md") if paths.is_generated(kb, path))


@pytest.mark.parametrize(
    "files",
    [SAMPLE_KB_FILES, MINIMAL_KB_FILES, DEGRADED_KB_FILES, UNNAMEABLE_FOLDER_KB_FILES],
)
def test_derived_byte_format_ge7(files: Mapping[str, str], tmp_path: Path) -> None:
    """UTF-8, LF, one trailing newline, no trailing whitespace, no tabs, 4-space indents (GE-7)."""
    kb = write_kb(tmp_path / "KB", files)
    regenerate_all(kb)

    for derived in _derived_files(kb):
        raw = derived.read_bytes()
        text = raw.decode("utf-8")  # valid UTF-8, and no BOM to strip
        assert not raw.startswith(b"\xef\xbb\xbf")
        assert b"\r" not in raw
        assert text.endswith("\n") and not text.endswith("\n\n")
        for line in text.split("\n"):
            assert line == line.rstrip(), repr(line)
            assert "\t" not in line
            assert (len(line) - len(line.lstrip(" "))) % 4 == 0, repr(line)


def test_write_derived_skips_an_identical_write_ge8(tmp_path: Path) -> None:
    """An unchanged render returns False and leaves ``st_mtime_ns`` untouched (GE-8)."""
    target = tmp_path / "index.md"

    assert base.write_derived(target, "# one\n") is True
    stamp = target.stat().st_mtime_ns

    assert base.write_derived(target, "# one\n") is False
    assert target.stat().st_mtime_ns == stamp

    assert base.write_derived(target, "# two\n") is True
    assert target.read_text(encoding="utf-8") == "# two\n"


@pytest.mark.superseded
def test_an_unnameable_extension_folder_still_gets_a_heading_ge7(tmp_path: Path) -> None:
    """A folder name that inlines to nothing must not render a bare ``## `` (GE-7, PA-18).

    Two of them, because the fallback has to keep the sections distinguishable: a generic literal
    would collapse both into one heading and leave a reader unable to tell which bullets belong
    where. The percent-encoded name is what the section's own bullets already link to (PA-18).
    """
    kb = write_kb(tmp_path / "KB", UNNAMEABLE_FOLDER_KB_FILES)
    text = render_topic_index(scan(kb), "Physics")
    headings = [line for line in text.split("\n") if line.startswith("##")]

    assert "## " not in headings
    assert "## %20" in headings
    assert "## %C2%A0" in headings
    assert "](%20/alpha.md)" in text  # the heading names the folder its links point into
    assert "](%C2%A0/beta.md)" in text


def test_write_derived_leaves_no_temp_files_ge8(tmp_path: Path) -> None:
    """The atomic rename cleans up after itself (GE-8)."""
    base.write_derived(tmp_path / "index.md", "# one\n")
    assert sorted(path.name for path in tmp_path.iterdir()) == ["index.md"]


def _case_insensitive(directory: Path) -> bool:
    """Probe the filesystem rather than ``sys.platform`` (PA-17).

    PA-17 exists because the dev host is case-insensitive APFS while a deploy host may not be. A
    platform check would make the collision tests pass vacuously on a case-sensitive filesystem
    that happens to be mounted on macOS; only the filesystem itself can answer.
    """
    probe = directory / "pkb-case-probe.md"
    probe.write_text("probe\n", encoding="utf-8")
    try:
        return (directory / "PKB-CASE-PROBE.md").exists()
    finally:
        probe.unlink()


def test_write_derived_refuses_a_case_variant_collision_pa17(tmp_path: Path) -> None:
    """An authored ``INDEX.md`` is never replaced by the derived ``index.md`` (PA-17, MA-5)."""
    if not _case_insensitive(tmp_path):
        pytest.skip("case-sensitive filesystem: the two names cannot collide here")

    authored = tmp_path / "INDEX.md"
    authored.write_bytes(b"Ten years of irreplaceable notes.\n")

    with pytest.raises(OSError):
        base.write_derived(tmp_path / "index.md", "# generated\n")

    assert authored.read_bytes() == b"Ten years of irreplaceable notes.\n"
    assert sorted(path.name for path in tmp_path.iterdir()) == ["INDEX.md"]


@pytest.mark.superseded
def test_a_case_variant_collision_flags_without_aborting_the_flush_pa17(tmp_path: Path) -> None:
    """The refusal costs one derived file and one finding, not the flush (PA-17, MA-9, MA-14)."""
    if not _case_insensitive(tmp_path):
        pytest.skip("case-sensitive filesystem: the two names cannot collide here")

    kb = write_kb(tmp_path / "KB", SAMPLE_KB_FILES)
    authored = kb / "Cooking" / "INDEX.md"
    authored.write_bytes(b'---\ntitle: "Index cards"\n---\n\nTen years of notes.\n')
    before = authored.read_bytes()

    report = regenerate_all(kb)

    assert authored.read_bytes() == before  # human content survives a flush (MA-5, §5)
    assert "Cooking/index.md" not in report.written
    assert "Cooking/index.md" not in report.unchanged
    collisions = [f for f in report.findings if f.code == "DERIVED_NAME_CASE_COLLISION"]
    assert [f.path for f in collisions] == ["Cooking/index.md"]
    # The fix is a rename, so the finding must say so rather than blame the directory (PA-17, CX-6).
    assert "rename" in (collisions[0].hint or "")
    # MA-14: every other derived file is still regenerated.
    assert {"index.md", "tags.md", "BBQ/index.md"} <= set(report.written)


def test_renderers_perform_no_io_ge9(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Every ``render_*`` runs with the filesystem taken away (GE-9)."""
    kb = write_kb(tmp_path / "KB", SAMPLE_KB_FILES)
    snapshot = scan(kb)

    def _blocked(*args: object, **kwargs: object) -> None:
        raise AssertionError("render_* must not touch the filesystem (GE-9)")

    monkeypatch.setattr("builtins.open", _blocked)
    monkeypatch.setattr(os, "scandir", _blocked)
    for method in ("open", "read_text", "read_bytes", "write_text", "write_bytes"):
        monkeypatch.setattr(Path, method, _blocked)

    assert render_root_tags(snapshot)
    assert render_topic_index(snapshot, "Cooking")


def _changed_lines(before: str, after: str) -> int:
    old = before.split("\n")
    new = after.split("\n")
    assert len(old) == len(new), "line count changed, not a line edit"
    return sum(1 for a, b in zip(old, new, strict=True) if a != b)


# --------------------------------------------------------------------------------------
# GE-10 … GE-13 — the root catalog: retired outright (T-37). The root registry took over this
# surface's responsibilities — the lifted description, the totality guarantee, the expert marker —
# and tests/core/test_tree_rules.py's T-22..T-27 section (Task 6) is where they are asserted now.
# --------------------------------------------------------------------------------------


# --------------------------------------------------------------------------------------
# GE-14 … GE-18 — the topic index
# --------------------------------------------------------------------------------------


@pytest.mark.superseded
def test_every_content_file_appears_exactly_once_ge14(tmp_path: Path) -> None:
    """Each non-excluded markdown file of the topic gets exactly one bullet (GE-14, GE-25)."""
    kb = write_kb(tmp_path / "KB", SAMPLE_KB_FILES)
    snapshot = scan(kb)
    text = render_topic_index(snapshot, "Cooking")

    expected = [
        "topic.md",
        "notes/summary.md",
        "notes/grill-performance-in-windy-conditions.md",
        "notes/old-idea/old-idea.md",
        "references/summary.md",
        "references/grill-basics/grill-basics.md",
        "recipes/ribeye-on-gas.md",
    ]
    for relative in expected:
        assert text.count(f"]({relative})") == 1, relative
    # The conflicted note is the one deliberate exception: once as an item, once under Needs
    # review, because an agent scanning either section must find it.
    assert text.count("](notes/preheat-the-grill.md)") == 2

    # The list above is exhaustive: nothing the snapshot calls authored markdown is missing from it.
    indexed = {
        record.path
        for record in snapshot.files_in_topic("Cooking")
        if record.is_markdown and record.file_class is FileClass.AUTHORED
    }
    assert indexed == {f"Cooking/{r}" for r in (*expected, "notes/preheat-the-grill.md")}


def test_topic_index_exclusions_ge15(tmp_path: Path) -> None:
    """Skills, ``expert.md``, assets and the ``sub-topics/`` subtree render no item (GE-15)."""
    files = {
        **SAMPLE_KB_FILES,
        "Cooking/skills/voice/SKILL.md": "---\nname: voice\ndescription: local voice\n---\n",
    }
    kb = write_kb(tmp_path / "KB", files)
    snapshot = scan(kb)
    text = render_topic_index(snapshot, "Cooking")

    for excluded in ("SKILL.md", "expert.md", "photo.jpg", "grill-basics.pdf"):
        assert f"]({excluded}" not in text
    assert "sub-topics/Grilling/notes" not in text
    # ...but the excluded asset is still in the snapshot for orphan analysis.
    assert "Cooking/notes/old-idea/media/photo.jpg" in snapshot.files


def test_topic_index_does_not_recurse_into_sub_topics_ge16(tmp_path: Path) -> None:
    """Thirty notes under a sub-topic add one line to the parent; a thirty-first adds none."""
    kb = write_kb(tmp_path / "KB", SAMPLE_KB_FILES)
    notes = kb / "Cooking" / "sub-topics" / "Grilling" / "notes"
    before = render_topic_index(scan(kb), "Cooking")

    for index in range(30):
        (notes / f"note-{index:02d}.md").write_text(
            _note(f"Note {index}", "Grilling detail", "topic.cooking.grilling"), encoding="utf-8"
        )
    with_thirty = render_topic_index(scan(kb), "Cooking")
    assert with_thirty == before

    (notes / "note-30.md").write_text(
        _note("Note 30", "Grilling detail", "topic.cooking.grilling"), encoding="utf-8"
    )
    assert render_topic_index(scan(kb), "Cooking") == before
    assert before.count("](sub-topics/Grilling/index.md)") == 1


def _block(text: str, heading: str) -> list[str]:
    """The lines of one ``## heading`` block, heading excluded."""
    lines = text.split("\n")
    start = lines.index(f"## {heading}") + 1
    rest = lines[start:]
    end = next((i for i, line in enumerate(rest) if line.startswith("## ")), len(rest))
    return [line for line in rest[:end] if line]


@pytest.mark.superseded
def test_topic_tag_subtree_equals_the_registry_block_ge17(tmp_path: Path) -> None:
    """A topic's ``## Tag subtree`` equals its ``## Namespace:`` block in ``tags.md`` (GE-17).

    Superseded by Task 6 (T-23): the registry's root node now carries a lifted description plus
    ``*(custom expert)*`` instead of the generic "root topic" gloss, but ``topic_index.py``'s own
    ``## Tag subtree`` section still renders the old ``ROOT_TOPIC_ANNOTATION`` — Task 6's own scope
    is ``tags_registry.py``/``derive.py``/``generators/__init__.py``, not ``topic_index.py``. Task
    7's own interface is explicit that it picks this up: "Consumes: ... Task 6's registry
    conventions (same renderer for the tag subtree)."
    """
    snapshot = scan(write_kb(tmp_path / "KB", SAMPLE_KB_FILES))
    index = render_topic_index(snapshot, "Cooking")
    registry = render_root_tags(snapshot)

    assert _block(index, "Tag subtree") == _block(registry, "Namespace: topic.cooking")
    assert not any("domain." in line or "type." in line for line in _block(index, "Tag subtree"))


def test_topic_tag_subtree_shows_tags_used_elsewhere_ge17(tmp_path: Path) -> None:
    """The subtree is a branch of the *global* tree, not of the topic's own files (GE-17)."""
    files = {
        **SAMPLE_KB_FILES,
        "BBQ/notes/sous-vide.md": _note(
            "Sous vide", "Filed under BBQ, tagged under Cooking", "topic.cooking.sous-vide"
        ),
    }
    snapshot = scan(write_kb(tmp_path / "KB", files))
    assert "`topic.cooking.sous-vide`" in render_topic_index(snapshot, "Cooking")


def test_topic_mappings_are_local_left_and_appear_once_globally_ge18(tmp_path: Path) -> None:
    """Every topic-index mapping is local-left, and each pair appears once in ``tags.md`` (GE-18)."""
    snapshot = scan(write_kb(tmp_path / "KB", SAMPLE_KB_FILES))
    cooking = _block(render_topic_index(snapshot, "Cooking"), "Cross-topic mappings")
    bbq = _block(render_topic_index(snapshot, "BBQ"), "Cross-topic mappings")
    registry = _block(render_root_tags(snapshot), MAPPINGS_HEADING)

    assert all(line.startswith("- `topic.cooking") for line in cooking)
    assert all(line.startswith("- `topic.bbq") for line in bbq)
    assert len(registry) == len(set(registry)) == 2


# --------------------------------------------------------------------------------------
# GE-19, GE-20 — cross-topic mappings
# --------------------------------------------------------------------------------------


def test_mappings_are_the_cartesian_product_ge19(tmp_path: Path) -> None:
    """Two ``topic.*`` tags crossed with one ``related_topics`` target: two pairs (GE-19, C13)."""
    snapshot = scan(write_kb(tmp_path / "KB", SAMPLE_KB_FILES))
    pairs, findings = derive.cross_topic_pairs(snapshot)

    assert pairs == [
        ("topic.cooking.grilling", "topic.bbq.equipment"),
        ("topic.cooking.heat-management", "topic.bbq.equipment"),
    ]
    assert findings == []


def test_shared_domain_tags_produce_no_mapping_ge19(tmp_path: Path) -> None:
    """``related_topics`` is the only source — proximity and shared tags are not (GE-19)."""
    files = {
        "A/topic.md": _summary("A", "First topic", "topic.a"),
        "A/notes/one.md": _note("One", "In A", "topic.a").replace(
            "  - type.note", "  - domain.legal.compliance\n  - type.note"
        ),
        "B/topic.md": _summary("B", "Second topic", "topic.b"),
        "B/notes/two.md": _note("Two", "In B", "topic.b").replace(
            "  - type.note", "  - domain.legal.compliance\n  - type.note"
        ),
    }
    snapshot = scan(write_kb(tmp_path / "KB", files))
    pairs, _ = derive.cross_topic_pairs(snapshot)
    assert pairs == []


def test_mapping_orientation_follows_the_declaration_ge20(tmp_path: Path) -> None:
    """One declaration renders as declared; the reciprocal collapses to one, smaller-left (GE-20)."""
    declaring = _note("Grill", "Declares the link", "topic.cooking.grilling").replace(
        "source_type: note", "related_topics: [ bbq.equipment ]\nsource_type: note"
    )
    files = {
        "BBQ/topic.md": _summary("BBQ", "Barbecue", "topic.bbq"),
        "BBQ/notes/equipment.md": _note("Equipment", "Kit", "topic.bbq.equipment"),
        "Cooking/topic.md": _summary("Cooking", "Cooking", "topic.cooking"),
        "Cooking/notes/grill.md": declaring,
    }
    kb = write_kb(tmp_path / "KB", files)
    pairs, _ = derive.cross_topic_pairs(scan(kb))
    assert pairs == [("topic.cooking.grilling", "topic.bbq.equipment")]

    reciprocal = kb / "BBQ" / "notes" / "equipment.md"
    reciprocal.write_text(
        reciprocal.read_text(encoding="utf-8").replace(
            "source_type: note", "related_topics: [ cooking.grilling ]\nsource_type: note"
        ),
        encoding="utf-8",
    )
    flipped, _ = derive.cross_topic_pairs(scan(kb))
    assert flipped == [("topic.bbq.equipment", "topic.cooking.grilling")]


def test_unrenderable_related_topic_is_reported_ge19(tmp_path: Path) -> None:
    """A ``related_topics`` value that cannot be a tag is reported, never dropped in silence."""
    broken = _note("Broken", "Bad target", "topic.physics").replace(
        "source_type: note", 'related_topics: [ "Heat Management" ]\nsource_type: note'
    )
    files = {
        "Physics/topic.md": _summary("Physics", "Physics", "topic.physics"),
        "Physics/notes/broken.md": broken,
    }
    snapshot = scan(write_kb(tmp_path / "KB", files))
    pairs, findings = derive.cross_topic_pairs(snapshot)

    assert pairs == []
    assert [f.code for f in findings] == ["UNRENDERABLE_RELATED_TOPIC"]
    assert findings[0].rule_id == "GE-19"


# --------------------------------------------------------------------------------------
# GE-21 … GE-24 — the tag registry
# --------------------------------------------------------------------------------------


def test_root_tags_literals_ge21(tmp_path: Path) -> None:
    """Frontmatter, H1 and the absence of file listings are pinned to the byte (GE-21)."""
    kb = write_kb(tmp_path / "KB", SAMPLE_KB_FILES)
    text = render_root_tags(scan(kb))

    assert text.split("\n")[:6] == [
        "---",
        'title: "PKB Tag Registry"',
        "source_type: tag-registry",
        "---",
        "",
        "# PKB Tag Registry",
    ]
    assert "](" not in text  # no file listings, no inverted tag→file index

    for index in range(3):
        (kb / "Cooking" / "notes" / f"extra-{index}.md").write_text(
            _note(f"Extra {index}", "Reuses known tags", "topic.cooking"), encoding="utf-8"
        )
    assert render_root_tags(scan(kb)) == text


@pytest.mark.superseded
def test_root_tags_section_order_ge22(tmp_path: Path) -> None:
    """topic sections (sorted) → type → status → domain → mappings (GE-22, C15)."""
    text = render_root_tags(scan(write_kb(tmp_path / "KB", SAMPLE_KB_FILES)))
    headings = [line for line in text.split("\n") if line.startswith("## ")]
    assert headings == [
        "## Namespace: topic.bbq",
        "## Namespace: topic.cooking",
        "## Namespace: type",
        "## Namespace: status",
        "## Namespace: domain",
        f"## {MAPPINGS_HEADING}",
    ]
    assert "## Namespace: topic.cooking.grilling" not in text  # sub-topics get no H2


def test_tag_tree_renders_the_full_chain_ge23(tmp_path: Path) -> None:
    """A single four-level tag renders four nodes; removing it removes all four (GE-23)."""
    files = {
        "Cooking/topic.md": _summary("Cooking", "Cooking", "topic.cooking"),
        "Cooking/notes/gas.md": _note("Gas", "Deep tag", "topic.cooking.grilling.gas"),
    }
    kb = write_kb(tmp_path / "KB", files)
    block = _block(render_root_tags(scan(kb)), "Namespace: topic.cooking")
    # The topic-backed root node's summary is lifted from `topic.md`'s own `description` (T-23),
    # not the retired generic "root topic" gloss `_summary`'s second argument became.
    root_topic = f"- `topic.cooking`{tags.TAG_DEF_SEP}Cooking"
    assert block == [
        root_topic,
        "    - `topic.cooking.grilling`",
        "        - `topic.cooking.grilling.gas`",
    ]

    (kb / "Cooking" / "notes" / "gas.md").unlink()
    assert _block(render_root_tags(scan(kb)), "Namespace: topic.cooking") == [root_topic]


@pytest.mark.superseded
def test_extension_marker_follows_the_folder_ge24(tmp_path: Path) -> None:
    """The marker is derived from the tree: delete the folder and it goes, the node stays (GE-24)."""
    kb = write_kb(tmp_path / "KB", SAMPLE_KB_FILES)
    marked = "- `topic.cooking.recipes`" + tags.EXTENSION_MARKER
    assert marked in render_root_tags(scan(kb))

    # Move the recipe out of recipes/ so the tag survives while the directory does not.
    recipe = kb / "Cooking" / "recipes" / "ribeye-on-gas.md"
    (kb / "Cooking" / "notes" / "ribeye-on-gas.md").write_bytes(recipe.read_bytes())
    recipe.unlink()
    (kb / "Cooking" / "recipes").rmdir()

    text = render_root_tags(scan(kb))
    assert tags.EXTENSION_MARKER not in text
    assert "`topic.cooking.recipes`" in text


@pytest.mark.superseded
def test_static_definitions_are_always_rendered_ge29(tmp_path: Path) -> None:
    """``type`` and ``status`` are generator text, identical for an empty and a full KB (C17)."""
    empty = render_root_tags(scan(_mkdir(tmp_path / "Empty")))
    full = render_root_tags(scan(write_kb(tmp_path / "KB", SAMPLE_KB_FILES)))
    for namespace in ("Namespace: type", "Namespace: status"):
        assert _block(empty, namespace) == _block(full, namespace)


# --------------------------------------------------------------------------------------
# GE-25, GE-26, GE-27 — totality, text safety, ordering
# --------------------------------------------------------------------------------------


def test_generators_are_total_over_a_degraded_tree_ge25(tmp_path: Path) -> None:
    """Each degraded case renders one line plus one diagnostic, and the flush completes (GE-25).

    The registry is the one derived file above the topics now (T-37), so it is the registry —
    not a root catalog, retired — that carries the degraded topic-backed node and its diagnostic.
    """
    kb = write_kb(tmp_path / "KB", DEGRADED_KB_FILES)
    snapshot = scan(kb)

    registry = render_root_tags(snapshot)
    assert f"`topic.physics`{tags.TAG_DEF_SEP}*(missing topic metadata)*" in registry
    assert "NotATopic" not in registry  # a directory without topic.md is not a topic

    index = render_topic_index(snapshot, "Physics")
    assert "- [Notes summary](notes/summary.md) — *(no description)*" in index

    # `Physics/notes/summary.md`'s own `status.draft` tag (T-17: retired namespace) also surfaces
    # an UNKNOWN_TAG_NAMESPACE finding here, incidental to this fixture rather than to GE-25/T-23's
    # own totality guarantee, which is what the filter below isolates.
    registry_codes = [f.code for f in root_tags_findings(snapshot)]
    assert registry_codes.count("MISSING_TOPIC_METADATA") == 1
    assert [f.code for f in topic_index_findings(snapshot, "Physics")] == ["MISSING_DESCRIPTION"]

    report = regenerate_all(kb)
    assert sorted(report.written) == ["Physics/index.md", "tags.md"]


def test_a_file_is_never_silently_dropped_ge25(tmp_path: Path) -> None:
    """Markdown in a place the structure does not describe still gets a bullet (GE-25)."""
    files = {
        **MINIMAL_KB_FILES,
        "Physics/scratch.md": _note("Scratch", "Loose at the topic root", "topic.physics"),
    }
    snapshot = scan(write_kb(tmp_path / "KB", files))
    text = render_topic_index(snapshot, "Physics")
    assert "## Other" in text
    assert "](scratch.md)" in text


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("plain", "plain"),
        ("  padded  ", "padded"),
        ("two\nlines", "two lines"),
        ("wide     gap", "wide gap"),
        ("a · b", "a - b"),
        ("see [x] here", "see \\[x\\] here"),
        ("tab\tsplit", "tab split"),
        ("ends with backslash \\", "ends with backslash \\\\"),
        ("already \\[escaped\\]", "already \\\\\\[escaped\\\\\\]"),
    ],
    ids=[
        "plain",
        "padded",
        "newline",
        "runs",
        "middle-dot",
        "brackets",
        "tab",
        "backslash",
        "pre-escaped",
    ],
)
def test_inline_makes_text_one_line_safe_ge26(raw: str, expected: str) -> None:
    """Whitespace collapses, the reserved dot goes, brackets escape, nothing truncates (GE-26)."""
    assert base.inline(raw) == expected


def test_inline_escapes_a_trailing_backslash_in_link_text_ge26(tmp_path: Path) -> None:
    """A title ending in ``\\`` must not swallow the bullet's closing ``]`` (GE-26).

    GE-26's acceptance criterion is a markdown-parser round-trip, not string equality. CommonMark
    reads ``\\]`` as a literal bracket, so an unescaped backslash at the end of the link text
    destroys the link entirely — the bullet renders as plain prose and the path stops being a link.
    Asserted without a parser dependency: the run of backslashes immediately before the closing
    bracket must be even-length, which is exactly the condition under which ``]`` still closes.
    """
    windows = _note("PLACEHOLDER", "Where backslashes come from", "topic.physics").replace(
        '"PLACEHOLDER"', '"Windows paths start with C:\\\\"'
    )
    files = {**MINIMAL_KB_FILES, "Physics/notes/paths.md": windows}
    text = render_topic_index(scan(write_kb(tmp_path / "KB", files)), "Physics")

    bullet = next(line for line in text.split("\n") if "](notes/paths.md)" in line)
    link_text = bullet[len("- [") : bullet.index("](notes/paths.md)")]
    assert link_text == "Windows paths start with C:\\\\"  # the source backslash, escaped
    assert (len(link_text) - len(link_text.rstrip("\\"))) % 2 == 0


def test_inline_renders_one_well_formed_bullet_ge26(tmp_path: Path) -> None:
    """A hostile description still produces exactly one bullet line (GE-26)."""
    hostile = _note("Hostile", "placeholder", "topic.physics").replace(
        '"placeholder"', '"first · [x]\\n  second"'
    )
    files = {**MINIMAL_KB_FILES, "Physics/notes/hostile.md": hostile}
    text = render_topic_index(scan(write_kb(tmp_path / "KB", files)), "Physics")
    bullet = next(line for line in text.split("\n") if "](notes/hostile.md)" in line)
    assert bullet.endswith(
        "— first - \\[x\\] second · tags: `status.draft` `topic.physics` `type.note`"
    )


def test_item_bullets_sort_by_path_not_title_ge27(tmp_path: Path) -> None:
    """Renaming moves one line; a title edit changes one line in place (GE-27)."""
    kb = write_kb(tmp_path / "KB", SAMPLE_KB_FILES)
    before = render_topic_index(scan(kb), "Cooking")

    note = kb / "Cooking" / "notes" / "preheat-the-grill.md"
    note.write_text(
        note.read_text(encoding="utf-8").replace(
            'title: "Preheat the grill"', 'title: "Aardvark preheating"'
        ),
        encoding="utf-8",
    )
    after = render_topic_index(scan(kb), "Cooking")
    assert _changed_lines(before, after) == 2  # the Notes bullet and the Needs review bullet
    assert after.split("\n").index("## Notes") == before.split("\n").index("## Notes")

    # A rename moves exactly one line: out of its old slot and into the new sorted position.
    # (A note that is also listed under Needs review would move two, which is why this uses the
    # unconflicted one.)
    wind = kb / "Cooking" / "notes" / "grill-performance-in-windy-conditions.md"
    wind.rename(wind.with_name("zzz-wind.md"))
    moved = render_topic_index(scan(kb), "Cooking").split("\n")
    kept = [line for line in after.split("\n") if line in moved]
    assert len(after.split("\n")) - len(kept) == 1


def test_bullet_tags_are_sorted_independently_of_frontmatter_ge27(tmp_path: Path) -> None:
    """Rendered tag order is lexicographic, whatever order the author listed them in (GE-27)."""
    kb = write_kb(tmp_path / "KB", MINIMAL_KB_FILES)
    note = kb / "Physics" / "notes" / "free-fall.md"
    note.write_text(
        note.read_text(encoding="utf-8").replace(
            "  - topic.physics.mechanics\n  - type.note\n  - status.draft",
            "  - status.draft\n  - type.note\n  - topic.physics.mechanics",
        ),
        encoding="utf-8",
    )
    text = render_topic_index(scan(kb), "Physics")
    assert "· tags: `status.draft` `topic.physics.mechanics` `type.note`" in text


# --------------------------------------------------------------------------------------
# GE-28 … GE-30 — the derived set and the entry point
# --------------------------------------------------------------------------------------


@pytest.mark.superseded
def test_derived_set_holds_no_conflict_history_ge28(tmp_path: Path) -> None:
    """Resolving a conflict leaves no trace of it in any derived file (GE-28)."""
    kb = write_kb(tmp_path / "KB", SAMPLE_KB_FILES)
    regenerate_all(kb)
    assert sorted(paths.rel(kb, path) for path in _derived_files(kb)) == [
        "BBQ/index.md",
        "Cooking/index.md",
        "Cooking/sub-topics/Grilling/index.md",
        "index.md",
        "tags.md",
    ]

    note = kb / "Cooking" / "notes" / "preheat-the-grill.md"
    note.write_text(
        note.read_text(encoding="utf-8")
        .replace("status.conflict-review", "status.approved")
        .replace(
            "review_note: \"Reference 'Grill Basics' says preheat for 10 min. Note says 15 min.\"\n",
            "",
        ),
        encoding="utf-8",
    )
    regenerate_all(kb)

    for derived in _derived_files(kb):
        text = derived.read_text(encoding="utf-8")
        assert "Needs review" not in text
        assert "says preheat for 10 min" not in text  # the review note left no residue
        if derived.name == paths.INDEX_FILE:
            # `status.conflict-review` survives only in tags.md's static vocabulary block (TG-12),
            # which is generator text and not a record that a conflict happened.
            assert "conflict" not in text.lower()


def test_empty_kb_generates_one_file_ge29(tmp_path: Path) -> None:
    """An empty KB yields the one root artifact and no topic index (GE-29, T-37)."""
    kb = _mkdir(tmp_path / "Empty")
    report = regenerate_all(kb)

    assert sorted(report.written) == ["tags.md"]
    assert (kb / "tags.md").read_bytes().decode("utf-8") == _read_golden("empty_tags.md")
    assert not (kb / "index.md").exists()


def test_regenerate_all_takes_no_lock_ge30() -> None:
    """The flush documents a sole-writer contract; it acquires nothing itself (GE-30, MA-15)."""
    source = inspect.getsource(sys.modules[regenerate_all.__module__])
    for primitive in ("fcntl", "threading", "asyncio", "Lock(", "flock"):
        assert primitive not in source
    assert "sole writer" in (regenerate_all.__doc__ or "")


def test_regenerate_all_accepts_a_prepared_snapshot_ge30(tmp_path: Path) -> None:
    """One walk feeds validation, generation and maintenance alike (decision C)."""
    kb = write_kb(tmp_path / "KB", SAMPLE_KB_FILES)
    snapshot = scan(kb)
    assert regenerate_all(kb, snapshot=snapshot).written
    assert regenerate_all(kb, snapshot=snapshot).written == []


@pytest.mark.superseded
def test_an_unwritable_topic_does_not_abort_the_flush_ma14(tmp_path: Path) -> None:
    """One unwritable directory is reported; every other derived file is still regenerated."""
    kb = write_kb(tmp_path / "KB", SAMPLE_KB_FILES)
    locked = kb / "BBQ"
    locked.chmod(0o555)
    try:
        report = regenerate_all(kb)
    finally:
        locked.chmod(0o755)

    assert [f.code for f in report.findings if f.severity is Severity.ERROR] == [
        "DERIVED_WRITE_FAILED"
    ]
    assert sorted(report.written) == [
        "Cooking/index.md",
        "Cooking/sub-topics/Grilling/index.md",
        "index.md",
        "tags.md",
    ]


def test_generate_topic_index_refuses_the_kb_root_pa2(tmp_path: Path) -> None:
    """The knowledge-base root is not a topic root, at any depth of the API (PA-2)."""
    kb = write_kb(tmp_path / "KB", SAMPLE_KB_FILES)
    snapshot = scan(kb)
    with pytest.raises(NotATopicRootError):
        generate_topic_index(kb, snapshot, kb)
    with pytest.raises(NotATopicRootError):
        render_topic_index(snapshot, "Cooking/notes")


# --------------------------------------------------------------------------------------
# MA-10 — maintenance flags
# --------------------------------------------------------------------------------------


def test_maintenance_flags_section_self_clears_ma10(tmp_path: Path) -> None:
    """The section renders the topic's flags and disappears when they are gone (MA-10)."""
    snapshot = scan(write_kb(tmp_path / "KB", SAMPLE_KB_FILES))

    flagged = render_topic_index(snapshot, "Cooking", [ORPHAN_FLAG])
    assert _block(flagged, "Maintenance flags") == [
        "- orphan-asset: `notes/old-idea/media/photo.jpg` "
        "(not referenced by `notes/old-idea/old-idea.md`)"
    ]
    assert "## Maintenance flags" not in render_topic_index(snapshot, "Cooking")


def test_flags_are_routed_to_the_nearest_topic_ma10(tmp_path: Path) -> None:
    """A finding inside a sub-topic flags the sub-topic, not its parent (MA-10)."""
    kb = write_kb(tmp_path / "KB", SAMPLE_KB_FILES)
    snapshot = scan(kb)
    deep = Finding(
        code="BROKEN_LINK",
        severity=Severity.WARNING,
        message="target does not exist",
        rule_id="MA-7",
        path="Cooking/sub-topics/Grilling/notes/summary.md",
    )
    assert flags_for_topic(snapshot, snapshot.topic("Cooking"), [deep]) == []
    grilling = snapshot.topic("Cooking/sub-topics/Grilling")
    assert flags_for_topic(snapshot, grilling, [deep]) == [deep]

    report = regenerate_all(kb, flags=[deep])
    assert "broken-link" in (kb / "Cooking/sub-topics/Grilling/index.md").read_text(
        encoding="utf-8"
    )
    assert "broken-link" not in (kb / "Cooking/index.md").read_text(encoding="utf-8")
    assert report.written


# --------------------------------------------------------------------------------------
# GE-32 — property tests behind the tag rules
# --------------------------------------------------------------------------------------

_SEGMENT = st.from_regex(r"\A[a-z0-9]{1,6}(-[a-z0-9]{1,4})?\Z")
_TAGS = st.lists(
    st.builds(
        lambda namespace, rest: ".".join([namespace, *rest]),
        st.sampled_from([n.value for n in tags.Namespace]),
        st.lists(_SEGMENT, min_size=0, max_size=3),
    ),
    max_size=12,
)


def _parse_rendered(lines: Iterable[str]) -> list[tuple[int, str]]:
    """``(indent, tag)`` for each rendered bullet — the inverse of the tree renderer."""
    out: list[tuple[int, str]] = []
    for line in lines:
        indent = len(line) - len(line.lstrip(" "))
        out.append((indent, line.strip().removeprefix("- ").strip("`")))
    return out


@settings(max_examples=200, deadline=None)
@given(_TAGS)
def test_rendered_tag_tree_round_trips_to_the_ancestor_closure_ge32(raw: list[str]) -> None:
    """Complete ancestor chains, depth ≤ 4, stable order, invariant to input order (GE-32)."""
    lines = tags.render_tag_tree(tags.build_tag_forest(raw))
    parsed = _parse_rendered(lines)

    rendered = [tag for _, tag in parsed]
    # Parses back to exactly the ancestor closure of the input: nothing invented, nothing lost.
    assert sorted(rendered, key=tags.tag_sort_key) == tags.ancestor_closure(raw)
    seen: set[str] = set()
    for indent, tag in parsed:
        assert indent == 4 * tag.count("."), tag  # 4 spaces per level below the section root
        assert tag.count(".") + 1 <= tags.MAX_TAG_DEPTH
        parent = tag.rpartition(".")[0]
        assert not parent or parent in seen, tag  # pre-order: a parent precedes its children
        seen.add(tag)
    assert tags.render_tag_tree(tags.build_tag_forest(list(reversed(raw)))) == lines


@settings(max_examples=25, deadline=None)
@given(st.lists(_SEGMENT, min_size=1, max_size=4, unique=True))
def test_registry_rendering_is_invariant_to_file_order_ge4(segments: list[str]) -> None:
    """The same tag set filed in a different order renders the same registry (GE-4, GE-32).

    Hypothesis reuses one example's tree for the next, so this builds its own temporary directory
    instead of taking the function-scoped ``tmp_path`` fixture.
    """
    forward = {f"T/notes/{name}.md": _note(name, "One", f"topic.t.{name}") for name in segments}
    forward["T/topic.md"] = _summary("T", "A topic", "topic.t")
    backward = dict(reversed(list(forward.items())))

    with tempfile.TemporaryDirectory() as workdir:
        root = Path(workdir)
        first = render_root_tags(scan(write_kb(root / "one", forward)))
        second = render_root_tags(scan(write_kb(root / "two", backward)))
    assert first == second


# --------------------------------------------------------------------------------------
# --update-golden
# --------------------------------------------------------------------------------------


def _update_goldens() -> list[str]:
    """Rewrite the *live* goldens from the current renderers. Used by the script entry point (GE-31).

    Restricted to :data:`_LIVE_GOLDENS` rather than every key :func:`render_goldens` renders: the
    topic-index goldens are Task 7's to regenerate, and a script that quietly overwrote them ahead
    of that rework would make the stale comparison in :func:`test_goldens_match_ge31` stale in the
    other direction — passing against bytes nobody reviewed rather than failing loudly.
    """
    with tempfile.TemporaryDirectory() as workdir:
        rendered = render_goldens(Path(workdir))
    GOLDEN.mkdir(parents=True, exist_ok=True)
    for name in _LIVE_GOLDENS:
        (GOLDEN / name).write_bytes(rendered[name].encode("utf-8"))
    return sorted(_LIVE_GOLDENS)


if __name__ == "__main__":  # pragma: no cover - developer entry point
    if "--update-golden" not in sys.argv[1:]:
        raise SystemExit("usage: python tests/core/test_generators.py --update-golden")
    for updated in _update_goldens():
        print(f"updated {GOLDEN / updated}")
