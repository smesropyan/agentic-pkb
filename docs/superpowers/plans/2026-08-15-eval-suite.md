# The Fresh Eval Suite for Phases 3 and 4

**Version:** v2, 2026-08-15. Minted fresh per the operator's directive of 2026-08-14: the old
five-task evaluation priced the superseded design's workload and is not reused. This suite derives
from `README.md`'s own loop and `DESIGN.md` §2–§7's mechanisms. It is versioned beside the research
brief; scores compare only within one version, and a change to any task or fixture bumps the
version and re-runs the incumbent. v2 adds E8 (a live web search) and extends E6 (a closed
Librarian session, and §7.9's skill-or-note routing); v1 was superseded before any run priced a
candidate, so there is no incumbent score to re-run.

**Purpose:** price candidate agent and prompt designs by live runs on the operator's real Ollama
models — the cloud default and the local fallback, both verified reachable, credentials in `.env`.
Reading a repo recommends; a run decides. Every measurement lands in the research brief beside the
finding it prices.

**The rule:** every candidate runs all eight tasks, and so does the incumbent, on the same fixture
KB and the same prompts. A candidate that skips a task has no score. Runs are budgeted against the
Ollama Pro plan's 5-hour and weekly quota windows; a full run is ~135 model calls, roughly 35
minutes on the cloud default and roughly 55 on the local fallback, so timeouts are sized for the
fallback and a run that failed over says so in its record.

## Scoring

Each task lists its criteria and each criterion scores 0–2: **2** the criterion holds as written,
**1** it holds with a defect the operator would correct by hand, **0** it does not hold. A
mechanical criterion (a file exists, frontmatter validates, bytes match) is checked by code and
scores 0 or 2 only. A judgment criterion is scored by the operator or by a grader that is not the
model under test, because a model grading its own draft measured worse than the same model checking
nothing (DESIGN §5.6). Every run also records wall-clock time, model-call count, and which model
answered, because a design that scores even and runs at half the calls wins.

## The fixture KB

One deterministic fixture tree, built by script before every run and discarded after: three topics
(Cooking, Trading, Health), each with `topic.md`, the two breadth summaries, and two or three notes.
The fixtures carry **planted facts that contradict general knowledge** — the operator's pit runs
cold and needs a 15-minute preheat where every published guide says ten; a Trading note caps
position size below the textbook figure. A grounded reply is then distinguishable from a recalled
one: citing the planted file scores, reciting the internet does not. The registry is regenerated
from the fixture before the run, so classification reads real one-line summaries.

---

### E1: Ingest a source into a topic

**Setup:** the Cooking topic holds its breadth files and no references beyond `references/summary.md`.
A real short article (~1,500 words, on dry-brining) is staged as a file the way an accepted source
stages.

**Prompt:** the operator, in a session on the Cooking expert, points at the staged article and asks
for it to be ingested.

**PASS:** `references/[source-name]/[source-name].md` exists and is the map §1.1 fixes — thesis,
provenance, one section per part as the source names them, one bullet per argument the topic cares
about, and the list of what nobody read. The captured source sits beside the map. Frontmatter
validates: the seven required fields, `source_type: reference`, exactly one `type.*` tag
(`type.reference`), at least one `topic.*` tag, no tag the PKB has never used landing without a
proposal.

**Scoring (0–2 each):** map shape per §1.1 · frontmatter and tags valid (mechanical) · captured
source kept beside the map (mechanical) · three spot-checked bullets each traceable to the
article's own text, none invented.

**Budget:** ~12 model calls.

### E2: Brainstorm breadth-first across topics

**Setup:** the full fixture, registry regenerated. Each `topic.md` carries one planted approach in
vocabulary that shares no word with the objective below; two of the three bear on it.

**Prompt:** an objective to the Librarian that neither topic's name matches by vocabulary but the
two planted approaches genuinely serve.

**PASS:** the classify step names both bearing topics with a one-line reason each; the fan-out
opens a session per expert; the merge is attributed, each answer under its own expert's heading,
and claims nothing about an expert that never ran; the reply carries candidate approaches naming
the planted files behind them; the part no expert answered stands under its own heading as a gap;
the round ends with the operator's options and writes nothing into the tree.

**Scoring (0–2 each):** classification hits both planted topics, the vocabulary-mismatched one
included · merge attribution honest (mechanical: headings match the experts that ran) · candidate
approaches cite the planted files, not general knowledge · the round ends at the operator with no
tree write (mechanical) · every dispatched brief carries the objective and none of the operator's
notes or beliefs — the briefs grep clean of the planted notes' text (mechanical, §5.8).

**Budget:** ~18 model calls.

### E3: Settle an approach and plan it deep with the owning experts

**Setup:** E2's session state, scripted: three expert sessions open, the operator's next turn
settles approach N, which two topics hold and one does not.

**Prompt:** the operator settles the approach in their own words.

**PASS:** the Librarian closes the session to the expert the approach left out — a real close, all
three effects of §2.6, the session entering the learning queue; `planning` opens on the objective,
the settled approach and the named topics, restated once; each deep-phase brief carries the
approach verbatim and asks what the topic holds that tells against it, bounded and factual, never
"what is wrong with this draft"; the experts' answers come back attributed, each claim naming its
file or page.

**Scoring (0–2 each):** the left-out session is closed and queued (mechanical) · the brief carries
its four parts and withholds the operator's raw turn and the other experts' answers · the check
questions are the bounded factual set of §5.6 · expert answers name files that exist (mechanical
path check) · briefs and sub-agent questions carry no note or belief content — grep against the
planted notes comes back empty (mechanical, §5.8).

**Budget:** ~15 model calls.

### E4: Draft an instruction set

**Setup:** a settled approach and its planning context, scripted from E3's fixtures.

**Prompt:** the deep phase drafts the instruction set for the one experiment the approach needs.

**PASS:** the set states why the work is necessary and what it must achieve, and no steps; the
result clause is checkable, or names the operator as the check and says so rather than
manufacturing an assertion nothing runs; the boundary states in full what the set consumes and
produces, resolving nothing by reference to another set; every claim the tree supports names its
file; the placeholder scan comes back clean.

**Scoring (0–2 each):** both parts present, no method steps · result clause checkable or honestly
the operator's · boundary self-contained (no "as in set 2") · every cited KB-relative path exists
(mechanical).

**Budget:** ~8 model calls.

### E5: Work a session through `/close`

**Setup:** the Cooking topic with the planted preheat note. A scripted four-turn conversation:
open with an objective, dictate a note mid-session, report an experiment's result, `/close`.

**Prompt:** the four turns, verbatim from the script.

**PASS:** `sessions/[objective-title].md` exists from the first turn with valid frontmatter —
`topic: "(session)"`, `type.summary`, a `topic.cooking` tag; the running record appends in turn
order and no earlier entry is rewritten; the dictated note lands as a capture in its own turn, the
operator's approved bytes verbatim; `/close` appends an entry naming the command and the date,
detaches every channel, and the session enters the learning queue whatever it produced.

**Scoring (0–2 each):** session-file lifecycle and frontmatter (mechanical) · capture lands
in-turn with the approved bytes unchanged (mechanical byte compare) · body append-only across the
four turns (mechanical) · `/close` takes all three effects, queue entry included (mechanical).

**Budget:** ~20 model calls.

### E6: Distil a closed session for the learning queue

**Setup:** three closed session files scripted into the queue. Session A holds a genuine lesson:
the operator cooked three times, reported each result, and dictated a lesson that contradicts a
planted note. Session B holds only §7.5's exclusions — an approach that never worked and an error
a retry cleared. Session C is a closed Librarian session that ran across Cooking and Health: each
expert answered one brief, the operator confirmed one lesson bearing on each topic, and mid-session
they settled a way of working for rounds — the kind of thing that changes the next draft before
anybody asks a question (§7.9).

**Prompt:** the operator establishes an analysis session to the Learning agent; the worker drains
all three entries.

**PASS:** for A, the analysis drafts the candidate quoting the running-record entries it rests on,
sliced not summarized; asks the topic's expert what is new; raises the conflict pair against the
planted note, quoting both sides and changing nothing; waits for the operator; files the approved
bytes verbatim through the topic's own expert; appends the distillation and seals on `/end`. For
B, the analysis puts **nothing** to the operator: the distillation says the session established
nothing and the file seals silently. B is the load-bearing case — the filing bar runs inside the
analysis, and the default is silence (§7.4). For C, the analysis fans out to each participating
expert on the step-3 grammar (§7.3); each kept note lands inside its own topic and nothing is
proposed under the Librarian or the Learning agent; the way of working routes to a root
process-skill proposal naming its scope and any shipped skill it would shadow, with the exact
text underneath (§7.9–§7.10) — never to a note.

**Scoring (0–2 each):** B stays silent, no candidate raised (mechanical: no operator turn asked) ·
A's candidate quotes the record entries behind it · the conflict pair is raised and nothing changes
before the operator answers (mechanical: tree diff empty) · approved bytes land unchanged through
the topic's expert (mechanical byte compare) · C's notes land one per participating topic, none
anywhere else (mechanical: proposal paths) · C's way of working becomes a skill proposal naming
scope and shadow while the fact lessons stay notes — §7.9's who-acts-first test applied.

**Budget:** ~35 model calls.

### E7: A write-time conflict on a dictated note

**Setup:** the Cooking topic with `references/grill-basics/grill-basics.md` saying a ten-minute
preheat is sufficient. The §6.4 run, end to end.

**Prompt:** the operator dictates *preheat the grill for 15 minutes*; when the pair is reported,
they answer that their pit runs cold, the third resolution.

**PASS:** the draft starts the check; the sub-agent labels the one pair code picked; the report
names both files and quotes both sides, and says whether they genuinely oppose or are both true
under conditions neither states; nothing lands until the operator answers; the redraft carries the
condition that separates them and the operator's approved text lands with ordinary frontmatter;
the reference is untouched byte for byte; the running record names the pair and the decision.

**Scoring (0–2 each):** pair found and both sides quoted · reporter-never-editor: the reference
unchanged (mechanical byte compare) · the landed note carries the separating condition in the
approved bytes · the running record holds the pair and the resolution (mechanical).

**Budget:** ~10 model calls.

### E8: An objective that needs the internet

**Setup:** the full fixture. The objective's answer sits in no fixture file and no topic claims
it. The harness carries the two-layer budgets as production ships them — the numeric soft budget
disclosed in the skill text, the step cap and wall clock in code — and pins one sub-agent's budget
to the lowest tier, so the exhaustion path runs by construction.

**Prompt:** the operator gives the Librarian an objective a web search genuinely serves; after the
merged reply, a second scripted operator turn asks a follow-up that needs another round.

**PASS:** classify names the web questions instead of pretending a topic holds the answer; each
search sub-agent carries one question holding the objective and nothing else (§5.8); the sub-agent
paces against its disclosed budget and ends by a terminal typed call, never by prose trailing off;
the pinned-budget question comes back as the typed exhaustion result, and the merge says that
search stopped short because the type says so, not because a model read the transcript; every
quotation locates in the bytes the harness holds; the operator's second turn opens a new round and
no cap, guard or warning fires on the round boundary; nothing lands in the tree.

**Scoring (0–2 each):** budgets respected — calls within the disclosed tier, the hard cap never
hit (mechanical: call count) · exhaustion surfaces as the typed result and the merge names it from
the type (mechanical: result type present; the merge sentence judged) · the second operator round
runs with nothing firing on the round boundary (mechanical) · quotations locate in the held bytes
(mechanical).

**Budget:** ~15 model calls, plus the search calls the tier allows.

---

## Recording a run

One row per task per candidate in the research brief: score per criterion, total, model calls,
wall clock, model that answered, and any failover. The incumbent's row sits beside every
candidate's, because the rule is the comparison, not the number.
