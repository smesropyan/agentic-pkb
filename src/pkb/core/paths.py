"""The shared vocabulary of the knowledge-base tree (rules PA-1 … PA-19).

Every other Layer 1 module asks this one what a path *is*: which directory owns it, what role its
location gives it, which tag and agent id its topic carries, and whether a generator owns its bytes.
Nothing here parses markdown and nothing here writes.

Two habits are deliberate and load-bearing:

* **Case-exact directory listings.** The development host is case-insensitive APFS, so
  ``Path.exists()`` happily confirms ``notes/Steak/steak.md`` matches its folder while a
  case-sensitive deploy host disagrees. Every name test therefore goes through
  :func:`has_case_exact_entry`, which compares against an ``os.scandir`` listing (PA-17).
* **No directory constants.** Every function that needs to know where the tree starts takes an
  explicit ``kb_root`` (CX-3). The names below are file and directory *names*, never locations.
"""

from __future__ import annotations

import os
import re
import unicodedata
from collections.abc import Collection
from pathlib import Path, PurePath, PurePosixPath
from urllib.parse import quote

from pkb.core.errors import NotATopicRootError
from pkb.core.models import FileClass, FileRole

__all__ = [
    "EXPERT_FILE",
    "IGNORED_NAMES",
    "INDEX_FILE",
    "LIBRARIAN_AGENT_ID",
    "MARKDOWN_SUFFIX",
    "MAX_SLUG_LENGTH",
    "MEDIA_DIR",
    "NOTES_DIR",
    "REFERENCES_DIR",
    "RESERVED_ITEM_NAMES",
    "RESERVED_NAMES",
    "SKILLS_DIR",
    "SKILL_FILE",
    "STRUCTURAL_DIRS",
    "SUBTOPICS_DIR",
    "SUMMARY_FILE",
    "TAGS_FILE",
    "TOPIC_FILE",
    "TOPIC_TAG_NAMESPACE",
    "agent_id_for",
    "classify",
    "dir_names",
    "extension_folders",
    "find_topic_roots",
    "has_case_exact_entry",
    "has_case_exact_file",
    "is_derived_name",
    "is_generated",
    "is_ignored",
    "is_reserved_item_name",
    "is_topic_root",
    "link_target",
    "main_file_for_item",
    "owning_topic_root",
    "path_for_topic_tag",
    "rel",
    "resolve_expert",
    "resolve_skills",
    "slugify",
    "sort_key",
    "topic_path_for_agent_id",
    "topic_tag_for",
]

# --------------------------------------------------------------------------------------
# Names
# --------------------------------------------------------------------------------------

SUBTOPICS_DIR = "sub-topics"
"""Literal directory name for nested topic roots (PA-4). Elided from tags and agent ids (C9)."""

NOTES_DIR = "notes"
REFERENCES_DIR = "references"
MEDIA_DIR = "media"
SKILLS_DIR = "skills"

STRUCTURAL_DIRS: frozenset[str] = frozenset(
    {REFERENCES_DIR, NOTES_DIR, MEDIA_DIR, SKILLS_DIR, SUBTOPICS_DIR}
)
"""Directory names with structural meaning inside a topic root (PA-6).

They contribute no segment to a ``topic.*`` tag, and any directory directly under a topic root that
is *not* one of them is an extension folder (PA-7).
"""

TOPIC_FILE = "topic.md"
INDEX_FILE = "index.md"
EXPERT_FILE = "expert.md"
TAGS_FILE = "tags.md"
SUMMARY_FILE = "summary.md"
SKILL_FILE = "SKILL.md"
MARKDOWN_SUFFIX = ".md"

RESERVED_NAMES: frozenset[str] = frozenset(
    {TOPIC_FILE, INDEX_FILE, EXPERT_FILE, TAGS_FILE, SUMMARY_FILE}
)
"""File names reserved by the structure — none may be used as an item name (PA-19).

These are full file *names*. An item is identified by its stem (``notes/steak.md`` and
``notes/steak/`` are both the item ``steak``), so name checks go through
:func:`is_reserved_item_name`, which accepts either form.
"""

RESERVED_ITEM_NAMES: frozenset[str] = frozenset(
    name.removesuffix(MARKDOWN_SUFFIX) for name in RESERVED_NAMES
)
"""The stems of :data:`RESERVED_NAMES` — the form an item name actually takes (PA-19)."""

IGNORED_NAMES: frozenset[str] = frozenset({"__pycache__"})
"""Explicitly ignored entry names (PA-16). Dot-prefixed entries are ignored by rule, not by name."""

TOPIC_TAG_NAMESPACE = "topic"
LIBRARIAN_AGENT_ID = "librarian"
"""The agent id of the knowledge-base root itself (PA-10)."""

MAX_SLUG_LENGTH = 80
"""Slug length cap (PA-8, Q14)."""

_AGENT_ID_PREFIX = "topic"
_SLUG_ALPHABET = frozenset("abcdefghijklmnopqrstuvwxyz0123456789-")
_DASH_RUN = re.compile(r"-+")
_SEPARATOR_CHARS = "_/\\"

# Discovery never descends into these (PA-5). ``sub-topics`` is deliberately absent: it is the
# documented route to a nested topic root.
_NO_DESCENT = frozenset({REFERENCES_DIR, NOTES_DIR, MEDIA_DIR, SKILLS_DIR})

# The two directories VA-36 re-opens: a ``topic.md`` misfiled under ``notes/`` or ``references/`` is
# still discovered, so it is never invisible. ``media/`` and ``skills/`` stay closed on both walks —
# VA-6 keeps skills out of tag generation entirely and GE-15 excludes them from the topic index, so
# a ``topic.md`` there is not a topic by any rule. ``pkb.core.scan.RECORD_ONLY_DIRS`` states the
# same policy from the walk's side; the two must move together or the two walks disagree.
_MISPLACED_DESCENT = frozenset({REFERENCES_DIR, NOTES_DIR})

# The directories neither walk ever enters looking for a topic. ``pkb.core.scan.RECORD_ONLY_DIRS``
# is the same set: their contents are recorded as files, but nothing inside them is ever a topic.
_UNDISCOVERABLE_DIRS = _NO_DESCENT - _MISPLACED_DESCENT


# --------------------------------------------------------------------------------------
# Path arithmetic
# --------------------------------------------------------------------------------------


def sort_key(name: str) -> tuple[str, str]:
    """The one sibling order used everywhere: case-insensitive, then codepoint (PA-5, GE-4).

    Exported because generators, discovery and the tag renderer must agree byte-for-byte;
    ``casefold`` keeps it locale-independent.
    """
    return (name.casefold(), name)


def _normalize(path: Path) -> Path:
    """Absolute and lexically normalized — never ``resolve()``.

    Symlink resolution would rewrite a caller's ``kb_root`` (macOS ``/var`` → ``/private/var``) and
    make otherwise-equal paths compare unequal.
    """
    return Path(os.path.normpath(path.absolute()))


def rel(kb_root: Path, path: Path) -> str:
    """``path`` as a knowledge-base-relative POSIX string — the form every model field uses.

    Returns ``"."`` for ``kb_root`` itself. Raises :class:`ValueError` when ``path`` is outside the
    tree; that is a caller bug, not a content defect (CX-5).
    """
    root = _normalize(kb_root)
    target = _normalize(path)
    try:
        relative = target.relative_to(root)
    except ValueError:
        raise ValueError(f"{path} is not inside the knowledge base at {kb_root}") from None
    return relative.as_posix()


def _rel_or_none(kb_root: Path, path: Path) -> str | None:
    try:
        return rel(kb_root, path)
    except ValueError:
        return None


def _rel_parts(relative: str) -> tuple[str, ...]:
    """Split a value returned by :func:`rel`. ``"."`` yields an empty tuple."""
    return PurePosixPath(relative).parts if relative != "." else ()


def is_ignored(name: str, *, ignored: Collection[str] = IGNORED_NAMES) -> bool:
    """True for entries every walk skips: dot-prefixed names and ``__pycache__`` (PA-16).

    The set is configurable so a host with its own noise can extend it without a fork; the default
    is what keeps ``.DS_Store`` out of every golden file.
    """
    return name.startswith(".") or name in ignored


def is_reserved_item_name(name: str) -> bool:
    """True when ``name`` may not be used as an item name at any depth (PA-19).

    Accepts either the bare item name (``topic``) or a file name (``topic.md``).
    """
    return name in RESERVED_NAMES or name in RESERVED_ITEM_NAMES


def slugify(name: str) -> str:
    """Map a display name to a tag / id segment (PA-8).

    NFKD-decompose, drop combining marks, casefold, turn whitespace / ``_`` / ``/`` / punctuation
    into ``-``, discard everything still outside ``[a-z0-9-]``, collapse and strip ``-``, cap at
    :data:`MAX_SLUG_LENGTH`.

    Dropping (rather than transliterating) unmapped characters is what guarantees the result always
    matches the tag-segment regex of TG-4 — the property the tag layer relies on. A name with no
    mappable characters slugifies to ``""``; callers that need a non-empty segment (SC-11) must
    check.
    """
    decomposed = unicodedata.normalize("NFKD", name)
    without_marks = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    folded = without_marks.casefold()
    separated = "".join("-" if _is_separator(ch) else ch for ch in folded)
    kept = "".join(ch for ch in separated if ch in _SLUG_ALPHABET)
    collapsed = _DASH_RUN.sub("-", kept).strip("-")
    return collapsed[:MAX_SLUG_LENGTH].rstrip("-")


def _is_separator(ch: str) -> bool:
    if ch.isspace() or ch in _SEPARATOR_CHARS:
        return True
    return unicodedata.category(ch)[0] in "PS"


def link_target(from_dir: Path, to_path: Path) -> str:
    """A relative, POSIX, percent-encoded markdown link target (PA-18).

    Relative-only is what makes derived files independent of where the tree lives on disk, which is
    half of GE-4's determinism. Each segment is percent-encoded, so spaces, non-ASCII and the
    parentheses that would otherwise terminate a markdown link all survive; the link *text* stays
    human-readable because it never passes through here.
    """
    relative = os.path.relpath(_normalize(to_path), _normalize(from_dir))
    parts = PurePath(relative).parts
    if not parts:
        return "."
    return "/".join(quote(part, safe="") for part in parts)


def main_file_for_item(item_dir: Path) -> Path:
    """The main content file of a folder-hosted item: ``<dir>/<dir>.md`` (PA-17, VA-16).

    Pure path arithmetic — it names the file the convention requires, whether or not it exists.
    """
    return item_dir / f"{item_dir.name}{MARKDOWN_SUFFIX}"


# --------------------------------------------------------------------------------------
# Directory listings (case-exact, PA-17)
# --------------------------------------------------------------------------------------


def _entry_names(directory: Path) -> frozenset[str]:
    try:
        with os.scandir(directory) as entries:
            return frozenset(entry.name for entry in entries)
    except OSError:
        return frozenset()


def _file_names(directory: Path) -> frozenset[str]:
    try:
        with os.scandir(directory) as entries:
            return frozenset(entry.name for entry in entries if entry.is_file())
    except OSError:
        return frozenset()


def dir_names(directory: Path) -> list[str]:
    """Real subdirectory names, in :func:`sort_key` order (PA-5). Ignoring is the caller's job.

    ``follow_symlinks=False`` is load-bearing, not defensive. ``DirEntry.is_dir()`` follows links,
    so a symlink to a directory *inside* the tree would be listed as a directory here, alias a
    second topic root at a second path, and split one topic's files across two addresses. The
    depth-first walk then records the alias, the identity guard refuses the original, and every
    regeneration rewrites both indexes — the tree never converges (GE-5). A link is a link: it is
    not a directory the walk owns.
    """
    try:
        with os.scandir(directory) as entries:
            names = [entry.name for entry in entries if entry.is_dir(follow_symlinks=False)]
    except OSError:
        return []
    return sorted(names, key=sort_key)


def has_case_exact_entry(directory: Path, name: str) -> bool:
    """True when ``directory`` lists an entry named exactly ``name`` (PA-17).

    ``Path.exists()`` cannot answer this: on case-insensitive APFS it accepts ``Steak.md`` for
    ``steak.md``, so a folder-hosted item that is broken on a case-sensitive host would pass here.
    A missing or unreadable directory answers False rather than raising.
    """
    return name in _entry_names(directory)


def has_case_exact_file(directory: Path, name: str) -> bool:
    """True when ``directory`` lists a *file* named exactly ``name`` (PA-17).

    The file-only sibling of :func:`has_case_exact_entry`, and the one to use whenever the answer
    decides that something will later be *read*. A directory named ``expert.md`` or ``SKILL.md`` is
    a legal tree (PA-7 makes any unknown directory an extension folder, and VA-38 flags only loose
    *files*), so the name alone cannot be trusted: handing such a path to Layer 2 as a prompt buys
    an ``IsADirectoryError`` (PA-13, PA-14). :func:`is_topic_root` has always made this distinction
    for ``topic.md``; expert and skill resolution make it here.
    """
    return name in _file_names(directory)


# --------------------------------------------------------------------------------------
# Topic roots
# --------------------------------------------------------------------------------------


def is_topic_root(path: Path) -> bool:
    """True iff ``path`` directly contains a file named exactly ``topic.md`` (PA-3).

    ``topic.md`` is the sole structural marker of topichood: ``notes/`` and ``references/`` do not
    make a directory a topic, and a directory holding only ``topic.md`` is one. The knowledge-base
    root is never a topic root (PA-2) — it holds no ``topic.md`` — and the tree functions below
    never consider it one regardless of what is on disk.
    """
    return TOPIC_FILE in _file_names(path)


def find_topic_roots(kb_root: Path, *, include_misplaced: bool = False) -> list[Path]:
    """Every topic root, depth-first pre-order, parent before child (PA-5).

    Discovery is recursive, not depth-1: a sub-topic is a full topic root with its own agent id
    (C1). Siblings are ordered by :func:`sort_key`, so the result is stable across filesystems.
    The walk descends into ``sub-topics/`` and into extension folders but never into
    ``references/``, ``notes/``, ``media/``, ``skills/`` or ignored entries.

    ``include_misplaced=True`` descends into ``notes/`` and ``references/`` as well, so that a topic
    root misfiled there is discovered rather than invisible (VA-36). It is off by default because
    PA-5 defines the canonical walk. ``media/`` and ``skills/`` stay closed either way: VA-6 keeps
    skills out of tag generation and GE-15 excludes them from the topic index, so a ``topic.md``
    under them is not a topic by any rule.

    The misplaced-inclusive set is deliberately the set :func:`pkb.core.scan.scan` publishes, which
    is what makes PA-9's and PA-10's inverses total: every topic the snapshot hands Layer 2 has a
    tag and an agent id that resolve back to a directory.
    """
    root = _normalize(kb_root)
    found: list[Path] = []
    seen: set[tuple[int, int]] = set()

    def walk(directory: Path) -> None:
        try:
            key = directory.stat()
        except OSError:
            return
        identity = (key.st_dev, key.st_ino)
        if identity in seen:  # a hard-linked or bind-mounted loop must not hang the walk
            return
        seen.add(identity)
        for name in dir_names(directory):
            if is_ignored(name):
                continue
            if name in _NO_DESCENT and not (include_misplaced and name in _MISPLACED_DESCENT):
                continue
            child = directory / name
            if is_topic_root(child):
                found.append(child)
            walk(child)

    walk(root)
    return found


def owning_topic_root(kb_root: Path, path: Path) -> Path | None:
    """The nearest ancestor topic root, or ``None`` for a path owned by no topic (PA-15).

    A directory is considered its own owner; anything else is resolved from its parent, and a path
    that does not exist is treated as a file (``validate_content`` gates writes before the file is
    on disk, VA-1). The knowledge-base root is never returned (PA-2).

    A candidate sitting under ``media/`` or ``skills/`` is skipped rather than returned, and the
    walk continues upward. Discovery cannot reach those directories (PA-5), so a ``topic.md``
    dropped in one is not a topic — and if ownership disagreed, a ``skills/<name>/SKILL.md`` beside
    it would stop resolving as a skill and start collecting the seven required-field errors VA-6
    exists to prevent.
    """
    root = _normalize(kb_root)
    target = _normalize(path)
    if target == root or root not in target.parents:
        return None
    current = target if target.is_dir() else target.parent
    while current != root and root in current.parents:
        if is_topic_root(current) and not _under_undiscoverable_dir(root, current):
            return current
        current = current.parent
    return None


def _under_undiscoverable_dir(root: Path, candidate: Path) -> bool:
    """True when ``candidate`` lies inside a directory no walk descends into (PA-5, VA-6)."""
    return bool(_UNDISCOVERABLE_DIRS.intersection(candidate.relative_to(root).parts))


def _topic_root_chain(kb_root: Path, topic_path: Path) -> list[Path]:
    """``topic_path`` (when it is a topic root) and every ancestor topic root, nearest first."""
    root = _normalize(kb_root)
    current = _normalize(topic_path)
    chain: list[Path] = []
    while current != root and root in current.parents:
        if is_topic_root(current):
            chain.append(current)
        current = current.parent
    return chain


def extension_folders(topic_path: Path) -> list[str]:
    """Directory names directly under a topic root that are extension folders (PA-7).

    An extension folder is any non-structural, non-ignored directory: human-approved, arbitrarily
    named, open-set. Its presence is never a finding — the only rule that binds it is the
    folder-hosted item convention (VA-16). Returned sorted by :func:`sort_key`; GE-24 reads this
    list to decide which tag leaves carry the extension marker.
    """
    return [
        name
        for name in dir_names(topic_path)
        if not is_ignored(name) and name not in STRUCTURAL_DIRS
    ]


# --------------------------------------------------------------------------------------
# Addressing
# --------------------------------------------------------------------------------------


def _topic_segments(kb_root: Path, topic_path: Path) -> list[str]:
    relative = rel(kb_root, topic_path)
    if relative == ".":
        raise NotATopicRootError(
            "the knowledge base root is not a topic root and carries no topic tag (PA-2)"
        )
    # PA-6: *every* structural directory is elided, not only ``sub-topics``. PA-9 defines the tag
    # as the topic-folder names from the root down, and ``notes``/``references``/``media``/
    # ``skills`` are not topic-folder names. A misplaced topic root is still discovered (VA-36), so
    # narrowing this to ``sub-topics`` would mint ``topic.cooking.notes.smuggled`` — a structural
    # directory promoted to a knowledge node in the ontology GE-21 pins, two of TG-3's four levels
    # burnt, and every file inside it forced to choose between the tag PA-6 requires and the one
    # VA-15 checks against. Do not "restore" the single-name filter from PA-9's phrasing.
    segments = [slugify(part) for part in _rel_parts(relative) if part not in STRUCTURAL_DIRS]
    kept = [segment for segment in segments if segment]
    if not kept:
        raise NotATopicRootError(f"{relative!r} yields no addressable topic segment (PA-8)")
    return kept


def topic_tag_for(kb_root: Path, topic_path: Path) -> str:
    """The ``topic.*`` tag of a topic root (PA-9).

    ``topic.`` plus the slugified *topic-folder* names from the root down, with every
    :data:`STRUCTURAL_DIRS` segment elided: ``Cooking/sub-topics/Grilling`` →
    ``topic.cooking.grilling``, and the misplaced ``Cooking/notes/Smuggled`` (VA-36) →
    ``topic.cooking.smuggled``. Eliding ``sub-topics`` is what keeps a three-level tree inside the
    four-level tag budget (TG-3, C9); eliding the rest is PA-6.

    Pure path arithmetic — it does not require the topic to exist, so the scaffolder can check a
    prospective tag before creating anything (SC-9).
    """
    return f"{TOPIC_TAG_NAMESPACE}." + ".".join(_topic_segments(kb_root, topic_path))


def path_for_topic_tag(kb_root: Path, tag: str) -> Path | None:
    """The topic root a ``topic.*`` tag addresses, or ``None`` when no topic carries it (PA-9).

    The inverse has to consult the tree because :func:`slugify` is lossy — ``Heat Management`` and
    ``heat_management`` share a slug — so only an existing topic can be named. Resolution order is
    :func:`find_topic_roots` order over the misplaced-inclusive set, which makes the answer
    deterministic even for a tree that (illegally) contains two folders with the same slug.

    ``include_misplaced=True`` is what makes "round-trips for existing topics" true: the snapshot
    publishes every topic root (VA-36 warns about a misplaced one but keeps it in the catalog), so
    resolving against the narrower PA-5 walk would refuse tags the catalog itself renders.
    """
    wanted = tag.strip()
    for topic in find_topic_roots(kb_root, include_misplaced=True):
        if topic_tag_for(kb_root, topic) == wanted:
            return topic
    return None


def agent_id_for(kb_root: Path, topic_path: Path) -> str:
    """The agent id of a topic root — ``topic/cooking/grilling`` (PA-10).

    The tree with every :data:`STRUCTURAL_DIRS` segment elided (PA-6) and the rest slugified. The
    knowledge-base root's agent is the Librarian. Bijective with :func:`topic_path_for_agent_id`
    over every topic the snapshot publishes, misplaced roots included.
    """
    if _rel_or_none(kb_root, topic_path) == ".":
        return LIBRARIAN_AGENT_ID
    return f"{_AGENT_ID_PREFIX}/" + "/".join(_topic_segments(kb_root, topic_path))


def topic_path_for_agent_id(kb_root: Path, agent_id: str) -> Path:
    """The topic root an agent id addresses (PA-10, inverse of :func:`agent_id_for`).

    Like :func:`path_for_topic_tag` this resolves against the tree — over the same
    misplaced-inclusive set, so the two halves of the bijection can never see different topics —
    because the id carries slugs and not folder names. Raises
    :class:`~pkb.core.errors.NotATopicRootError` for an id no topic answers to — an unaddressable
    agent is a routing bug, not a content defect.
    """
    wanted = agent_id.strip()
    if wanted == LIBRARIAN_AGENT_ID:
        return _normalize(kb_root)
    for topic in find_topic_roots(kb_root, include_misplaced=True):
        if agent_id_for(kb_root, topic) == wanted:
            return topic
    raise NotATopicRootError(f"no topic root resolves to agent id {agent_id!r}")


def resolve_expert(kb_root: Path, topic_path: Path) -> Path | None:
    """The ``expert.md`` governing a topic, or ``None`` when the default template applies (PA-13).

    Nearest ancestor topic root wins, the topic itself first. Pure path resolution: Layer 1 finds
    the file, Layer 2 instantiates the agent — which is why the test is
    :func:`has_case_exact_file` and not :func:`has_case_exact_entry`: a *directory* named
    ``expert.md`` is an extension folder (PA-7), and returning it would hand Layer 2 a path whose
    ``read_text()`` raises.
    """
    for topic in _topic_root_chain(kb_root, topic_path):
        if has_case_exact_file(topic, EXPERT_FILE):
            return topic / EXPERT_FILE
    return None


def _skills_in(skills_dir: Path) -> dict[str, Path]:
    """Skill directories holding a case-exact ``SKILL.md`` *file* (PA-14).

    Flat ``<name>.md`` is legacy (C2). The file-only test is the same one :func:`resolve_expert`
    makes: a directory named ``SKILL.md`` is not a prompt Layer 2 can read.
    """
    return {
        name: skills_dir / name / SKILL_FILE
        for name in dir_names(skills_dir)
        if not is_ignored(name) and has_case_exact_file(skills_dir / name, SKILL_FILE)
    }


def resolve_skills(kb_root: Path, topic_path: Path) -> dict[str, Path]:
    """Skills visible to a topic, keyed by skill name, nearest overload winning (PA-14).

    A skill is a directory — ``skills/<skill-name>/SKILL.md`` — at the knowledge-base root and at
    topic-level overload folders (arch D7 supersedes the flat ``skills/<name>.md`` layout, C2).
    Root skills are the base; each topic root on the path to ``topic_path`` overrides same-named
    entries, outermost first, so the topic's own overload wins. A flat ``skills/voice.md`` is not
    discovered — validation reports it as a legacy layout rather than silently honouring it.
    """
    merged: dict[str, Path] = dict(_skills_in(_normalize(kb_root) / SKILLS_DIR))
    for topic in reversed(_topic_root_chain(kb_root, topic_path)):
        merged.update(_skills_in(topic / SKILLS_DIR))
    return {name: merged[name] for name in sorted(merged, key=sort_key)}


# --------------------------------------------------------------------------------------
# Derived, generated, and the location → role table
# --------------------------------------------------------------------------------------


def is_derived_name(kb_root: Path, path: Path) -> bool:
    """True for paths an agent may never write: any ``index.md``, plus the root ``tags.md`` (PA-11).

    This is the deny set — deliberately wider than what the generators actually write (PA-12,
    C14). ``notes/x/index.md`` is denied even though no generator maintains it, because an item
    named ``index.md`` shadows the machine-generated index (VA-17). A per-topic ``tags.md`` is
    *not* in this set: there is no such artifact, and VA-27 rejects it as a reserved name.
    """
    if path.name == INDEX_FILE:
        return True
    return _rel_or_none(kb_root, path) == TAGS_FILE


def is_generated(kb_root: Path, path: Path) -> bool:
    """True for the three artifacts the generators own: root index, root tags, topic index (PA-12).

    Narrower than :func:`is_derived_name`: an ``index.md`` that is derived-by-name but sits
    somewhere no generator writes is stale content to be flagged (VA-17), never a file Layer 1
    rewrites or deletes.

    "Somewhere no generator writes" includes a directory that holds a ``topic.md`` but that no walk
    reaches — under ``media/`` or ``skills/`` (PA-5). Asking :func:`is_topic_root` alone would call
    such an ``index.md`` generated, and it would then be written by nobody *and* flagged by nobody:
    invisible in exactly the way PA-12 exists to prevent.
    """
    root = _normalize(kb_root)
    relative = _rel_or_none(root, path)
    if relative is None:
        return False
    if relative in (INDEX_FILE, TAGS_FILE):
        return True
    if path.name != INDEX_FILE:
        return False
    parent = _normalize(path).parent
    return is_topic_root(parent) and not _under_undiscoverable_dir(root, parent)


def classify(kb_root: Path, path: Path) -> tuple[FileRole, FileClass]:
    """What a file *is*, decided by where it sits (the VA-13 / VA-14 location table).

    The single place the location → role mapping lives: validation and every generator read it
    rather than re-deriving roles from path shapes, so the two can never drift.

    The edges worth naming:

    * ``references/summary.md`` is ``REFERENCES_SUMMARY``, not a ``REFERENCE`` (VA-13).
    * ``notes/x/index.md`` is ``UNKNOWN`` but class ``DERIVED`` — derived by name (PA-11), owned by
      no generator (PA-12), and reported by VA-17.
    * A markdown file directly at a topic root that is not ``topic.md`` / ``index.md`` /
      ``expert.md`` is ``UNKNOWN`` (VA-38); an unknown *directory* is an extension folder and is
      never a finding (PA-7).
    * ``expert.md`` and everything under ``skills/`` are class ``SKILL``: agent instructions, not
      indexable knowledge, so they are exempt from the seven required fields and from index and tag
      generation (VA-6, C3, C6). ``EXPERT`` is returned only at a topic root — ``expert.md``
      anywhere else is misplaced (VA-20) and classifies by its actual location.

    Total by construction: an unrecognized path is ``(UNKNOWN, AUTHORED)`` or ``(ASSET, ASSET)``,
    never an exception, because a file that vanishes from the walk is invisible to every agent
    (GE-25). A path outside the tree raises :class:`ValueError` (caller bug).
    """
    relative = rel(kb_root, path)
    parts = _rel_parts(relative)
    if not parts or any(is_ignored(part) for part in parts):
        return FileRole.UNKNOWN, FileClass.IGNORED

    is_markdown = path.name.endswith(MARKDOWN_SUFFIX)
    owner = owning_topic_root(kb_root, path)
    if owner is None:
        return _classify_outside_topic(parts, is_markdown=is_markdown)

    inner = _rel_parts(rel(owner, path))
    return _classify_in_topic(inner, is_markdown=is_markdown)


def _classify_outside_topic(
    parts: tuple[str, ...], *, is_markdown: bool
) -> tuple[FileRole, FileClass]:
    if len(parts) == 1:
        if parts[0] == INDEX_FILE:
            return FileRole.ROOT_INDEX, FileClass.DERIVED
        if parts[0] == TAGS_FILE:
            return FileRole.ROOT_TAGS, FileClass.DERIVED
    if parts[0] == SKILLS_DIR:
        return _classify_skill(is_markdown=is_markdown)
    if not is_markdown:
        return FileRole.ASSET, FileClass.ASSET
    if parts[-1] == INDEX_FILE:
        return FileRole.UNKNOWN, FileClass.DERIVED
    return FileRole.UNKNOWN, FileClass.AUTHORED


def _classify_in_topic(inner: tuple[str, ...], *, is_markdown: bool) -> tuple[FileRole, FileClass]:
    if not inner:
        # The topic root directory itself: a location, not a file the role table describes.
        return FileRole.UNKNOWN, FileClass.IGNORED
    if len(inner) == 1:
        name = inner[0]
        if name == TOPIC_FILE:
            return FileRole.TOPIC_OVERVIEW, FileClass.AUTHORED
        if name == INDEX_FILE:
            return FileRole.TOPIC_INDEX, FileClass.DERIVED
        if name == EXPERT_FILE:
            return FileRole.EXPERT, FileClass.SKILL
        if not is_markdown:
            return FileRole.ASSET, FileClass.ASSET
        return FileRole.UNKNOWN, FileClass.AUTHORED

    head = inner[0]
    if head == SKILLS_DIR:
        return _classify_skill(is_markdown=is_markdown)
    if inner[-1] == INDEX_FILE:
        # Derived by name, generated by nobody (PA-11 vs PA-12); VA-17 reports it.
        return FileRole.UNKNOWN, FileClass.DERIVED
    if not is_markdown:
        return FileRole.ASSET, FileClass.ASSET
    is_section_summary = len(inner) == 2 and inner[1] == SUMMARY_FILE
    if head == NOTES_DIR:
        return (
            (FileRole.NOTES_SUMMARY, FileClass.AUTHORED)
            if is_section_summary
            else (FileRole.NOTE, FileClass.AUTHORED)
        )
    if head == REFERENCES_DIR:
        return (
            (FileRole.REFERENCES_SUMMARY, FileClass.AUTHORED)
            if is_section_summary
            else (FileRole.REFERENCE, FileClass.AUTHORED)
        )
    if head in (SUBTOPICS_DIR, MEDIA_DIR):
        return FileRole.UNKNOWN, FileClass.AUTHORED
    return (
        (FileRole.EXTENSION_SUMMARY, FileClass.AUTHORED)
        if is_section_summary
        else (FileRole.EXTENSION_ITEM, FileClass.AUTHORED)
    )


def _classify_skill(*, is_markdown: bool) -> tuple[FileRole, FileClass]:
    """Everything under ``skills/`` is agent instruction, whatever its layout (PA-14, VA-6)."""
    return FileRole.SKILL, (FileClass.SKILL if is_markdown else FileClass.ASSET)
