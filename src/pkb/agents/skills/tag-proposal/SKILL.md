---
name: tag-proposal
description: Use before filing content that would introduce a hierarchical tag not already in use anywhere in the knowledge base. Proposes the tag to the human with its rationale and place in the tree, and waits for approval.
---

# Tag proposal

Tags are how anything is found across topics, so they are governed rather than invented. A tag that
already exists somewhere in the knowledge base can be used freely. A tag that does not exist yet is
**approved by the human before the file lands** — and the file that would use it does not land until
they say yes.

**You propose the tag by writing the file with it.** A write that introduces an unused tag is held
before it lands, and the human is shown the file together with the new tag it would create; that
pause is the proposal. Do not stop and ask in conversation first — asking costs the human an extra
round trip to reach the same decision, and if the turn ends there, nothing was filed and the tag was
never really proposed.

What you must not do is dodge the question: filing under an approximate tag that already exists,
because it avoids the pause, is how a tag tree stops being useful. Use the right tag and let the
write be held.

## Get the shape right on the first try

These four constraints are mechanical, and a proposal that breaks one is refused before the human
ever sees it. They are worth knowing by heart:

- **Four namespaces, and only four** — `topic.*`, `domain.*`, `type.*`, `status.*` (Layer 1 rule
  TG-2). There is no fifth.
- **At most 4 segments, counting the namespace** (TG-3). `topic.woodworking.joinery.dovetails` is at
  the limit; one more level is refused. If you need a fifth, what you actually need is a sub-topic.
- **Each segment is lowercase, digits and hyphens only** — `heat-management`, not `HeatManagement`
  or `heat_management` (TG-4). Segments are joined by single dots.
- **A nested tag implies its parent.** `topic.woodworking.joinery` already means
  `topic.woodworking`, so never add both to one file to be safe. Tag at the most specific level that
  is true.

## What may be proposed, and what may not

**Proposable:**

- `topic.*` — the subject area, mirroring the topic's place in the folder tree.
- `domain.*` — a cross-topic functional domain, e.g. a compliance or security angle that shows up in
  several unrelated topics.

**Closed vocabularies — never extend these, never propose an addition to them:**

- `type.*` has exactly four values: `type.note`, `type.reference`, `type.solution`, `type.summary`.
- `status.*` has exactly three: `status.draft`, `status.approved`, `status.conflict-review`.

If the content seems to need a fifth type or a fourth status, the classification is wrong, not the
vocabulary. Say what you were trying to express and let the human decide.

## Making a proposal a person can answer

A good proposal is four short lines:

1. **The tag**, written out in full.
2. **Where it sits** — its parent in the existing tree, and the sibling tags it would join.
3. **The nearest existing tag, and why it does not fit.** This is the part that does the work: most
   proposed tags are near-duplicates of something already in use, and naming the near-miss is what
   lets the human see that immediately.
4. **What it would be used for** — the file being filed now, and honestly, whether you expect
   anything else to use it.

A tag that will only ever apply to one file is usually not a tag. Say so when that is the case.

## There is no register step

Do not edit the root `tags.md` and do not edit any `index.md`. Both are machine-generated from the
tags files actually carry, so an approved tag appears in the registry on its own the moment a file
uses it, and a tag that stops being used disappears the same way. There is nothing to register,
nothing to keep in sync, and an edit there would be overwritten anyway.

## The approval gate

Filing a file that introduces a genuinely new `topic.*` or `domain.*` tag pauses for the human. That
is the new-tag gate, and it fires whether or not this skill ran — this skill exists to make the
question a good one, not to satisfy the check. Re-using a tag that already exists anywhere does not
pause.

What the human sees is the file about to be written and the new tag it introduces. So propose the
tag in conversation first, get the answer, and only then file: an approval dialog is a poor place to
discover that the tag was never discussed.

---

*This is a starter draft.* It ships with the implementation so that something sensible happens on
day one, and it is not meant to survive contact with the way you actually work. Rewrite it — with
your agent's help — until it says what you want done, in your words. Ask your agent to adopt this
skill and it will place an editable copy in a `skills/` folder: at the knowledge-base root to change
the procedure everywhere, or inside a single topic to change it for that topic alone. From that
moment your copy replaces this text and later shipped improvements no longer reach it; deleting your
copy brings this one back.
