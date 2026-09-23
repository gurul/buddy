"""chrome_consent.ConsentBroker: the owner's Telegram yes/no answers Chrome's "Allow remote debugging?"."""

from __future__ import annotations

import asyncio

import pytest

from cc_buddy_bridge.chrome_consent import QUESTION, ConsentBroker


def rig(reply=None, *, showing_after: int = 0, never_shows: bool = False, no_telegram: bool = False,
        ask_raises: bool = False):
    pressed: list[str] = []
    asked: list[str] = []
    polls = {"n": 0}

    async def showing() -> bool:
        polls["n"] += 1
        return not never_shows and polls["n"] > showing_after

    async def pressing(label: str) -> bool:
        pressed.append(label)
        return True

    async def ask(q: str) -> str:
        asked.append(q)
        if ask_raises:
            raise RuntimeError("telegram down")
        if reply is None:
            await asyncio.sleep(10)
        return reply

    broker = ConsentBroker(None if no_telegram else ask, showing=showing, pressing=pressing, appear_secs=0.05,
                           ask_timeout_secs=0.05, poll_secs=0.01)
    return broker, pressed, asked


@pytest.mark.parametrize("reply", ["yes", "Yes please", "ok", "allow", "go ahead"])
def test_a_clear_yes_presses_allow(reply: str) -> None:
    broker, pressed, asked = rig(reply, showing_after=2)
    assert asyncio.run(broker.answer_own_connection()) == "allowed"
    assert pressed == ["Allow"] and asked == [QUESTION]


@pytest.mark.parametrize("reply", ["no", "nope", "yeah no", "ok wait", "maybe", ""])
def test_anything_else_presses_cancel(reply: str) -> None:
    broker, pressed, _ = rig(reply)
    assert asyncio.run(broker.answer_own_connection()) == "declined" and pressed == ["Cancel"]


def test_silence_and_a_broken_telegram_press_cancel() -> None:
    broker, pressed, _ = rig(None)
    assert asyncio.run(broker.answer_own_connection()) == "unanswered" and pressed == ["Cancel"]
    broker, pressed, _ = rig("yes", ask_raises=True)
    assert asyncio.run(broker.answer_own_connection()) == "unanswered" and pressed == ["Cancel"]


def test_no_way_to_ask_presses_cancel_without_asking() -> None:
    broker, pressed, asked = rig("yes", no_telegram=True)
    assert asyncio.run(broker.answer_own_connection()) == "no_way_to_ask" and pressed == ["Cancel"] and asked == []


def test_no_dialog_means_nothing_is_pressed_or_asked() -> None:
    broker, pressed, asked = rig("yes", never_shows=True)
    assert asyncio.run(broker.answer_own_connection()) == "no_dialog" and pressed == [] and asked == []


def test_only_allow_and_cancel_can_be_pressed() -> None:
    from cc_buddy_bridge.chrome_consent import press

    with pytest.raises(ValueError):
        asyncio.run(press("Turn off in settings"))


def _broker(showing_seq, reply="yes", press_ok=True, slow_reply=False):
    told: list[str] = []
    pressed: list[str] = []
    seq = list(showing_seq)

    async def showing() -> bool:
        return seq.pop(0) if len(seq) > 1 else seq[0]

    async def pressing(label: str) -> bool:
        pressed.append(label)
        return press_ok

    async def ask(q: str) -> str:
        if slow_reply:
            await asyncio.sleep(10)
        return reply

    async def tell(text: str) -> None:
        told.append(text)

    b = ConsentBroker(ask, tell_owner=tell, showing=showing, pressing=pressing, appear_secs=1,
                      ask_timeout_secs=2, poll_secs=0.01)
    return b, pressed, told


def test_answered_at_the_mac_withdraws_the_phone_question() -> None:
    from cc_buddy_bridge.chrome_consent import GONE_LINE

    b, pressed, told = _broker([True, True, False], slow_reply=True)     # the dialog vanishes while we wait
    assert asyncio.run(b.answer_own_connection()) == "answered_at_mac"
    assert pressed == [] and told == [GONE_LINE]


def test_a_press_that_fails_while_the_dialog_is_still_up_says_why() -> None:
    from cc_buddy_bridge.chrome_consent import PRESS_FAILED_LINE

    b, pressed, told = _broker([True], reply="yes", press_ok=False)
    assert asyncio.run(b.answer_own_connection()) == "press_failed"
    assert pressed == ["Allow"] and told == [PRESS_FAILED_LINE]


def test_the_phone_wait_ends_before_the_connection_gives_up() -> None:
    from cc_buddy_bridge import browser_lane, chrome_consent

    assert chrome_consent.ASK_TIMEOUT_SECS * 1000 < browser_lane.CONSENT_TIMEOUT_MS


def test_a_waiting_question_is_never_overwritten_by_the_chrome_question() -> None:
    from types import SimpleNamespace

    from cc_buddy_bridge.daemon import Daemon

    asked = []

    async def ask_user(q, chat, title=""):
        asked.append(q)
        return "yes"

    busy = SimpleNamespace(_telegram=SimpleNamespace(_chat_id=1, _ask_user=ask_user, _awaiting_answer=True))
    with pytest.raises(RuntimeError, match="already waiting"):
        asyncio.run(Daemon._ask_owner_on_phone(busy, "Allow?"))
    assert asked == []
