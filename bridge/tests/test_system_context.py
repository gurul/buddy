from datetime import datetime, timezone

import pytest

from cc_buddy_bridge import system_context, telegram, think, voice_agent


@pytest.mark.parametrize(("month", "offset", "hour"), [(1, "-08:00", "04"), (9, "-07:00", "05")])
def test_clock_uses_configured_timezone_and_daylight_saving(month, offset, hour):
    result = system_context.context(
        {"CC_BUDDY_LOCATION": "Example Apartments, Seattle", "CC_BUDDY_TIMEZONE": "America/Los_Angeles"},
        now=datetime(2026, month, 22, 12, 0, tzinfo=timezone.utc))
    assert f"T{hour}:00:00{offset}" in result
    assert "Timezone: America/Los_Angeles" in result
    assert "Owner-configured location: Example Apartments, Seattle" in result
    assert "not live GPS" in result and "snapshot, not a ticking clock" in result


def test_missing_location_and_invalid_timezone_use_local_clock():
    result = system_context.context({"CC_BUDDY_TIMEZONE": "Missing/Zone"})
    assert "Owner-configured location" not in result
    assert "Missing/Zone" not in result
    assert "Local clock when this context was generated:" in result


def test_context_reaches_each_brain_and_refreshes_for_new_requests(monkeypatch):
    monkeypatch.setenv("CC_BUDDY_LOCATION", "Example Building, Portland, Oregon")
    monkeypatch.setenv("CC_BUDDY_TIMEZONE", "America/Los_Angeles")
    session = voice_agent.session_config(voice_agent.configured())
    prompts = [session["instructions"], session["delegation"]["responses"]["instructions"],
               telegram.request(telegram.configured(), [])["instructions"],
               think.request(think.configured(), [])["instructions"]]
    for prompt in prompts:
        assert "Example Building, Portland, Oregon" in prompt
        assert "Timezone: America/Los_Angeles" in prompt
        assert "Local clock when this context was generated:" in prompt
    monkeypatch.setattr(system_context, "context", lambda: "FRESH_CONTEXT")
    assert "FRESH_CONTEXT" in think.request(think.configured(), [])["instructions"]
    assert "FRESH_CONTEXT" in telegram.request(telegram.configured(), [])["instructions"]
    assert "FRESH_CONTEXT" in voice_agent.session_config(voice_agent.configured())["instructions"]
