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
