"""The chat window's reply box: {"evt":"chat","text":..} on the daemon's socket is a Telegram text from the owner."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

from cc_buddy_bridge import daemon


class Chat:
    def __init__(self, chat_id=7) -> None:
        self._chat_id = chat_id
        self.heard: list[str] = []

    def hear(self, text: str) -> None:
        self.heard.append(text)


def ask(tg, req):
    return asyncio.run(daemon.IPC_HANDLERS["chat"](SimpleNamespace(_telegram=tg), req))


def test_typed_words_reach_the_chat_as_the_owners() -> None:
    tg = Chat()
    assert ask(tg, {"evt": "chat", "text": "  what's on my calendar?  "}) == {"ok": True}
    assert tg.heard == ["what's on my calendar?"]


def test_long_words_are_cut_to_a_telegram_message() -> None:
    tg = Chat()
    ask(tg, {"text": "x" * 10000})
    assert len(tg.heard[0]) == daemon.CHAT_MAX_CHARS


def test_nothing_is_sent_when_it_cannot_be() -> None:
    tg = Chat()
    assert ask(tg, {"text": "   "})["ok"] is False and tg.heard == []
    assert ask(None, {"text": "hi"})["ok"] is False
    no_owner = Chat(chat_id=None)
    assert ask(no_owner, {"text": "hi"})["ok"] is False and no_owner.heard == []
