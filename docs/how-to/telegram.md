# Connect your phone to the knowledge base

A chat with a bot, and whatever you send it is filed by one of your experts, in its own conversation.
And because the daemon owns the run, an approval it asks for — a delete, a new topic, a conflict
resolution — is a pair of buttons on your phone that you can press hours later, from anywhere, while
the daemon keeps running and the turn keeps its state.

**Read this before you plan a layout.** The daemon addresses agents by chat: a chat maps to exactly
one agent, and that is how "which expert am I talking to?" stops being ambiguous. But Telegram gives
a person exactly **one** private chat per bot, the daemon holds exactly **one** bot token, and groups
and channels are refused ([§2](#2-find-your-user-id-and-your-chat-id), TG-19). So one human with one
bot has one chat and therefore **one** agent on their phone. Map it to `librarian` unless you know
you want otherwise: the Librarian classifies what you send and routes it to the right expert, which
is the only way one chat reaches a whole tree. A second entry in the mapping is a second **person** —
their private chat with the same bot, on their own topic — and they need to be on the allow-list too.

Ten minutes, start to finish. Every command, path, variable name, JSON key and log line below comes
from the code or from a real run of it.

**This assumes a daemon you can already start** — a clone, `make install`, a knowledge base
directory and `python -m pkb.daemon` — and it does not explain what an approval or a topic expert
is. If any of that is new, read [`getting-started.md`](getting-started.md) first; the bot is one of
the doors it describes, not the front one.

---

## 1. Create the bot

In Telegram, message [@BotFather](https://t.me/BotFather):

```
/newbot
```

It asks for a display name, then a username ending in `bot`. It replies with a token that looks like

```
123456789:AAH-fake-do-not-use-this-one-it-is-an-example
```

That token *is* the bot. Anyone holding it can read every message sent to it and send messages as
it. Keep it out of your shell history, your screenshots and your chats with an assistant — see
[§10](#10-revoking-a-token).

If you already have a bot, `/token` in BotFather reprints its token.

## 2. Find your user id and your chat id

**For a private chat they are the same number.** This trips everyone up, so: the daemon needs two
different-sounding things, and in a one-to-one chat they are one value.

* your **user id** — who may say yes, in `PKB_TELEGRAM_OWNERS`;
* the **chat id** of your one-to-one chat with the bot — which expert that chat talks to, in the
  mapping file.

Telegram gives a private chat the same id as the user on the other side of it, so in the ordinary
one-human deployment the *same* number goes in both places. Check it once rather than assuming it:

**The easy way.** Message [@userinfobot](https://t.me/userinfobot). It replies with your `Id`.

**The way that proves it.** Send your own bot any message ("hello"), then, **before you start the
daemon**:

```bash
read -rs PKB_TELEGRAM_TOKEN && export PKB_TELEGRAM_TOKEN   # paste the token; it is not echoed
curl -s "https://api.telegram.org/bot$PKB_TELEGRAM_TOKEN/getUpdates" | python3 -m json.tool
```

Read `result[0].message.from.id` (your user id) and `result[0].message.chat.id` (the chat id). For a
private chat they match, and `chat.type` is `"private"`.

Two cautions on that command:

* Keep the token in the variable, never typed into the command line, or it lands in your shell
  history. The variable only solves *history*: the shell still expands it before `exec`, so for the
  second or two that `curl` runs, the token is visible in `ps` to anyone else on the machine. That
  is exactly why the daemon takes it from the environment and not from a flag. On a single-user
  laptop this is fine; on a shared box, do this step somewhere else.
* Do it **before** the daemon starts. Telegram allows exactly one `getUpdates` consumer per token,
  so a `curl` against a running daemon gives one of you a `409 Conflict` ([§9](#9-troubleshooting)).

Group and channel ids are negative. They are refused at startup — only private chats are eligible
(TG-19), because a group is many senders with no identity check in front of a tree that has no undo,
and Telegram's group privacy mode silently drops most messages, so a mapped group would *half* work.

## 3. Put both secrets in `.env`

From the repository root:

```bash
cp .env.example .env
chmod 600 .env
```

Edit it. Two variables, both required:

```
PKB_TELEGRAM_TOKEN=123456789:AAH-fake-do-not-use-this-one-it-is-an-example
PKB_TELEGRAM_OWNERS=987654321
```

`PKB_TELEGRAM_OWNERS` is a comma- or space-separated list of Telegram **user** ids — not chat ids,
not usernames. A one-person deployment has one entry — yours. If you are giving somebody else a chat
of their own, add theirs: `987654321, 987654322` and `987654321 987654322` are both accepted.

**Say it plainly: that list is the only thing standing between a stranger and your knowledge base.**
A bot's username is discoverable, and the bot is a public inbound path into a process that writes
files with no undo and no version history. The chat mapping answers *which expert*; it never answers
*who may say yes*. If your id is not on this list, nothing you send runs — and if someone else's id
is, they can approve a delete. Empty or unset refuses everyone, which is the correct default for a
half-finished deployment, and the daemon warns about it at startup:

```
WARNING __main__: telegram has a token and 2 chat(s) but PKB_TELEGRAM_OWNERS names no owners: every message and every button press will be refused until an owner user id is added
```

(`__main__`, not `pkb.daemon`: `python -m pkb.daemon` runs that module *as* `__main__`, so every
line the composition root logs carries that name.)

Three more things about the file, all of them real behaviour:

* `.env`, `.env.*` are gitignored (`!.env.example` is the committed template). Check before you
  commit anyway.
* **A real environment variable always wins over a line in `.env`.** A systemd `Environment=`, a
  container secret or a one-off `PKB_TELEGRAM_TOKEN=… python -m pkb.daemon` is never silently
  overridden by a stale file. The daemon logs the *names* it took from the file, never the values:
  `INFO __main__: read PKB_TELEGRAM_OWNERS, PKB_TELEGRAM_TOKEN from .env`.
* If you skip the `chmod` it starts anyway, and says so:

```
WARNING __main__: .env is readable beyond its owner (mode 644); it holds the bot token and the owner allow-list, so `chmod 600 .env`
```

`--env-file` moves the file; the default is `.env` in the working directory.

## 4. Map your chat to an agent

The mapping lives beside the SQLite database, at `<db>.telegram.json`. With the default database
(`<kb>/../pkb.sqlite`) that is `pkb.sqlite.telegram.json`. It holds **only** the mapping:

```json
{
  "chats": {
    "987654321": "librarian"
  }
}
```

That is the one-human deployment, and `librarian` is the recommendation: you have one chat with this
bot, so mapping it to a topic makes exactly that topic reachable and nothing else, while the
Librarian classifies each note and routes it to the expert it belongs to. Map it to a topic only if
you want a dedicated notebook for one subject and nothing else on your phone.

A second entry is a second person, with their own private chat and their own user id — and their id
has to be in `PKB_TELEGRAM_OWNERS` as well, or everything they send is silently ignored:

```json
{
  "chats": {
    "987654321": "librarian",
    "987654322": "topic/woodworking"
  }
}
```

Keys are chat ids (as JSON strings, because JSON object keys are strings); values are agent ids. An
agent id is the topic's path under the knowledge base root, slugified, with the structural folders
elided: `<kb>/Cooking` is `topic/cooking`, and a sub-topic — which lives at
`<kb>/Cooking/sub-topics/Grilling`, never at `<kb>/Cooking/Grilling` — is `topic/cooking/grilling`.

Do not guess them. Start the daemon once with an empty `{"chats": {}}` and read `unmapped_agents`
from `/health` ([§6](#6-read-health)) — it lists every agent no chat is mapped to, spelled exactly as
this file wants it. Against a two-topic knowledge base with nothing mapped, that is:

```json
"unmapped_agents": ["librarian", "topic/cooking", "topic/woodworking"]
```

(With an empty `chats` the bot reports `"enabled": false` — expected; it needs a token *and* at least
one chat. `librarian` is in that list because it is a real, addressable agent, and it is the one most
single-chat deployments should map.)

**This file holds no secret and is safe to commit.** The token and the allow-list are both
environment variables, precisely so that this one names no credential. (The repo's `.gitignore` still
excludes `*.telegram.json` by default, on the assumption that a deployment's chat ids are nobody
else's business; delete that line if you want yours in version control.)

Two chats may map to the same agent (two people, one topic); one chat mapping to two agents is
impossible by the file's shape. A file that still carries an `"owners"` key is a **startup error**,
not a warning — see [§9](#9-troubleshooting).

The mapping is read **once, at startup**. Editing it means restarting the daemon.

## 5. Start the daemon

```bash
cd /path/to/agentic-pkb
uv run python -m pkb.daemon /path/to/kb
```

Flags: `--db`, `--host`, `--port` (default `127.0.0.1:8765`), `--telegram-config`, `--env-file`.

A healthy start looks like this — the first two lines are the ones that say Telegram is wired:

```
2026-08-08 19:49:39,931 INFO __main__: read PKB_TELEGRAM_OWNERS, PKB_TELEGRAM_TOKEN from .env
2026-08-08 19:49:40,896 INFO __main__: telegram enabled for 1 chat(s)
INFO:     Started server process [34983]
INFO:     Waiting for application startup.
2026-08-08 19:13:01,242 INFO mcp.server.streamable_http_manager: StreamableHTTP session manager started
INFO:     Application startup complete.
INFO:     Uvicorn running on http://127.0.0.1:8765 (Press CTRL+C to quit)
2026-08-08 19:13:01,612 INFO httpx: HTTP Request: POST https://api.telegram.org/bot123456789[redacted]/getUpdates "HTTP/1.1 200 OK"
```

Then a `getUpdates` line roughly every 25 seconds and nothing else. Note `[redacted]` — the daemon
installs a log filter over `httpx` before the first credentialed request, so the token does not reach
the log even though it lives in the URL path. That is a backstop, not a licence: a token that has
been anywhere it should not be still needs revoking.

Two normal first-start extras:

* **`WARNING pkb.server.telegram: telegram: cold start — discarding the backlog up to update N`**,
  and one message in each mapped chat saying so — but only if Telegram was actually holding
  something for this bot. A first start with an empty backlog says nothing. Telegram holds
  undelivered updates for up to 24 hours; on a database with no ledger, replaying a day of chat as
  turns into a tree with no undo is worse than losing it, so the backlog is dropped and you are told
  to re-send.
* Nothing at all about Telegram, if there is no token and no mapping file. The bot is optional and a
  missing one never costs you the HTTP API.

The TUI, for the same knowledge base, is `uv run python -m pkb.tui` (`--daemon
http://127.0.0.1:8765`).

## 6. Read `/health`

```bash
curl -s http://127.0.0.1:8765/health | python3 -m json.tool
```

`/health` is always `200` while the process serves; degradation is in the body. The `telegram` block,
verbatim from a real run — one chat mapped to `librarian`, against a two-topic knowledge base, with a
token BotFather never issued, which is why it is crash-looping:

```json
"telegram": {
    "enabled": true,
    "state": "restarting",
    "restarts": 5,
    "last_error": "ExceptionGroup: unhandled errors in a TaskGroup (1 sub-exception)",
    "last_error_at": "2026-08-09T00:49:57Z",
    "started_at": "2026-08-09T00:49:40Z",
    "chats": 1,
    "last_poll_ok_at": null,
    "last_send_error": null,
    "invalid_chats": [],
    "unmapped_agents": ["topic/cooking", "topic/woodworking"]
}
```

| Field | What it actually means |
|---|---|
| `enabled` | A token **and** at least one chat were configured. `false` means the bot was never started. |
| `state` | The supervised **task**'s lifecycle: `disabled`, `starting`, `running`, `restarting`, `stopped`. |
| `restarts` | How many times that task crashed and was restarted. Climbing is the signal; a single sample is not. |
| `last_error`, `last_error_at` | The last crash, redacted. Often unhelpfully generic (see above) — the log has the real cause. |
| `started_at` | When the task first reached `running`. |
| `chats` | How many chats your mapping names. Configuration, not traffic; it never changes at runtime. |
| `last_poll_ok_at` | **The reachability field.** Stamped every time `getUpdates` returns. |
| `last_send_error` | The last outbound failure, redacted. Reported only — it never makes the daemon `degraded`. |
| `invalid_chats` | Mapped chats naming an agent that does not exist. Those chats are answered as unmapped. |
| `unmapped_agents` | Agents with **no** chat pointing at them — i.e. not addressable directly from a phone. |

Two of these are worth stating twice.

**`running` does not mean Telegram is reachable.** The supervisor stamps `running` *before* it awaits
the first line of the bot's body, so a daemon whose token was revoked five minutes ago reports
`state: "running"` for the whole of its next poll while every request is answered `401` — and it goes
back to `running` after each restart, so a `/health` sampled at the wrong moment says `running` no
matter how badly the token is wrong. **`last_poll_ok_at` is the only field that reports
connectivity.** `null` after the first minute means no poll has ever succeeded; a timestamp going
stale means it stopped. (Verified against a real daemon with a made-up token: `state` alternates
between `running` and `restarting`, `restarts` reached 5 in the first twenty seconds — the retry
backoff doubles from 1 s to a ceiling of 60 s — and `last_poll_ok_at` stays `null` throughout.)

**`unmapped_agents` is a set difference, computed on every `/health` request** between the live agent
catalog and the agent ids your mapping names. On a one-chat deployment every topic is in it, which is
correct and not a problem: the Librarian your chat *is* mapped to reaches them by routing rather than
by address. Read it the other way round — if you mapped your chat straight to a topic, everything
listed there is unreachable from your phone, and that is otherwise a completely silent condition: you
create a topic, the bot carries on ignoring it, and nothing anywhere says why.

Elsewhere in the body, top-level `"status": "degraded"` means an *enabled* subsystem is not
`running`. A failed send or a bad mapping line never causes it.

## 7. Use it

**Send a note.** Type into the mapped chat. That runs one turn on that chat's current conversation
with the agent the chat is mapped to; the reply comes back as one or more messages (long replies are
split on line boundaries and numbered `(1/2)`, never summarised).

**Three lines typed as three messages is fine.** The bot serializes a chat's messages: the second
waits for the first turn to finish and then runs on its own. What you get back is two replies, in
order, not a refusal. The refusal — *"Still finishing your last message — send this again in a
moment. It was not sent: …"*, with your text quoted back so re-sending is one long-press away — is
for the case the lock cannot cover: something **else** started a run on that same conversation, the
TUI usually. There is a second, different refusal (*"There is an approval waiting on this
conversation, so this was not sent"*), which re-posts the buttons you have not answered yet.

**The five commands**, and there are only five:

| | |
|---|---|
| `/new` | Start a fresh conversation here. The old one stays in the thread list. |
| `/threads` | This expert's conversations, most in need of attention first. |
| `/agents` | Which expert this chat talks to, and which are configured across all chats. |
| `/pending` | Everything waiting on you, across **every** expert — and it re-posts the buttons. |
| `/cancel` | Stop the run in this chat. |

There is no `/connect`: a chat is bound to its expert by the mapping file, deliberately, because a
chat that can re-point itself is a chat that files a note into the wrong topic.

**An approval** arrives as the whole thing, then the buttons:

1. First, the complete description of what would be written.
2. Then a second message: any validation failures hoisted to the top with their rule ids, the tool
   and the reason (`write_file · delete`), the line **"There is no undo for this."** when the reason
   warrants it, a preview of the description, and the buttons — **Approve** and **Reject**, one per
   row, because on a phone one row is one thumb.
3. Pressing one answers the button immediately, then runs the turn — unless the reason is one of the
   three that ask for a second tap first (below). The outcome arrives as a **new**
   message (`Answered: 1 approved.`) and the buttons go away — the description you decided against
   keeps its text, because this chat is the only surviving record of what you approved.

**A long description arrives as a file** (`write_file-0.diff` or `.txt`, captioned with the tool and
reason). Telegram's message limit is 4,096 UTF-16 units and a real delete embeds the entire current
file — around 8,000 characters. Truncating would put bullets 60–119 behind an irreversible button, so
the whole text is uploaded first and the message underneath carries only a preview. If the upload
fails, the approval arrives **with no buttons** and a line telling you to use the TUI: a description
you cannot read is one you must not be asked to approve.

**Three reasons take two taps** — `delete`, `topic-creation` and `conflict-resolution`. The first
press, **whichever one it is**, brings up a second message: *"There is no undo for this. Confirm?"*
with **Yes, do it** / **Cancel**. Reject is confirmed too; the confirmation is about the reason, not
about the answer. On a phone two buttons in one row are neighbouring keys, and a thumb on a moving
train is a worse input device than a keyboard. **Cancel** means "I have not decided": nothing is
recorded, and the original buttons — which stay live throughout, the confirm pair being a new
message rather than a replacement — still work.

If an approval covers several actions, each gets its own message and **nothing is submitted until
every one is answered**. Answering half of them leaves the turn parked, exactly as it was.

**Not built, on purpose:** attachments (the bot replies that it can only read text and downloads
nothing); editing a proposal from the phone; a typed rejection reason; groups; webhooks (long
polling only — a webhook needs a public HTTPS endpoint on a daemon that has no auth); and **push
notification of an approval raised in the TUI** (D3 shares state, not streams). That last one is the
one you will notice: an approval a desk-started run is waiting on will not ping your phone. Ask for
it with `/pending`.

## 8. Change something

Edited the mapping, added a topic, rotated the token? **Restart the daemon.** All of it is read once,
at startup. Conversations, pending approvals and open button prompts are in SQLite and survive the
restart; on the way back up the bot re-posts the keyboard for anything still parked and tells any
chat whose message was lost mid-crash to send it again.

## 9. Troubleshooting

Symptom first.

---

**I send a message and absolutely nothing happens — no reply, no error, nothing in the log.**

Your user id is not in `PKB_TELEGRAM_OWNERS`. **The silence is deliberate** (TG-20): the bot's
username is discoverable, so a bot that answered strangers would be both a reply amplifier and an
oracle telling anyone who probed it that a knowledge base is here. Messages from non-owners get *zero*
replies on every path.

Button presses are different — they get a visible alert, because on a phone a silent answer is
indistinguishable from a successful one, and a stranger pressing Approve on a delete should not be
left believing it happened:

> This knowledge base does not accept decisions from this account. Nothing was approved, rejected or
> written.

and, in the log:

```
WARNING pkb.server.telegram: telegram: refused a button press from user 12345 in chat 12345 — not in the owner allow-list, which is this deployment's only authentication boundary
```

**Fix:** put your numeric user id in `PKB_TELEGRAM_OWNERS` and restart. Check the startup warning
quoted in [§3](#3-put-both-secrets-in-env) — if that line is in the log, the list is empty and everyone is
refused, including you.

---

**"This chat is not connected to anything, so I have not kept this message and nothing has been
filed."**

The chat id is not in the mapping — or it is, but points at an agent that no longer exists. The
message names the chat id you need. Check `/health`:

* `invalid_chats: [987654322]` means that chat's agent id is wrong. The log says which — a typo'd or
  renamed topic is reported, never fatal, so the other chats keep working:

```
ERROR pkb.server.telegram: telegram: chat 987654322 is mapped to agent 'topic/wodworking', which does not exist; that chat will be answered as unmapped until the configuration names a real agent
```

* `unmapped_agents` spells the agent ids that are free, exactly as the file wants them.

Add the chat id, restart. And note the rate limit: **you get this reply at most once per chat per
hour** (a bot that answered every probe would earn a Telegram rate limit that then delays *your*
approval keyboards), so silence after the first one is expected, not a second bug.

---

**The buttons do nothing / spin forever.**

* *Spinner never resolves, no alert:* the daemon is not running, or cannot reach Telegram. Check
  `last_poll_ok_at`. Telegram queues the press for up to 24 hours, and the bot resolves it against
  the durable prompt row when it comes back — an approval survives a restart.
* *"Already answered — from another channel, or earlier here."* Someone answered it in the TUI, or
  you did here and the message scrolled. Nothing was sent. This is the expected two-channel case,
  not an error.
* *"That approval could not be located."* The prompt row is gone (a fresh database, usually). It may
  still be parked — the TUI lists everything pending and can answer it.
* *"That button no longer matches the approval it belongs to."* An old message's buttons against a
  changed approval. Nothing was recorded; the live approval is still waiting.
* *"This knowledge base does not accept decisions from this account."* See the first entry.

Nothing in that list ever writes anything, and none of them are retried — a retry would apply your
taps to a *different* write.

---

**The bot answers, but in the wrong topic.**

`/agents` in that chat prints which expert it is bound to. If it is wrong, the chat id is on the
wrong line of the mapping — fix it and restart. On the next message you will see:

> This chat now talks to topic/woodworking, so I have started a new conversation here. The previous
> one is still in the thread list of the expert it belonged to.

That announcement is the point: the alternative is a chat that quietly keeps filing into the
previous expert forever. Notes already filed into the wrong topic are **not** moved — there is no
undo; move them yourself.

---

**`409 Conflict`, or `/health` `last_error` mentions another consumer.**

```
ERROR pkb.server.telegram: telegram: Conflict: terminated by other getUpdates request; make sure that only one bot instance is running. another consumer of this bot token is polling — either a second daemon is running against it, or a poller from a previous generation of this task is still alive. Polling is stopped; restarting this one would add a third.
```

Telegram permits exactly one `getUpdates` per token. This is never a transport blip: another daemon
is running against the same token, or your `curl … /getUpdates` from
[§2](#2-find-your-user-id-and-your-chat-id) is still going. It is **not** retried in a tight loop and
the task is **not** restarted (a restart would add a *third* poller); it re-probes every 60 seconds
and recovers by itself:

```
WARNING pkb.server.telegram: telegram: polling resumed; the other consumer of this token is gone
```

**Fix:** stop the other consumer. Two knowledge bases need two bots, i.e. two tokens.

---

**The daemon started, but `/health` says `"enabled": false, "state": "disabled"`.**

The bot needs a token **and** at least one chat. Missing either leaves it off. In order of likelihood:

* No mapping file. With no `--telegram-config`, a missing `<db>.telegram.json` means "not deployed"
  and is **completely silent** — the bot is optional. Confirm the path: it is the database path plus
  `.telegram.json`, and the default database is `<kb>/../pkb.sqlite`, *not* inside the knowledge
  base. `/health` prints `runtime.db_path`; append `.telegram.json` to it.
* No token — `INFO __main__: telegram is configured in <path> but PKB_TELEGRAM_TOKEN is unset;
  the bot stays off`. Usually a `.env` in a different working directory, or an empty
  `PKB_TELEGRAM_TOKEN=` already exported in the shell: remember that a real environment variable
  beats the file.
* An empty `"chats": {}`.

A path you typed yourself is treated differently: `--telegram-config` naming no file is an error
(`--telegram-config names no file: …`), because a typo is not "not deployed".

---

**`"state": "running"` but nothing works / `restarts` climbing with `last_poll_ok_at: null`.**

A wrong, revoked or truncated token. `last_error` is unhelpfully generic — `"ExceptionGroup:
unhandled errors in a TaskGroup (1 sub-exception)"` — so read the log, where the cause is plain:

```
INFO httpx: HTTP Request: POST https://api.telegram.org/bot123456789[redacted]/getUpdates "HTTP/1.1 401 Unauthorized"
WARNING pkb.server.app: telegram crashed; restarting
… pkb.server.telegram_api.TelegramError: getUpdates failed: 401 Unauthorized
```

Re-copy the token from BotFather (`/token`). Watch for a trailing newline or a quote pasted into
`.env`.

---

**Startup dies with `the owner allow-list moved out of this file`.**

```
pkb-daemon: error: …/pkb.sqlite.telegram.json: the owner allow-list moved out of this file and into $PKB_TELEGRAM_OWNERS — set it beside the token and delete the "owners" key. It is refused rather than ignored because an allow-list sitting in a file that nothing reads looks exactly like an allow-list that is in force
```

Move the ids into `PKB_TELEGRAM_OWNERS` in `.env` and delete the `"owners"` key. Two related startup
refusals, both by design:

* a negative chat id — `chat -1001234567890 is a group, supergroup or channel, and only private
  chats are eligible (TG-19)`;
* a non-numeric owner — `$PKB_TELEGRAM_OWNERS: '@yourname' is not a Telegram user id. Expected
  numeric ids separated by commas or spaces`. Usernames are not ids, and silently dropping the entry
  would quietly shrink your allow-list.

## 10. Revoking a token

In [@BotFather](https://t.me/BotFather):

```
/revoke
```

Pick the bot. The old token stops working immediately and you are handed a new one. Put it in `.env`
and restart the daemon; nothing else changes, and your conversations, mapping and pending approvals
are untouched.

**A token that has been in a log file, a screenshot, a pasted terminal transcript, a commit, a bug
report or a chat with an assistant is a token to revoke.** Not "probably fine" — revoke it. It costs
one command and thirty seconds, and the thing it protects is a knowledge base that no version
history and no undo can restore. This daemon redacts the token from its own logs and from `/health`,
but redaction is a backstop against accidents inside this process; it cannot reach anywhere the
token has already been.

Revoking also fixes a leaked `.env`. Rotating the file without revoking does not.
