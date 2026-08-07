"""The mount seam between an agent's filesystem view and the knowledge base on disk (RT-8, RT-9).

Layer 1 speaks knowledge-base-root-relative POSIX strings and is forbidden from containing the mount
literal at all (CX-3); the harness speaks backend paths under a ``CompositeBackend`` route. This
module is the single place those two vocabularies meet. :data:`KB_MOUNT` is spelled here and nowhere
else under ``src/pkb`` — every middleware, permission builder, gate and tool converts through the
pair below, so moving the mount is a one-line change rather than a grep.

**Why :func:`to_kb_relative` normalizes instead of testing a prefix (D-3).** The ``file_path`` a
``wrap_tool_call`` middleware reads out of ``request.tool_call["args"]`` is the *raw model string*.
deepagents normalizes it only later, inside the tool body, through
``deepagents.backends.utils.validate_path``. So a model that emits ``kb/Cooking/notes/b.md`` — no
leading slash — reaches the middleware verbatim; a naive ``raw.startswith(...)`` test answers "not a
knowledge-base path", the middleware waves it through, and the tool then normalizes it and writes it
into the tree anyway. That bypass was executed against the pinned harness, not theorised. Calling the
harness's own normalizer first is what makes a middleware see exactly what the tool will see. Do not
replace it with a string test.

Permissions are unaffected by that quirk — ``FilesystemMiddleware`` checks them *after* normalization
— which is why invariant I3 is airtight where a prefix test is not (RT-11).

**A third vocabulary: what the disk calls things.** :func:`to_kb_relative` settles syntax, not
spelling, and on a case-insensitive filesystem two spellings of one path are one file. Everything
Layer 2 decides per path — the gate table's exact-string lookups, ``validate_content``, the flush's
stamp — keys off the string, so the string has to be the one the disk uses.
:func:`canonical_kb_path` is that conversion, and it lives here for the same reason the mount
literal does: one seam, so the consumers cannot drift apart.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Final

from deepagents.backends.utils import validate_path

from pkb.core.paths import has_case_exact_entry

__all__ = [
    "KB_MOUNT",
    "SKILLS_MOUNT",
    "canonical_kb_path",
    "to_backend_path",
    "to_kb_relative",
]

KB_MOUNT: Final = "/kb/"
"""The ``CompositeBackend`` route the knowledge base is mounted on (RT-6, RT-8).

The one occurrence of this literal in ``src/pkb``. Layer 1 never names it (CX-3) and Layer 3 never
sees it, because a backend path is a harness concept and I2 keeps the harness out of the transports.
"""

SKILLS_MOUNT: Final = "/skills/"
"""The read-only route the packaged skills are mounted on (RT-6, SK-3).

Lives beside :data:`KB_MOUNT` because it is the same kind of fact — where the harness sees something
the agent must not treat as ordinary scratch space. Writes to it are denied for every agent (RT-17):
the shipped skills belong to the installation, not to one knowledge base.
"""


def to_backend_path(rel: str) -> str:
    """Map a knowledge-base-relative POSIX path (or glob) to the path an agent sees (RT-8).

    ``Cooking/notes/steak.md`` becomes ``/kb/Cooking/notes/steak.md``. Glob patterns go through the
    same door — ``**/index.md`` becomes one of ``permissions.DERIVED_DENY_GLOBS`` — which is what
    keeps the mount literal out of ``permissions.py`` and RT-8's grep down to one file.

    A leading slash on *rel* is tolerated and stripped, because callers reasonably hold paths in
    either shape; an *empty* *rel* is refused. Empty would yield the mount root itself, and the one
    place that matters is the topic-scoped write allow (RT-15), where ``"" + "/**"`` would silently
    widen an expert's scope from its own subtree to the whole knowledge base. Failing loudly on a
    caller bug beats a permission list that is quietly permissive.

    Raises:
        ValueError: If *rel* is empty or consists only of slashes.
    """
    trimmed = rel.lstrip("/")
    if not trimmed:
        msg = f"expected a knowledge-base-relative path, got {rel!r}"
        raise ValueError(msg)
    return KB_MOUNT + trimmed


def to_kb_relative(raw: object) -> str | None:
    """Map a raw model-supplied backend path to a KB-relative POSIX path, or ``None`` (RT-9, RT-10).

    ``/kb/x.md``, ``kb/x.md``, ``/kb//x.md`` and ``/kb/./x.md`` all yield ``x.md``, because the
    harness's own :func:`~deepagents.backends.utils.validate_path` runs first — see the module
    docstring for the bypass that motivates it.

    ``None`` means "not a file inside the knowledge base", and a caller must treat it as "this is not
    mine, forward it to the handler untouched". It is returned for three shapes:

    * a path under another mount (``/scratch/x.md``, ``/skills/voice/SKILL.md``);
    * the mount root itself (``/kb``), which addresses a directory rather than a file in the tree —
      handing ``""`` to ``pkb.core.validate_content`` would be nonsense, and the harness rejects the
      call on its own;
    * a path the harness refuses outright — ``..``, ``~``, a Windows drive prefix. **This is RT-10**:
      Layer 2 neither raises nor swallows, it forwards, so deepagents produces its own ``Error: {e}``
      ToolMessage. Re-wording a harness error would give the model two different messages for one
      failure and put Layer 2 in the business of maintaining someone else's error text.

    *raw* is typed :class:`object` on purpose: it comes straight out of ``tool_call["args"]``, where
    a model can put ``None``, a number, or a nested object. Any of those is simply not a
    knowledge-base path — and an uncaught ``AttributeError`` here would abort the run, which (D-1)
    also skips the maintenance flush.
    """
    if not isinstance(raw, str):
        return None
    try:
        normalized = validate_path(raw)
    except (ValueError, NotImplementedError):
        return None
    if not normalized.startswith(KB_MOUNT):
        return None
    return normalized[len(KB_MOUNT) :]


def canonical_kb_path(kb_root: Path, rel: str) -> str | None:
    """*rel* re-spelled the way the tree is actually spelled on disk, or ``None`` if undecidable.

    :func:`to_kb_relative` normalizes *syntax* — ``..``, ``~``, ``.``, ``//``, the missing leading
    slash (RT-9). It cannot normalize *spelling*, and on a case-insensitive filesystem spelling is
    where the knowledge base and the agent stop agreeing about which file is which. macOS APFS,
    Windows NTFS/exFAT and every iCloud/Dropbox/OneDrive mount resolve
    ``Cooking/sub-topics/grilling/notes/summary.md`` to the very same inode as
    ``Cooking/sub-topics/Grilling/notes/summary.md`` — so a write to the first *is* a write to the
    human-approved breadth file — while every dictionary keyed by
    :class:`~pkb.core.models.KbSnapshot`'s exact strings, and every tuple compare such as
    ``inner == (TOPIC_FILE,)``, answers "I have never heard of it". The gate table, Layer 1's
    ``validate_content`` and the maintenance flush then each decide about a different file. This
    function is what makes them decide about one.

    :func:`pathlib.Path.resolve` and :func:`os.path.realpath` are **not** substitutes: neither
    re-spells case on macOS (measured, not assumed). The only oracle for "which entry is this?" is
    the directory listing, so each segment is matched against ``os.scandir`` — first by exact name
    (Layer 1's :func:`~pkb.core.paths.has_case_exact_entry`, PA-17), then, when the operating system
    resolves the segment anyway, by identity: the entry whose ``st_dev``/``st_ino`` are the ones
    ``stat`` reports for the caller's spelling. Comparing inodes rather than folded strings means
    the answer is right for whatever equivalence *this* filesystem implements — ASCII case, Unicode
    NFC/NFD, HFS+ folding — without Layer 2 modelling any of them.

    A segment with nothing behind it on disk ends the walk: it and everything below pass through
    verbatim, because a genuinely new file must stay creatable under the name its author chose.

    Returns ``None`` — "this path resolves to something and I cannot say what" — when a segment
    exists but no entry claims its inode: an unreadable directory, or a rename racing the walk.
    A caller must fail closed on it. For a gate that means interrupting: a check that cannot be
    evaluated is not a check that passed.

    Args:
        kb_root: The knowledge-base root. Not itself canonicalised — it is the caller's own path.
        rel: A KB-relative POSIX path, as :func:`to_kb_relative` produces it.
    """
    segments = rel.split("/")
    current = kb_root
    canonical: list[str] = []
    for index, segment in enumerate(segments):
        if has_case_exact_entry(current, segment):
            actual = segment
        elif not os.path.lexists(current / segment):
            canonical.extend(segments[index:])
            return "/".join(canonical)
        else:
            resolved = _entry_named_like(current, segment)
            if resolved is None:
                return None
            actual = resolved
        canonical.append(actual)
        current = current / actual
    return "/".join(canonical)


def _entry_named_like(directory: Path, name: str) -> str | None:
    """The entry of *directory* that ``directory / name`` actually addresses, by inode identity.

    ``follow_symlinks=False`` on both sides so a link is compared as a link: resolving it here would
    make a link and its target look like the same directory entry, and which of two names a write
    lands on is precisely what :func:`pkb.agents.permissions.resolves_elsewhere` refuses rather than
    canonicalises.
    """
    try:
        target = (directory / name).stat(follow_symlinks=False)
    except OSError:
        return None
    try:
        with os.scandir(directory) as entries:
            for entry in entries:
                # One unreadable entry must not blind the walk to the rest of the directory.
                try:
                    same = os.path.samestat(entry.stat(follow_symlinks=False), target)
                except OSError:
                    continue
                if same:
                    return entry.name
    except OSError:
        return None
    return None
