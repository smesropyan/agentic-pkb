"""A menu of channels: the picker keyboard and the press behind it (TG-96 … TG-101).

``_binding_offer`` states the gap this file closes in its own docstring: without a listing, the
human is told to type ``/channels <agent-id>`` *"with no way to learn an agent id from a phone,
which is an instruction that cannot be followed"*. §9 printed the ids. §10 makes each one a button.

Everything runs against the **real** :class:`~pkb.server.telegram.TelegramAdapter`, the real
:class:`~pkb.service.telegram.SqliteTelegramStore` over ``tmp_path``, and the §9 fake transport with
one addition — :class:`PickyBotApi` refuses 65 bytes of ``callback_data`` the way the live API does,
so TG-97 fails here rather than under a thumb.

Three properties decide what is worth asserting, and each one is a different way to hand a human a
channel they did not ask for:

* **A payload carries an identity, never a position** (TG-97). A Telegram message keeps its buttons
  live forever while the catalog moves under it, so row 7 of a month-old keyboard is a different
  expert.
* **A press re-reads the world** (TG-98). The mark on a row is a claim about a moment that has
  passed, and the press runs the same ``/channels <agent-id>`` code the typed command runs, so
  bind-or-create cannot be answered two ways.
* **A catalog past the bound is counted, never dropped** (TG-99). A human whose expert is missing
  from the menu cannot tell that from an expert that does not exist.
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import aiosqlite
import pytest
import pytest_asyncio

from pkb.contracts import AgentDescriptor
from pkb.core.paths import MAX_SLUG_LENGTH
from pkb.server import telegram as adapter_module
from pkb.server.telegram import (
    PICKER_BUDGET,
    PICKER_LABEL_UNITS,
    PICKER_MESSAGES,
    PICKER_PREFIX,
    PICKER_ROWS,
    Channel,
    callback_data,
    parse_callback,
    parse_picker,
    picker_callback,
    resolve_picker,
    utf16_len,
)
from pkb.server.telegram_api import CALLBACK_DATA_LIMIT, GENERAL, TelegramError
from pkb.service.telegram import SqliteTelegramStore
from tests.server.stub import COOKING, GRILLING, LIBRARIAN
from tests.server.test_telegram_topics import (
    CHAT,
    OMITTED,
    OWNER,
    FakeBotApi,
    Journal,
    TopicService,
    channel_for,
    deliver,
    drain,
    say,
    topical,
)

STRANGER = 111000111
DEEP_SLUG = "a" * MAX_SLUG_LENGTH
"""One legal slug at ``pkb.core.paths.MAX_SLUG_LENGTH``. ``topic/`` plus this is 86 bytes."""


class PickyBotApi(FakeBotApi):
    """The §9 fake plus the one refusal the live API makes: 65 bytes of ``callback_data``.

    Measured against the real API (P-28): 64 bytes is accepted and 65 answers
    ``BUTTON_DATA_INVALID``. Neither PTB nor aiogram checks it at construction, so a build that
    emitted an over-budget row would learn about it from a 400 at the moment a human tapped. The
    fake carries the refusal so the suite learns about it instead.
    """

    async def send_message(
        self,
        chat_id: int,
        text: str,
        *,
        keyboard: Sequence[Sequence[Mapping[str, str]]] | None = None,
        topic_id: int = OMITTED,
    ) -> Mapping[str, Any]:
        for row in keyboard or ():
            for button in row:
                if len(str(button.get("callback_data", "")).encode()) > CALLBACK_DATA_LIMIT:
                    raise TelegramError("sendMessage", 400, "Bad Request: BUTTON_DATA_INVALID")
        return await super().send_message(chat_id, text, keyboard=keyboard, topic_id=topic_id)


# --------------------------------------------------------------------------------------
# Fixtures
# --------------------------------------------------------------------------------------


@pytest_asyncio.fixture
async def connection(tmp_path: Path) -> Any:
    """The daemon's own connection settings: autocommit and WAL (ST-1, ST-2, AP-4)."""
    handle = await aiosqlite.connect(tmp_path / "pkb.sqlite", isolation_level=None)
    try:
        await handle.execute("PRAGMA journal_mode=WAL")
        yield handle
    finally:
        await handle.close()


@pytest_asyncio.fixture
async def store(connection: aiosqlite.Connection) -> SqliteTelegramStore:
    telegram_store = SqliteTelegramStore(connection)
    await telegram_store.setup()
    return telegram_store


@pytest.fixture
def journal() -> Journal:
    return []


@pytest.fixture
def api(journal: Journal) -> PickyBotApi:
    return PickyBotApi(journal)


@pytest.fixture
def service(journal: Journal) -> TopicService:
    return TopicService(journal)


# --------------------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------------------


def descriptor(agent_id: str, title: str | None = None) -> AgentDescriptor:
    return AgentDescriptor(
        agent_id=agent_id,
        title=title or agent_id.rsplit("/", 1)[-1].title(),
        description="",
        has_custom_expert=False,
        model_id="ollama:deepseek-v4-flash:cloud",
    )


def catalog_of(size: int) -> list[AgentDescriptor]:
    """``size`` agents, ordered the way no client-side sort would leave them.

    The Librarian leads because it is the agent this chat's General is mapped to, and a General
    whose agent left the catalog is answered by TG-79 before any command runs.
    """
    return [descriptor(LIBRARIAN, "Librarian")] + [
        descriptor(f"topic/subject-{index:02d}") for index in reversed(range(size - 1))
    ]


def press(
    data: str,
    *,
    update_id: int = 90,
    sender: int = OWNER,
    chat_id: int = CHAT,
    topic_id: int = GENERAL,
    query_id: str = "cbq-picker",
) -> dict[str, Any]:
    """One ``callback_query``, carrying the message it was attached to (F-1)."""
    message: dict[str, Any] = {"message_id": 5, "chat": {"id": chat_id, "type": "private"}}
    if topic_id != GENERAL:
        message["message_thread_id"] = topic_id
    return {
        "update_id": update_id,
        "callback_query": {
            "id": query_id,
            "from": {"id": sender},
            "data": data,
            "message": message,
        },
    }


def keyboards(api: FakeBotApi) -> list[list[list[Mapping[str, str]]]]:
    """Every keyboard that went out, in order, skipping the plain messages."""
    return [entry["kb"] for entry in api.sent if entry["kb"]]


def rows(api: FakeBotApi) -> list[Mapping[str, str]]:
    """Every button of every keyboard, flattened — one button per row by construction."""
    return [button for keyboard in keyboards(api) for row in keyboard for button in row]


def payloads(api: FakeBotApi) -> list[str]:
    return [str(button["callback_data"]) for button in rows(api)]


def labels(api: FakeBotApi) -> list[str]:
    return [str(button["text"]) for button in rows(api)]


# --------------------------------------------------------------------------------------
# § the keyboard: one row per agent, in the catalog's order (TG-96)
# --------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_picker_draws_one_row_per_agent_in_catalog_order_tg96(
    service: TopicService, store: SqliteTelegramStore, api: PickyBotApi
) -> None:
    """The catalog's order is the order the TUI sidebar and ``/agents`` show.

    A phone that re-ranks makes the human's two views of one knowledge base disagree about where an
    expert sits, and TG-40 already ruled it for ``/threads``: re-sorting buries the row the human
    came back for. The catalog here is deliberately out of alphabetical order, so a client-side
    sort fails rather than passing by coincidence.
    """
    service.catalog = [descriptor(GRILLING), descriptor(LIBRARIAN), descriptor(COOKING)]
    bot = await topical(service, store, api)

    await say(bot, "/channels")

    assert payloads(api) == [
        f"{PICKER_PREFIX}|{GRILLING}",
        f"{PICKER_PREFIX}|{LIBRARIAN}",
        f"{PICKER_PREFIX}|{COOKING}",
    ]


@pytest.mark.asyncio
async def test_every_row_of_the_keyboard_holds_exactly_one_button_tg96(
    service: TopicService, store: SqliteTelegramStore, api: PickyBotApi
) -> None:
    """One button per row, so a thumb travelling down the list cannot land on a neighbour.

    Two buttons on a row also halve the label budget, and the tail of an agent id is what tells one
    row from the next. The flattening every other assertion in this file does is only honest while
    this holds.
    """
    bot = await topical(service, store, api)

    await say(bot, "/channels")

    assert [len(row) for row in keyboards(api)[0]] == [1, 1, 1]


@pytest.mark.asyncio
async def test_a_row_is_marked_by_whether_that_agent_has_a_channel_here_tg96(
    service: TopicService, store: SqliteTelegramStore, api: PickyBotApi
) -> None:
    """The mark is the whole point of drawing the roster rather than listing it.

    Without it the human taps an expert that already has a channel, gets a pointer instead of a
    channel, and has no way to tell which rows are worth tapping. General is folded into the
    directory, so the Librarian counts as bound in a chat whose General is its.
    """
    bot = await topical(service, store, api)
    await channel_for(bot, api, COOKING)

    await say(bot, "/channels", update_id=2)

    assert labels(api) == [f"✓ {LIBRARIAN}", f"✓ {COOKING}", f"+ {GRILLING}"]


@pytest.mark.asyncio
async def test_the_body_carries_no_second_listing_tg96(
    service: TopicService, store: SqliteTelegramStore, api: PickyBotApi
) -> None:
    """The roster is the keyboard. Two orderings on one screen is one that can disagree.

    The body naming the agents as well would also make the message twice as long on the surface
    where the human is holding the phone one-handed.
    """
    bot = await topical(service, store, api)

    await say(bot, "/channels")

    body = api.texts[-1]
    assert [agent for agent in (LIBRARIAN, COOKING, GRILLING) if agent in body] == []
    assert len(body.splitlines()) == 1


@pytest.mark.asyncio
async def test_a_long_agent_id_is_cut_to_the_label_budget_keeping_its_leaf_tg96(
    service: TopicService, store: SqliteTelegramStore, api: PickyBotApi
) -> None:
    """The leaf is what tells one row from the next; the path repeats down the whole keyboard.

    Cut from the head, every row of a deep sub-tree reads ``topic/cooking/tech…`` and the human
    picks by guessing.
    """
    deep = f"topic/cooking/{DEEP_SLUG}"
    service.catalog = [descriptor(LIBRARIAN, "Librarian"), descriptor(deep, title="Deep")]
    bot = await topical(service, store, api)

    await say(bot, "/channels")

    label = labels(api)[1]
    assert utf16_len(label) == PICKER_LABEL_UNITS
    assert label.startswith("+ …")
    assert label.endswith(deep[-10:])


@pytest.mark.asyncio
async def test_an_empty_catalog_gets_one_line_and_no_keyboard_tg96(
    service: TopicService, store: SqliteTelegramStore, api: PickyBotApi
) -> None:
    """An empty keyboard on a phone reads as a delivery that went wrong.

    A sentence reads as an answer, which is what a knowledge base with no topics in it deserves.
    Typed in a bare topic, because a catalog with nothing in it has no agent for General either and
    TG-79 answers that channel before any command runs.
    """
    service.catalog = []
    bot = await topical(service, store, api)

    await say(bot, "/channels", topic_id=444)

    assert [entry["kb"] for entry in api.sent] == [None]
    assert "no agents" in api.texts[-1]


# --------------------------------------------------------------------------------------
# § the payload: an identity, inside 64 bytes (TG-97)
# --------------------------------------------------------------------------------------


def test_every_payload_fits_64_bytes_for_the_deepest_legal_agent_id_tg97() -> None:
    """Measured, not reasoned: 65 bytes answers ``BUTTON_DATA_INVALID`` and 64 is accepted (P-28).

    Agent ids are ASCII by construction (``pkb.core.paths._SLUG_ALPHABET``), so bytes equal
    characters and the arithmetic is exact. The ids built here are the longest this repository can
    produce: five path segments at ``MAX_SLUG_LENGTH`` each.
    """
    ids = [LIBRARIAN] + ["topic/" + "/".join([DEEP_SLUG] * depth) for depth in range(1, 6)]

    for agent_id in ids:
        assert len(picker_callback(agent_id).encode()) <= CALLBACK_DATA_LIMIT

    assert CALLBACK_DATA_LIMIT == 64
    assert PICKER_BUDGET == CALLBACK_DATA_LIMIT - len(f"{PICKER_PREFIX}|".encode()) == 61


def test_topic_plus_one_legal_slug_takes_the_digest_form_tg97() -> None:
    """The fallback is reachable with a topic name a human may type, so it has a test.

    ``topic/`` plus one 80-character slug is an 86-byte id against a 61-byte budget, which would be
    an 89-byte payload against a 64-byte limit. Shipping the inline form alone would 400 on a deep
    sub-topic at the moment the human tapped it.
    """
    agent_id = f"topic/{DEEP_SLUG}"

    assert len(agent_id.encode()) == 86 > PICKER_BUDGET
    assert len(f"{PICKER_PREFIX}|{agent_id}".encode()) == 89
    assert picker_callback(agent_id).startswith(f"{PICKER_PREFIX}|#")
    assert len(picker_callback(agent_id).encode()) == 20


def test_the_shallow_case_rides_inline_and_is_readable_in_a_log_tg97() -> None:
    """A digest for every id would make every press unreadable in a log for no budget gained."""
    assert picker_callback(LIBRARIAN) == f"{PICKER_PREFIX}|{LIBRARIAN}"
    assert picker_callback(GRILLING) == f"{PICKER_PREFIX}|{GRILLING}"
    assert len(GRILLING.encode()) == 22


def test_the_two_callback_grammars_refuse_each_other_tg97() -> None:
    """One press, one handler. A grammar that overlapped would route a tap to the wrong code.

    Two fields against four, and no agent id can hold a ``|``, so neither parser can be fooled by
    the other's payload.
    """
    approval = callback_data("abcd", 0, "a")
    channel = picker_callback(COOKING)

    assert parse_callback(channel) is None
    assert parse_picker(approval) is None
    assert parse_picker(channel) == COOKING
    assert parse_callback(approval) == ("abcd", 0, "a")


@pytest.mark.asyncio
async def test_a_row_keeps_its_payload_when_the_catalog_moves_under_it_tg97(
    service: TopicService, store: SqliteTelegramStore, api: PickyBotApi
) -> None:
    """A positional encoding is the tempting one and it is the dangerous one.

    Index 2 of a month-old keyboard is a different expert once a topic is added above it, and the
    human gets a channel for one they did not choose — TG-1's mis-file arriving through the
    affordance built to prevent it. Here Grilling moves from the last row to the second, and its
    payload does not move with it.
    """
    bot = await topical(service, store, api)
    await say(bot, "/channels")
    first = keyboards(api)[0]
    service.catalog = [
        descriptor(LIBRARIAN, "Librarian"),
        descriptor(GRILLING),
        descriptor(COOKING),
    ]

    await say(bot, "/channels", update_id=2)

    second = keyboards(api)[1]
    grilling = f"{PICKER_PREFIX}|{GRILLING}"
    assert [row[0]["callback_data"] for row in first].index(grilling) == 2
    assert [row[0]["callback_data"] for row in second].index(grilling) == 1


def test_a_digest_matching_two_agents_resolves_to_neither_tg97(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Picking one of two would be a channel for an expert the human did not tap, silently.

    A blake2b-64 collision cannot be constructed in a test, so the digest function is replaced by a
    constant one. The branch is what is under test; the hash is not.
    """
    monkeypatch.setattr(adapter_module, "_picker_digest", lambda agent_id: "collision")

    assert resolve_picker("#collision", [COOKING, GRILLING]) is None
    assert resolve_picker("#collision", [COOKING]) == COOKING
    assert resolve_picker("#nothing-matches", [COOKING]) is None


# --------------------------------------------------------------------------------------
# § the press: re-read the world, then run the typed command (TG-98)
# --------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_press_and_a_typed_command_produce_identical_replies_tg98(
    service: TopicService, store: SqliteTelegramStore, api: PickyBotApi, tmp_path: Path
) -> None:
    """A second implementation of bind-or-create passes every other test in this file.

    The press has to enter the same function, or the two doors answer Q30 differently and the human
    learns which one they used from where their expert is not. Driven side by side over two stores,
    with the replies compared byte for byte.
    """
    other = await aiosqlite.connect(tmp_path / "second.sqlite", isolation_level=None)
    await other.execute("PRAGMA journal_mode=WAL")
    second_store = SqliteTelegramStore(other)
    await second_store.setup()
    try:
        typed_api = PickyBotApi([])
        tapped_api = PickyBotApi([])
        typed = await topical(TopicService([]), store, typed_api)
        tapped = await topical(TopicService([]), second_store, tapped_api)

        await say(typed, f"/channels {COOKING}")
        await deliver(tapped, press(picker_callback(COOKING)))

        assert tapped_api.texts == typed_api.texts
        assert [entry["name"] for entry in tapped_api.creates] == [
            entry["name"] for entry in typed_api.creates
        ]
        assert dict(await second_store.channels(CHAT)) == dict(await store.channels(CHAT))
    finally:
        await other.close()


@pytest.mark.asyncio
async def test_the_press_is_answered_before_the_topic_is_created_tg98(
    service: TopicService, store: SqliteTelegramStore, api: PickyBotApi, journal: Journal
) -> None:
    """A creation takes a Bot API round trip, and the button spins until the query is answered.

    Answered afterwards, the human with no other feedback presses again, and the second press is a
    pointer at a channel the first one made — harmless here and only because TG-77 made it so.
    """
    bot = await topical(service, store, api)

    await deliver(bot, press(picker_callback(COOKING)))

    kinds = [kind for kind, _ in journal]
    assert kinds.index("answer_callback") < kinds.index("create_forum_topic")


@pytest.mark.asyncio
async def test_a_press_binds_an_unbound_topic_and_says_so_tg98(
    service: TopicService, store: SqliteTelegramStore, api: PickyBotApi
) -> None:
    """Bind-here is the only recovery path for a lost SQLite file (F-5), so a press reaches it too.

    A press inside an unbound topic that created a *new* topic beside it would leave the human in a
    channel that still addresses nobody, holding a menu that says it worked.
    """
    bot = await topical(service, store, api)

    await deliver(bot, press(picker_callback(COOKING), topic_id=555))

    assert api.creates == []
    assert "Bound this topic" in api.texts[-1]
    assert dict(await store.channels(CHAT)) == {555: COOKING}


@pytest.mark.asyncio
async def test_a_press_on_an_agent_that_left_the_catalog_creates_nothing_tg98(
    service: TopicService, store: SqliteTelegramStore, api: PickyBotApi
) -> None:
    """The keyboard is a claim about a moment that has passed, and this is the cheapest proof.

    Creating the topic anyway would mint one named after an agent the knowledge base no longer has,
    addressable, bound to nothing, and the human's problem to delete.
    """
    bot = await topical(service, store, api)
    await say(bot, "/channels")
    service.catalog = [entry for entry in service.catalog if entry.agent_id != COOKING]

    await deliver(bot, press(picker_callback(COOKING), update_id=2))

    assert api.creates == []
    assert f"no agent called {COOKING}" in api.texts[-1]


@pytest.mark.asyncio
async def test_a_press_on_an_agent_that_already_has_a_channel_points_at_it_tg98(
    service: TopicService, store: SqliteTelegramStore, api: PickyBotApi
) -> None:
    """Two channels for one agent in one chat is that expert's history split in half (TG-77).

    Reachable through a stale ``+``: the channel was made in another chat, from the TUI, or by the
    human's own earlier press on this same keyboard.
    """
    bot = await topical(service, store, api)
    await say(bot, "/channels")
    topic_id = await channel_for(bot, api, COOKING)
    before = len(api.creates)

    await deliver(bot, press(picker_callback(COOKING), update_id=3))

    assert len(api.creates) == before
    assert f"{COOKING} already has a channel" in api.texts[-1]
    assert str(topic_id) in api.texts[-1]
    assert adapter_module._ALREADY_HERE.format(agent_id=COOKING) in api.to(topic_id)


@pytest.mark.asyncio
async def test_a_press_on_a_row_whose_topic_was_deleted_repairs_it_tg102(
    service: TopicService, store: SqliteTelegramStore, api: PickyBotApi
) -> None:
    """The sequence a human ran on the live bot on 2026-08-09, through the button they pressed.

    They deleted the Cooking topic in their client and tapped Cooking's row. The row's channel is
    still in the directory, so TG-77 refuses to create a second one, which is right; the pointer it
    answers with used to go to General, where it proved nothing. TG-82 and TG-83 recreate a dead
    channel and both are triggered by a **failed send**, so a pointer that never touches the topic
    leaves the expert unreachable from the phone, permanently and in silence.

    Delivered into the channel it names, the pointer raises ``message thread not found`` and the
    repair that was already built runs. The human is told in the channel they were standing in,
    because TG-100 leaves the keyboard live and unmarked and a press answered only somewhere else
    reads as a press that did nothing.
    """
    bot = await topical(service, store, api)
    await say(bot, "/channels")
    cooking = await channel_for(bot, api, COOKING)
    api.missing_thread_errors = True
    api.delete_topic(cooking)

    await deliver(bot, press(picker_callback(COOKING), update_id=7))

    live = int(api.next_topic_id)
    assert live != cooking
    assert await store.channels(CHAT) == {live: COOKING}
    assert api.to(live) == [adapter_module._ALREADY_HERE.format(agent_id=COOKING)]
    assert adapter_module._REOPENED.format(agent_id=COOKING, title="Cooking") in api.to(GENERAL)


@pytest.mark.asyncio
async def test_a_double_tap_on_a_dead_row_recreates_one_topic_tg102(
    service: TopicService, store: SqliteTelegramStore, api: PickyBotApi
) -> None:
    """A thumb landing twice on a row whose topic is gone must produce one topic, not two.

    TG-101's double tap is the same gesture against the create branch, and it produced two
    ``createForumTopic`` calls and one directory row before ``_creations[chat_id]`` existed. The
    repair branch reaches ``createForumTopic`` by a different road, so it gets its own assertion:
    two topics here would spend both of ``MAX_RECREATIONS`` on one deletion and leave a second
    topic carrying the expert's title that nothing addresses and the bot may never delete (TG-78).

    The second press answers from the row the first one repaired, so the human is pointed at the
    live topic rather than told about the dead one.
    """
    bot = await topical(service, store, api)
    cooking = await channel_for(bot, api, COOKING)
    api.missing_thread_errors = True
    api.delete_topic(cooking)
    row = picker_callback(COOKING)

    await asyncio.gather(
        bot._dispatch(press(row, update_id=90, query_id="first")),
        bot._dispatch(press(row, update_id=91, query_id="second")),
    )
    await drain(bot)

    live = int(api.next_topic_id)
    assert [entry["name"] for entry in api.creates] == ["Cooking", "Cooking"], "one create, one fix"
    assert await store.channels(CHAT) == {live: COOKING}
    assert api.to(live) == [adapter_module._ALREADY_HERE.format(agent_id=COOKING)] * 2
    assert api.texts[-1] == adapter_module._ALREADY.format(agent_id=COOKING, where=f"topic {live}")


@pytest.mark.asyncio
async def test_a_second_press_on_the_same_row_creates_nothing_tg98(
    service: TopicService, store: SqliteTelegramStore, api: PickyBotApi
) -> None:
    """The keyboard stays live after a press (TG-100), so the same row is one tap away forever.

    A human who taps twice — the reply scrolled off, the network was slow, the thumb bounced — gets
    one channel and a sentence saying where it is. Two topics for one agent is that expert's history
    split in half, and the second press is the likeliest way to reach that.
    """
    bot = await topical(service, store, api)
    await say(bot, "/channels")
    payload = payloads(api)[1]

    await deliver(bot, press(payload, update_id=2, query_id="cbq-first"))
    await deliver(bot, press(payload, update_id=3, query_id="cbq-second"))

    assert [entry["name"] for entry in api.creates] == ["Cooking"]
    assert len(await store.channels(CHAT)) == 1
    assert f"{COOKING} already has a channel" in api.texts[-1]


@pytest.mark.asyncio
async def test_a_press_after_threaded_mode_was_turned_off_creates_nothing_tg98(
    service: TopicService, store: SqliteTelegramStore, api: PickyBotApi
) -> None:
    """The toggle is the human's and it can go back. A button cannot create a topic without it.

    Re-probed rather than poked: the flag is discovered from ``getMe`` and setting the field by hand
    would test an attribute instead of the path a restart takes.
    """
    bot = await topical(service, store, api)
    await say(bot, "/channels")
    api.has_topics_enabled = False
    await bot._probe_topics()

    await deliver(bot, press(picker_callback(COOKING), update_id=2))

    assert api.creates == []
    assert "Threaded Mode" in api.texts[-1]


@pytest.mark.asyncio
async def test_a_press_from_a_chat_that_left_the_mapping_is_refused_tg98(
    service: TopicService, store: SqliteTelegramStore, api: PickyBotApi, journal: Journal
) -> None:
    """A chat removed from the configuration is a chat this daemon no longer answers in.

    An alert rather than a message, because the chat it would land in is the one the human just
    took out of the mapping.
    """
    bot = await topical(service, store, api)
    bot.config.chats = {}

    await deliver(bot, press(picker_callback(COOKING)))

    assert api.creates == []
    assert api.of("answer_callback") == [{"id": "cbq-picker", "alert": True}]
    assert api.sent == []


@pytest.mark.asyncio
async def test_a_press_from_outside_the_allow_list_is_refused_before_anything_tg98(
    service: TopicService, store: SqliteTelegramStore, api: PickyBotApi
) -> None:
    """One authorization boundary, and a channel button does not get a second, weaker one (TG-95).

    On a phone a silent answer is indistinguishable from a successful one, so the refusal carries an
    alert.
    """
    bot = await topical(service, store, api)

    await deliver(bot, press(picker_callback(COOKING), sender=STRANGER))

    assert api.creates == []
    assert api.of("answer_callback") == [{"id": "cbq-picker", "alert": True}]


@pytest.mark.asyncio
async def test_a_press_carrying_a_digest_no_agent_answers_to_is_refused_tg98(
    service: TopicService, store: SqliteTelegramStore, api: PickyBotApi
) -> None:
    """A digest names an agent that has left, and there is no id in the payload to name back.

    Guessing at the nearest catalog entry is the one answer that must not be given: it is a channel
    for an expert the human did not tap, with nothing on the screen saying so.
    """
    bot = await topical(service, store, api)

    await deliver(bot, press(f"{PICKER_PREFIX}|#deadbeefdeadbeef"))

    assert api.creates == []
    assert api.of("answer_callback") == [{"id": "cbq-picker", "alert": True}]


# --------------------------------------------------------------------------------------
# § past one keyboard: more keyboards, and a count (TG-99)
# --------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_catalog_of_thirty_draws_numbered_keyboards_with_every_agent_once_tg99(
    service: TopicService, store: SqliteTelegramStore, api: PickyBotApi
) -> None:
    """Thirty experts do not fit one keyboard, and the human still has to reach all thirty."""
    service.catalog = catalog_of(30)
    bot = await topical(service, store, api)

    await say(bot, "/channels")

    assert len(keyboards(api)) == 3
    assert [len(keyboard) for keyboard in keyboards(api)] == [PICKER_ROWS, PICKER_ROWS, 6]
    assert [text.splitlines()[0] for text in api.texts] == ["(1/3)", "(2/3)", "(3/3)"]
    assert sorted(payloads(api)) == sorted(
        picker_callback(entry.agent_id) for entry in service.catalog
    )


@pytest.mark.asyncio
async def test_a_catalog_past_the_bound_states_the_count_it_did_not_draw_tg99(
    service: TopicService, store: SqliteTelegramStore, api: PickyBotApi
) -> None:
    """Silent truncation leaves a human unable to tell a missing expert from a missing topic.

    Asserted as arithmetic rather than as a substring: a test that matched the sentence survives the
    day somebody changes the bound, and the sum is what actually has to hold.
    """
    service.catalog = catalog_of(50)
    bot = await topical(service, store, api)

    await say(bot, "/channels")

    drawn = len(rows(api))
    undrawn = 50 - PICKER_ROWS * PICKER_MESSAGES
    assert drawn == PICKER_ROWS * PICKER_MESSAGES
    assert drawn + undrawn == 50
    assert f"{undrawn} more agent(s)" in api.texts[-1]
    assert "/channels <agent-id>" in api.texts[-1]
    assert "/channels all" in api.texts[-1]


@pytest.mark.asyncio
async def test_a_catalog_inside_the_bound_says_nothing_about_a_remainder_tg99(
    service: TopicService, store: SqliteTelegramStore, api: PickyBotApi
) -> None:
    """A count of zero printed as a sentence is a message about nothing, on a phone."""
    service.catalog = catalog_of(PICKER_ROWS * PICKER_MESSAGES)
    bot = await topical(service, store, api)

    await say(bot, "/channels")

    assert "more agent(s)" not in api.transcript


@pytest.mark.asyncio
async def test_a_press_on_the_last_keyboard_works_like_one_on_the_first_tg99(
    service: TopicService, store: SqliteTelegramStore, api: PickyBotApi
) -> None:
    """Every row holds its own identity, so a later message is not a later *page* of anything."""
    service.catalog = catalog_of(30)
    bot = await topical(service, store, api)
    await say(bot, "/channels")
    last = parse_picker(payloads(api)[-1]) or ""

    await deliver(bot, press(payloads(api)[-1], update_id=2))

    assert [entry["name"] for entry in api.creates] == [descriptor(last).title]
    assert set((await store.channels(CHAT)).values()) == {last}


@pytest.mark.asyncio
async def test_no_payload_carries_a_page_number_or_a_cursor_tg99(
    service: TopicService, store: SqliteTelegramStore, api: PickyBotApi
) -> None:
    """A cursor puts a position in a payload whose keyboard outlives the catalog it was drawn from.

    Page 2 after a rename then skips an agent, which is the failure reached through the fix for it.
    """
    service.catalog = catalog_of(30)
    bot = await topical(service, store, api)

    await say(bot, "/channels")

    assert payloads(api) == [picker_callback(entry.agent_id) for entry in service.catalog]


# --------------------------------------------------------------------------------------
# § after the press: the keyboard is left alone (TG-100)
# --------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_press_leaves_the_picker_keyboard_and_its_text_alone_tg100(
    service: TopicService, store: SqliteTelegramStore, api: PickyBotApi
) -> None:
    """TG-63 disarms an approval because its button commits an irreversible write. Neither half
    holds here: a second press is a pointer (TG-77) and the bot deletes nothing (TG-78).

    Clearing would also make a human who wants three channels retype ``/channels`` between each one,
    which is the typing this whole section removes.
    """
    bot = await topical(service, store, api)
    await say(bot, "/channels")
    drawn = keyboards(api)[0]

    await deliver(bot, press(picker_callback(COOKING), update_id=2))

    assert api.of("clear_keyboard") == []
    assert api.of("edit_message") == []
    assert keyboards(api)[0] == drawn


@pytest.mark.asyncio
async def test_three_presses_on_one_keyboard_open_three_channels_tg100(
    service: TopicService, store: SqliteTelegramStore, api: PickyBotApi
) -> None:
    """One menu, three experts, no retyping. A cleared keyboard costs a command per channel."""
    service.catalog = catalog_of(4)
    bot = await topical(service, store, api)
    await say(bot, "/channels")
    unbound = [payload for payload in payloads(api) if parse_picker(payload) != LIBRARIAN]

    for index, payload in enumerate(unbound):
        await deliver(bot, press(payload, update_id=10 + index, query_id=f"cbq-{index}"))

    assert len(api.creates) == 3
    assert len(await store.channels(CHAT)) == 3


# --------------------------------------------------------------------------------------
# § one press, one channel (TG-101)
# --------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_keyboard_draws_no_create_everything_button_tg101(
    service: TopicService, store: SqliteTelegramStore, api: PickyBotApi
) -> None:
    """Decision AA's three arguments against a burst of creations survive being moved to a thumb.

    Thirty channels bury the four the human opens, no single action undoes them (TG-78), and a
    partial failure leaves a chat whose state nothing can reconstruct — no API enumerates a chat's
    topics (F-5).
    """
    bot = await topical(service, store, api)

    await say(bot, "/channels")

    assert [parse_picker(payload) for payload in payloads(api)] == [LIBRARIAN, COOKING, GRILLING]
    assert "all" not in [parse_picker(payload) for payload in payloads(api)]


@pytest.mark.asyncio
async def test_a_fabricated_all_payload_creates_nothing_tg101(
    service: TopicService, store: SqliteTelegramStore, api: PickyBotApi
) -> None:
    """The ``all`` branch is unreachable from a button **by construction**, not by omission.

    A payload is 64 bytes of text anyone holding the chat can replay, and ``/channels all`` typed by
    a human is a different act from a thumb landing on a scrolling list.
    """
    bot = await topical(service, store, api)

    await deliver(bot, press(f"{PICKER_PREFIX}|all"))

    assert api.creates == []
    assert api.of("answer_callback") == [{"id": "cbq-picker", "alert": True}]


@pytest.mark.asyncio
async def test_a_payload_carrying_two_agents_is_refused_rather_than_iterated_tg101(
    service: TopicService, store: SqliteTelegramStore, api: PickyBotApi
) -> None:
    """One press, one channel. A payload with two fields is neither grammar and runs nothing."""
    bot = await topical(service, store, api)

    await deliver(bot, press(f"{PICKER_PREFIX}|{COOKING}|{GRILLING}"))

    assert api.creates == []
    assert api.of("answer_callback") == [{"id": "cbq-picker", "alert": True}]
    assert await store.channels(CHAT) == {}


@pytest.mark.asyncio
async def test_one_press_issues_at_most_one_create_tg101(
    service: TopicService, store: SqliteTelegramStore, api: PickyBotApi
) -> None:
    """A catalog of three, one tap, one topic."""
    bot = await topical(service, store, api)

    await deliver(bot, press(picker_callback(GRILLING)))

    assert [entry["name"] for entry in api.creates] == ["Grilling"]


@pytest.mark.asyncio
async def test_a_double_tap_on_one_row_creates_one_topic_tg101(
    service: TopicService, store: SqliteTelegramStore, api: PickyBotApi
) -> None:
    """A thumb landing twice is one gesture, and TG-77's "creates nothing" has to survive it.

    Measured against this fake before the lock existed: two ``createForumTopic`` calls and **one**
    directory row, because ``pkb_telegram_channels`` is keyed on ``(chat_id, agent_id)`` and the
    second write replaced the first. That leaves a second topic named *Grilling* in the chat which
    nothing addresses, which the bot may never delete (TG-78) and which no API can enumerate (F-5),
    so the human's only repair is to find it by eye.

    The picker is what makes the race ordinary. Typing ``/channels topic/cooking/grilling`` twice
    inside one poll batch takes a fast pair of hands; tapping a button twice does not.
    """
    bot = await topical(service, store, api)
    row = picker_callback(GRILLING)

    await asyncio.gather(
        bot._dispatch(press(row, update_id=90, query_id="first")),
        bot._dispatch(press(row, update_id=91, query_id="second")),
    )
    await drain(bot)

    assert [entry["name"] for entry in api.creates] == ["Grilling"]
    assert await store.channels(CHAT) == {101: GRILLING}
    assert api.texts[-1] == adapter_module._ALREADY.format(agent_id=GRILLING, where="topic 101")


# --------------------------------------------------------------------------------------
# § the picker writes nothing durable of its own (decision AG)
# --------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_drawing_a_keyboard_writes_no_row_tg97(
    service: TopicService, store: SqliteTelegramStore, api: PickyBotApi, connection: Any
) -> None:
    """A handle would index a copy of state the press re-reads anyway, and add a failure the design
    does not have: a missing row turns a working button into a "could not be located" hand-off.

    An approval needs the row because a live interrupt on a specific thread is 97 bytes (P-28) and
    cannot be re-derived. An agent id is a catalog name and fits.
    """
    bot = await topical(service, store, api)

    await say(bot, "/channels")

    cursor = await connection.execute("SELECT COUNT(*) FROM pkb_telegram_prompts")
    assert (await cursor.fetchone())[0] == 0


def test_the_adapter_holds_the_picker_bounds_as_constants_tg99() -> None:
    """Twelve rows is about a phone screen and three messages is three seconds of a chat's budget.

    Reasoned rather than measured (Q33), and named here so a change to them is a change to a
    constant with a rule behind it.
    """
    assert (PICKER_ROWS, PICKER_MESSAGES, PICKER_LABEL_UNITS) == (12, 3, 48)


def test_the_picker_channel_is_the_pressed_message_channel_tg98() -> None:
    """A press acts where the keyboard sits, so a menu drawn in a topic binds *that* topic."""
    query = press(picker_callback(COOKING), topic_id=808)["callback_query"]

    assert adapter_module._channel_of_query(query) == Channel(CHAT, 808)


# --------------------------------------------------------------------------------------
# § a press that binds also names (TG-105)
# --------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_press_inside_an_unbound_topic_binds_it_and_names_it_tg105(
    service: TopicService, store: SqliteTelegramStore, api: PickyBotApi
) -> None:
    """A press and a typed command run one function (TG-98), and the press is the door the human hit.

    They made the topic in their own client, where Telegram called it *New Chat*, opened the picker
    inside it and tapped the Cooking row. Decision AE prints no agent id on an ordinary reply in a
    channel, on the ground that the topic header names the expert, so a channel that keeps
    Telegram's own name leaves an unnamed expert writing to a tree with no undo. A second
    implementation of bind-or-create would pass every other assertion in this file and disagree with
    the typed form here.
    """
    bot = await topical(service, store, api)

    await deliver(bot, press(picker_callback(COOKING), topic_id=101))

    assert api.of("create_forum_topic") == [], "a press on an unbound topic binds it"
    assert api.of("edit_forum_topic") == [{"chat_id": CHAT, "topic_id": 101, "name": "Cooking"}]
    assert dict(await store.channels(CHAT)) == {101: COOKING}
    assert "named it Cooking" in api.texts[-1]
    # TG-61, for the work this rule added. The rename is a network call the press now waits on, and
    # a query answered after it leaves the button spinning until Telegram expires it, which is the
    # state a human answers by pressing again.
    kinds = [kind for kind, _ in api.journal]
    assert kinds.index("answer_callback") < kinds.index("edit_forum_topic")
