"""Exa fallbacks and persisted references."""
import errno
import io
import json
import os
from unittest.mock import Mock, patch

import pytest

from cc_buddy_bridge.learning.search import search_problems
from cc_buddy_bridge.learning.server import LearningApp
from cc_buddy_bridge.learning.store import Store


def response(data):
    return io.BytesIO(json.dumps(data).encode())


def test_missing_key(monkeypatch):
    monkeypatch.delenv("EXA_API_KEY", raising=False)
    with patch("urllib.request.urlopen") as call:
        sources, note = search_problems("Counting", "Kindergarten")
    assert not sources and "EXA_API_KEY" in note
    call.assert_not_called()


@pytest.mark.parametrize("failure", [TimeoutError(), ValueError("bad JSON")])
def test_search_failure(monkeypatch, failure):
    monkeypatch.setenv("EXA_API_KEY", "secret")
    with patch("urllib.request.urlopen", side_effect=failure):
        sources, note = search_problems("Algebra", "Grade 7")
    assert sources == [] and "without search references" in note
    assert "secret" not in note


def test_references_and_privacy(monkeypatch, tmp_path):
    monkeypatch.setenv("EXA_API_KEY", "exa-secret")
    monkeypatch.setenv("OPENAI_API_KEY", "tutor-secret")
    monkeypatch.setenv("CC_BUDDY_LEARNING_PROVIDER", "openai")
    app = LearningApp(tmp_path)
    s = app.dispatch({"action": "create", "topic": "Algebra", "level": "Grade 7"})
    app.dispatch({"action": "save", "work": {"ideas": "private learner work"}})
    ref = {"url": "https://openstax.org/books/algebra", "title": "Algebra", "text": "Equation exercises"}
    reply = {"problem": "Solve 3x = 12.", "feedback": "Try this.", "step": "", "status": "continue"}
    with patch("urllib.request.urlopen", side_effect=[
        response({"results": [ref, {**ref, "url": "javascript:alert(1)"}]}),
        response({"status": "completed", "output": [{"content": [{"type": "output_text", "text": json.dumps(reply)}]}]}),
    ]) as call:
        app.dispatch({"action": "generate"})
    search_req, tutor_req = [c.args[0] for c in call.call_args_list]
    assert "Grade 7" in search_req.data.decode()
    assert "private learner work" not in search_req.data.decode()
    assert "Equation exercises" in tutor_req.data.decode()
    event = Store(tmp_path).get(s["id"])["events"][-1]
    assert event["sources"] == [{"title": "Algebra", "url": ref["url"]}]


def test_demo_never_searches(monkeypatch, tmp_path):
    monkeypatch.setenv("EXA_API_KEY", "secret")
    app = LearningApp(tmp_path, demo=True)
    app.dispatch({"action": "create", "topic": "Counting"})
    with patch("urllib.request.urlopen") as call:
        app.dispatch({"action": "generate"})
    call.assert_not_called()


def test_env_file_supplies_exa_key_to_standalone_launcher(monkeypatch, tmp_path):
    """tools/start_learning.py and `python -m cc_buddy_bridge.learning` go through server.main,
    which loads the env file before the server starts and before any search runs."""
    from cc_buddy_bridge.learning import server

    env_file = tmp_path / "env"
    env_file.write_text("EXA_API_KEY=exa-from-file\n")
    # delenv records the keys as absent, so teardown removes what load_env_file adds.
    monkeypatch.delenv("EXA_API_KEY", raising=False)
    monkeypatch.delenv("CC_BUDDY_ENV_FILE", raising=False)
    seen = {}

    def fake_start(*args, **kwargs):
        seen["key"] = os.environ.get("EXA_API_KEY")
        raise OSError(errno.EACCES, "stop before serving")

    with patch.object(server, "start", side_effect=fake_start):
        with pytest.raises(OSError):
            server.main(["--env-file", str(env_file), "--data-dir", str(tmp_path), "--no-open"])
    assert seen["key"] == "exa-from-file"
    with patch("urllib.request.urlopen", return_value=response({"results": []})) as call:
        search_problems("Fractions", "Grade 4")
    assert call.call_args.args[0].get_header("X-api-key") == "exa-from-file"


def test_daemon_cli_loads_env_before_subcommands(monkeypatch):
    """The launchd daemon and `cc-buddy-bridge learning` both start in cli.main, which loads the env file."""
    from cc_buddy_bridge import cli

    loader = Mock()
    monkeypatch.setattr(cli, "load_env_file", loader)
    with patch("cc_buddy_bridge.learning.server.main", return_value=0) as learning_main:
        assert cli.main(["learning", "--no-open"]) == 0
    loader.assert_called_once()
    learning_main.assert_called_once()
