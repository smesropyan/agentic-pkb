"""The shipped skills: where they live, how one is adopted, and how a broken one is reported.

Eight starter skills ship as package data under ``pkb/agents/skills/<name>/SKILL.md`` (SK-1). They
are **mounted**, not seeded: the runtime routes ``/skills/`` at :func:`packaged_skills_root` and
puts it first in every agent's skill source list, so an untouched shipped skill is whatever the
installed version says (SK-3, RT-6, RT-17).

Why mounting rather than copying them into the human's tree on first run — this is the decision
most likely to be "simplified" back into seeding, so it is worth stating plainly (Q1/C7): the
knowledge base has no version control and no undo (arch D6). A seeded copy the human never touched
is therefore **indistinguishable** from one they rewrote. On the next release the implementation
would have to either overwrite the human's work or never ship an improvement again, and a skill the
human deliberately deleted would silently reappear. Mounting keeps upgrades automatic for everything
untouched; :func:`adopt_skill` makes the fork a decision instead of an accident.

Adoption is permanent (SK-5). The copy shadows the packaged default forever — deepagents resolves
skills last-source-wins by name, with whole-record replacement — so the notice has to reach the
human at the moment they open the file, months later, on a machine where the command output is long
gone. It is written **into** the copy and returned, not just returned.

Nothing here reimplements Layer 1: precedence is :func:`pkb.core.resolve_skills`, the skill layout
predicate is :mod:`pkb.core.paths`, and validation of an adopted copy is
:func:`pkb.core.validate_content` (SK-6, SK-16).
"""

from __future__ import annotations

import atexit
import shutil
from contextlib import ExitStack
from dataclasses import dataclass
from functools import lru_cache
from importlib import resources
from pathlib import Path
from typing import Final

from pkb.contracts import PkbAgentError
from pkb.core import Finding, NotATopicRootError, Severity
from pkb.core import paths as core_paths
from pkb.core.frontmatter import parse

__all__ = [
    "DEFAULT_SKILL_NAMES",
    "DRAFT_FOOTER",
    "AdoptResult",
    "UnknownSkillError",
    "adopt_skill",
    "check_skill_dir",
    "packaged_skills_root",
]

_PACKAGE: Final = "pkb.agents"
"""Anchor for :mod:`importlib.resources`. A literal rather than ``__package__`` so mypy sees a
``str`` and a future move of this module cannot silently re-anchor the mount."""

DEFAULT_SKILL_NAMES: Final[tuple[str, ...]] = (
    "conflict-detection",
    "discovery",
    "ingestion-classification",
    "research",
    "sub-topic-proposal",
    "summarization",
    "tag-proposal",
    "voice",
)
"""The eight starter skills (SK-1), in directory-listing order.

README §2.4's "others as needed (e.g. interviewing)" is deliberately deferred. Names are singular
and effectively permanent: renaming one after a human has adopted it breaks the override link
silently, because deepagents matches overrides by skill name alone.
"""

DRAFT_FOOTER: Final = """---

*This is a starter draft.* It ships with the implementation so that something sensible happens on
day one, and it is not meant to survive contact with the way you actually work. Rewrite it — with
your agent's help — until it says what you want done, in your words. Ask your agent to adopt this
skill and it will place an editable copy in a `skills/` folder: at the knowledge-base root to change
the procedure everywhere, or inside a single topic to change it for that topic alone. From that
moment your copy replaces this text and later shipped improvements no longer reach it; deleting your
copy brings this one back."""
"""The shared closing footer every shipped body ends with (SK-12).

Kept here rather than only in the files so the suite can assert all eight carry it byte-for-byte —
a footer that drifts per skill is how "the human is expected to rewrite this" quietly stops being
said. Plain prose on purpose: the audience is the person rewriting the skill, not a programmer.
"""

_SKILLS_DIRNAME: Final = "skills"

_MATERIALIZED = ExitStack()
"""Keeps an extracted copy of the package data alive for the process, for the zip-import case only.

An ordinary wheel or editable checkout puts the skills on a real filesystem and this stack stays
empty. It exists because deepagents' ``FilesystemBackend`` needs a directory it can ``open()``,
which a zip-imported ``Traversable`` is not.
"""

atexit.register(_MATERIALIZED.close)


class UnknownSkillError(PkbAgentError):
    """No shipped skill by that name (SK-1).

    A :class:`~pkb.contracts.PkbAgentError` so a transport that already translates the agent layer's
    typed errors picks it up without importing this module.
    """


@dataclass(frozen=True, slots=True)
class AdoptResult:
    """What :func:`adopt_skill` did, in a form a CLI, a TUI or an agent can render (SK-4)."""

    name: str
    """The skill's name — unchanged by adoption, because the name *is* the override key."""

    path: str
    """Knowledge-base-relative POSIX path of the adopted ``SKILL.md``."""

    adopted: bool
    """``False`` when a copy already existed and nothing was written (SK-4 refuses to overwrite)."""

    notice: str
    """The fork notice written into the copy as its first body line; ``""`` when nothing was
    written. Returned as well as written so the caller can echo it immediately (SK-5)."""

    message: str
    """One human-readable paragraph naming the shadowing consequence and the way back (SK-5)."""


# --------------------------------------------------------------------------------------
# The packaged mount (SK-3)
# --------------------------------------------------------------------------------------


@lru_cache(maxsize=1)
def packaged_skills_root() -> Path:
    """The directory holding the eight shipped skills, as a real filesystem path (SK-3, RT-6).

    Resolved through :mod:`importlib.resources` so it works from an editable checkout and from an
    installed wheel alike. The runtime mounts the result read-only at ``/skills/`` and passes it as
    the first skill source, which is what makes shipped improvements arrive without touching the
    human's tree — and what makes a KB-side copy of the same name win (SK-3, EX-8, RT-17).
    """
    traversable = resources.files(_PACKAGE).joinpath(_SKILLS_DIRNAME)
    if isinstance(traversable, Path):
        return traversable
    return _MATERIALIZED.enter_context(resources.as_file(traversable))


def _packaged_skill_dir(name: str) -> Path:
    directory = packaged_skills_root() / name
    if name not in DEFAULT_SKILL_NAMES or not (directory / core_paths.SKILL_FILE).is_file():
        raise UnknownSkillError(
            f"No shipped skill named {name!r}. Shipped skills: {', '.join(DEFAULT_SKILL_NAMES)}."
        )
    return directory


# --------------------------------------------------------------------------------------
# Adoption (SK-4, SK-5)
# --------------------------------------------------------------------------------------


def _fork_notice(name: str, *, topic_scoped: bool) -> str:
    scope = "this topic and its sub-topics" if topic_scoped else "this whole knowledge base"
    return (
        f"> **Adopted copy — this file is yours now.** It replaces the shipped `{name}` skill for "
        f"{scope}, permanently: later improvements to the shipped version will never reach it. "
        f"Deleting this folder restores the shipped skill, and that is the only way back — nothing "
        f"here is under version control."
    )


def _with_notice(text: str, notice: str) -> str:
    """Insert ``notice`` as the copy's first body line, leaving the frontmatter untouched (SK-5).

    ``ParsedDocument.body`` is a byte-exact *suffix* of the source text, so slicing the header off
    by length preserves the frontmatter block verbatim — including comments and key order, which a
    parse-and-reserialize round trip would quietly normalize away. deepagents re-parses this file,
    so "quietly normalize" is not hypothetical: a rewritten block that loses ``name`` or
    ``description`` makes the skill vanish from the prompt with no error anywhere (SK-2).
    """
    body = parse(text).body
    header = text[: len(text) - len(body)]
    return header + notice + "\n\n" + body.lstrip("\n")


def adopt_skill(kb_root: Path, name: str, *, topic_path: Path | None = None) -> AdoptResult:
    """Copy one shipped skill into the knowledge base so the human can rewrite it (SK-4, SK-5).

    The copy lands at ``<kb>/skills/<name>/`` or, with ``topic_path``, at
    ``<topic>/skills/<name>/`` — the same act as authoring a topic-level overload, so one mechanism
    covers both (README §2.4). It is the **only** way a shipped skill reaches the tree; nothing is
    seeded when the daemon starts (SK-3).

    An existing copy is never overwritten and never merged: the result reports ``adopted=False`` and
    the bytes on disk are untouched. There is no undo in this system (arch D6), and a half-merged
    skill the human did not write is worse than a refusal they can act on.

    The fork notice goes **into** the copied file as its first body line as well as into the result
    (SK-5). Whoever ran the command will not be the one who opens the file six months later.

    Raises :class:`UnknownSkillError` for a name that does not ship, and
    :class:`~pkb.core.NotATopicRootError` when ``topic_path`` holds no ``topic.md`` — an overload
    anywhere else is inert, reachable by no expert's source chain (SK-15).
    """
    source = _packaged_skill_dir(name)
    holder = kb_root
    if topic_path is not None:
        holder = topic_path if topic_path.is_absolute() else kb_root / topic_path
        if not core_paths.is_topic_root(holder):
            raise NotATopicRootError(
                f"{holder} holds no {core_paths.TOPIC_FILE}, so a skills/ folder there would be "
                "reachable by no agent."
            )

    destination = holder / _SKILLS_DIRNAME / name
    skill_file = destination / core_paths.SKILL_FILE
    rel_dir = core_paths.rel(kb_root, destination)
    rel_file = core_paths.rel(kb_root, skill_file)

    if destination.exists():
        return AdoptResult(
            name=name,
            path=rel_file,
            adopted=False,
            notice="",
            message=(
                f"{rel_dir}/ already exists, so nothing was written — adoption never overwrites and "
                f"there is no version history to fall back on. Delete {rel_dir}/ first if you meant "
                f"to start again from the shipped text."
            ),
        )

    notice = _fork_notice(name, topic_scoped=topic_path is not None)
    destination.parent.mkdir(parents=True, exist_ok=True)
    # The whole directory, because override is whole-record replacement and a skill may bundle
    # files beside its SKILL.md — minus the noise Layer 1's walk ignores anyway (PA-16).
    shutil.copytree(source, destination, ignore=shutil.ignore_patterns(".*", "__pycache__"))
    copied = skill_file.read_text(encoding="utf-8")
    skill_file.write_text(_with_notice(copied, notice), encoding="utf-8")

    scope = "for this topic" if topic_path is not None else "everywhere in this knowledge base"
    return AdoptResult(
        name=name,
        path=rel_file,
        adopted=True,
        notice=notice,
        message=(
            f"Copied the shipped {name!r} skill to {rel_dir}/. Your copy replaces the shipped one "
            f"{scope} from now on, so later improvements to the shipped version will not reach it. "
            f"Delete {rel_dir}/ to go back to the shipped skill — that is the only way back."
        ),
    )


# --------------------------------------------------------------------------------------
# Skill-health diagnostics Layer 1 cannot see (SK-15)
# --------------------------------------------------------------------------------------


def check_skill_dir(kb_root: Path, skill_dir: Path) -> list[Finding]:
    """Report the two ways a skill can be present on disk and still never load (SK-15).

    Layer 1 checks that a ``SKILL.md`` declares ``name`` and ``description`` (VA-6) and stops there,
    because both of these depend on how *deepagents* resolves skills rather than on anything the
    knowledge base means:

    * ``SKILL_NAME_MISMATCH`` — the frontmatter ``name`` differs from the directory name. deepagents
      matches overrides by name and logs a warning nobody reads; the skill simply does not shadow
      the one the human meant to override, with no visible error.
    * ``INERT_SKILL_OVERLOAD`` — a ``skills/`` folder somewhere that is neither the knowledge-base
      root nor a topic root. :func:`pkb.core.resolve_skills` walks the topic-root chain, so no
      expert's source list ever reaches it (PA-14).

    Both are warnings: the tree is well-formed and nothing should be refused, but the human's
    intent is not in effect. Returns ``[]`` for a directory holding no case-exact ``SKILL.md`` —
    that is Layer 1's VA-6/PA-14 territory, not a resolution problem.
    """
    if not core_paths.has_case_exact_file(skill_dir, core_paths.SKILL_FILE):
        return []

    skill_file = skill_dir / core_paths.SKILL_FILE
    rel_file = core_paths.rel(kb_root, skill_file)
    findings: list[Finding] = []

    declared = str(
        (parse(skill_file.read_text(encoding="utf-8")).raw or {}).get("name", "")
    ).strip()
    if declared and declared != skill_dir.name:
        findings.append(
            Finding(
                code="SKILL_NAME_MISMATCH",
                severity=Severity.WARNING,
                message=(
                    f"This skill declares name {declared!r} but sits in a directory named "
                    f"{skill_dir.name!r}, so it overrides neither."
                ),
                rule_id="SK-15",
                path=rel_file,
                field="name",
                value=declared,
                hint=(
                    f"Rename the directory to {declared!r}, or set name: {skill_dir.name} — the two "
                    "must match exactly."
                ),
            )
        )

    holder = skill_dir.parent.parent
    rel_holder = core_paths.rel(kb_root, holder)
    reachable = rel_holder == "." or core_paths.is_topic_root(holder)
    if skill_dir.parent.name == _SKILLS_DIRNAME and not reachable:
        findings.append(
            Finding(
                code="INERT_SKILL_OVERLOAD",
                severity=Severity.WARNING,
                message=(
                    f"This skill sits under {rel_holder}/, which is not a topic root, so no agent "
                    "ever loads it."
                ),
                rule_id="SK-15",
                path=rel_file,
                hint=(
                    "Move it to the knowledge-base root's skills/ folder to change every topic, or "
                    "into a topic root's skills/ folder to change that topic."
                ),
            )
        )
    return findings
