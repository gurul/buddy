"""consent.py: one fail-closed reading of the owner's yes or no, for every reply that gates an action."""

from __future__ import annotations

import pytest

from cc_buddy_bridge import consent


@pytest.mark.parametrize("reply", ["yes", "Yes.", "y", "yeah", "yep", "ok", "okay", "OK!", "sure", "go", "allow",
                                   "approve", "approved", "do it", "go ahead", "yes please", "ok go ahead",
                                   "sure thing", "confirm", "yes, do it", "  yes  "])
def test_a_clear_yes(reply: str) -> None:
    assert consent.approves(reply) and consent.decision(reply) == "allow"


@pytest.mark.parametrize("reply", [
    "yikes, no",            # the old prefix rule read this as yes: it starts with "y"
    "you know what, no",
    "yeah no",
    "ok wait",
    "sure, but don't send it",
    "do not",
    "yes but only once",
    "ok not now",
    "okay later",
    "",
    "???",
    "maybe",
    "what is it doing?",
])
def test_anything_less_is_not_a_yes(reply: str) -> None:
    assert not consent.approves(reply)


def test_go_away_is_honestly_a_yes_by_this_rule() -> None:
    # The first word is a yes-word and nothing holds it. Pinned so a change here is a decision, not an accident.
    assert consent.approves("go away")


@pytest.mark.parametrize("reply", ["no", "No.", "n", "nope", "nah", "deny", "stop", "don't", "dont do that",
                                   "block it", "cancel", "never"])
def test_a_clear_no(reply: str) -> None:
    assert consent.denies(reply) and consent.decision(reply) == "deny"


@pytest.mark.parametrize("reply", ["maybe", "hmm", "what does it do", "", "yeah no"])
def test_neither_defers(reply: str) -> None:
    assert consent.decision(reply) == ""


def test_curly_apostrophes_are_read() -> None:
    assert consent.denies("don’t") and not consent.approves("ok don’t")
