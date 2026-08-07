"""Invariant I3's teeth: the ``FilesystemPermission`` list every agent is compiled with.

I3 says agents are blocked from writing derived files *at the harness level, never by prompt*
(RT-19). ``FilesystemMiddleware`` checks these rules inside the tool body, after the path has been
normalized, so a model cannot slip past them with ``kb/Cooking/index.md`` the way it can past a naive
middleware prefix test (D-3). That is why I3 lives here and not in a system prompt.

**Read the ordering before touching this list.** ``_check_fs_permission`` is *first-match-wins with a
default of allow*: it walks the rules in order, returns the mode of the first whose operation matches
and whose glob matches, and allows anything that matches nothing. Two consequences with teeth:

* the derived deny must come **before** a topic's write allow, or an expert may rewrite its own
  ``index.md``;
* the knowledge-base-wide deny must come **last**, or it swallows the topic allow and no expert can
  write at all — and the mirror-image mistake, dropping it, silently grants every expert write access
  to every other topic while every test about its *own* topic still passes.

Reads are never denied (RT-13, RT-15). Derived files must stay readable — the Librarian routes off
the root ``index.md`` and each expert reads its own topic index — and breadth-first research needs a
knowledge-base-wide read view. A read deny would also be invisible rather than loud: bulk tools
(``ls``/``glob``/``grep``) filter denied entries out of their results instead of erroring, so the
routing view would just quietly go missing.

Permissions constrain the *agent*, not the process (RT-18). Layer 1's generators write derived files
straight to disk and ``pkb.core.scaffold_topic`` writes a new topic's skeleton; both sit outside this
layer by design, and both are the intended sole writers of what these rules deny.

**A glob list alone is not all of I3.** Two harness facts mean the pattern the middleware matches and
the file the backend opens can be different files, so this module also exports the two predicates
that close the gap — :func:`is_denied_derived` and :func:`resolves_elsewhere`. Both are here rather
than in the middleware because they answer the same question the rules above answer ("may an agent's
write land here?") and must never drift from them; ``tests/agents/test_permissions.py`` pins the
first to the globs by equivalence. See each function for the harness fact it exists to survive.
"""

from __future__ import annotations

from pathlib import Path, PurePosixPath
from typing import Final

from deepagents import FilesystemPermission
from wcmatch import glob as wcglob

from pkb.agents.paths import SKILLS_MOUNT, to_backend_path
from pkb.core.paths import INDEX_FILE, TAGS_FILE

__all__ = [
    "DERIVED_DENY_GLOBS",
    "SKILLS_DENY_GLOBS",
    "is_denied_derived",
    "kb_permissions",
    "resolves_elsewhere",
]

_DENIED_BASENAMES: Final[frozenset[str]] = frozenset({INDEX_FILE, TAGS_FILE})
"""The two file names :data:`DERIVED_DENY_GLOBS` denies at any depth, ASCII-lowercased."""


def _case_insensitive(name: str) -> str:
    """Spell *name* as a glob that matches every ASCII case variant of it.

    ``index.md`` becomes ``[iI][nN][dD][eE][xX].[mM][dD]``. Character classes are the only tool
    available: :func:`deepagents.middleware.filesystem._check_fs_permission` compiles every rule
    with a fixed ``BRACE | GLOBSTAR`` and no ``IGNORECASE``, and Layer 2 cannot pass flags.
    """
    return "".join(f"[{c.lower()}{c.upper()}]" if c.isalpha() else c for c in name)


DERIVED_DENY_GLOBS: Final[tuple[str, ...]] = (
    to_backend_path(f"**/{_case_insensitive(INDEX_FILE)}"),
    to_backend_path(f"**/{_case_insensitive(TAGS_FILE)}"),
)
"""The I3 deny set, derived from ``pkb.core.is_derived_name`` rather than restated (RT-11, RT-12).

``is_derived_name`` is True for any file named ``index.md`` anywhere, plus the root ``tags.md``.
Two globs cover it, and ``tests/agents/test_permissions.py`` proves the equivalence over a walk of a
real tree rather than trusting the reading — if Layer 1 widens the predicate, that test fails.

The contract is *deny ⊇ derived*, widened on exactly two axes, both asserted by that test:

* **Per-topic ``tags.md``.** ``is_derived_name`` excludes it (Layer 1's C14/PA-11: no generator owns
  one), but Layer 1 rejects it after the fact as a reserved name (VA-27). Without this glob a
  scripted ``write_file`` of ``Cooking/tags.md`` was verified to land on disk — a file that looks
  authoritative, is maintained by nobody, and shadows the root registry. Denying it up front is
  cheaper than a validation finding on a file that already exists (RT-12).
* **Case.** ``_check_fs_permission`` matches case-sensitively, but a personal knowledge base lives on
  a case-insensitive filesystem — APFS and NTFS both, i.e. the stated deployment. There
  ``Cooking/INDEX.md`` *is* ``Cooking/index.md``: same inode, same bytes. Matched case-exactly the
  deny let a Topic Expert overwrite its own generated index through a one-character respelling, and
  mint the ``Cooking/tags.md`` the second glob exists to prevent, with ``status='success'`` and no
  finding — I3 defeated at the layer that is supposed to be its floor (verified end-to-end through a
  real ``create_deep_agent`` graph). Worse than the overwrite: where the topic index has not been
  generated yet, a literal ``INDEX.md`` lands beside nothing and wedges Layer 1's generator on
  ``DERIVED_NAME_CASE_COLLISION`` at that startup and every startup after.

  The widening is on the *deny* only. The topic allow (:func:`_topic_subtree_glob`) stays
  case-exact: it is a human-chosen directory name, and matching it loosely would hand an expert
  write access to a neighbour whose name differs only in case.

  Do **not** answer this by widening ``pkb.core.is_derived_name`` instead. Its byte-exactness is
  deliberate (PA-17) — it is what lets the generator *detect* the collision rather than destroy the
  authored file. Layer 2 denies the spelling; Layer 1 reports the one that got in some other way.

One thing about the pair is easy to "fix" back into a bug: **there is no separate root-index glob.**
wcmatch's ``GLOBSTAR`` matches *zero* directories, so ``/kb/**/index.md`` already matches
``/kb/index.md``. Architecture I3's three-glob list is one glob redundant — adding it back is
harmless but noise.
"""

SKILLS_DENY_GLOBS: Final[tuple[str, ...]] = (SKILLS_MOUNT + "**",)
"""The packaged-skill mount is read-only for every agent (RT-17).

Agents read the shipped defaults; a write here would edit the *installation*, changing behaviour for
every knowledge base on the machine. A human who wants to change a skill adopts it into their tree
(SK-4), where it is theirs to edit.
"""


def kb_permissions(topic_path: str | None = None) -> list[FilesystemPermission]:
    """Build the write-permission rules for one agent (RT-11 … RT-17).

    Args:
        topic_path: The topic root's knowledge-base-relative POSIX path, exactly as
            ``pkb.core.models.TopicRecord.path`` gives it — ``Cooking`` or
            ``Cooking/sub-topics/Grilling``. ``None`` builds the Librarian's rules.

    Returns:
        Rules in evaluation order. ``operations=["write"]`` throughout: reads are never denied
        (RT-13), and ``delete`` is covered because deepagents' ``_DEFAULT_FS_TOOL_OPS`` classifies it
        as a write (RT-14) — a recursive delete of a directory holding denied descendants is refused
        outright rather than executed partially.

    A **Topic Expert** (*topic_path* given) gets ``[deny derived, deny skills, allow write its own
    subtree, deny write the knowledge base]``. Write access confined to its own subtree is what makes
    README §1.8 rule 4 — "a solution note lives in exactly one topic, there are no copies" —
    mechanical instead of prompt-level, and it stops a mis-routed ingestion filing into a neighbour's
    tree: the expert has to hand the item back instead. Reads stay knowledge-base-wide.

    Note the topic *directory itself* is not in the allow (``/kb/Cooking/**`` does not match
    ``/kb/Cooking``), so the final deny catches it: an expert can write anything inside its topic but
    cannot delete the topic root out from under itself.

    The **Librarian** (*topic_path* ``None``) gets no write capability in the tree at all (RT-16).
    Its only mutation is the gated ``create_topic`` tool, which calls ``pkb.core.scaffold_topic``
    directly on disk — outside this layer, by RT-18's design. Filing a note needs the topic's skills,
    voice overload and ``expert.md`` behaviour, none of which the Librarian loads, so a Librarian
    write would be a note written without the expertise the note is supposed to carry.

    The rules also reach the auto-added ``general-purpose`` subagent, which inherits ``permissions``
    even though it does *not* inherit our custom middleware (D-2) — I3 holds on that path even where
    validation does not.

    Raises:
        ValueError: If *topic_path* is empty or only slashes. That would produce an allow of
            ``/kb/**`` — silently granting the expert the whole tree — so it fails loudly.
    """
    rules = [
        FilesystemPermission(operations=["write"], paths=list(DERIVED_DENY_GLOBS), mode="deny"),
        FilesystemPermission(operations=["write"], paths=list(SKILLS_DENY_GLOBS), mode="deny"),
    ]
    if topic_path is not None:
        rules.append(
            FilesystemPermission(
                operations=["write"], paths=[_topic_subtree_glob(topic_path)], mode="allow"
            )
        )
    rules.append(
        FilesystemPermission(operations=["write"], paths=[to_backend_path("**")], mode="deny")
    )
    return rules


def is_denied_derived(rel: str) -> bool:
    """Does :data:`DERIVED_DENY_GLOBS` deny a write to this knowledge-base-relative path (RT-11)?

    The globs' predicate, spelled as a function so the two places that must agree with them can ask
    instead of guessing. ``rel`` is a KB-relative POSIX path as ``pkb.agents.paths.to_kb_relative``
    produces it — ``Cooking/INDEX.md``, not ``/kb/Cooking/INDEX.md``.

    Both call sites are early returns that mean "I3 is about to refuse this inside the tool body, so
    say nothing here":

    * ``KbValidationMiddleware._decide`` (MW-11/D-13) — emitting a finding as well would give one
      refused write two ToolMessages that disagree;
    * ``gates.requires_approval`` (RT-35) — gating it would ask a human to approve a write that is
      then denied anyway.

    Both currently ask ``pkb.core.is_derived_name``, which is *narrower* than these globs on two
    axes (a per-topic ``tags.md``, and case). On a case-**sensitive** host that is a live
    contradiction, not a nicety: ``Cooking/INDEX.md`` is denied by the rules above while
    ``is_derived_name`` calls it an ordinary file, so the write draws a validation finding *and* the
    permission denial. The predicate must be this one, not Layer 1's — Layer 1's answers a different
    question (PA-17, and see :data:`DERIVED_DENY_GLOBS`).

    Case is folded with :meth:`str.lower`, which is exactly as wide as the globs' ASCII character
    classes and no wider — a dotless-i or dotted-capital-I spelling of ``index.md`` is denied by
    neither, and the equivalence test asserts they agree about that too. :meth:`str.casefold` would
    not: it maps a long s to ``s``, so the predicate would start denying names the rules allow.
    """
    return PurePosixPath(rel).name.lower() in _DENIED_BASENAMES


def resolves_elsewhere(kb_root: Path, rel: str) -> bool:
    """Would a write to *rel* land on a different file than ``kb_root / rel`` (I3, RT-11, RT-15)?

    True when a symlink — on the file itself or on any ancestor directory — redirects the write.

    **The harness fact this exists for.** ``FilesystemMiddleware`` checks permissions against the
    *virtual* path (``filesystem.py:2012``, ``_check_fs_permission(rules, "write",
    validated_path)``), and then hands that same string to ``FilesystemBackend.write``, whose
    ``_resolve_path`` calls ``Path.resolve()`` — following symlinks — before opening. The backend's
    ``O_NOFOLLOW`` does not help: the link has already been resolved away by the time the ``open``
    happens, so the flag guards a path that is no longer a link. The two therefore disagree about
    which file is being written whenever a link is in the way, and the disagreement is silent.

    ``_resolve_path`` does reject a target outside the root, so the reachable shape is an in-tree
    link to an in-tree file. That is enough to defeat I3 (``Cooking/references/x/x.md`` →
    ``../../index.md`` puts the agent's body in the generated index), RT-15 (→ ``../../../Physics/
    topic.md`` writes outside the expert's own subtree) and every content gate at once
    (→ ``../../notes/steak.md`` overwrites an approved human note with no interrupt, because
    ``requires_approval`` keys ``_is_curated`` off the virtual ``references/`` path, which RT-31
    exempts). Verified end-to-end through a real ``create_deep_agent`` graph.

    No agent tool can create such a link — ``write_file``/``edit_file`` create regular files and
    ``pkb.core`` never symlinks — so it takes a human or an external sync (iCloud, Dropbox) to plant
    one. That is a condition this project already treats as real: ``pkb.core.paths`` and
    ``pkb.core.scan`` both carry load-bearing ``follow_symlinks=False``. Layer 2's write path is
    where that hardening stopped.

    A caller that gets ``True`` should refuse the call, not gate it: a human asked to approve
    ``references/x/x.md`` cannot see that they are approving a write to ``notes/steak.md``.

    Args:
        kb_root: The knowledge-base root on disk.
        rel: A KB-relative POSIX path, as ``pkb.agents.paths.to_kb_relative`` produces it.

    Returns:
        ``False`` only when the path provably resolves to itself. An unresolvable path — a symlink
        loop, an unreadable directory — returns ``True``, because a check that cannot decide must
        not report "safe".

    Both sides are resolved: on macOS a knowledge base is routinely reached through a symlinked
    ancestor (``/tmp`` → ``/private/tmp``), and comparing against an unresolved root would then
    refuse every write in the tree. Layer 1 resolves the root for the same reason.
    """
    try:
        return (kb_root / rel).resolve() != kb_root.resolve() / rel
    except (OSError, RuntimeError, ValueError):
        return True


def _topic_subtree_glob(topic_path: str) -> str:
    """The allow pattern for a topic's own subtree (RT-15).

    The directory names are escaped before they become a glob. A topic folder is a human-chosen name
    and Layer 1 puts no character restriction on it, so ``Cooking [old]`` would otherwise be read by
    wcmatch as a character class: the expert would lose write access to its own topic *and* gain it
    over a neighbour whose name happened to match. That is the silently-permissive failure this
    module's ordering rules exist to prevent, so it is escaped rather than assumed away.

    Surrounding whitespace is stripped along with the slashes purely so that a blank scope is caught
    by the guard below instead of compiling to a glob no path can ever match — which would leave the
    expert unable to write anywhere and look, from the outside, like a broken model.
    """
    scope = topic_path.strip().strip("/")
    if not scope:
        msg = f"expected a topic root's knowledge-base-relative path, got {topic_path!r}"
        raise ValueError(msg)
    return to_backend_path(f"{wcglob.escape(scope)}/**")
