# Personal Knowledge Base: Design Specification

---

## Guiding Principles

- A successful AI-driven future depends on human–AI synergy, and that synergy needs a defined, active human role.
- Breadth of knowledge drives creativity and novel insight more than depth does (David Epstein, *Range*).
- On a problem, humans go wide and AI goes deep (Marc Andreessen).
- AI agents are tactically brilliant but strategically retarded (author).

---

## System Goal

The Personal Knowledge Base (PKB) is an AI-assisted expert for the subject areas the operator works in. It fuses
theoretical, practical and procedural knowledge about each topic. It learns from the experience of working with the
operator on operator-led objectives. The operator states an objective to the Librarian, which researches breadth-first
across all topics. The operator may address a single Topic Expert instead, when the owning topic is known. When an
objective requires a new tool or a process to follow, the session produces one or more instruction sets. An instruction
set states why the work is necessary and what it must achieve. A human follows the instruction set, or another agentic
system implements it. One session may produce several instruction sets, for separate experiments or for separate tools.
The operator reports the results of each experiment or tool back into the session. The session uses them to advance the
objective.

When the operator closes a session, the Learning agent picks it up, and the two of them analyze the record to distil a
repeatable skill or a piece of practical knowledge. The operator approves what enters the PKB. Each entry improves the
expert agents. This creates a self-learning loop.

---

## The Three Pillars

The PKB holds three kinds of knowledge, and every topic has a place for each:

| Pillar          | Folder        | It holds                                                                      |
|-----------------|---------------|-------------------------------------------------------------------------------|
| **Theoretical** | `references/` | Knowledge others established. Books, papers, articles, anything read.         |
| **Practical**   | `notes/`      | Knowledge the operator established by doing, under their own conditions.      |
| **Procedural**  | `skills/`     | Knowledge on how to perform a task using practical and theoretical knowledge. |

The external world supplies theoretical knowledge: books, papers and internet articles. The PKB ingests a source during
a session, when the operator points it at a web page or gives it a file that holds an eBook or a scientific paper.

The operator's own experience of working with that theoretical knowledge creates the other two. Practical knowledge is a
guide to the theory: how to apply it in the real world. Procedural knowledge captures the know-how to perform a specific
task, action or skill.

---

## Reasoning over the PKB

Achieving a goal or solving a problem generally splits into two stages: finding how to solve the problem, and
implementing the solution. The search for a solution is the most creative part. That is where insightful approaches
appear, the kind that are obvious in retrospect and cheap to carry out, but hard to think of directly. The creativity of
such a solution often lies in combining similar approaches from different areas, a breadth-first search over other
domains (brainstorming) for similar problems and how they were solved there. In his book *Range*, David Epstein shows
how creativity thrives on breadth of experience and defined constraints rather than on early specialization or endless
freedom.

To facilitate this approach the PKB indexes what it stores in two ways, breadth (for brainstorming) and depth (for
planning), and every topic carries the pair. `summary.md` holds the distilled ideas and insights of a topic, along with
the approaches that could be valuable, and the operator approves it. `index.md` records exactly where the details of a
particular approach can be found, and the PKB maintains it itself.

That organization lets an agent load `summary.md` from many topics into its context and reason over the ideas from a
position of breadth, assisted by the operator. Once the approach is settled, the agent purges the wide context and loads
the specific topics the solution needs, reasoning over what tools or processes must be created to implement it.

The PKB brainstorms a solution to an objective and plans its implementation, and it implements and executes nothing
itself. It writes the instructions for what needs to be done, and the operator or external tooling carries them out. The
operator either reports the results back into the session or instructs the agent to run the tools that fetch them.

---

## Sessions and the Self-Learning Loop

The PKB meets the outside world through sessions. A session is a conversation with an agent: a Topic Expert, the agent
that holds one subject, the Librarian, the agent that oversees every topic and can reason across several knowledge
domains at once, or the Learning agent, which distills what closed sessions established. When the Librarian needs a
topic, it connects to that topic's expert the same way the operator would.

Whoever establishes a session and sets its goal is that session's operator, a human or an agent alike. There is one way
in, the API, and the TUI and the Telegram chat are two clients of it. At creation the operator names the target agent,
the Librarian, a specific topic's expert, or the Learning agent.

A session usually has an objective: a problem to solve or a question to answer. Sessions are durable and stay open as
long as they are needed, and the session's operator is the one who closes it, when they judge that it has met its goal.
Keep a separate session for each question or goal, because a session holds its own memory and manages its own context.
Nothing limits how many sessions an agent may hold at once.

During a session the operator may propose a note or a skill, or ingest a book and then reason over what it holds to
reach the objective.

When the operator decides there is nothing more to do, they issue `/close`.

The self-learning loop runs in a session the operator establishes to the Learning agent, the same way every other
session is established. Every closed session enters the learning queue, and the Learning agent picks them up one at a
time and tries to generalize and extract knowledge worth keeping, practical or procedural. It checks whether what the
session produced conflicts with what the PKB already holds, and it asks the operator to review and approve every change.
When the analysis is complete and every edit is made, whether resolving a conflict or creating a note or a skill, the
operator marks the analyzed session ended with `/end` and that session is archived. A session that contributes nothing
to the PKB is archived without asking the operator for anything.

---

## The Librarian, the Topic Experts and the Learning Agent

The search for a solution goes wide first, across many topics, looking for what a topic knows that is analogous to the
objective and could apply to it. Once the operator and the agent have settled which approach is worth taking and which
topics hold it, the work goes deep and turns the idea into instructions.

A Topic Expert has deep knowledge of one topic and no awareness of any other. The Librarian reaches one by opening a
session on it, the same way the operator opens a session on a Topic Expert directly, and asks it for its summaries and
its approaches. One mechanism serves both, so nothing about a session changes because an agent rather than a person is
on the other side of it.

Once the operator and the Librarian have settled the approach, the Librarian works with the Topic Experts it selected to
produce a detailed plan, and it uses them to check that the plan and the chosen approach are accurate. The expert
sessions it opened are its to close, and they enter the learning queue like every other closed session.

The Learning agent summarizes the session and distills any knowledge or process worth keeping in the PKB. It applies its
own rules first, and works with the Topic Experts where it needs them to tell what is new. It puts what it found to the
operator and files the changes they approve.

---

## The technical design

`DESIGN.md` holds the design that implements the description above: the topic tree and its frontmatter, the tag system,
conflict handling, the agents and their permissions, the session lifecycle, and the instruction sets a session hands
out.
