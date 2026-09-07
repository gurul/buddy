"""KeyTapper — the swipe-to-key path.

A swipe on the pet asks the host to press one key. The allowlist is the whole
security story here ("synthesize any keystroke on request" is a much bigger
surface than this feature needs), and the event source is load-bearing: a tap
built from a real HID source is swallowed by Warp's global key handling.

Hold-the-pet push-to-talk used to live in this module and is gone, so there is
no longer any way for this code to leave a key down.
"""

from __future__ import annotations

from cc_buddy_bridge.key_tap import KEY_RETURN, KeyTapper


class FakePoster:
    def __init__(self) -> None:
        self.events: list[tuple[int, bool, int]] = []
        self.sources: list[bool] = []   # True = HID source

    def __call__(self, keycode: int, down: bool, flags: int,
                 hold: bool = True) -> None:
        self.events.append((keycode, down, flags))
        self.sources.append(hold)


def _tapper(trusted: bool = True) -> tuple[KeyTapper, FakePoster]:
    p = FakePoster()
    k = KeyTapper(poster=p, trust_check=(lambda: trusted) if not trusted else None)
    return k, p


def test_tap_enter_presses_and_releases() -> None:
    k, p = _tapper()
    assert k.tap("enter")
    assert p.events == [(KEY_RETURN, True, 0), (KEY_RETURN, False, 0)]


def test_tap_refuses_unknown_key() -> None:
    k, p = _tapper()
    assert not k.tap("rm-rf")
    assert p.events == []


def test_tap_next_prev_map_to_arrows() -> None:
    from cc_buddy_bridge.key_tap import KEY_DOWN_ARROW, KEY_UP_ARROW
    k, p = _tapper()
    assert k.tap("next")
    assert k.tap("prev")
    assert p.events == [
        (KEY_DOWN_ARROW, True, 0), (KEY_DOWN_ARROW, False, 0),
        (KEY_UP_ARROW, True, 0), (KEY_UP_ARROW, False, 0),
    ]


def test_tap_untrusted_posts_nothing() -> None:
    k, p = _tapper(trusted=False)
    assert not k.tap("enter")
    assert p.events == []


def test_no_poster_is_a_clean_noop(monkeypatch) -> None:
    """No Quartz (non-mac host, pyobjc missing): tap reports failure, never raises."""
    import cc_buddy_bridge.key_tap as kt
    monkeypatch.setattr(kt, "_quartz_poster", lambda: None)
    k = KeyTapper()
    assert not k.tap("enter")


def test_enter_defaults_to_main_return() -> None:
    from cc_buddy_bridge.key_tap import _enter_keycode
    assert _enter_keycode() == KEY_RETURN


def test_enter_can_be_switched_to_keypad(monkeypatch) -> None:
    from cc_buddy_bridge.key_tap import KEY_KEYPAD_ENTER, _enter_keycode
    monkeypatch.setenv("CC_BUDDY_ENTER_KEY", "keypad")
    assert _enter_keycode() == KEY_KEYPAD_ENTER


def test_taps_use_the_null_event_source() -> None:
    """Regression: Return built from a real HID source was swallowed by Warp's
    global key handling and never reached the focused app. Taps use NULL."""
    k, p = _tapper()
    k.tap("enter")
    assert p.sources == [False, False]
