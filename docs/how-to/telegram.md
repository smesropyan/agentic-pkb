# Connect your phone to the knowledge base

You chat with a bot, and one of your experts files whatever you send, in its own conversation. The
daemon owns the run, so an approval it asks for (a delete, a new topic, a conflict resolution)
arrives as a pair of buttons on your phone. Press them hours later, from anywhere. The daemon keeps
running and the turn keeps its state.

**Read this before you plan a layout.** The daemon addresses agents by chat. A chat maps to exactly
one agent, so "which expert am I talking to?" always has one answer. Telegram gives a person exactly
**one** private chat per bot. The daemon holds exactly **one** bot token, and it refuses groups and
channels ([§2](#2-find-your-user-id-and-your-chat-id), TG-19). One human with one bot therefore has
one chat and **one** agent on their phone. Map it to `librarian` unless you have a reason not to.
The Librarian classifies what you send and routes it to the right expert, and that routing is the
only way one chat reaches a whole tree. A second entry in the mapping is a second **person**, with
their own private chat with the same bot, on their own topic. Add their id to the allow-list too.

Setup takes about ten minutes. Every command, path, variable name, JSON key and log line below comes
from the code or from a real run of it.

**This assumes a daemon you can already start**: a clone, `make install`, a knowledge base directory
and `python -m pkb.daemon`. It does not explain what an approval or a topic expert is. If any of
that is new, read [`getting-started.md`](getting-started.md) first. The bot is one of the channels
that guide describes, and not the main one.

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

That token *is* the bot. Anyone who holds it can read every message you send to the bot, and can
send messages as the bot. Keep it out of your shell history, your screenshots and your chats with an
assistant. See [§10](#10-revoking-a-token).

If you already have a bot, `/token` in BotFather reprints its token.

## 2. Find your user id and your chat id

**For a private chat they are the same number.** The daemon asks for two different-sounding things,
and in a one-to-one chat they are one value.

* your **user id**. It says who may say yes, and it goes in `PKB_TELEGRAM_OWNERS`.
* the **chat id** of your one-to-one chat with the bot. It says which expert that chat talks to, and
  it goes in the mapping file.

Telegram gives a private chat the same id as the user on the other side of it. In the ordinary
one-human deployment the *same* number goes in both places. Check it once rather than assuming it:

**The easy way.** Message [@userinfobot](https://t.me/userinfobot). It replies with your `Id`.

**The way that proves it.** Send your own bot any message ("hello"), then, **before you start the
daemon**, run:

```bash
read -rs PKB_TELEGRAM_TOKEN && export PKB_TELEGRAM_TOKEN   # paste the token; it is not echoed
curl -s "https://api.telegram.org/bot$PKB_TELEGRAM_TOKEN/getUpdates" | python3 -m json.tool
```

Read `result[0].message.from.id` (your user id) and `result[0].message.chat.id` (the chat id). For a
private chat they match, and `chat.type` is `"private"`.

Two cautions on that command:

* Keep the token in the variable. Never type it on the command line, or it lands in your shell
  history. The variable solves *history* and nothing else. The shell expands it before `exec`,
  so for the second or two that `curl` runs, anyone else on the machine can read the token in `ps`.
  The daemon takes the token from the environment and not from a flag for that same reason. On a
  single-user laptop this is fine. On a shared box, do this step somewhere else.
* Do it **before** the daemon starts. Telegram allows exactly one `getUpdates` consumer per token,
  so a `curl` against a running daemon gives one of you a `409 Conflict` ([§9](#9-troubleshooting)).

Group and channel ids are negative. The daemon refuses them at startup, and only private chats are
eligible (TG-19). A group is many senders with no identity check in front of a tree that has no
undo. Telegram's group privacy mode also drops most messages and reports nothing, so a mapped group
would *half* work.

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

`PKB_TELEGRAM_OWNERS` is a comma- or space-separated list of Telegram **user** ids. Chat ids and
usernames do not work there. A one-person deployment has one entry, your own id. To give somebody
else a chat of their own, add their id: `987654321, 987654322` and `987654321 987654322` are both
accepted.

**That list is the only thing standing between a stranger and your knowledge base.** A bot's
username is discoverable, and the bot is a public inbound path into a process that writes files with
no undo and no version history. The chat mapping answers *which expert*. It never answers *who may
say yes*. If your id is not on this list, nothing you send runs. If someone else's id is on it, they
can approve a delete. An empty or unset list refuses everyone, which is the correct default for a
half-finished deployment, and the daemon warns about it at startup:

```
WARNING __main__: telegram has a token and 2 chat(s) but PKB_TELEGRAM_OWNERS names no owners: every message and every button press will be refused until an owner user id is added
```

(`__main__`, not `pkb.daemon`: `python -m pkb.daemon` runs that module *as* `__main__`, so every
line the composition root logs carries that name.)

More about the file, all of it real behaviour:

* `.env`, `.env.*` are gitignored (`!.env.example` is the committed template). Check before you
  commit anyway.
* **A real environment variable always wins over a line in `.env`.** A stale file never overrides a
  systemd `Environment=`, a container secret or a one-off
  `PKB_TELEGRAM_TOKEN=… python -m pkb.daemon`. The daemon logs the *names* it took from the file,
  never the values:
  `INFO __main__: read PKB_TELEGRAM_OWNERS, PKB_TELEGRAM_TOKEN from .env`.
* If you skip the `chmod`, the daemon starts anyway and says so:

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

That is the one-human deployment, and `librarian` is the recommendation. You have one chat with this
bot, so a chat mapped to a topic makes that topic reachable and nothing else. The Librarian instead
classifies each note and routes it to the expert it belongs to. Map your chat to a topic only if you
want a dedicated notebook for one subject and nothing else on your phone.

A second entry is a second person, with their own private chat and their own user id. Put their id
in `PKB_TELEGRAM_OWNERS` as well, or the bot ignores everything they send and answers nothing:

```json
{
  "chats": {
    "987654321": "librarian",
    "987654322": "topic/woodworking"
  }
}
```

Keys are chat ids, written as JSON strings because JSON object keys are strings. Values are agent
ids. An agent id is the topic's path under the knowledge base root, slugified, with the structural
folders elided. `<kb>/Cooking` is `topic/cooking`. A sub-topic lives at
`<kb>/Cooking/sub-topics/Grilling` and never at `<kb>/Cooking/Grilling`, so it is
`topic/cooking/grilling`.

Do not guess them. Start the daemon once with an empty `{"chats": {}}` and read `unmapped_agents`
from `/health` ([§6](#6-read-health)). It lists every agent that no chat maps to, spelled exactly as
this file wants it. Against a two-topic knowledge base with nothing mapped, that is:

```json
"unmapped_agents": ["librarian", "topic/cooking", "topic/woodworking"]
```

(With an empty `chats` the bot reports `"enabled": false`. That is expected: it needs a token *and*
at least one chat. `librarian` is in that list because it is a real, addressable agent, and it is
the one most single-chat deployments should map.)

**This file holds no secret and is safe to commit.** The token and the allow-list are both
environment variables, so that this one names no credential. (The repo's `.gitignore` still excludes
`*.telegram.json` by default, on the assumption that a deployment's chat ids are nobody else's
business; delete that line if you want yours in version control.)

Two chats may map to the same agent, for two people on one topic. The file's shape makes one chat
with two agents impossible. A file that still carries an `"owners"` key is a **startup error** and
not a warning. See [§9](#9-troubleshooting).

The daemon reads the mapping **once, at startup**. Restart the daemon after you edit it.

## 5. Start the daemon

```bash
cd /path/to/agentic-pkb
uv run python -m pkb.daemon /path/to/kb
```

Flags: `--db`, `--host`, `--port` (default `127.0.0.1:8765`), `--telegram-config`, `--env-file`.

A healthy start looks like this. The first two lines are the ones that say Telegram is wired:

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

After that you get a `getUpdates` line about every 25 seconds and nothing else. Note `[redacted]`.
The daemon installs a log filter over `httpx` before the first credentialed request, so the token
does not reach the log even though it lives in the URL path. Treat that as a backstop and not a
licence: a token that has been anywhere it should not be still needs revoking.

Two normal extras on a first start:

* **`WARNING pkb.server.telegram: telegram: cold start — discarding the backlog up to update N`**,
  and one message in each mapped chat saying so. You see this only if Telegram was holding
  something for this bot. A first start with an empty backlog says nothing. Telegram holds
  undelivered updates for up to 24 hours. On a database with no ledger, replaying a day of chat as
  turns into a tree with no undo is worse than losing it, so the bot drops the backlog and asks you
  to re-send.
* Nothing at all about Telegram, if there is no token and no mapping file. The bot is optional, and
  a missing bot never costs you the HTTP API.

For the same knowledge base, run the TUI with `uv run python -m pkb.tui` (`--daemon
http://127.0.0.1:8765`).

## 6. Read `/health`

```bash
curl -s http://127.0.0.1:8765/health | python3 -m json.tool
```

`/health` returns `200` while the process serves, and degradation shows up in the body. The
`telegram` block below is verbatim from a real run: one chat mapped to `librarian`, against a
two-topic knowledge base, with a token BotFather never issued. That bad token is why it is
crash-looping:

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

| Field | What it means |
|---|---|
| `enabled` | A token **and** at least one chat are configured. `false` means the daemon never started the bot. |
| `state` | The supervised **task**'s lifecycle: `disabled`, `starting`, `running`, `restarting`, `stopped`. |
| `restarts` | How many times that task crashed and the supervisor restarted it. Watch whether the number climbs; one sample says little. |
| `last_error`, `last_error_at` | The last crash, redacted. Often too generic to use (see above). The log holds the real cause. |
| `started_at` | When the task first reached `running`. |
| `chats` | How many chats your mapping names. This is configuration and not traffic, and it never changes at runtime. |
| `last_poll_ok_at` | **The reachability field.** The bot stamps it every time `getUpdates` returns. |
| `last_send_error` | The last outbound failure, redacted. The daemon reports it and never turns `degraded` because of it. |
| `invalid_chats` | Mapped chats that name an agent which does not exist. The bot answers those chats as unmapped. |
| `unmapped_agents` | Agents with **no** chat pointing at them. You cannot address them directly from a phone. |

Two fields need more than a table row.

**`running` does not mean Telegram is reachable.** The supervisor stamps `running` *before* it
awaits the first line of the bot's body. A daemon whose token was revoked five minutes ago reports
`state: "running"` for the whole of its next poll while Telegram answers every request with `401`.
The state returns to `running` after each restart too, so a `/health` sampled at the wrong moment
says `running` however wrong the token is. **`last_poll_ok_at` is the only field that reports
connectivity.** `null` after the first minute means no poll has ever succeeded. A timestamp that
goes stale means polling stopped. (Verified against a real daemon with a made-up token: `state`
alternates between `running` and `restarting`, `restarts` reached 5 in the first twenty seconds, and
`last_poll_ok_at` stays `null` throughout. The retry backoff doubles from 1 s to a ceiling of 60 s.)

**The daemon computes `unmapped_agents` on every `/health` request**, as the set difference between
the live agent catalog and the agent ids your mapping names. On a one-chat deployment every topic is
in it, which is correct: the Librarian your chat *is* mapped to reaches them by routing rather than
by address. Read it the other way round if you mapped your chat straight to a topic. Everything
listed there is then unreachable from your phone, and nothing else reports that condition. You
create a topic, the bot carries on ignoring it, and no line anywhere says why.

Elsewhere in the body, top-level `"status": "degraded"` means an *enabled* subsystem is not
`running`. A failed send or a bad mapping line never causes it.

## 7. Use it

**Send a note.** Type into the mapped chat. The daemon runs one turn on that chat's current
conversation, with the agent the chat is mapped to. The reply comes back as one or more messages.
The bot splits a long reply on line boundaries and numbers the parts `(1/2)`. It never summarises.

**Three lines typed as three messages is fine.** The bot serializes a chat's messages. The second
waits for the first turn to finish and then runs on its own. You get two replies, in order, and no
refusal.

The bot refuses a message in the one case the lock cannot cover: something **else** started a run on
that same conversation, and the TUI is the common cause. The refusal reads *"Still finishing your
last message — send this again in a moment. It was not sent: …"*, and it quotes your text back, so
re-sending is one long-press away. A second, different refusal (*"There is an approval waiting on
this conversation, so this was not sent"*) re-posts the buttons you have not answered yet.

**The five commands.** There are no others.

| | |
|---|---|
| `/new` | Start a fresh conversation here. The old one stays in the thread list. |
| `/threads` | This expert's conversations, most in need of attention first. |
| `/agents` | The expert this chat talks to, and the experts configured across all chats. |
| `/pending` | Everything waiting on you, across **every** expert. It re-posts the buttons. |
| `/cancel` | Stop the run in this chat. |

There is no `/connect`. The mapping file binds a chat to its expert, by design, because a chat that
can re-point itself files a note into the wrong topic.

**An approval** arrives as the whole description, then the buttons:

1. First, the complete description of what would be written.
2. Then a second message. It carries any validation failures at the top with their rule ids, the
   tool and the reason (`write_file · delete`), the line **"There is no undo for this."** when the
   reason warrants it, a preview of the description, and the buttons. **Approve** and **Reject** get
   one row each, because on a phone one row is one thumb.
3. Press one. The bot answers the button, then runs the turn. Three of the reasons ask for a second
   tap before that (below). The outcome arrives as a **new** message (`Answered: 1 approved.`) and
   the buttons go away. The description you decided against keeps its text, because this chat is the
   only surviving record of what you approved.

**A long description arrives as a file** (`write_file-0.diff` or `.txt`, captioned with the tool and
reason). Telegram's message limit is 4,096 UTF-16 units, and a real delete embeds the entire current
file, around 8,000 characters. Truncation would put bullets 60–119 behind an irreversible button, so
the bot uploads the whole text first and the message underneath carries only a preview. If the
upload fails, the approval arrives **with no buttons** and a line telling you to use the TUI. You
must not be asked to approve a description you cannot read.

**Three reasons take two taps**: `delete`, `topic-creation` and `conflict-resolution`. The first
press, **whichever button it is**, brings up a second message, *"There is no undo for this.
Confirm?"*, with **Yes, do it** / **Cancel**. Reject takes the confirmation too, because the
confirmation is about the reason and not about the answer. On a phone two buttons in one row are
neighbouring keys, and a thumb on a moving train is a worse input device than a keyboard. **Cancel**
means "I have not decided". The bot records nothing, and the original buttons still work. They stay
live throughout, because the confirm pair is a new message and not a replacement.

If an approval covers several actions, each action gets its own message, and **the bot submits
nothing until you answer every one**. Answer half of them and the turn stays parked, as it was.

**Not built, on purpose:** attachments (the bot replies that it can only read text and downloads
nothing); editing a proposal from the phone; a typed rejection reason; groups; webhooks (long
polling only, because a webhook needs a public HTTPS endpoint on a daemon that has no auth); and
**push notification of an approval raised in the TUI** (D3 shares state, not streams). You will
notice the last one. An approval that a desk-started run waits on does not ping your phone. Ask for
it with `/pending`.

## 8. Change something

**Restart the daemon** after you edit the mapping, add a topic or rotate the token. The daemon reads
all of it once, at startup. Conversations, pending approvals and open button prompts live in SQLite
and survive the restart. On the way back up, the bot re-posts the keyboard for anything still
parked, and it tells any chat whose message was lost mid-crash to send it again.

## 9. Troubleshooting

Symptom first.

---

**I send a message and nothing happens: no reply, no error, nothing in the log.**

Your user id is not in `PKB_TELEGRAM_OWNERS`. **The silence is deliberate** (TG-20). The bot's
username is discoverable, so a bot that answered strangers would work as a reply amplifier, and it
would tell anyone who probed it that a knowledge base is here. A message from a non-owner gets
*zero* replies on every path.

Button presses differ. They get a visible alert, because on a phone a silent answer looks the same
as a successful one, and a stranger who presses Approve on a delete must not be left believing it
happened:

> This knowledge base does not accept decisions from this account. Nothing was approved, rejected or
> written.

and, in the log:

```
WARNING pkb.server.telegram: telegram: refused a button press from user 12345 in chat 12345 — not in the owner allow-list, which is this deployment's only authentication boundary
```

**Fix:** put your numeric user id in `PKB_TELEGRAM_OWNERS` and restart. Check the startup warning
quoted in [§3](#3-put-both-secrets-in-env). If that line is in the log, the list is empty and the
bot refuses everyone, including you.

---

**"This chat is not connected to anything, so I have not kept this message and nothing has been
filed."**

The chat id is not in the mapping, or it is there and points at an agent that no longer exists. The
message names the chat id you need. Check `/health`:

* `invalid_chats: [987654322]` means that chat's agent id is wrong. The log says which one. The
  daemon reports a typo or a renamed topic and never treats it as fatal, so the other chats keep
  working:

```
ERROR pkb.server.telegram: telegram: chat 987654322 is mapped to agent 'topic/wodworking', which does not exist; that chat will be answered as unmapped until the configuration names a real agent
```

* `unmapped_agents` spells the agent ids that are free, exactly as the file wants them.

Add the chat id, then restart. Note the rate limit: **you get this reply at most once per chat per
hour.** A bot that answered every probe would earn a Telegram rate limit, and that limit would then
delay *your* approval keyboards. Silence after the first reply is expected and not a second bug.

---

**The buttons do nothing / spin forever.**

* *Spinner never resolves, no alert:* the daemon is not running, or it cannot reach Telegram. Check
  `last_poll_ok_at`. Telegram queues the press for up to 24 hours, and the bot resolves it against
  the durable prompt row when it comes back. An approval survives a restart.
* *"Already answered — from another channel, or earlier here."* Someone answered it in the TUI, or
  you did here and the message scrolled. Nothing was sent. This is the expected two-channel case and
  not an error.
* *"That approval could not be located."* The prompt row is gone, and a fresh database is the common
  cause. The approval may still be parked. The TUI lists everything pending and can answer it.
* *"That button no longer matches the approval it belongs to."* You pressed an old message's buttons
  against a changed approval. The bot recorded nothing, and the live approval is still waiting.
* *"This knowledge base does not accept decisions from this account."* See the first entry.

None of these cases writes anything, and the bot retries none of them. A retry would apply your taps
to a *different* write.

---

**The bot answers, but in the wrong topic.**

`/agents` in that chat prints the expert it is bound to. If that is wrong, the chat id is on the
wrong line of the mapping. Fix the line and restart. On the next message you see:

> This chat now talks to topic/woodworking, so I have started a new conversation here. The previous
> one is still in the thread list of the expert it belonged to.

That announcement is the point. Without it, the chat keeps filing into the previous expert and says
nothing. The daemon does **not** move notes already filed into the wrong topic. There is no undo, so
move them yourself.

---

**`409 Conflict`, or `/health` `last_error` mentions another consumer.**

```
ERROR pkb.server.telegram: telegram: Conflict: terminated by other getUpdates request; make sure that only one bot instance is running. another consumer of this bot token is polling — either a second daemon is running against it, or a poller from a previous generation of this task is still alive. Polling is stopped; restarting this one would add a third.
```

Telegram permits exactly one `getUpdates` per token. This is never a transport blip. Another daemon
is running against the same token, or your `curl … /getUpdates` from
[§2](#2-find-your-user-id-and-your-chat-id) is still going. The bot does **not** retry in a tight
loop, and the supervisor does **not** restart the task, because a restart would add a *third*
poller. The bot re-probes every 60 seconds and recovers by itself:

```
WARNING pkb.server.telegram: telegram: polling resumed; the other consumer of this token is gone
```

**Fix:** stop the other consumer. Two knowledge bases need two bots, which means two tokens.

---

**The daemon started, but `/health` says `"enabled": false, "state": "disabled"`.**

The bot needs a token **and** at least one chat. Missing either leaves it off. In order of likelihood:

* No mapping file. With no `--telegram-config`, a missing `<db>.telegram.json` means "not deployed",
  and the daemon says nothing about it, because the bot is optional. Confirm the path. It is the
  database path plus `.telegram.json`, and the default database is `<kb>/../pkb.sqlite`, *outside*
  the knowledge base. `/health` prints `runtime.db_path`; append `.telegram.json` to it.
* No token. The log says `INFO __main__: telegram is configured in <path> but PKB_TELEGRAM_TOKEN is
  unset; the bot stays off`. The common causes are a `.env` in a different working directory, or an
  empty `PKB_TELEGRAM_TOKEN=` already exported in the shell. Remember that a real environment
  variable beats the file.
* An empty `"chats": {}`.

A path you typed yourself gets different treatment. A `--telegram-config` that names no file is an
error (`--telegram-config names no file: …`), because a typo is not "not deployed".

---

**`"state": "running"` but nothing works / `restarts` climbing with `last_poll_ok_at: null`.**

The token is wrong, revoked or truncated. `last_error` is too generic to use: `"ExceptionGroup:
unhandled errors in a TaskGroup (1 sub-exception)"`. Read the log, where the cause is plain:

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

* a negative chat id: `chat -1001234567890 is a group, supergroup or channel, and only private
  chats are eligible (TG-19)`;
* a non-numeric owner: `$PKB_TELEGRAM_OWNERS: '@yourname' is not a Telegram user id. Expected
  numeric ids separated by commas or spaces`. Usernames are not ids, and a daemon that dropped the
  entry would shrink your allow-list without telling you.

## 10. Revoking a token

In [@BotFather](https://t.me/BotFather):

```
/revoke
```

Pick the bot. The old token stops working at once, and BotFather hands you a new one. Put it in
`.env` and restart the daemon. Nothing else changes, and your conversations, mapping and pending
approvals are untouched.

**Revoke a token that has been in a log file, a screenshot, a pasted terminal transcript, a commit,
a bug report or a chat with an assistant.** Revoke it even when it looks fine. It costs one command
and thirty seconds, and the thing it protects is a knowledge base that no version history and no
undo can restore. This daemon redacts the token from its own logs and from `/health`, but redaction
is a backstop against accidents inside this process. It cannot reach anywhere the token has already
been.

Revoking also fixes a leaked `.env`. Rotating the file without revoking does not.
