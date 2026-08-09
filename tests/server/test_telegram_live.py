"""The facts about Telegram itself that only Telegram can settle (§6.5, §9.7, F-8 … F-11).

Every test in this file opens a socket to ``api.telegram.org``, creates a real topic in a real
private chat and deletes it again. Each one carries ``@pytest.mark.live`` and skips before it
reaches the network unless both ``PKB_TELEGRAM_TEST_TOKEN`` and ``PKB_TELEGRAM_TEST_CHAT`` are set.

**The environment skip is the only guard, and that is deliberate.** ``pyproject.toml`` registers the
``live`` marker with the help text *"deselected by default"*, and its ``addopts`` carries
``-q --strict-markers`` and no ``-m "not live"``. A plain ``pytest`` therefore *collects* this file.
The module-level ``skipif`` below is what keeps the default suite off the network, so it holds even
for a developer who never types ``-m live`` and for CI that runs ``make test`` unadorned. Moving the
guard onto the command line would arm the network for everyone who forgot the flag.

Running them::

    export PKB_TELEGRAM_TEST_TOKEN=123456789:AA-fake-throwaway-bot-token
    export PKB_TELEGRAM_TEST_CHAT=987654321
    uv run pytest -m live tests/server/test_telegram_live.py

§6.5's standing conditions apply and this file honours both. The token belongs to a **throwaway
bot**, and nothing here calls ``start_run`` or touches a knowledge base: a live test that files a
note is a live test writing to a tree with no undo (D6). The bot needs BotFather's **Threaded
Mode** on, which is the toggle F-8 measured; without it ``createForumTopic`` answers
``400 Bad Request: the chat is not a forum`` (F-4) and the three topic tests skip with that
instruction rather than failing a fact the API got right.

**No token, chat id or bot id is written here.** The repo is public (§0), so both secrets arrive
through the environment and the docstrings quote the fake token above and user id ``987654321``.

What this file pins, and why a fake settles none of it:

* **``getMe`` publishes ``has_topics_enabled``** (F-8, TG-75). The startup probe reads one field
  from one call, and the whole topic feature hangs off the answer. Telegram is the only witness
  that the field exists at all.
* **``createForumTopic`` succeeds in a private chat** (F-9, TG-76, TG-78). Bot API 9.4 allowed it
  and this deployment's own bot refused it as recently as the morning of 2026-08-09 (F-4).
* **A send into a live topic reports the topic back** (F-10, TG-80). The comparison TG-80 makes
  needs a truthful success case, otherwise it reads every healthy send as a relocation.
* **A send into a deleted topic raises ``400 message thread not found``** (F-11, TG-83), and does
  **not** answer ``ok: true`` into General (F-2, tdlib/telegram-bot-api#854). One probe settles
  both halves, because a raise and a relocation are mutually exclusive answers to the same call.

The deletion helper below calls ``deleteForumTopic`` over raw ``httpx`` rather than through
:class:`~pkb.server.telegram_api.BotApi`. That is not an oversight: TG-78 and decision AD keep
every topic-mutating method off the Protocol on purpose, because a bot that tidies the human's chat
destroys the only surviving record of what they approved. Cleanup in a test is the one caller that
needs it, so the call lives here where no production path can reach it, and no fake has to grow a
method the adapter must never own.
"""

from __future__ import annotations

import contextlib
import os
from collections.abc import AsyncIterator, Mapping, Sequence
from contextlib import asynccontextmanager
from typing import Any, Final

import httpx
import pytest
import pytest_asyncio

from pkb.server.telegram_api import (
    GENERAL,
    HttpBotApi,
    TelegramError,
    landed_topic_id,
    shield_credentialed_http_logs,
)

TOKEN_ENV: Final = "PKB_TELEGRAM_TEST_TOKEN"
"""The throwaway bot's token. Absent by default, which is what keeps `pytest` off the network."""

CHAT_ENV: Final = "PKB_TELEGRAM_TEST_CHAT"
"""The private chat to probe, as the numeric id the bot sees. A test chat, never a real one."""

DELETED_TOPIC_ENV: Final = "PKB_TELEGRAM_TEST_DELETED_TOPIC"
"""A topic id the **human** deleted from their own client, for the half of F-11 a bot cannot stage.

Optional. The test that reads it skips when it is unset, because staging it takes a human holding a
phone; see that test's docstring for the recipe.
"""

API_BASE: Final = "https://api.telegram.org"

ARMED: Final[Sequence[Sequence[Mapping[str, str]]]] = [
    [{"text": "Approve", "callback_data": "v1|probe|0|approve"}]
]
"""An inline keyboard shaped like TG-57's, so the armed probe of F-11 sends what an approval sends.

The armed case is the one that made HAZARD 1 frightening: under #854's reported behaviour a live
Approve button for an irreversible write lands in General under the wrong expert's name.
"""

MISSING_THREAD: Final = "Bad Request: message thread not found"
"""The exact description F-11 measured four times on 2026-08-09.

Asserted whole rather than by substring on purpose. ``TelegramError.is_missing_thread`` matches a
fragment of this string case-insensitively and says in its own docstring that the match is fragile
because Telegram ships no error code for the case. This file is that fragility's canary: the day
Telegram rewords the description, one live test goes red and names the new wording, instead of the
adapter quietly reclassifying a dead topic as an ordinary 400 and abandoning the repair.
"""

pytestmark = [
    pytest.mark.live,
    pytest.mark.asyncio,
    pytest.mark.skipif(
        not (os.environ.get(TOKEN_ENV) and os.environ.get(CHAT_ENV)),
        reason=f"live Telegram probe: set {TOKEN_ENV} and {CHAT_ENV}",
    ),
]


# --------------------------------------------------------------------------------------
# § Credentials, cleanup and the topic lifecycle
# --------------------------------------------------------------------------------------


@pytest.fixture(scope="session")
def token() -> str:
    """The bot token, straight from the environment and never from a file in this repo."""
    return os.environ[TOKEN_ENV]


@pytest.fixture(scope="session")
def chat_id() -> int:
    """The chat to probe.

    A malformed value fails rather than skips. A skip on a typo reads as "no token configured" and
    hides the run the operator asked for, which is the failure mode this whole file exists to avoid
    one level up.
    """
    raw = os.environ[CHAT_ENV]
    try:
        return int(raw)
    except ValueError:
        pytest.fail(f"{CHAT_ENV} must be a numeric chat id, e.g. 987654321")


@pytest_asyncio.fixture
async def api(token: str) -> AsyncIterator[HttpBotApi]:
    """The shipped client against the real API, entered and closed like the daemon enters it.

    The real :class:`HttpBotApi` rather than raw ``httpx`` for every assertion below, because a
    probe that bypassed it would pin Telegram's behaviour and leave the client's own translation of
    that behaviour unproven. ``_unwrap`` turning ``ok: false`` into a :class:`TelegramError` with
    the right code is half of what F-11 is worth.
    """
    async with HttpBotApi(token=token) as client:
        yield client


async def delete_topic(token: str, chat_id: int, topic_id: int) -> bool:
    """Delete a probe topic through ``deleteForumTopic``, and report whether Telegram agreed.

    Never on :class:`~pkb.server.telegram_api.BotApi` and never in ``pkb.server`` (TG-78, decision
    AD). Test cleanup is the only legitimate caller in the repository, so the raw call sits here.

    Failures are swallowed by the caller on purpose: a topic these tests already deleted answers
    ``400`` on the second attempt, and a cleanup path that raised would replace a real assertion
    failure with a teardown error and hide the finding.

    ``shield_credentialed_http_logs`` runs first because this function hands a bot token to an HTTP
    client, and ``httpx`` logs the whole request line at INFO with the token as a path segment
    (TG-16). :class:`HttpBotApi` installs the same filter in its ``__post_init__``; a helper that
    leans on some other object having been constructed first leaks the credential the day the
    ordering changes.
    """
    shield_credentialed_http_logs()
    async with httpx.AsyncClient(timeout=10.0) as client:
        response = await client.post(
            f"{API_BASE}/bot{token}/deleteForumTopic",
            json={"chat_id": chat_id, "message_thread_id": topic_id},
        )
    payload: Any = response.json()
    return bool(payload.get("ok"))


@asynccontextmanager
async def probe_topic(
    api: HttpBotApi, token: str, chat_id: int, name: str
) -> AsyncIterator[dict[str, Any]]:
    """Create a topic, hand the whole ``ForumTopic`` over, and delete it however the body ends.

    The deletion sits in a ``finally`` and swallows its own errors, so an assertion that fails
    mid-probe still leaves the human's chat as it found it. A probe topic abandoned in a real chat
    is litter the human has to clean up by hand, and a second run adds another one.
    """
    created = dict(await api.create_forum_topic(chat_id, name))
    try:
        yield created
    finally:
        with contextlib.suppress(httpx.HTTPError):
            await delete_topic(token, chat_id, int(created["message_thread_id"]))


async def require_topic_mode(api: HttpBotApi) -> None:
    """Skip, with the instruction, when the bot's BotFather **Threaded Mode** is off (TG-75, F-4).

    A bot without the toggle answers ``400 Bad Request: the chat is not a forum`` on every creation
    attempt. That answer is Telegram behaving correctly, so a red bar would blame the API for a
    deployment setting the human owns. The skip names the toggle, because an operator reading
    "the chat is not a forum" has no path from that sentence to BotFather.

    The presence of ``has_topics_enabled`` stays unconditional and lives in its own test below, so
    a field Telegram removes still turns something red rather than skipping the file into silence.
    """
    me = await api.get_me()
    if not me.get("has_topics_enabled"):
        pytest.skip("the test bot has BotFather Threaded Mode off; turn it on to run topic probes")


# --------------------------------------------------------------------------------------
# § The toggle and the creation call (F-8, F-9 — TG-75, TG-78)
# --------------------------------------------------------------------------------------


async def test_get_me_publishes_the_topic_mode_flag_tg75(api: HttpBotApi) -> None:
    """``getMe`` carries ``has_topics_enabled``, and it is the only call that does (F-8, F-1).

    TG-75 gates the entire topic feature on one field of one startup call. Two ways for that to
    rot, and this test catches both: Telegram drops the field from ``User``, or it moves the field
    to a method the adapter never calls. Either turns the probe into "topics are off" for a
    deployment where topics are on, and every expert silently collapses back into General.

    Asserted as a real boolean rather than for truthiness, so a future string or object answer goes
    red here instead of passing through ``if me.get(...)`` at startup with a meaning nobody chose.

    The value itself is left alone. The toggle belongs to the human and TG-75 promises both
    settings keep working, so a bot with Threaded Mode off must pass this test.
    """
    me = await api.get_me()

    assert isinstance(me.get("has_topics_enabled"), bool), (
        f"getMe no longer publishes has_topics_enabled; TG-75 reads {sorted(me)}"
    )
    assert isinstance(me.get("allows_users_to_create_topics"), bool)


async def test_create_forum_topic_succeeds_in_a_private_chat_tg78(
    api: HttpBotApi, token: str, chat_id: int
) -> None:
    """Bot API 9.4 let a bot open a topic in a **private** chat, and it still does (F-9).

    Measured on 2026-08-09 against this deployment's own bot: one call, ``message_thread_id
    163395``. The same bot refused the same call that morning with ``400 Bad Request: the chat is
    not a forum`` (F-4), so the difference between the two runs is a BotFather toggle and nothing
    in this repository. That gap is the reason the fact needs a live test at all: a fake proves the
    adapter calls the method, and Telegram proves the method answers.

    The returned id is asserted above zero because :data:`GENERAL` is ``0`` and decision Y rests on
    that number staying free forever. Telegram mints a topic id out of the chat's message-id
    sequence, which starts at 1, so a minted ``0`` would collide General with a real channel in the
    directory's unique index and hand one expert another expert's messages.

    The topic is deleted on the way out, including when an assertion fails.
    """
    await require_topic_mode(api)

    async with probe_topic(api, token, chat_id, "pkb live probe: creation") as created:
        topic_id = created["message_thread_id"]

        assert isinstance(topic_id, int)
        assert topic_id > GENERAL, "a minted topic id of 0 would collide with General (decision Y)"
        assert created["name"] == "pkb live probe: creation"


# --------------------------------------------------------------------------------------
# § The send response is a fact about where the message went (F-10 — TG-80)
# --------------------------------------------------------------------------------------


async def test_a_send_into_a_live_topic_reports_that_topic_back_tg80(
    api: HttpBotApi, token: str, chat_id: int
) -> None:
    """A healthy topic send answers with the id it was given (F-10, measured 2026-08-09).

    TG-80 compares the ``message_thread_id`` on the returned ``Message`` against the one sent, and
    treats a difference as a deleted topic. The comparison is worthless without this measurement:
    if Telegram omitted the field on a successful topic send, ``landed_topic_id`` would map the
    absence to :data:`GENERAL` (its documented answer for a General message) and TG-80 would
    declare every healthy channel dead, recreating topics until TG-82's bound retired each expert.

    Driven through :func:`landed_topic_id` rather than through a raw dictionary lookup, so the
    helper the adapter actually calls is the thing under test.
    """
    await require_topic_mode(api)

    async with probe_topic(api, token, chat_id, "pkb live probe: round trip") as created:
        topic_id = int(created["message_thread_id"])

        sent = await api.send_message(chat_id, "pkb live probe: round trip", topic_id=topic_id)

        assert landed_topic_id(sent) == topic_id
        assert sent["message_thread_id"] == topic_id


# --------------------------------------------------------------------------------------
# § A deleted topic refuses the send (F-11, F-2 — TG-83, and the limit of TG-80)
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize("armed", [False, True], ids=["bare", "with-keyboard"])
async def test_a_send_into_a_topic_the_bot_deleted_raises_missing_thread_tg83(
    api: HttpBotApi, token: str, chat_id: int, armed: bool
) -> None:
    """**HAZARD 1 does not reproduce, measured 2026-08-09.** This is that measurement, as a test.

    tdlib/telegram-bot-api#854 reports that a send naming a deleted topic of a **private** chat
    answers ``ok: true``, drops the ``message_thread_id`` and delivers to General. §9 called that
    report *"the single most important constraint in this section"* and built TG-80 and TG-81
    around it. It did not reproduce.

    **Date**: 2026-08-09. **Method**: single JSON-over-HTTPS calls to ``api.telegram.org`` with a
    real bot token, against a private chat with BotFather Threaded Mode on, outside the daemon and
    outside this suite. **Four probes**, differing in two dimensions:

    * **Deleted by the bot**, through ``deleteForumTopic``, unarmed and armed with an
      ``inline_keyboard``. That is the path this test stages, once per ``armed`` value.
    * **Deleted by the human**, from their own Telegram client, unarmed and armed. A bot cannot
      stage a human's tap, so that half lives in the test below and reads a topic id the human
      supplies.

    All four answered ``ok: false``, ``Bad Request: message thread not found``. Nothing landed in
    General and no stray message existed afterwards, which settles the half of F-11 that made the
    hazard frightening: a dead topic refuses an **armed** send too, so the live Approve button
    posted under the wrong expert's name has no live example.

    Both assertions matter and they are different claims. The raise is TG-83's foundation, the path
    that repairs a channel today. The absence of a returned ``Message`` is F-2's negative half: it
    is what demotes TG-80's response comparison from the working mechanism to defence against a
    behaviour nobody here could reproduce. §9.3.3 keeps both rules, so this test guards the
    boundary between them and will announce the day Telegram moves back.

    ``try``/``except`` rather than ``pytest.raises``, so the failure message on a relocation names
    where the message landed and which ``message_id`` is now sitting armed in General.
    """
    await require_topic_mode(api)

    async with probe_topic(api, token, chat_id, "pkb live probe: deleted topic") as created:
        topic_id = int(created["message_thread_id"])
        assert await delete_topic(token, chat_id, topic_id), "deleteForumTopic refused the probe"

        try:
            stray = await api.send_message(
                chat_id,
                "pkb live probe: deleted topic",
                keyboard=ARMED if armed else None,
                topic_id=topic_id,
            )
        except TelegramError as error:
            assert error.method == "sendMessage"
            assert error.code == 400
            assert error.description == MISSING_THREAD
            assert error.is_missing_thread
        else:
            pytest.fail(
                "tdlib/telegram-bot-api#854 has returned: the send into deleted topic "
                f"{topic_id} answered ok with message_id {stray.get('message_id')} in topic "
                f"{landed_topic_id(stray)}. TG-80 and TG-81 are now the working path, "
                "armed keyboard and all. Re-read §9.3.3 before changing anything."
            )


@pytest.mark.skipif(
    not os.environ.get(DELETED_TOPIC_ENV),
    reason=f"the human-deleted half of F-11: set {DELETED_TOPIC_ENV} to a topic id deleted by hand",
)
async def test_a_send_into_a_topic_the_human_deleted_raises_missing_thread_tg83(
    api: HttpBotApi, chat_id: int
) -> None:
    """The other half of F-11's four probes: the topic the **human** deleted from their client.

    Measured on 2026-08-09 alongside the bot-deleted case, with the same answer both armed and
    bare. Kept as a separate test because a bot cannot stage a human's tap, and folding it into the
    parametrized case above would mean pretending ``deleteForumTopic`` and a human's long-press are
    the same event. They reach Telegram through different clients, and #854's report concerns what
    the *human's* deletion leaves behind.

    Staging it, for whoever runs this next: open the test chat, create a topic through the bot (the
    creation test above does it), note the ``message_thread_id`` the daemon logs, delete the topic
    from the Telegram client by hand, then::

        export PKB_TELEGRAM_TEST_DELETED_TOPIC=<that id>

    The armed variant is left to the bot-deleted case. Two dimensions crossed by hand is four
    manual setups per run, and the keyboard question is answered by the send path rather than by
    whose finger deleted the topic.

    Cleanup is nothing, on purpose. The topic is already gone before this test starts, and this is
    the one probe that creates no topic of its own.
    """
    await require_topic_mode(api)
    topic_id = int(os.environ[DELETED_TOPIC_ENV])

    with pytest.raises(TelegramError) as caught:
        await api.send_message(chat_id, "pkb live probe: human-deleted topic", topic_id=topic_id)

    assert caught.value.code == 400
    assert caught.value.description == MISSING_THREAD
    assert caught.value.is_missing_thread
