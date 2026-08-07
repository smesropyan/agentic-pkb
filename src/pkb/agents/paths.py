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
"""

from __future__ import annotations

from typing import Final

from deepagents.backends.utils import validate_path

__all__ = [
    "KB_MOUNT",
    "SKILLS_MOUNT",
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
