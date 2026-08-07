"""Findings that two entry points would otherwise word two ways.

Three structural defects are noticed twice: once by the walk (:mod:`pkb.core.scan`, whose findings
reach Layer 2 through ``FlushReport``) and once by the rule engine (:mod:`pkb.core.validation`,
whose findings reach Layer 2 through a rejected tool call). ``validation`` imports ``scan``, so the
shared wording cannot live in either — it lives here, in a leaf that imports only ``errors`` and
``paths``.

This matters because CX-6 has Layer 2 put ``Finding.message`` in front of the model verbatim. The
same tree defect described two different ways teaches the agent that the two descriptions are two
problems.
"""

from __future__ import annotations

from pkb.core import paths
from pkb.core.errors import Finding, Severity


def misplaced_topic_root(topic_path: str, expected: str) -> Finding:
    """A topic root reached other than through ``sub-topics/`` (VA-36).

    Anchored at the topic's ``topic.md`` rather than at the directory: that is the file the human
    or the agent has to move, and it is what the TUI can open.
    """
    return Finding(
        code="MISPLACED_TOPIC_ROOT",
        severity=Severity.WARNING,
        message=(
            f"The topic root {topic_path!r} is not reached through {paths.SUBTOPICS_DIR}/. "
            "It is still discovered, so nothing is lost, but nothing else in the tree expects it "
            "here."
        ),
        rule_id="VA-36",
        path=f"{topic_path}/{paths.TOPIC_FILE}",
        value=topic_path,
        hint=f"Move the topic to {expected}.",
    )


def unexpected_root_entry(name: str) -> Finding:
    """An entry at the KB root that is neither reserved nor a topic (PA-1).

    A warning, not an error: a stray root entry is untidy rather than corrupting, and the root is
    the one place a human is most likely to drop something by hand.
    """
    reserved = ", ".join(sorted({paths.INDEX_FILE, paths.TAGS_FILE, f"{paths.SKILLS_DIR}/"}))
    return Finding(
        code="UNEXPECTED_ROOT_ENTRY",
        severity=Severity.WARNING,
        message=f"{name!r} is neither a reserved root entry nor a topic root.",
        rule_id="PA-1",
        path=name,
        value=name,
        hint=(
            f"The knowledge-base root holds {reserved} plus one directory per top-level topic; "
            f"a directory becomes a topic root by holding {paths.TOPIC_FILE}."
        ),
    )


def frontmatter_parse_error(path: str, detail: str, line: int | None) -> Finding:
    """Frontmatter that is not readable YAML (VA-39).

    An error, but never an exception: the flush runs after failed agent turns, so one unreadable
    file must not stop the tree being put back in order (MA-14).
    """
    return Finding(
        code="FRONTMATTER_PARSE_ERROR",
        severity=Severity.ERROR,
        message=f"The frontmatter block could not be parsed as YAML: {detail}",
        rule_id="VA-39",
        path=path,
        line=line,
        hint="Fix the YAML between the --- fences; the rest of the file is untouched.",
    )
