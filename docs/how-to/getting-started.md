# Getting started

From a fresh clone to a knowledge base with something in it. Setup first, then daily use.

Every command, path, log line and file in this guide comes from a real run. Telegram is the
exception. It has a guide of its own, and this guide says so where Telegram comes up. A transcript
that comes from a different session than the one beside it also says so.

**Contents**

* [What this is](#what-this-is)
* [Before you start](#before-you-start)
* [1. Install](#1-install)
* [2. Create a knowledge base](#2-create-a-knowledge-base)
* [3. Start the daemon](#3-start-the-daemon)
* [4. The model](#4-the-model)
* [5. Your first conversation](#5-your-first-conversation)
* [6. Daily use](#6-daily-use)
* [7. The other channels](#7-the-other-channels)
* [8. When something goes wrong](#8-when-something-goes-wrong)
* [9. Not built yet](#9-not-built-yet)

---

## What this is

> The **Personal Knowledge Base (PKB)** – a structured, hierarchical repository of knowledge. It
> contains static sources and human experience. *(Knowledge + Experience = Wisdom)*
>
> — `README.md`

Concretely: a folder of markdown files that **only agents write to**. The root agent is the
**Librarian**. It reads what you send it, works out which topics the material concerns, then hands
it to one **Topic Expert** per topic. The expert writes the file. Deterministic code enforces the
mechanical part: frontmatter, tags, indexes and file placement. The agents only have to get the
*meaning* right.

The design principle behind the system, from `README.md` §1.9:

> *Enforce structure mechanically, curate meaning agentically.*

You talk to it through a terminal client or a Telegram bot. Another agent reaches it over MCP.

---

## Before you start

**You do not edit the tree. Agents write to it, and there is no undo.**

That is a deliberate decision (D6). The architecture spec states the consequence:

> The first draft writes plain markdown. The consequence is real and worth stating: there is **no
> undo**. If an agent writes something wrong and the human approves it, the previous content is
> gone. […] **Until then, back up the KB directory.**
>
> — `docs/superpowers/specs/2026-08-06-pkb-architecture-design.md` §10

Two things follow. Both matter on day one.

**First: not everything stops for you.** The same architecture paragraph says that "the approval
gate means nothing lands unreviewed". As built, that is wrong for two *locations*. Both are
**ungated** on purpose, because capture has to stay frictionless (RT-31, `README.md` §1.1 goal 3):
a plain note under `notes/`, and the *first* extraction of a source. Verified: the whole source
ingestion in [§6](#filing-a-source) landed with no approval.

The exemption covers the **path**, not the content. Every other rule still applies to the file
written there, and the rule that fires most is `new-tag` (RT-25). An expert that mints
`topic.coffee.bean-freshness` for its first note stops for you, on the tag rather than on the note.
A plain note therefore lands without an approval *sometimes*. Do not build a habit on it, and do
not read an approval on your first note as a bug. The consequential actions stop every time:
creating a topic, any delete, a new tag, changing a note's body, editing an approved summary,
resolving a conflict. Twelve reasons in total, tabulated in
[§6](#approvals-what-stops-for-you).

**Second: back the directory up yourself.** There is no git integration and no version history. The
design says nothing more specific than "back up the KB directory". Make it a git repository of your
own:

```bash
git init ~/pkb/tree     # the directory §2 has you create
```

Then commit after a session. Nothing in the system knows about that repository, which is why it
keeps working.

Nothing reverts a file you edit by hand. Layer 1 flags; it never repairs, and nothing moves or
deletes your content. Nothing warns you either. See
[hand-editing](#can-i-edit-a-file-by-hand) in §6.

---

## 1. Install

**The only prerequisite is [uv](https://docs.astral.sh/uv/).** You do not need Python installed.
`pyproject.toml` says `requires-python = ">=3.12"`, and uv downloads a conforming CPython itself if
the machine has none. `uv sync` on this machine reports `Using CPython 3.12.13`. uv fetched that
interpreter; it did not find one on the machine. Your system `python3` is probably 3.9 and *will
not work*. Every command below goes through `uv run` for that reason.

```bash
git clone <this-repo> agentic-pkb
cd agentic-pkb
make install
```

`make install` is `uv sync` and nothing else. On a cold HTTP cache, with all 118 packages
re-downloaded over the network:

```
$ rm -rf .venv && uv sync --no-cache
Using CPython 3.12.13
Resolved 121 packages in 2ms
Downloading lxml (8.2MiB)
Downloading mypy (13.4MiB)
Downloading ruff (10.1MiB)
...
Prepared 118 packages in 1.43s
Installed 118 packages in 226ms
real 2.07
```

Two seconds in total. Do not budget minutes for it.

**All of that output goes to stderr**, not stdout. `make install | tee log` looks like it printed
nothing.

**You can verify the install.** The suite needs no model, no API key and no network service:

```bash
make test
```

```
2014 passed in 113.93s (0:01:53)
```

Budget two minutes. The suite prints only dots for that long, which looks like a hang.

**Run everything from the repository root.** `uv run` resolves the project from the working
directory, and your knowledge base lives somewhere else:

```
$ cd /tmp && uv run python -c 'import pkb'
ModuleNotFoundError: No module named 'pkb'

$ cd /tmp && uv run --project /path/to/agentic-pkb python -c 'import pkb; print("ok")'
ok
```

Either `cd` to the repo first, or pass `--project`.

---

## 2. Create a knowledge base

```bash
mkdir -p ~/pkb/tree
```

That is the whole step. **A knowledge base is a plain directory.** It has no marker file, no
manifest and no `init` command. The daemon runs one `is_dir()` check on startup, in
`pkb/core/scan.py`:

```python
def _require_directory(kb_root: Path) -> Path:
    """Normalize the root, or refuse (KbNotFoundError). An empty directory is fine (GE-29)."""
    if not kb_root.is_dir():
        raise KbNotFoundError(f"knowledge base root {kb_root} does not exist or is not a directory")
    ...
```

(A typo'd path produces that message. See [§8](#8-when-something-goes-wrong).)

**There is no `pkb` command.** `pyproject.toml` declares no console scripts. The project has two
runnable surfaces: `python -m pkb.daemon` and `python -m pkb.tui`. (The daemon's `--help` says
`usage: pkb-daemon`, which looks like an installed binary. It is not one.)

Use `~/pkb/tree` rather than `~/kb`, because the database defaults to a **sibling** of the knowledge
base (`<kb>/../pkb.sqlite`). A tree at `~/kb` scatters `pkb.sqlite`, `pkb.sqlite-wal` and
`pkb.sqlite-shm` into your home directory. Give the tree its own parent folder, or pass `--db`.

---

## 3. Start the daemon

From the repository root, with an **absolute** path:

```bash
uv run python -m pkb.daemon ~/pkb/tree
```

A healthy start is five lines and a few seconds to first `/health`:

```
INFO:     Started server process [44573]
INFO:     Waiting for application startup.
2026-08-08 21:36:53,760 INFO mcp.server.streamable_http_manager: StreamableHTTP session manager started
INFO:     Application startup complete.
INFO:     Uvicorn running on http://127.0.0.1:8765 (Press CTRL+C to quit)
```

Two log formats interleave: uvicorn's own `INFO:     ` with no timestamp, and the project's
`<time> <level> <logger>: <message>`. Only the second carries a logger name.

Silence about Telegram is correct. The bot is optional ([§7](#telegram-your-phone)).

**The daemon binds `127.0.0.1:8765` and has no authentication** (arch §10). It is a local process.

**Look in the directory now.** It was empty when you started:

```
$ find ~/pkb/tree
~/pkb/tree
~/pkb/tree/index.md
~/pkb/tree/tags.md
```

The daemon ran a full regeneration on startup and wrote the two derived root files itself. You do
not create them. `index.md` is the Librarian's routing view:

```markdown
---
title: "PKB Topic Catalog"
description: "Every PKB topic with its description — the Librarian's routing view"
source_type: catalog
---

# PKB Topic Catalog

<!-- Generated by pkb.core. Do not edit: this file is overwritten on every flush. -->

## Topics

_No topics yet._
```

`_No topics yet._` is the specified empty state (GE-29). Neither file carries a timestamp or a
count. Derived output is byte-idempotent, which stops the end-of-turn flush from churning the tree
on every message.

### Check it

```bash
curl -s http://127.0.0.1:8765/health | python3 -m json.tool
```

Against the empty tree above, verbatim:

```json
{
    "status": "ok",
    "version": "0.1.0",
    "uptime_s": 10,
    "kb_root": "/Users/you/pkb/tree",
    "agent_count": 1,
    "active_runs": 0,
    "subscribers": 0,
    "runtime": {"open": true, "db_path": "/Users/you/pkb/pkb.sqlite",
                "durability": "sync", "fanout_limit": 3},
    "threads": {"total": 0, "pending_approvals": 0},
    "proposals": {"pending": 0},
    "scan_worker": {"state": "disabled", "pending": 0, "last_run_at": null, "last_error": null},
    "flush": {"last_report_at": "2026-08-09T02:36:53Z", "findings": 0},
    "telegram": {"enabled": false, "state": "disabled", "restarts": 0, "last_error": null,
                 "last_error_at": null, "started_at": null, "chats": 0, "last_poll_ok_at": null,
                 "last_send_error": null, "invalid_chats": [], "topics_enabled": false,
                 "channels": 0, "retired_channels": [], "unmapped_agents": ["librarian"]},
    "mcp": {"mounted": true, "sessions": 0}
}
```

Three fields look like faults. All three are normal on a fresh install:

* `scan_worker.state: "disabled"`: background conflict scanning does not run
  ([§9](#9-not-built-yet)).
* `telegram.state: "disabled"`: no bot configured.
* `telegram.unmapped_agents`: listed even when Telegram is off.

`agent_count: 1` on an empty tree is the Librarian alone. An empty knowledge base must still produce
a working Librarian (LB-6), and it does.

`/health` echoes `kb_root` as the **literal argument string**. Launch with `../tree` and it reports
`"kb_root": "../tree"`, which tells you nothing about which tree this daemon serves. Use absolute
paths.

Every flag:

```
usage: pkb-daemon [-h] [--db DB] [--host HOST] [--port PORT]
                  [--telegram-config TELEGRAM_CONFIG] [--env-file ENV_FILE]
                  kb_root
```

`--host` and `--port` carry no help text; the defaults are `127.0.0.1` and `8765`.

---

## 4. The model

| | Model | |
|---|---|---|
| **Default** | `ollama:deepseek-v4-flash:cloud` | cheap and fast; everything below ran on it |
| **Fallback** | `ollama:gemma4:31b` | **local**, ~20 GB, only for when the cloud is down |

**You do not need Ollama to install, create a tree, start the daemon, or browse it.** Verified: with
`OLLAMA_HOST=http://127.0.0.1:1` the daemon starts with an identical log, `/health` returns
`status: ok`, and `GET /agents` reports `"model_id": "ollama:deepseek-v4-flash:cloud"` without
contacting Ollama. The code builds a model only when a turn needs one.

To use one: install [Ollama](https://ollama.com), sign in for the cloud models, and check:

```
$ ollama list
NAME                            ID              SIZE      MODIFIED
gemma4:31b                      6316f0629137    19 GB     36 hours ago
deepseek-v4-flash:cloud         6ca9e29c41de    -         5 days ago
```

A `-` in SIZE means the model is a cloud tag with nothing stored on the machine.

### The cost of the fallback

**The fallback is a degraded backup, not a drop-in.** `CLAUDE.md` measures the same filing turn at
**284 seconds locally against about 16 seconds on the cloud default, about 18× slower**. A turn is
8–12 model calls, and each local call takes about 25 seconds over a growing context. Try the
system on the fallback without knowing this and you will conclude it is broken. It is slow, not
broken.

`gemma4:31b` is **not pulled by default** and nothing pulls it for you. If you need it and it is
missing, you get a `ModelNotInstalledError` naming the exact command:

```bash
ollama pull gemma4:31b
```

Only quota, concurrency and availability fail over: 429, 408, 5xx, connection and timeout errors. A
malformed request or a content error propagates untouched, because the second model would fail in
the same way.

**The daemon logs every failover at warning level.** This line comes from this guide's own session,
during a real cloud outage. Nobody forced it:

```
2026-08-08 21:41:05 INFO httpx: HTTP Request: POST http://127.0.0.1:11434/api/chat "HTTP/1.1 502 Bad Gateway"
2026-08-08 21:41:05 WARNING pkb.agents.models: model failover: the primary model
'ollama:deepseek-v4-flash:cloud' is unavailable (ResponseError: Post "https://ollama.com:443/api/chat":
net/http: TLS handshake timeout (status code: 502)), so this run is being answered by the fallback
model 'ollama:gemma4:31b'. The knowledge base keeps working, but the judgement in these turns is the
fallback's, not the primary's.
```

The next model call took 39 seconds and the turn completed. **If a turn takes minutes instead of
seconds, look for that line before you assume a hang.**

### Changing the model

There is no config file and no CLI flag for it. `python -m pkb.daemon` hardcodes its
`RuntimeConfig`. To change the model, the per-agent overrides, `fallback_model=None`,
`source_roots`, `fanout_limit` or `durability`, write a short launcher. It calls
`pkb.daemon.build_app(kb_root, db, config=...)` and hands the app to `uvicorn.run`.

The model is a **registry** concern (RG-21): no transport, route or channel picks one, and nothing
reads it from knowledge-base content.

---

## 5. Your first conversation

The walk-through below runs end to end, on a knowledge base that was empty ten seconds earlier.
Every line is real output from one continuous session.

**Read it for the shape, not as a script.** The topic name, the tags and *how many* approvals you
answer are the model's choices, and they change run to run. In a second run of these commands, on a
second empty tree, the model named the topic `coffee-brewing` rather than `Coffee` and asked four
times rather than twice. The mechanism is stable: an empty catalog forces a topic-creation approval,
the expert files on a thread of its own, and every gate in [§6](#approvals-what-stops-for-you)
applies.

The tree, before:

```
tree/index.md
tree/tags.md
```

### Send the note

Start a thread with the Librarian and send it a note. In the terminal client: select **Librarian**
in the sidebar, press `n`, type the note, press Enter. The same note over HTTP pastes into a
terminal:

```bash
TH=$(curl -s -X POST http://127.0.0.1:8765/agents/librarian/threads \
       -H 'content-type: application/json' -d '{}' \
     | python3 -c 'import sys,json;print(json.load(sys.stdin)["thread"]["thread_id"])')

curl -sN -X POST "http://127.0.0.1:8765/threads/$TH/runs" \
  -H 'content-type: application/json' \
  -d '{"message":"My espresso is sour when the beans are fresh. Resting them a week after roast fixes it."}'
```

(The thread id is nested: `{"thread": {"thread_id": ...}}`. `-N` disables curl's buffering so the
stream arrives live.)

### The response

**5.2 seconds.** The response is a server-sent event stream, and it ends parked:

```
event: run.started
event: tool.start
data: {"tool":"create_topic","summary":"name=Coffee, title=Coffee & Espresso, description=Brewing and…"}

event: interrupt
data: {"request":{"interrupt_id":"f73697341b6506e72b312a31c70d1da0","agent_id":"librarian",
       "actions":[{"tool":"create_topic",
        "args":{"name":"Coffee","title":"Coffee & Espresso","description":"Brewing and tasting…"},
        "allowed_decisions":["approve","edit","reject"],"reason":"topic-creation"}]}}

event: run.end
data: {"status":"interrupted"}
```

The Librarian did not offer you a menu of topics, because there are none. A menu of nothing is not a
choice (LB-6). With an empty catalog, every inbound item is a topic gap, so the turn ends parked on
a question: *shall I create a topic called Coffee?* Creating a topic is one of the twelve gated
actions. The Librarian proposes it and you decide (LB-7).

**Note the HTTP status is 200 even when a run fails.** Failures arrive as `event: run.error` inside
the stream. `curl -f` will not catch them.

### Approving

```bash
curl -sN -X POST "http://127.0.0.1:8765/threads/$TH/interrupt" \
  -H 'content-type: application/json' \
  -d '{"interrupt_id":"f73697341b6506e72b312a31c70d1da0","decisions":[{"type":"approve"}]}'
```

`interrupt_id` is **required** (RO-12). Omit it and the daemon answers 400.

**32 seconds.** In that time the scaffolder created the topic, the registry re-scanned itself, and a
Topic Expert started on a child thread and filed the note. The tree afterwards:

```
tree/index.md                     ← regenerated
tree/tags.md                      ← regenerated
tree/Coffee/topic.md
tree/Coffee/index.md
tree/Coffee/notes/summary.md
tree/Coffee/notes/rest-fresh-beans-a-week.md                              ← see below
tree/Coffee/notes/resting-fresh-beans-a-week-fixes-sour-espresso.md
tree/Coffee/references/summary.md
```

The scaffolder created six paths: the topic directory, `topic.md`, `notes/`, `notes/summary.md`,
`references/` and `references/summary.md` (SC-1). The flush regenerated three derived files, and the
expert wrote the note. `/health` went from `agent_count: 1` to `2` **with no restart**. Approving a
topic creation invalidates the registry, which re-scans the tree.

The root catalog now routes:

```markdown
## Topics

- [Coffee & Espresso](Coffee/topic.md) `topic/coffee` — Brewing and tasting coffee and espresso —
  bean freshness and resting, grind, extraction, dialing in, and troubleshooting flavor issues…
```

and the tag registry grew a namespace:

```markdown
## Namespace: topic.coffee

- `topic.coffee` – root topic
```

The catalog line shows the **agent id** `topic/coffee` with a slash; the tag is `topic.coffee` with
a dot. The two spellings name the same topic and are easy to conflate.

**In this run nobody approved the note.** It landed on its own, which is RT-31, capture staying
frictionless. That is not guaranteed. In the second run the same note stopped on `new-tag`, because
that expert minted `topic.coffee-brewing.bean-freshness` and no file in the tree carried it yet
(RT-25).

```markdown
---
title: "Resting fresh beans a week fixes sour espresso"
description: "Sour shots from beans right after roast clear up once the beans rest a week past the roast date."
topic: "Coffee"
tags:
  - topic.coffee
  - type.solution
  - status.draft
created: 2026-08-08
updated: 2026-08-08
source_type: solution
---

# Resting fresh beans a week fixes sour espresso

Espresso is sour when the beans are fresh off the roast. Resting them a week after the roast date fixes it.
```

Seven frontmatter fields. `type.solution` because the note solves a recurring problem, and
`status.draft` because every file an agent authors lands as a draft awaiting your look. Any write
that sets `status.approved` gates too (RT-27).

### The next approval is a delete

The stream ended `interrupted` again, and `/health` reported another pending approval. It is a
**delete**, and it is the approval most people meet first:

```
Approval required: delete
Tool: delete
Path: Coffee/notes/rest-fresh-beans-a-week.md (delete — permanent, there is no undo)

Current content:
---
title: "Resting fresh beans a week fixes sour espresso"
…
```

The expert wrote the note under a short filename, and Layer 1 raised an **advisory** finding. The
write succeeded; the finding does not block it.

```
[FILENAME_TITLE_DIVERGENCE/VA-35] (title) The file name 'rest-fresh-beans-a-week.md' does not match
its title 'Resting fresh beans a week fixes sour espresso'.
```

The expert then rewrote the note under the suggested name, and now wants to remove the first file.
**Every delete gates** (RT-30), so it stops for you. This is "findings, not exceptions"
on the wire: the write landed, Layer 1 flagged it, and the agent chose to fix it.

> ### ⚠ Check the rewrite has landed before you approve the delete
>
> The expert may propose the delete **before** it writes the replacement. Verified: in the second
> run the expert proposed the delete of `resting-fresh-beans-fixes-sour-espresso.md` while that file
> was still the only copy. Approving it emptied `notes/`, and the replacement write then gated again
> on `new-tag`. Reject that follow-up and the note is gone. There is no undo (D6).
>
> The delete's description embeds the whole current file, which is what makes the decision possible.
> Before you approve, check that the replacement file already exists.
> `ls <kb>/<Topic>/notes/` answers that in one command. If the file being deleted is the only note
> there, **reject** it. Rejecting changes nothing on disk and lets the run carry on. The worst case
> is then two copies, and you can recover from two copies
> ([§8](#i-have-two-copies-of-the-same-note)). **Later** (`escape`) does *not* help here. It parks
> the interrupt, and the queued write never runs.

Approve it again with `{"type":"approve"}` and the new `interrupt_id`. **2.8 seconds** later the
duplicate is gone, and the turn completes with the expert's own reply:

> Filed. I classified this as a **solution note** […] Tagged `topic.coffee` / `type.solution`, and
> left `status.draft` so it reads as a proposal until you've looked at it. […] **The breadth files
> are still placeholders** — `topic.md` and `notes/summary.md` are empty drafts. When you're ready,
> I can draft the breadth files from what's actually filed here.

Final tree:

```
tree/Coffee/topic.md
tree/Coffee/index.md
tree/Coffee/notes/summary.md
tree/Coffee/notes/resting-fresh-beans-a-week-fixes-sour-espresso.md
tree/Coffee/references/summary.md
tree/index.md
tree/tags.md
```

If nobody answers that delete, both files stay on disk with identical content. Any turn that cannot
raise a live approval leaves it unanswered, and that includes every MCP call. This reproduced in
every session that ran the example. The way out is [§8](#i-have-two-copies-of-the-same-note).

### The approval parked on a derived thread

The delete gate did **not** appear on the thread you typed into. It parked on the expert's derived
thread:

```
kind    agent_id       thread_id                                       pending
routed  topic/coffee   0fa7c37a-…::topic/coffee                        f55a6d55…
user    librarian      0fa7c37a-…                                      None
```

Derived thread ids are `<parent>::<agent_id>`. They contain both `::` and `/`. They work raw in a
URL because the route uses a path converter, so do **not** percent-encode the slash.

In the terminal client, press `p` (Needs you) to reach a parked approval. Pending rows carry a `●`
and sort first. If you go back to "the conversation I started", you will not find it.

---

## 6. Daily use

### The terminal client

With the daemon running, in another terminal, from the repository root:

```bash
uv run python -m pkb.tui
```

`--daemon http://127.0.0.1:8765` is the default. On the knowledge base built above:

```
 ⭘                                            PkbApp
▼ Agents                         │
├── ▼ Librarian                  │
└── ▼ Coffee & Espresso          │
                                 │
  What do my notes say about sour│
  My espresso is sour when the be│
  Untitled thread (routed)       │
                                 │
                                 │▊▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▎
                                 │▊  Ask, or hand something over…                ▎
                                 │▊▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▎
 p Needs you  n New thread  R Rename  P Proposals  c Cancel run  q Quit    ▏^p palette
```

Sidebar: a tree of agents over a list of threads. Main pane: transcript, a one-line status strip,
and the composer. Agents nest on the `/` in the id, so `topic/cooking/grilling` hangs under
`topic/cooking`. The order is the server's, and the client never re-sorts it (TU-8).

**Two traps in the first ten seconds.**

1. **Nothing is selected at startup**, even when the Librarian is the only agent. Press `n` first
   and it prints `pick an agent first`. Select an agent, *then* press `n`.
2. **The tree cursor starts on the "Agents" root row, and Enter there collapses everything.**
   Verified: `root expanded=True lines=3` → press Enter → `root expanded=False lines=1
   selected=None`. Arrow keys then look dead because there is one line left. Press Enter again to
   restore it, then arrow **down** onto a real agent before pressing Enter. (Enter on a topic that
   has sub-topics collapses its children the same way. Textual's tree treats Enter as
   select-and-toggle.)

### Every key

| Key | What it does |
|---|---|
| `p` | **Needs you.** Refetches all threads. Pending rows carry `●` and sort first. |
| `n` | New thread with the **selected** agent. `pick an agent first` if none is selected. |
| `R` | **Does not rename.** See below. |
| `P` | Proposals: writes that needed approval and could not get it ([§9](#9-not-built-yet)). |
| `c` | Cancel the running turn. Silent no-op if nothing is running. |
| `q` | Quit. The daemon keeps running; so does any turn in flight. |

Inside an approval modal: `a` approve, `e` edit, `r` reject, `escape` later. Approve and reject sit
apart on purpose (TU-50). A mistyped neighbour key on a write with no undo is unrecoverable. **The
modal has no footer, so it shows those four keys nowhere on screen.**

Capitalisation matters: `p` and `P` are different screens one shift key apart, and lowercase `r`
inside the modal is *Reject*.

The status strip is sticky. The client overwrites it and never clears it, so a message from three
actions ago is still there.

**`R` does not rename a thread.** It prefills the composer with `/rename <title>`. The submit
handler does not parse slash commands, so Enter sends `/rename …` to the model as an ordinary
message and burns a full turn. The title does not change. `PkbClient.rename` exists, and nothing but
its own test calls it. Treat `R` as not implemented ([§9](#9-not-built-yet)).

### Notes versus questions

There is no mode to select and no intent classifier. A question takes the identical path as
material. A turn is read-only when the expert calls no write tool.

Ask the Coffee expert directly, in its own thread:

> What do my notes say about sour espresso? Just answer, do not file anything.

**3.3 seconds**, status `completed`, and the file count in the tree did not change:

> There's one note on this: **Resting fresh beans a week fixes sour espresso.** The distilled point:
> espresso comes out sour when the beans are fresh off the roast, and resting them a week after the
> roast date clears it up. That's the whole note […] so there's nothing more filed on sour espresso
> beyond it.

Transcript: `ls`, `grep`, `read_file` under `/kb/Coffee`, then one message. No routing call, no
Librarian, no derived thread.

**The duration tells you which path a turn took.** Two independent sessions ran on the cloud
default, on the same machine, doing the same things. Read each pair as a range, not either number as
a figure:

| | first session | second session |
|---|---|---|
| Direct question to an expert | **3.3 s** | — |
| Empty knowledge base → topic-creation gate | **5.2 s** | **2.6 s** |
| Approve a delete → turn completes | **2.8 s** | **1.8 s** |
| Approve topic creation → scaffold, fan out, file a note | **32 s** | **13 s** |
| Ingest a small source file | **41 s** | **29 s** |

Three other sessions measured full Librarian filing turns at **244 s, 262 s and 269 s**, with
sibling processes contending for the same Ollama account. **A question is seconds; a filing turn
through the Librarian can be minutes.** Nothing measured here reproduced the "~16 s per filing
turn" `CLAUDE.md` quotes. A filing turn is 8–12 model calls plus a fan-out, and a busy account
stretches all of them. Sit at a prompt expecting sixteen seconds and you will think it hung.

### Talking to a specific expert

Two ways: select the expert in the sidebar and press `n`, or open any existing thread under it. Over
HTTP the agent id goes straight in the path, slash and all:

```bash
curl -s -X POST http://127.0.0.1:8765/agents/topic/coffee/threads \
  -H 'content-type: application/json' -d '{}'
```

Do this by habit. The Librarian costs a classification step and a fan-out. A direct question is one
expert and one turn, and the difference is minutes against seconds.

A fresh expert thread carries `title: null` and renders as `Untitled thread` until the first reply
lands a title.

### One message, two experts

Send the Librarian something touching two topics and it fans out. From a probe session with Cooking
and Gardening topics:

> Two things from this weekend. For gardening: I potted basil on the balcony. For cooking: I now
> make pesto with that basil, blitzed with pine nuts and pecorino.

Both experts ran at the same time. One filed and finished; one parked on an approval.
**Deterministic code merges the replies, never a second model** (LB-18). Each expert's own answer
sits under its own heading:

> Asked 2 experts; 1 could not finish. Every expert's own answer is under its own heading,
> unchanged.
>
> ## Cooking — `topic/cooking`
> _This expert is waiting on your decision before it can finish._ […]
>
> ## Gardening — `topic/gardening`
> Filed. […] _Filed: `Gardening/notes/potting-basil-on-the-balcony.md`_
>
> You can carry on directly with any of them: `topic/cooking` (thread …), `topic/gardening` (thread …).

**One expert that answers and one that parks is a success, not a partial failure.** An expert that
declines outright is also a success. An expert should not file material that holds nothing for its
topic (`README.md` §2.2).

`fanout_limit: 3` caps the fan-out width, and `/health` shows it.

The "continue with…" lines survive a reload. The client rebuilds them from the thread's children
and never parses them out of reply text. **They are not clickable and no key activates them.** The
offer names the expert; the `(routed)` row in the sidebar is the way there.

### Approvals: what stops for you

Twelve reasons. The runtime evaluates them in declaration order, and the first match wins:

| | |
|---|---|
| `topic-creation` | Scaffolding a new topic or sub-topic. |
| `delete` | Any delete under the tree. No version control, no undo (RT-30). |
| `unresolved-path` | The target exists but cannot be classified. A check that cannot run has not passed. |
| `conflict-resolution` | Clearing `status.conflict-review`. Adding it is exempt. |
| `breadth-approval` | `topic.md`, `notes/summary.md`, `references/summary.md`. |
| `expert-overload` | `<topic>/expert.md`. |
| `skill-overload` | Anything under a `skills/` folder. |
| `extension-folder` | A new non-structural folder directly under a topic root. |
| `reference-rewrite` | A re-ingestion that would remove or alter text already in a source file. |
| `new-tag` | The draft carries a tag no file in the tree uses. |
| `status-approved` | Introducing `status.approved`. |
| `human-content-edit` | Changing the body of an existing note. |

Five tools gate: `write_file`, `edit_file`, `delete`, `create_topic` and `create_subtopic`. Reads,
`grep` and delegation never interrupt.

Every write reason allows **approve / edit / reject**. `delete` allows only **approve / reject**.
You cannot edit a delete into a different delete (RT-30), and the modal says so.

**The gate is mechanical, not the model's opinion.** A probe captured an expert's message saying
"Filing it as an ordinary note, `status.draft`, no approval gate". The `new-tag` gate fired anyway.
The transcript can contradict the modal that is about to open.

### The approval modal

A real screen at 100 columns. The action on it creates a `Beekeeping` topic rather than §5's
`Coffee`. Look at the layout:

```
1 action(s) need your decision                    ▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔
create_topic  ·  topic-creation                       Approve      Edit      Reject
There is no undo for this.                        ▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁
Approval required: topic-creation
Tool: create_topic

description: Beekeeping and apiculture — hive
management, bee health, honey production, and
colony maintenance.
name: Beekeeping
title: Beekeeping
▊▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▎
▊  Beekeeping and apiculture — hive management,  ▎
▊▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▎
```

One editable input per argument, seeded with the proposed value. The controls come from the server's
`allowed_decisions`, verbatim (TU-42), so the modal never draws a button the server would reject.
The modal drew four buttons here. At 100 columns **"Later" is off the right edge.** Use `escape`.

**Edit gives you a single-line input.** Fine for a topic name, a description or a path. For a
`write_file` it puts the **entire markdown document on one line**, which is unusable. Reject and
re-dictate instead.

**The four decisions:**

* **Approve** (`a`): the write happens. An approval showing *one* action can release more than one
  file change. The runtime batches a message's interruptible calls into one interrupt, and an
  ungated sibling call in the same batch goes with them. Verified: approving a delete also released
  a queued write, and approving a topic creation touches **nine** paths, not one.
* **Edit** (`e`): change the values, then approve. A delete does not offer it.
* **Reject** (`r`): verified, **no change on disk**, and the run resumes and completes. Neither
  channel collects a reason, on purpose, so both behave the same way. The expert learns "no",
  not why.
* **Later** (`escape`): sends **nothing** (TU-47). The approval stays parked on its thread. That
  thread stays badged and first in the list, and you can answer it from any channel later. This is
  the correct "I'll think about it". Escape means cancel in most programs. Here it does not.

### Filing a source

**This works, and it is the most useful thing in the system.** A source is a path or a URL. Code
reads it in a loop rather than through a tool the model may decline to call. The harness walks the
source's own sections and asks the expert one bounded question per section, so the source bounds
what lands rather than a context window. This guide ingested a short markdown file. The 500-page
case belongs to the design spec, which verified it on one.

Ask an expert directly. There is no special syntax:

> Please ingest the source at ~/Documents/espresso-guide.md with your ingest_source tool.

**41 seconds** in this session, for a short markdown guide:

> The source is ingested. […] was read in 5 sections and filed to:
> ```
> /kb/Coffee/references/pkb-guide-probe-espresso/pkb-guide-probe-espresso.md
> /kb/Coffee/references/pkb-guide-probe-espresso/pkb-guide-probe-espresso.source.txt   (original)
> ```
> **What the topic took from it** — 5 arguments across 4 of the 5 sections […]

The reference file is a **map, not a summary**: provenance, one section per argument the source
makes, and an honest record of what the expert read:

```markdown
## Provenance

- Origin: `/Users/you/Documents/espresso-guide.md`
- Original in this topic: [pkb-guide-probe-espresso.source.txt](pkb-guide-probe-espresso.source.txt)
- Structure: `markdown-headings` — the source's own
- First read: 2026-08-08

## Grind is the primary lever

- Grind size is the primary lever on extraction — finer slows the shot and raises extraction […]

…

## Reading record

### Pass 1 — 2026-08-08

Read 5 sections; structure recovered as `markdown-headings`.

- Took something from: **Grind is the primary lever** — 2 arguments
- No text was extracted for: **Dialing in espresso: what actually moves the needle**
```

That last line is the whole point of the shape. `README.md` §1.2:

> The word *summary* names the failure this shape exists to prevent: a confident write-up of the part
> that fit in one context window, with nothing anywhere recording that the rest was never opened.

Three things to know:

* **`ingest_source` stages the source in `<kb>/.inbox/` and keeps both files**: the extraction the
  loop reads, and the original the topic gets a copy of. `.inbox` is dot-prefixed, so Layer 1's walk
  skips it. Layer 1 records, validates, indexes and tags nothing in there.
* **A topic only gets a copy if it gained something.** Zero insights leaves no folder, no stub and
  no copy. An empty folder would imply the source was considered and is relevant, so the ingestion
  leaves no trace at all.
* **The same source can go to several experts, and that is not duplication.** A management book
  routed to Management and to Parenting yields two different extractions, one per topic
  (`README.md` §2.2).

> ### ⚠ By default an expert may read anything under your home directory
>
> `RuntimeConfig.source_roots` defaults to empty, and empty means `Path.home()`, not "nothing".
> Verified in this session: the expert ingested a path under `$HOME` and copied it into the tree.
> `ingest_source` refused a path under `/private/tmp` with *"is outside the directories this
> knowledge base ingests from"*. `RuntimeConfig`'s own docstring records the reproduction:
> `~/.ssh/id_rsa` staged, ingested and copied into a topic as an ordinary reference.
>
> A prompt-injected turn can ask an expert to ingest a file from anywhere under your home directory.
> One field fixes it: `source_roots=(Path.home() / "Documents/pkb-sources",)`. You can set it from a
> launcher only, not from the CLI. `allow_url_sources` also defaults to `True`.

### Adding a topic yourself

You can, but the sanctioned path is the one in [§5](#5-your-first-conversation): send a note, let
the Librarian propose the topic, approve it. Do **not** `mkdir` a topic folder. A directory with no
`topic.md` is not a topic root (PA-3).

If you want one directly, ask the Librarian for it in words, or call the scaffolder:

```python
from datetime import date
from pathlib import Path
from pkb.core import scaffold_topic

scaffold_topic(
    Path("/Users/you/pkb/tree"), "Cooking",
    title="Cooking",
    description="Recipes, technique and kitchen notes.",
    today=date(2026, 8, 8),
)
```

`today` is a **required keyword-only** `date`. The caller injects it; the code never reads the
clock. The result:

```
ScaffoldResult(topic_path='Cooking',
  created=['Cooking', 'Cooking/topic.md', 'Cooking/notes', 'Cooking/notes/summary.md',
           'Cooking/references', 'Cooking/references/summary.md'],
  skipped=[],
  flush=FlushReport(written=['index.md', 'tags.md', 'Cooking/index.md'],
                    unchanged=['coffee-brewing/index.md'], stamped=[], findings=[],
                    scan_requests=[]))
```

(Line-wrapped here; the real repr is one line. `unchanged` shows the byte-idempotence rule in the
return value: the other topic's index rendered the same, so the flush did not rewrite it, GE-8.)

**A topic you scaffold yourself needs a daemon restart before you can talk to its expert.** The
daemon scans the catalog at startup, and re-scans it only when an *agent* creates a topic. Verified:
`/agents` did not show a topic created on disk under a running daemon until a restart or an
unrelated `create_topic` triggered the re-scan.

(Separately: a skill you add mid-session stays invisible to any thread that has already taken a
turn, because the runtime checkpoints the skill set per thread. That needs a *new thread*, not a
restart, RG-18.)

### Can I edit a file by hand?

Nothing stops you, and nothing reverts you. **There is no filesystem watcher.** Verified: a hand
edit to a note's tags left the topic's `index.md` showing the old value. It caught up at the next
turn's flush. The daemon regenerates derived files at the end of an agent run, never from a
filesystem event.

To force it, or to check a tree you have edited by hand:

```bash
uv run python -c "
from pathlib import Path
from pkb.core import validate_tree, render_findings, errors_only
f = validate_tree(Path('/Users/you/pkb/tree'))
print(render_findings(f) or '(no findings)')
print('errors:', len(errors_only(f)), 'total:', len(f))
"
```

On the tree from §5:

```
(no findings)
errors: 0 total: 0
```

On the same tree after ingesting one source:

```
Coffee/references/pkb-guide-probe-espresso/pkb-guide-probe-espresso.md:
  [FILENAME_TITLE_DIVERGENCE/VA-35] (title) The file name 'pkb-guide-probe-espresso.md' does not
  match its title 'Dialing in espresso: what actually moves the needle'. — Consider naming the file
  dialing-in-espresso-what-actually-moves-the-needle.md.
errors: 0 total: 1
```

And on a tree that had been hand-edited, from a probe session:

```
Cooking/notes/cast-iron-drying.md: [TOPIC_LOCATION_MISMATCH/VA-12] (topic) topic is 'Woodworking'
  but the file lives under the topic root 'Cooking'. — Set topic: "Cooking", or move the file into
  the Woodworking topic.
errors: 1 total: 4
```

Three notes on that recipe. `validate_tree` takes the **kb_root Path**, not a scan snapshot. `VA-35`
is a warning, not an error, and **every ingested reference trips it for good**. The filename comes
from the source and the title from the source's own heading, so a healthy tree carries one standing
VA-35 per ingested source, as above. And when the daemon does report something, it reports a
**count** only:

```
WARNING __main__: flush reported 4 finding(s)
```

(`__main__`, not `pkb.daemon`: `python -m pkb.daemon` runs that module *as* `__main__`.)

> **`flush.findings` on `/health` is not this number.** The two counts come from different passes.
> Do not read the daemon's silence as a clean tree. The end-of-turn flush reports the *maintenance*
> pass: the tree walk's own findings, the maintenance flags, and `updated`-stamp failures.
> `validate_tree` runs the full validator on top of that, and `VA-35` is one of the rules only it
> applies. Measured on the tree above, at the same moment: `validate_tree` **1**,
> `/health` `flush.findings` **0**.
>
> `flush.findings: 0` means "nothing broke while regenerating", not "the tree validates". The
> recipe above is the only thing that answers the second question. Both paths discard the finding
> *text*, so the recipe is also the only way to see what a non-zero count was about.

---

## 7. The other channels

### Telegram, your phone

A chat with a bot. One of your experts files whatever you send. The daemon owns the run, so an
approval it asks for is a pair of buttons you can press hours later, from anywhere.

**→ [`telegram.md`](telegram.md)**: ten minutes, start to finish. It covers how to create the bot,
the two secrets, the chat-to-agent mapping, a topic per expert, the six commands, and its own
symptom-first troubleshooting.

Two things to know before you read it. **One human with one bot gets one chat, and that chat holds
one conversation per expert**: a Telegram topic each, once you turn on BotFather's Threaded Mode and
ask for them. The mapping file names the expert for the part outside every topic. Map that to
`librarian`, the one agent that reaches the whole tree by routing. The owner allow-list is the
**only** authentication boundary in the system.

This guide ran nothing against Telegram.

### MCP, for other agents

The daemon mounts an MCP server at `/mcp`. Register it (executed in a probe session, against a
daemon on another port, in a throwaway config directory):

```bash
claude mcp add --transport http pkb http://127.0.0.1:8765/mcp
claude mcp list
```

```
pkb: http://127.0.0.1:8765/mcp (HTTP) - ✔ Connected
```

`--transport http` is **required**. The default is stdio, and this project has no stdio server, so
`claude mcp add pkb2 http://…` registers the URL as a *command*. The CLI prints a hint suggesting
`--transport sse` (not `http`), then adds it anyway. The entry shows up in `claude mcp list` as

```
pkb2: http://127.0.0.1:8765/mcp  - ✘ Failed to connect — ENOENT: … posix_spawn 'http://127.0.0.1:8765/mcp'
```

Use `/mcp` with no trailing slash. `/mcp/` answers `307` rather than serving, so a client that does
not follow redirects on a POST fails. No auth, no headers: the daemon binds localhost.

Four tools, listed live from the running daemon (`*` = required):

| Tool | Arguments |
|---|---|
| `pkb_ask` | `question*`, `agent_id` (default `librarian`), `thread_id` |
| `pkb_ingest` | `content*`, `source_type`, `topic_hint`, `thread_id` |
| `pkb_research_pack` | `query*`, `topics`, `include_index`, `budget_bytes` |
| `pkb_implementation_pack` | `topic*`, `include_subtopics`, `budget_bytes` |

Plus two resources (`pkb://agents`, `pkb://proposals`) and one resource **template**
(`pkb://proposals/{proposal_id}`) that only appears in `resources/templates/list`, never in
`resources/list`. There is no write tool and no approve tool, on purpose.

An implementation pack against the Coffee topic from §5, live:

```
- Coffee/notes/summary.md    | notes-summary  | 400 bytes
- Coffee/index.md            | topic-index    | 1022 bytes
- Coffee/notes/resting-fresh-beans-a-week-fixes-sour-espresso.md | type.solution | 471 bytes
truncated: False
```

That order is the contract (PK-10): human rules first, then the index, then reference depth files,
then notes tagged `type.solution`. **An implementation pack holds no ordinary notes**, only
`type.solution` ones. File ten `type.note` notes, ask for a pack, and you get two placeholder files
and conclude it is broken.

`budget_bytes` truncates by prefix. Once one entry does not fit, the pack omits every later entry
for the same reason, even one that would have fitted. `budget_bytes: 0` means *no budget*, not an
empty pack.

The tools return failures and never raise them, as an `isError` result with a machine code:

```
$ pkb_research_pack {"query":"sour espresso"}
isError: True
{"status":"error","code":"invalid_argument","message":"pkb_research_pack needs explicit `topics` in
 v1: read pkb://agents and name them. Topic selection by classification is a Layer 2 call and is not
 wired to this tool yet (PK-8, PK-9)."}

$ pkb_implementation_pack {"topic":"topic/coffe"}
isError: True
{"status":"error","code":"unknown_topic","message":"no topic answers to the agent id 'topic/coffe'
 — expected one of: topic/coffee"}
```

The refusal lists the valid ids, because nothing fuzzy-matches an id (RG-9).

**Three traps.**

1. **`pkb_ingest` cannot ingest a file.** It takes `content`, the material verbatim, and always
   enters at the Librarian, which holds no `ingest_source` tool. To read a file from disk, ask a
   *topic expert* in prose. The two names collide.
2. **`pkb_ingest` runs against a hard 300-second deadline.** A probe measured a single one-expert
   fan-out at **269 s** on the cloud default, 31 seconds short of a cancellation that returns
   `{"status":"timeout"}`. On the local fallback it cannot complete. Address an expert directly
   whenever you can.
3. **Every MCP call runs in propose-only mode.** Gates cannot raise a live approval, so they become
   proposals, and nothing can apply a proposal ([§9](#9-not-built-yet)).

**One flagged file blanks every MCP answer for its topic, with no warning.** If any file in a topic
carries `status.conflict-review`, a pack for that topic returns `status: "escalation"` with **zero
entries** and the review note. `pkb_ask` also flips to `escalation` and still answers. It is not an
error, on purpose, so a client branching on `isError` sees a success with an empty payload. Remove
the tag and the next call clears it, with no restart.

---

## 8. When something goes wrong

Symptom first.

---

**The daemon dies with forty lines of traceback.**

Read the **last** line:

```
pkb.core.errors.KbNotFoundError: knowledge base root /Users/you/pkb/tre does not exist or is not a
directory

ERROR:    Application startup failed. Exiting.
```

The knowledge base path is wrong or the directory does not exist. The daemon does **not** create it.
`mkdir -p` it, or fix the typo. One message covers both "missing" and "not a directory".

This is the most common first mistake, and it gets the ugliest error. `main()` wraps the Telegram
config in a clean one-line `parser.error()` and performs no check on `kb_root`.

**It also leaves a stray database behind.** The daemon creates the SQLite file before the check
fails, at the default location. That location is a *sibling* of the path you typed, so a typo'd
`~/pkb/tre` leaves it in `~/pkb`:

```
-rw-r--r--  1 you  wheel  49152 Aug  8 22:00 pkb.sqlite
```

Harmless, but two typos in two different parents leave two orphan databases.

---

**`[Errno 48] address already in use`.**

```
INFO:     Application startup complete.
ERROR:    [Errno 48] error while attempting to bind on address ('127.0.0.1', 8765): [errno 48]
          address already in use
INFO:     Waiting for application shutdown.
```

Another daemon holds the port. Find it:

```bash
lsof -nP -iTCP:8765 -sTCP:LISTEN
```

Note the ordering: **`Application startup complete.` prints before the bind fails**, so the flush
has already run and the daemon has already created the second database. A mistaken second daemon
does touch the tree before it exits.

---

**A turn returns HTTP 200 but nothing happens, and the stream says `run.error`.**

```
event: run.error
data: {"message":"All connection attempts failed","retryable":true,"status":"error"}
```

Ollama is not reachable. Check `curl -s http://localhost:11434/api/version`. If the daemon log also
shows the failover warning naming both models, then the cloud model *and* the local fallback were
both unreachable. The Ollama server itself is down; your quota did not run out.

`curl -f` will not catch this. The HTTP status is 200; the failure is inside the stream.

---

**A turn that used to take seconds now takes minutes.**

Search the daemon log for `model failover`. If the line is there, you are on `gemma4:31b`, about
18× slower, per `CLAUDE.md`'s measurement. The knowledge base still works; the judgement in those
turns is the fallback's. The warning fires at most once per outage.

If there is no failover line, and this is a Librarian turn on a topic-less or many-topic tree, the
cost is the classify and fan-out step. Address the expert directly.

---

**I quit the client in the middle of a turn.**

The turn is still running. **The daemon owns runs**, not the connection. A run is a task that
publishes into a per-run hub, and an HTTP response subscribes to it. A dropped connection
**detaches**; it never cancels. An ingestion turn killed because a phone crossed a tunnel is a
broken promise, and that is the point. Reopen the thread and the transcript is there.

Cancelling is a deliberate, separate act: `c` in the terminal client, or
`DELETE /runs/{run_id}`.

Kill the **daemon** and everything stops. To make it outlive the terminal that started it, start it
under `nohup`, `tmux` or a service manager. Threads, pending approvals and proposals live in SQLite
and survive a restart either way.

---

**I approved something, and now I cannot find the next approval.**

It parked on the expert's **routed child thread**, not the thread you typed into. Press `p` and look
for the `●`-badged `(routed)` row. It sorts first. Post the decision to the parent thread and you
get a 409, by design.

Over HTTP, list them:

```bash
curl -s http://127.0.0.1:8765/threads | python3 -c '
import sys,json
for t in json.load(sys.stdin)["threads"]:
    if t["pending_interrupt_id"]: print(t["thread_id"], t["pending_interrupt_id"])'
```

---

**`409 Stale interrupt`.**

```json
{"title":"Stale interrupt","status":409,"code":"stale_interrupt",
 "detail":"no approval is pending on this thread; interrupt 'f736…' is no longer current"}
```

Someone already answered it, from another channel, or you did a moment ago. The daemon wrote
nothing. `GET /threads` can lag a few seconds behind `GET /threads/{id}`, so a `●` badge that
persists for a moment after a decision is that lag, not a stuck approval. Re-list.

---

**I have two copies of the same note.**

<a name="i-have-two-copies-of-the-same-note"></a>

The expert wrote the note under a short slug. Layer 1 flagged `FILENAME_TITLE_DIVERGENCE/VA-35`, so
the expert rewrote it under the suggested name. Nobody approved its request to delete the first
file. Both files stay, byte-identical, and the topic's `index.md` lists both.

Reproduced in five independent sessions. Two situations:

* **In the terminal client or Telegram**: check the replacement is on disk, then approve the delete
  when it appears (see the warning in [§5](#the-next-approval-is-a-delete)).
* **From MCP or a background scan**: those run propose-only, so the delete became a proposal, and
  nothing can apply a proposal. Delete the file yourself with `rm`, or go back to that expert in the
  terminal client and ask again so the gate raises a live approval.

**The opposite case: `notes/` is empty and the note is gone.** Same mechanism, opposite order. The
expert proposed the delete *first*, you approved it, then you rejected or never answered the write
that would have replaced it. There is no undo and nothing to recover from. The text is still in the
thread transcript, so re-send it. The warning in §5 exists to prevent this case.

---

**MCP now returns nothing for one topic.**

A pack came back `status: "escalation"` with zero entries. Some file in that topic carries
`status.conflict-review`. Find it:

```bash
grep -rl --include='*.md' 'status.conflict-review' /Users/you/pkb/tree | grep -v '/tags.md$'
```

(Root `tags.md` lists the whole `status.*` vocabulary, so a plain `grep -rl` always matches it. It
is never the file you want.)

Read its `review_note` and decide. Then have an expert change the tag back to `status.approved` and
remove the note. That is itself a `conflict-resolution` gate, so it will ask you. The escalation
clears on the next call.

---

**A topic I created on disk is not in `/agents`.**

Restart the daemon. The daemon scans the agent catalog at startup, and re-scans it only when an
agent creates a topic.

---

**The TUI shows `no daemon at http://127.0.0.1:8765`.**

```
no daemon at http://127.0.0.1:8999 — start it with `python -m
pkb.daemon <kb-root>`  (ConnectError)
```

Usually the message is right: the client and the daemon are separate processes, and this client
pointed at a port with nothing listening on it.

**Read the exception name in the brackets before you trust the sentence.** The client wraps its
three startup calls in a bare `except Exception` and prints this same line for *anything* that goes
wrong. `(ConnectError)` is "no daemon", `(ReadTimeout)` is a daemon that is up and slow, and
anything else is a bug in the client rather than a missing server. Verified: a healthy daemon on
`8765` produced this message verbatim with `(RuntimeError)`. `curl -s
http://127.0.0.1:8765/health` settles it in one command.

---

**`ModuleNotFoundError: No module named 'pkb'`.**

You ran `uv run` from outside the repository. `cd` to the repo root, or use
`uv run --project /path/to/agentic-pkb`.

---

**An import traceback mentioning `mcp` that has nothing to do with MCP.**

You have a file called `mcp.py` in the directory you launched from. Python puts the script's
directory first on `sys.path`, and the daemon imports a top-level package named `mcp`. Rename your
file.

---

## 9. Not built yet

One line each. You cannot do any of these today.

* **Undo, version control, backups (D6).** Plain markdown, no git. Back the directory up yourself.
* **Applying a proposal (Q3).** `pkb_proposals` records, lists and dismisses; it cannot apply. A
  proposal is a **dead letter**. To make the write happen, go back to that expert in an interactive
  channel and ask again, so the gate raises a live approval. Dismissing is bookkeeping, not action.
* **Dismissing a proposal from the TUI.** `P` shows them and tells you to dismiss; no key does it.
  Use `curl -X DELETE http://127.0.0.1:8765/proposals/<id>`.
* **`R` (Rename) in the TUI.** Prefills the composer; submitting sends `/rename …` to the model.
* **Background conflict scanning.** Implemented, and never wired into `python -m pkb.daemon`.
  `/health` reports `scan_worker.state: "disabled"` on every stock daemon. Rows accumulate in
  `scan_queue` and nothing drains them, and `scan_worker.pending` stays `0` regardless, so it is not
  a backlog reading. To enable it you need a launcher. (When it does run, a conflict surfaces in
  three places only: the file's own frontmatter, the topic's `index.md`, and an MCP escalation.
  Nothing in the TUI or Telegram mentions `status.conflict-review`.)
* **`pkb_research_pack` picking its own topics (PK-8/PK-9).** You must name them; the tool discards
  `query`.
* **A CLI for creating a topic or validating a tree.** Python API only: the snippets in §6.
* **A config file or CLI flags for models, `source_roots`, `fanout_limit`, `durability`.** Launcher
  code only.
* **Multi-user access and authentication.** Out of scope; the daemon binds localhost.
* **An ACP adapter (Zed).** Deferred, and additive when it arrives.
* **Telegram:** attachments, editing a proposal from the phone, typed rejection reasons, groups,
  webhooks, and **push for an approval raised in the TUI**. See
  [`telegram.md`](telegram.md) §7.

Everything else in this guide is built. Telegram excepted, as [§7](#telegram-your-phone) says,
every command in it was run.
