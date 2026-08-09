# Connect your phone to the knowledge base

You chat with a bot, and one of your experts files whatever you send, in its own conversation. The
daemon owns the run, so an approval it asks for (a delete, a new topic, a conflict resolution)
arrives as a pair of buttons on your phone. Press them hours later, from anywhere. The daemon keeps
running and the turn keeps its state.

**Read this before you plan a layout.** The daemon addresses agents by **channel**. A channel is one
Telegram topic, or the part of the chat outside every topic. Telegram calls that part **General**. A
channel maps to exactly one agent, so "which expert am I talking to?" always has one answer, and on
a phone it is the title above the keyboard.

Telegram gives a person exactly one private chat per bot, and the daemon holds exactly one token.
Turn on **Threaded Mode** in BotFather and that one chat holds a topic per expert, created on
request with `/channels` ([§8](#8-a-channel-per-expert)). The toggle is off by default and the bot
works without it; with it off there is one channel, it is General, and one human has one agent.

**Map General to `librarian` unless you have a reason not to.** General is the only channel whose
title names no expert, so it is the one place the "which expert?" question can go unanswered. The
Librarian is the one agent for which that is harmless, because classifying what you send and routing
it onward is its whole job. The daemon refuses groups and broadcast channels
([§2](#2-find-your-user-id-and-your-chat-id), TG-19). A topic sits *inside* a private chat, so a
channel per expert stays within that rule. A second entry in the mapping is a second **person**,
with their own private chat with the same bot. Add their id to the allow-list too.

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
assistant. See [§11](#11-revoking-a-token).

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
  so a `curl` against a running daemon gives one of you a `409 Conflict` ([§10](#10-troubleshooting)).

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

That is the one-human deployment, and `librarian` is the recommendation.

**This file maps General, and only General.** Every other channel in the chat is a topic you create
from the phone with `/channels`, and the topic id Telegram mints for it lives in the daemon's own
database ([§8](#8-a-channel-per-expert)). There is nothing to hand-edit here. No Telegram client
shows a topic id, so you could not have typed one.

Map General straight to a topic expert if you want. The daemon allows it and says so at startup:

```
WARNING __main__: telegram chat 987654321 has 'topic/cooking' in its General area rather than 'librarian': messages sent outside a topic go to that expert, and General is the one channel whose title does not say so — `/agents` there names it, and `/channels topic/cooking` gives it a topic of its own
```

That mapping works, and `/agents` in General names the agent. With Threaded Mode off, General is the
*only* thing your phone reaches, and `librarian` is the only mapping that reaches a whole tree from
one channel.

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
not a warning. See [§10](#10-troubleshooting).

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
`telegram` block below is from a real run: one chat mapped to `librarian`, against a two-topic
knowledge base, with a token BotFather never issued. That token crash-loops the bot, and it leaves
the three topic fields at their startup defaults. The bot never got as far as `getMe`, so it never
learned whether it has topics:

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
    "topics_enabled": false,
    "channels": 0,
    "retired_channels": [],
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
| `chats` | How many chats your mapping names. It counts **chats**; `channels` below counts the topics. This is configuration and it never changes at runtime. |
| `last_poll_ok_at` | **The reachability field.** The bot stamps it every time `getUpdates` returns. |
| `last_send_error` | The last outbound failure, redacted. The daemon reports it and never turns `degraded` because of it. |
| `invalid_chats` | Mapped chats that name an agent which does not exist. The bot answers those chats as unmapped. |
| `topics_enabled` | BotFather's **Threaded Mode** for this bot, read once from `getMe` at startup. `false` means the deployment runs as it did before topics existed, and `/channels` answers with the BotFather instruction instead of creating anything. |
| `channels` | How many topics you have bound to an expert, across every chat. General is not counted: it is configuration, and `chats` holds it. Zero with `topics_enabled: true` means you have not run `/channels` yet. |
| `retired_channels` | Experts whose topic you deleted more than twice, so the bot stopped making new ones. Their messages arrive in General with the agent id on the first line until you send `/channels <agent-id>` ([§8](#8-a-channel-per-expert)). |
| `unmapped_agents` | Agents with **no** channel pointing at them: no chat's General, no topic. You cannot address them directly from a phone. |

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
the live agent catalog and the agents your mapping names **plus the ones that have a topic**. With
Threaded Mode off, every topic expert is in it, which is correct: the Librarian your chat is mapped
to reaches them by routing rather than by address. With it on, the list is your work queue.
`/channels <agent-id>` takes a name off it, so it shrinks as you go. Read it the other way round if
you mapped General straight to a topic and never turned Threaded Mode on: everything listed is then
unreachable from your phone, and nothing else reports that condition. You create a topic in the
knowledge base, the bot carries on ignoring it, and no line anywhere says why.

Elsewhere in the body, top-level `"status": "degraded"` means an *enabled* subsystem is not
`running`. A failed send or a bad mapping line never causes it.

## 7. Use it

**Send a note.** Type into a mapped channel. The daemon runs one turn on that channel's current
conversation, with the agent that channel talks to. The reply comes back as one or more messages.
The bot splits a long reply on line boundaries and numbers the parts `(1/2)`. It never summarises.

**Three lines typed as three messages is fine.** The bot serializes each channel's messages. The
second waits for the first turn to finish and then runs on its own. You get two replies, in order,
and no refusal. Turns in *different* topics do not wait for each other. A turn on the local fallback
model takes about 284 seconds, and one of those in Cooking would otherwise freeze every other expert
on your phone for five minutes, with nothing on screen saying why.

The bot refuses a message in the one case the lock cannot cover: something **else** started a run on
that same conversation, and the TUI is the common cause. The refusal reads *"Still finishing your
last message — send this again in a moment. It was not sent: …"*, and it quotes your text back, so
re-sending is one long-press away. A second, different refusal (*"There is an approval waiting on
this conversation, so this was not sent"*) re-posts the buttons you have not answered yet.

**The six commands.** There are no others, and every one of them acts on **the channel you typed it
in**.

| | |
|---|---|
| `/new` | Start a fresh conversation here. The old one stays in the thread list. A `/new` in General leaves every topic's conversation alone. |
| `/threads` | The conversations of this channel's expert, most in need of attention first. The listing is read-only, and nothing in it re-points the channel at a conversation. |
| `/agents` | The expert this channel talks to. It also lists every channel in this chat and the experts that have none. |
| `/pending` | Everything waiting on you, across **every** expert. The summary lands here; each set of buttons goes to its own expert's topic when it has one. |
| `/cancel` | Stop the run in **this** channel. It does not touch a run in another topic. |
| `/channels` | A menu of experts, one button each, to give one a channel. Bind and create by name too ([§8](#8-a-channel-per-expert)). |

There is no `/connect` and no `/talk`. This bot has no hidden "current expert" that a message
inherits. A topic title is on screen at the moment you hit send. A mode would not be.

**An approval** arrives as the whole description, then the buttons:

1. First, the complete description of what would be written.
2. Then a second message. It carries any validation failures at the top with their rule ids, **the
   expert this write belongs to**, the tool and the reason (`write_file · delete`), the line
   **"There is no undo for this."** when the reason warrants it, a preview of the description, and
   the buttons. **Approve** and **Reject** get one row each, because on a phone one row is one thumb.
   Every approval names the expert, even inside that expert's own topic. A lock-screen preview
   strips the topic title, and so do a forward and General's scrollback. None of them strips the
   first line. If the Librarian routed the write to another expert, the line carries that
   conversation's id as well. The id tells you the write is landing somewhere other than where you
   are reading.
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
polling only, because a webhook needs a public HTTPS endpoint on a daemon that has no auth);
re-pointing a channel at one of the conversations `/threads` lists (a 36-character id typed on a
phone, and afterwards the channel would carry no sign it had moved); and **push
notification of an approval raised in the TUI** (D3 shares state, not streams). You will notice the
last one. An approval that a desk-started run waits on does not ping your phone. Ask for it with
`/pending`.

## 8. A channel per expert

One chat, one topic per expert, each with its own conversation and its own approvals. All of this is
optional. Everything above works without it, and with the toggle off the bot behaves as it did
before topics existed.

### Turn on Threaded Mode

In [@BotFather](https://t.me/BotFather), open your bot's settings and turn on **Threaded Mode**. The
toggle is off by default and it is per bot. It is the only way to get topics in a one-to-one chat,
and no setting on the daemon substitutes for it.

**Then restart the daemon.** The daemon asks `getMe` once, at startup, and nothing else decides the
answer. Confirm with `/health`: `telegram.topics_enabled` is `true`. Until it is, `/channels` answers

> This bot does not have topics turned on, so there is one channel here and it is this one. Turn on
> Threaded Mode for this bot in BotFather to get a channel per expert.

and creates nothing.

### Give an expert a channel

The bot creates no topic on its own. A knowledge base with thirty topics would otherwise become a
phone chat with thirty conversations, and the four you open would sit behind twenty-six you never
do. No Telegram API lists a chat's topics afterwards either, so a half-finished burst leaves a state
nothing can reconstruct. Ask for them one command at a time:

| | |
|---|---|
| `/channels` | A menu: one button per expert. Tap one to give it a channel here. |
| `/channels topic/cooking` | Give that expert a channel here. |
| `/channels all` | One for every expert that has none. |

`/channels` on its own answers with a keyboard rather than a list, so you never have to type an
agent id on a phone:

> Tap an expert to give it a channel in this chat. A ✓ marks one that has a channel here.
>
> [ ✓ librarian ]
> [ ✓ topic/cooking ]
> [ + topic/cooking/grilling ]

A `+` row has no channel yet and a tap creates one. A `✓` row already has one and a tap answers with
where it is. The buttons stay live, so a menu you scrolled past last month still works, and each
tap re-reads the catalog rather than trusting what the row was drawn with. Tap three rows and you
get three channels.

Past 36 experts the menu says how many it left out and names the two typed forms. Use those for an
expert whose row did not fit.

`/channels <agent-id>` does one of two things, and **the reply always says which**:

> Created a new topic, Cooking, for topic/cooking. Send it anything from there.

> Bound this topic to topic/cooking. Nothing was created — anything you send here from now on goes
> to that expert.

It binds when you type it **inside a topic that has no expert yet**: one you made by hand, or one
left over from a database you had to restore. That is the only way back after you lose the daemon's
SQLite file, because nothing can enumerate the topics already on your phone. Everywhere else it
creates. An expert that already has a channel here gets neither:

> topic/cooking already has a channel in this chat (topic 101), so I created nothing.

Two channels for one expert in one chat would split that expert's history in half, and nothing on
screen would say which half you were writing to.

The bot titles the topic with the expert's own title from the knowledge base. **Rename it, move it
or mute it.** The binding is by id, so none of that changes anything. The bot never renames, closes,
reopens or deletes a topic. The topic is your record of what you approved there.

Type in a topic you made by hand and the bot answers with the offer and the ids you can use:

> This topic is not connected to an expert yet, so I have not kept this message and nothing has been
> filed.
>
> Send /channels &lt;agent-id&gt; here to make this topic that expert's channel.

Nothing runs in an unbound topic, and the bot stores nothing from it.

### General keeps its job

General is everything outside a topic. The mapping file names its expert
([§4](#4-map-your-chat-to-an-agent)), and General keeps working as before. With `librarian` there:
**type into a topic to talk to that expert, and type into General to have the Librarian work out
where a note goes.** Both file into the same tree. `/agents` in any channel prints who
answers there and lists the rest.

### If you delete a topic

You can delete a topic, and the bot handles it. Telegram gives the bot almost nothing to work with.
**A message sent to a deleted topic is not an error.** Telegram accepts it, ignores the topic and
drops it into General, and no update of any kind announces that a topic is gone. The one piece of
evidence is the send response, which names the topic the message landed in. The bot compares that
against the topic it sent to, on every send.

The bot recovers from a mismatch in this order:

1. **The bot clears the stray message's keyboard first.** An Approve button for an irreversible
   write, sitting in General under no expert's name, is the one thing that must not stay pressable.
2. **It posts a line under that message**, naming the expert whose topic is gone. It leaves the
   stray message's text standing: this chat is the only record of what you were asked, and deleting
   that record at the moment the machinery misfires tells you nothing.

   > The topic for topic/cooking has been deleted, so the message above this one was delivered here
   > instead of there — Telegram accepted it without an error. Its buttons no longer work, so
   > nothing can be approved from it.
   >
   > I am re-sending it where it belongs.
3. **It makes a new topic and re-sends the message into it, whole.** Your conversation moves with
   it, so the reply and the approval you were in the middle of are still there.

**Twice, and then it stops.** Delete the replacement a third time and the bot retires that expert:
its messages arrive in General with its name on the first line, and the bot tells you once.

> The topic for topic/cooking has been deleted more than twice, so I have stopped making new ones.
> Everything from that expert will arrive here, with its name on the first line, until you send
> /channels topic/cooking to give it a channel again.

Two is a deliberate bound. It survives a slip and a second slip, and it does not turn a topic you
delete on purpose into a fight. The count lives in the database, so a daemon restart does not hand
out a fresh pair. `/health` lists retired experts in `retired_channels`, and `/channels <agent-id>`
brings one back with a clean allowance.

**Delete a topic and the knowledge base loses nothing.** The deletion is a Telegram-side act on a
conversation. The notes that expert already filed are files on disk. To remove those, ask the expert
to delete them and approve the delete.

## 9. Change something

**Restart the daemon** after you edit the mapping, add a topic to the knowledge base, rotate the
token or flip Threaded Mode. The daemon reads all of it once, at startup. Conversations, pending
approvals, open button prompts and the channels you created live in SQLite and survive the restart.
On the way back up, the bot re-posts the keyboard for anything still parked, in the channel it came
from, and it tells any channel whose message was lost mid-crash to send it again.

**An upgrade from a version without topics costs you nothing.** The daemon migrates the database in
place on the first start: the conversation you were having becomes General's, every pending approval
and every open button prompt keeps working, and the old rows stay where they are. Nothing appears in
your chat until you turn Threaded Mode on and ask for a channel.

## 10. Troubleshooting

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

**The bot answers, but as the wrong expert.**

`/agents` in that channel prints who answers there. If **General** answers as the wrong expert, the
chat id is on the wrong line of the mapping. Fix the line and restart. On the next message you see:

> This chat now talks to topic/woodworking, so I have started a new conversation here. The previous
> one is still in the thread list of the expert it belonged to.

That announcement is the point. Without it, the chat keeps filing into the previous expert and says
nothing. The daemon does **not** move notes already filed into the wrong topic. There is no undo, so
move them yourself.

If a **topic** answers as the wrong expert, a `/channels` command bound that topic to that expert,
and the mapping file has nothing to do with it. There is no rebind. Delete the topic, or make
another one with `/channels <agent-id>`. The bot never re-points a topic you have been filing into.

---

**"This topic is not connected to an expert yet."**

You typed in a topic that has no expert: one you created by hand, or one whose binding went with a
database you replaced. The reply lists the experts with no channel here. Send
`/channels <agent-id>` **in that topic** and the topic becomes theirs, with nothing created. The
rate limit above applies: one explanation per topic per hour, so silence afterwards is expected.

---

**One expert's messages started arriving in General with its name on the first line.**

You deleted its topic more than twice, so the bot retired the channel instead of making a fourth
([§8](#8-a-channel-per-expert)). The expert is in `/health` under `retired_channels`, and it stays
there across restarts on purpose. A daemon bounce is not a reason to start the fight again.
`/channels <agent-id>` gives it a channel and a clean allowance.

The same name on the first line of a **single** message means the bot sent that one message to a
topic you had deleted a moment before. Read the line under it. Nothing was lost, because the bot
re-sends the message whole into the replacement topic.

---

**`/channels` says the bot does not have topics turned on.**

Threaded Mode is off, or you have not restarted the daemon since you turned it on. The bot reads the
flag once, from `getMe`, at startup. Check `/health`: `telegram.topics_enabled`. Everything else
keeps working meanwhile. With the toggle off, the chat has exactly one channel and it is General.

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

## 11. Revoking a token

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
