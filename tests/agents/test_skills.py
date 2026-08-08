"""The ``pkb.agents`` write-site allow-list — RT-18.

RT-18's normative half is behavioural and is asserted elsewhere: permissions are enforced at the tool
layer inside ``FilesystemMiddleware``, never at the backend (``test_permissions.py``'s RT-11 … RT-19
block, and the MW-25 … MW-27 middleware tests). Its *corollary* — "no other ``pkb.agents`` code may
write under ``kb_root``" — is a property of the source, and the rule spells out how to check it:
"AST/grep: no ``open(...,'w')``, ``Path.write_text``, or ``backend.write`` in ``pkb.agents`` outside
``maintenance.py``'s ``flush`` call and ``tools/topics.py``'s scaffold call."

That audit is what this module implements, and it is what stops the *next* direct writer — the one
added to ``scans.py`` or ``runtime.py`` next month — from landing green. It is deliberately an AST
walk rather than the text grep the rule sketches, for the reason the package's other source audits
give (``test_runtime.py``'s RT-33 audit, ``test_expert.py``'s RG-22 audit): the forbidden spellings
now appear legitimately in docstrings that *explain* the rule, and a text grep teaches the next
author to delete the explanation rather than the breach. It also catches a real call that a grep for
``write_text(`` would miss because it is spelled oddly.

**Two corrections to RT-18's own allow-list**, both recorded here because a test written against the
rule's literal two-name list would fail on correct code:

* ``maintenance.py``'s flush and ``tools/topics.py``'s scaffold do not write *directly* — they call
  :func:`pkb.core.flush` and :func:`pkb.core.scaffold_topic`, so neither trips the pattern list at
  all. Neither does ``runtime.py``'s startup :func:`pkb.core.regenerate_all` (RT-7), which RT-18's
  prose does not name but plainly sanctions on the same grounds. The second audit below pins that
  delegation set, which is the corollary's real content.
* ``skills.py``'s :func:`~pkb.agents.skills.adopt_skill` is the one genuine direct writer, and it is
  a carve-out RT-18's assertion column omits rather than a violation. SK-4 is error-severity and
  *requires* the shipped skill directory to be copied into the tree; ``pkb.core`` exposes no adoption
  primitive to delegate to, so the write cannot move without inventing a Layer 1 function. It is
  human-initiated (registered as no agent tool — see the RG-22-style reachability assertion below),
  it lands only under ``skills/``, which SK-17 excludes from every generated artifact and no deny
  glob covers, and it correctly takes **no** write lock: RT-51 scopes the lock to ``pkb.core.flush``
  and ``pkb.core.scaffold_topic`` only, so taking it here would violate RT-51 rather than honour it.

**A third and fourth writer, added 2026-08-07 with large-source ingestion**, and named here because
the design that adds them says in as many words that a rule silently violated is worse than a rule
changed. ``ingestion.py``'s ``_write`` lands the source file and ``_copy_original`` lands LS-1's copy
of the original beside it. Both are the amendment RT-18 was always going to need: its *intent* was
"no ad-hoc writers" — a middleware or a scan pass deciding to fix up an index — not "one writer
forever", and the design names the chunked ingestion workflow as the writer explicitly, because the
copy is a binary (``write_file`` takes text, and MW-7 intercepts exactly ``write_file``/``edit_file``)
and because the copy is a *deterministic consequence* of LS-6 rather than a judgement, so routing it
through a tool call the model must remember to make buys nothing.

What makes them safe is that they meet every property that made ``adopt_skill``'s carve-out safe,
and two more the copy needs:

* they write only under ``<topic>/references/<slug>/``, never a derived file and never outside the
  topic that is doing the reading (RT-15's scope, kept structurally rather than by permission);
* ``_write`` calls :func:`pkb.core.validate_content` and refuses on an error finding, so nothing
  lands that the tool layer would have refused (MW-9/MW-13), and it calls
  :func:`pkb.agents.gates.requires_approval` and refuses on a gate, so nothing lands that a human
  would have had to approve (RT-21);
* both take the process-wide write lock, exactly as the scaffolder does (RT-51);
* both record what they wrote in ``kb_touched``, so MW-17 … MW-20's single flush stamps and indexes
  it — a copy made any other way is invisible to the flush, and LS-1's second amendment names that
  precise hole.

The carve-outs are scoped by function name, not by module, so a future direct write anywhere else in
``skills.py`` or ``ingestion.py`` still fails the audit.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from pkb.agents import skills as skills_module
from pkb.agents.skills import adopt_skill
from pkb.core.scaffold import scaffold_topic
from tests.agents.conftest import TODAY

AGENTS_ROOT = Path(skills_module.__file__).parent
"""``src/pkb/agents``, found through the package rather than through the working directory."""

SANCTIONED_WRITE_SITES = {
    ("skills.py", "adopt_skill"),
    ("ingestion.py", "_write"),
    ("ingestion.py", "_copy_original"),
}
"""The only ``pkb.agents`` code that writes to a file itself (see the docstring for each carve-out)."""

SANCTIONED_LAYER1_WRITERS = {
    "middleware/maintenance.py": {"flush"},
    "runtime.py": {"regenerate_all"},
    "tools/topics.py": {"scaffold_subtopic", "scaffold_topic"},
}
"""Who may invoke a Layer 1 generator, and which one. Layer 1 remains the sole writer of derived
files (RT-18's normative half); this pins *which* Layer 2 modules are allowed to ask it to run."""

LAYER1_WRITE_ENTRYPOINTS = frozenset(
    {"flush", "regenerate_all", "scaffold_subtopic", "scaffold_topic"}
)

DIRECT_WRITE_METHODS = frozenset({"write", "write_text", "write_bytes"})
"""``backend.write`` and ``Path.write_text``/``write_bytes`` — RT-18's own pattern list."""

TREE_MUTATORS = frozenset(
    {
        "copy",
        "copy2",
        "copyfile",
        "copyfileobj",
        "copytree",
        "makedirs",
        "mkdir",
        "move",
        "remove",
        "removedirs",
        "rename",
        "renames",
        "replace",
        "rmdir",
        "rmtree",
        "unlink",
    }
)
"""``shutil``/``os`` functions that create, move or destroy a path.

Matched **only** when qualified as ``shutil.x``/``os.x`` or imported from one of those modules by
name — never on a bare attribute — because ``copy``, ``replace`` and ``remove`` are ordinary method
names on dicts, strings and lists, and an audit with false positives is an audit that gets deleted.
The unqualified path-object spellings are covered by :data:`PATH_MUTATORS`.
"""

PATH_MUTATORS = frozenset({"hardlink_to", "mkdir", "rmdir", "symlink_to", "touch", "unlink"})
"""``pathlib.Path`` mutators whose names are distinctive enough to match on the attribute alone.

``rename`` and ``replace`` are deliberately absent: ``str.replace`` is everywhere and matching it
would drown the signal. They are still caught in their ``os.`` spelling above, and any *content*
they could move is caught by :data:`DIRECT_WRITE_METHODS` at the point it was written.
"""

WRITE_MODE_CHARS = frozenset("wax+")


def _module_name(path: Path) -> str:
    return path.relative_to(AGENTS_ROOT).as_posix()


def _sources() -> list[Path]:
    files = sorted(AGENTS_ROOT.rglob("*.py"))
    assert files, f"no sources under {AGENTS_ROOT}"
    return files


def _enclosing_functions(tree: ast.AST) -> dict[ast.AST, str]:
    """Every node mapped to the name of the innermost function containing it.

    Nodes outside any function map to ``"<module>"`` — module-level writes are exactly the kind of
    import-time side effect this audit should be loudest about.
    """
    owner: dict[ast.AST, str] = {}

    def visit(node: ast.AST, current: str) -> None:
        for child in ast.iter_child_nodes(node):
            name = (
                child.name if isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef) else current
            )
            owner[child] = name
            visit(child, name)

    owner[tree] = "<module>"
    visit(tree, "<module>")
    return owner


def _imported_mutators(tree: ast.AST) -> set[str]:
    """Local names bound to an ``os``/``shutil`` mutator by a ``from`` import.

    Keyed on the *local* binding, resolved from the *original* name, so ``from shutil import copytree
    as ct`` is still caught when it is spelled ``ct(src, dst)``. Matching the local name against
    :data:`TREE_MUTATORS` instead would let one ``as`` clause walk straight through the audit.
    """
    local: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module in {"os", "shutil"}:
            local.update(
                alias.asname or alias.name for alias in node.names if alias.name in TREE_MUTATORS
            )
    return local


def _mode_argument(call: ast.Call, *, builtin: bool) -> ast.expr | None:
    """The ``mode`` argument of an ``open`` call, wherever it was spelled.

    ``builtins.open(file, mode)`` puts it second; ``Path.open(mode)`` puts it first. Both accept it
    as a keyword. Returning ``None`` means no mode was passed at all, and both spellings then default
    to ``"r"`` — a read.
    """
    for keyword in call.keywords:
        if keyword.arg == "mode":
            return keyword.value
    index = 1 if builtin else 0
    return call.args[index] if len(call.args) > index else None


def _opens_for_writing(call: ast.Call, *, builtin: bool) -> bool:
    """``open(..., "w")`` and friends, failing **safe** on a mode this audit cannot read.

    A non-literal mode (``open(path, mode)``) counts as a write: the whole point of the rule is that
    a reviewer can answer "does this write?" from the source, and a computed mode means they cannot.
    """
    mode = _mode_argument(call, builtin=builtin)
    if mode is None:
        return False
    if isinstance(mode, ast.Constant) and isinstance(mode.value, str):
        return bool(WRITE_MODE_CHARS & set(mode.value))
    return True


def _is_write_call(call: ast.Call, *, qualified_mutators: set[str]) -> bool:
    func = call.func
    if isinstance(func, ast.Name):
        if func.id == "open":
            return _opens_for_writing(call, builtin=True)
        return func.id in qualified_mutators
    if not isinstance(func, ast.Attribute):
        return False
    if func.attr == "open":
        return _opens_for_writing(call, builtin=False)
    if func.attr in DIRECT_WRITE_METHODS or func.attr in PATH_MUTATORS:
        return True
    return (
        isinstance(func.value, ast.Name)
        and func.value.id in {"os", "shutil"}
        and func.attr in TREE_MUTATORS
    )


def _write_sites() -> set[tuple[str, str]]:
    sites: set[tuple[str, str]] = set()
    for path in _sources():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        owner = _enclosing_functions(tree)
        qualified = _imported_mutators(tree)
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and _is_write_call(node, qualified_mutators=qualified):
                sites.add((_module_name(path), owner.get(node, "<module>")))
    return sites


def _layer1_write_references() -> dict[str, set[str]]:
    """Every *reference* to a Layer 1 write entry point, not only every call of one.

    ``runtime.py`` never calls ``regenerate_all`` in source position — it hands the function to
    ``asyncio.to_thread`` (RT-7's startup repair must not block the loop), and ``tools/topics.py``
    wraps its scaffold in a ``lambda``. An audit that matched only ``ast.Call`` would report the
    runtime as invoking no generator while it regenerates the whole tree on every startup, which is
    the exact "a real call slipped past because it is spelled oddly" failure RT-18 warns about.
    """
    references: dict[str, set[str]] = {}
    for path in _sources():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        imported = {
            alias.asname or alias.name
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom | ast.Import)
            for alias in node.names
        }
        for node in ast.walk(tree):
            name = ""
            if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
                name = node.id
            elif isinstance(node, ast.Attribute):
                name = node.attr
            if name in LAYER1_WRITE_ENTRYPOINTS and (
                name in imported or isinstance(node, ast.Attribute)
            ):
                references.setdefault(_module_name(path), set()).add(name)
    return references


# --------------------------------------------------------------------------------------
# RT-18 — the source audit the rule's test-assertion column asks for
# --------------------------------------------------------------------------------------


def test_only_the_sanctioned_function_writes_a_file_itself_rt18() -> None:
    """No ``pkb.agents`` code writes under ``kb_root`` except ``skills.adopt_skill`` (RT-18).

    The whole point is that this is a *standing* audit rather than a review habit. Layer 1 is the
    sole writer of derived files, and the deny globs only constrain the agent — nothing stops a
    future maintainer from having a middleware or a scan pass "just fix up the index quickly", which
    is precisely the write that would produce a file no generator maintains and no validator saw.

    If this fails, the fix is almost never to widen ``SANCTIONED_WRITE_SITES``: it is to route the
    write through a ``pkb.core`` entry point, which is the only thing that stamps, validates and
    regenerates. Widening is right only for a writer the design named and argued for — ``adopt_skill``
    and the two ingestion sites are the three that exist — and then the docstring above must say why.
    """
    assert _write_sites() == SANCTIONED_WRITE_SITES


def test_the_audit_sees_through_an_odd_spelling_and_past_a_docstring_rt18() -> None:
    """The audit is AST-shaped, so prose about it is safe and a disguised call is not.

    Both halves are load-bearing. This very module, and ``skills.py``'s own docstrings, name
    ``write_text`` and ``copytree`` in prose; a text grep would flag them and teach the next author
    to delete the explanation. And a real write spelled ``getattr``-free but unusually — a keyword
    ``mode``, an alias import, an attribute on a Path built inline — must still be caught.
    """

    def writes(source: str) -> bool:
        """Run one module source through the same two steps the package audit runs."""
        tree = ast.parse(source)
        qualified = _imported_mutators(tree)
        return any(
            isinstance(node, ast.Call) and _is_write_call(node, qualified_mutators=qualified)
            for node in ast.walk(tree)
        )

    assert not writes('"""A docstring naming write_text, copytree and open(path, "w")."""\n')

    for source in [
        'open(path, mode="w")',
        "open(path, mode)",  # a computed mode is unreadable, so it counts as a write
        '(root / "index.md").write_text(text)',
        '(root / "index.md").open("a").write(text)',
        "from shutil import copytree as ct\nct(src, dst)",  # the alias must not launder it
        "import shutil\nshutil.copytree(src, dst)",
        "import os\nos.makedirs(path)",
        "target.unlink()",
    ]:
        assert writes(source), f"the audit misses a real write spelled {source!r}"

    for readonly in [
        'target.open("rb")',
        "path.open()",
        "payload.copy()",
        "text.replace(a, b)",
        "from shutil import ignore_patterns\nignore_patterns('.*')",
    ]:
        assert not writes(readonly), f"the audit false-positives on {readonly!r}"


def test_only_the_sanctioned_modules_invoke_a_layer1_generator_rt18() -> None:
    """The corollary's real content: who may ask Layer 1 to write (RT-18, RT-7).

    ``maintenance.py``'s ``flush``, ``tools/topics.py``'s scaffold and ``runtime.py``'s startup
    ``regenerate_all`` write under ``kb_root`` by delegation, so RT-18's literal pattern list never
    sees them — which is exactly why they need their own allow-list. RT-18's prose names the first
    two; ``regenerate_all`` is RT-7's startup repair and belongs beside them.
    """
    assert _layer1_write_references() == SANCTIONED_LAYER1_WRITERS


def test_the_one_direct_writer_is_reachable_by_no_agent_rt18() -> None:
    """``adopt_skill`` is the carve-out only while it stays human-initiated.

    The moment it is registered as a tool it becomes an agent write that bypasses RT-29's
    ``<topic>/skills/**`` gate — the exception would then have eroded I3 rather than sat beside it.
    So the audit pins reachability, not just the write: nothing outside ``skills.py`` calls it, and
    ``pkb.agents``'s own ``__init__`` does not export it.
    """
    callers = {
        _module_name(path)
        for path in _sources()
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8")))
        if isinstance(node, ast.Call)
        and (node.func.id if isinstance(node.func, ast.Name) else getattr(node.func, "attr", ""))
        == "adopt_skill"
    }
    assert callers == set(), f"adopt_skill is called from {sorted(callers)}"

    import pkb.agents as package

    assert not hasattr(package, "adopt_skill")


# --------------------------------------------------------------------------------------
# RT-18 / SK-4 — the carve-out's blast radius, asserted rather than assumed
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize("scope", ["root", "topic"])
def test_adopting_a_skill_writes_only_under_a_skills_folder_rt18(kb: Path, scope: str) -> None:
    """The one sanctioned direct write touches ``skills/`` and nothing else (RT-18, SK-4, SK-17).

    This is what makes the carve-out safe to grant. ``skills/`` is excluded from every generated
    artifact (SK-17), so a copy landing there cannot collide with a derived file, cannot change what
    a topic index or a tag page says, and cannot alter a single byte of human content. Asserted as a
    before/after diff of the whole tree, because "it only writes under ``skills/``" is a claim about
    everything it did *not* touch.
    """
    scaffold_topic(kb, "Trading", title="Trading", description="Positions", today=TODAY)
    before = {path: path.read_bytes() for path in kb.rglob("*") if path.is_file()}

    topic_path = Path("Trading") if scope == "topic" else None
    result = adopt_skill(kb, "voice", topic_path=topic_path)
    assert result.adopted

    after = {path: path.read_bytes() for path in kb.rglob("*") if path.is_file()}
    assert {path: text for path, text in before.items() if after.get(path) != text} == {}, (
        "adopting a skill modified a file that already existed"
    )
    created = sorted(path.relative_to(kb).as_posix() for path in set(after) - set(before))
    expected_root = "Trading/skills/" if scope == "topic" else "skills/"
    assert created, "adoption wrote nothing"
    assert all(path.startswith(expected_root) for path in created), created
