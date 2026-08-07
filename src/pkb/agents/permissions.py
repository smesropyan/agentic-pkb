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
"""

from __future__ import annotations

from typing import Final

from deepagents import FilesystemPermission
from wcmatch import glob as wcglob

from pkb.agents.paths import SKILLS_MOUNT, to_backend_path

__all__ = [
    "DERIVED_DENY_GLOBS",
    "SKILLS_DENY_GLOBS",
    "kb_permissions",
]

DERIVED_DENY_GLOBS: Final[tuple[str, ...]] = (
    to_backend_path("**/index.md"),
    to_backend_path("**/tags.md"),
)
"""The I3 deny set, derived from ``pkb.core.is_derived_name`` rather than restated (RT-11, RT-12).

``is_derived_name`` is True for any file named ``index.md`` anywhere, plus the root ``tags.md``.
Two globs cover it, and ``tests/agents/test_permissions.py`` proves the equivalence over a walk of a
real tree rather than trusting the reading — if Layer 1 widens the predicate, that test fails.

Two things about the pair are easy to "fix" back into bugs:

* **There is no separate root-index glob.** wcmatch's ``GLOBSTAR`` matches *zero* directories, so
  ``/kb/**/index.md`` already matches ``/kb/index.md``. Architecture I3's three-glob list is one glob
  redundant — adding it back is harmless but noise.
* **The second glob is deliberately wider than the predicate.** ``is_derived_name`` excludes a
  per-topic ``tags.md`` (Layer 1's C14/PA-11: no generator owns one), but Layer 1 rejects it after
  the fact as a reserved name (VA-27). Without this glob a scripted ``write_file`` of
  ``Cooking/tags.md`` was verified to land on disk — a file that looks authoritative, is maintained
  by nobody, and shadows the root registry. Denying it up front is cheaper than a validation finding
  on a file that already exists. So the equivalence is *deny ⊇ derived*, with this one asserted
  extra.
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
