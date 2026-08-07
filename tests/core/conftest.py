"""Shared fixtures for the ``pkb.core`` suite — above all the §4.1 fixture knowledge base.

Every later phase's golden files are rendered from :data:`SAMPLE_KB_FILES`, so its contents are
load-bearing: the titles, descriptions, tags and ``related_topics`` below are exactly the ones the
rules document quotes in §4.2 (root ``index.md``), §4.3 (topic ``index.md``) and §4.4 (root
``tags.md``). Changing a description here changes a golden file.

Two deliberate choices:

* **No derived files.** §4.1 draws ``index.md`` and ``tags.md`` in the tree marked "← generated".
  They are absent from the fixture because they are *output*: the generators write them, GE-2
  truncates and replaces whatever is there, and GE-3 requires the result to be identical whether or
  not a stale copy existed. A test that needs one writes it with :func:`write_kb`.
* **Documented defects.** The tree is otherwise valid, but it deliberately carries the two defects
  §4.3's golden renders — an unreferenced asset (``notes/old-idea/media/photo.jpg`` →
  ``ORPHAN_ASSET``, MA-8) and an open conflict (``notes/preheat-the-grill.md`` carries
  ``status.conflict-review`` plus its ``review_note``, VA-29) — plus one divergence between a file
  stem and its title (``old-idea.md`` titled "An old idea" → ``FILENAME_TITLE_DIVERGENCE``, VA-35).
"""

from __future__ import annotations

import contextlib
import os
from collections.abc import Iterator, Mapping
from pathlib import Path

import pytest

# --------------------------------------------------------------------------------------
# The §4.1 fixture knowledge base
# --------------------------------------------------------------------------------------

SAMPLE_KB_FILES: Mapping[str, str] = {
    # ---- root ------------------------------------------------------------------------
    "skills/voice/SKILL.md": """\
---
name: voice
description: How the assistant writes on the human's behalf
---

# Voice

Write plainly. Prefer short sentences. Never invent a fact the knowledge base does not hold.
""",
    # ---- BBQ -------------------------------------------------------------------------
    "BBQ/topic.md": """\
---
title: "BBQ"
description: "Barbecue equipment, fuel, and technique"
topic: "BBQ"
tags:
  - topic.bbq
  - type.summary
  - status.approved
created: 2024-09-01
updated: 2024-09-01
source_type: summary
---

# BBQ

Grills, smokers, charcoal, and gas.
""",
    "BBQ/notes/summary.md": """\
---
title: "Notes summary"
description: "Distilled rules from barbecue experience"
topic: "BBQ"
tags:
  - topic.bbq.equipment
  - type.summary
  - status.approved
created: 2024-09-01
updated: 2024-09-01
source_type: summary
---

# Notes summary

Nothing distilled yet.
""",
    "BBQ/references/summary.md": """\
---
title: "References summary"
description: "Overview of ingested barbecue sources"
topic: "BBQ"
tags:
  - topic.bbq
  - type.summary
  - status.approved
created: 2024-09-01
updated: 2024-09-01
source_type: summary
---

# References summary

No sources ingested yet.
""",
    # ---- Cooking ---------------------------------------------------------------------
    "Cooking/topic.md": """\
---
title: "Cooking"
description: "Home cooking: technique, equipment, and recipes"
topic: "Cooking"
tags:
  - topic.cooking
  - type.summary
  - status.approved
created: 2024-09-01
updated: 2024-10-16
source_type: summary
---

# Cooking

Technique, equipment, and recipes for the home kitchen.
""",
    # expert.md carries no PKB frontmatter: it is agent instruction, not indexable knowledge (C3).
    "Cooking/expert.md": """\
# Cooking Topic Expert

You are the Topic Expert for Cooking. When a note and a reference disagree, the note wins.
""",
    "Cooking/notes/summary.md": """\
---
title: "Notes summary"
description: "Distilled rules from cooking experience"
topic: "Cooking"
tags:
  - topic.cooking
  - type.summary
  - status.approved
created: 2024-09-01
updated: 2024-10-16
source_type: summary
---

# Notes summary

Preheat matters more than fuel choice.
""",
    "Cooking/notes/grill-performance-in-windy-conditions.md": """\
---
title: "Grill Performance in Windy Conditions"
description: "How wind affects grill temperature and how to compensate for it"
topic: "Cooking"
tags:
  - topic.cooking.grilling
  - topic.cooking.heat-management
  - type.note
  - status.approved
created: 2024-10-15
updated: 2024-10-16
related_topics: [ bbq.equipment ]
source_type: note
---

# Grill Performance in Windy Conditions

Wind strips heat from the grill body. Turn the lid vent away from the wind and add ten minutes.
""",
    "Cooking/notes/preheat-the-grill.md": """\
---
title: "Preheat the grill"
description: "How long to preheat the grill before cooking"
topic: "Cooking"
tags:
  - topic.cooking.grilling
  - topic.cooking.heat-management
  - type.note
  - status.conflict-review
created: 2024-10-15
updated: 2024-12-16
related_topics: [ bbq.equipment ]
source_type: note
review_note: "Reference 'Grill Basics' says preheat for 10 min. Note says 15 min."
---

# Preheat the grill

Fifteen minutes on high with the lid closed, measured on a three-burner gas grill.
""",
    "Cooking/notes/old-idea/old-idea.md": """\
---
title: "An old idea"
description: "An idea captured before the grill notes"
topic: "Cooking"
tags:
  - topic.cooking
  - type.note
  - status.draft
created: 2024-08-20
updated: 2024-08-20
source_type: note
---

# An old idea

Kept for the record. The photo beside this note was never wired into the text.
""",
    # Unreferenced on purpose: this is the ORPHAN_ASSET of §4.3's Maintenance flags block (MA-8).
    "Cooking/notes/old-idea/media/photo.jpg": "binary placeholder: no test reads asset bytes\n",
    "Cooking/references/summary.md": """\
---
title: "References summary"
description: "Overview of ingested cooking sources"
topic: "Cooking"
tags:
  - topic.cooking
  - type.summary
  - status.approved
created: 2024-09-01
updated: 2024-10-16
source_type: summary
---

# References summary

One beginner guide so far.
""",
    "Cooking/references/grill-basics/grill-basics.md": """\
---
title: "Grill Basics"
description: "Beginner guide to charcoal grilling"
topic: "Cooking"
tags:
  - topic.cooking.grilling
  - type.reference
  - status.approved
created: 2024-10-10
updated: 2024-10-10
source_type: reference
---

# Grill Basics

Preheat for ten minutes before cooking. The scanned source is the [original PDF](grill-basics.pdf).
""",
    "Cooking/references/grill-basics/grill-basics.pdf": (
        "binary placeholder: referenced by grill-basics.md so it is not an orphan\n"
    ),
    # recipes/ is the human-approved extension folder of PA-7 and GE-24's marker test.
    "Cooking/recipes/ribeye-on-gas.md": """\
---
title: "Ribeye on gas"
description: "Reverse-sear ribeye on a three-burner gas grill"
topic: "Cooking"
tags:
  - topic.cooking.recipes
  - type.note
  - status.approved
created: 2024-11-02
updated: 2024-11-02
source_type: note
---

# Ribeye on gas

Two burners low, one burner off. Finish over the live burner.
""",
    # ---- Cooking/sub-topics/Grilling --------------------------------------------------
    "Cooking/sub-topics/Grilling/topic.md": """\
---
title: "Grilling"
description: "Charcoal and gas grilling"
topic: "Grilling"
tags:
  - topic.cooking.grilling
  - type.summary
  - status.approved
created: 2024-09-05
updated: 2024-09-05
source_type: summary
---

# Grilling

Charcoal and gas grilling.
""",
    "Cooking/sub-topics/Grilling/notes/summary.md": """\
---
title: "Notes summary"
description: "Distilled rules from grilling experience"
topic: "Grilling"
tags:
  - topic.cooking.grilling
  - type.summary
  - status.approved
created: 2024-09-05
updated: 2024-09-05
source_type: summary
---

# Notes summary

Nothing distilled yet.
""",
    # The KB's only domain.* tag — it is what makes §4.4's `## Namespace: domain` section render.
    "Cooking/sub-topics/Grilling/references/summary.md": """\
---
title: "References summary"
description: "Overview of ingested grilling sources"
topic: "Grilling"
tags:
  - topic.cooking.grilling
  - domain.legal.compliance
  - type.summary
  - status.approved
created: 2024-09-05
updated: 2024-09-05
source_type: summary
---

# References summary

Includes the local fire-safety regulations for open flame.
""",
}
"""The §4.1 tree, keyed by KB-relative POSIX path. Authored content only — see the module docstring."""


# --------------------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------------------


def write_kb(root: Path, files: Mapping[str, str]) -> Path:
    """Materialise ``files`` (KB-relative POSIX path → text) under ``root`` and return ``root``.

    Parent directories are created as needed and every file is written UTF-8 with LF endings, so a
    fixture built on Windows is byte-identical to one built on macOS (GE-4, GE-7).
    """
    root.mkdir(parents=True, exist_ok=True)
    for relative, text in files.items():
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8", newline="\n")
    return root


class _ReversedEntries:
    """A drop-in ``os.scandir`` result that yields its entries backwards."""

    def __init__(self, entries: list[os.DirEntry[str]]) -> None:
        self._entries = entries

    def __enter__(self) -> _ReversedEntries:
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def __iter__(self) -> Iterator[os.DirEntry[str]]:
        return iter(self._entries)


_REAL_SCANDIR = os.scandir
"""Captured at import time: the reversing stub must not call the patched name (infinite recursion)."""


def _reverse_scandir(path: str | os.PathLike[str] = ".") -> _ReversedEntries:
    with _REAL_SCANDIR(path) as entries:
        return _ReversedEntries(list(entries)[::-1])


@contextlib.contextmanager
def reversed_directory_order() -> Iterator[None]:
    """Make every ``os.scandir`` in the process yield entries backwards (GE-4).

    The one test that a walk really sorts its siblings rather than inheriting the filesystem's
    order. Patching the ``os`` module attribute reaches ``pkb.core.paths`` and ``pkb.core.scan``
    alike, because both look ``os.scandir`` up at call time.
    """
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(os, "scandir", _reverse_scandir)
        yield


# --------------------------------------------------------------------------------------
# Fixtures
# --------------------------------------------------------------------------------------


@pytest.fixture
def sample_kb(tmp_path: Path) -> Path:
    """The §4.1 fixture knowledge base, materialised under ``tmp_path`` (CX-2)."""
    return write_kb(tmp_path / "KB", SAMPLE_KB_FILES)


@pytest.fixture
def empty_kb(tmp_path: Path) -> Path:
    """An existing but empty directory — a valid knowledge base with no topics (GE-29)."""
    root = tmp_path / "EmptyKB"
    root.mkdir()
    return root


@pytest.fixture(autouse=True)
def _no_network_no_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    """Sockets closed and provider credentials unset for the whole core suite (CX-1, CX-2).

    Layer 1 is plain Python over a directory tree. If a future edit reaches for the network, the
    test that does it fails here rather than silently depending on a host being reachable.
    """

    def _blocked(*args: object, **kwargs: object) -> None:
        raise RuntimeError("pkb.core performs no network I/O (CX-1, CX-2)")

    import socket

    monkeypatch.setattr(socket, "socket", _blocked)
    monkeypatch.setattr(socket, "create_connection", _blocked)
    for variable in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY"):
        monkeypatch.delenv(variable, raising=False)
