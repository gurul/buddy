"""Firecrawl practice references: fallbacks, privacy, and persisted references."""
import errno
import io
import json
import os
from unittest.mock import Mock, patch

import pytest

from cc_buddy_bridge import watch
from cc_buddy_bridge.learning.search import search_problems
from cc_buddy_bridge.learning.server import LearningApp
from cc_buddy_bridge.learning.store import Store


def response(data):
    return io.BytesIO(json.dumps(data).encode())


def firecrawl(pages, credits=4):
    """A fake watch.http_request answering Firecrawl's search; records what was sent."""
    sent = []

    def fake(url, *, data=None, headers=None, timeout=0):
        sent.append({"url": url, "body": json.loads(data), "headers": headers})
        return 200, json.dumps({"success": True, "creditsUsed": credits, "data": {"web": pages}})
    fake.sent = sent
    return fake


def test_missing_key(monkeypatch):
    monkeypatch.delenv("FIRECRAWL_API_KEY", raising=False)
    monkeypatch.setattr(watch, "http_request", lambda *a, **k: pytest.fail("no key, no request"))
    sources, note = search_problems("Counting", "Kindergarten")
    assert not sources and "FIRECRAWL_API_KEY" in note


@pytest.mark.parametrize("failure", [watch.FetchError("the site answered HTTP 402", 402), TimeoutError(),
                                     ValueError("bad JSON")])
def test_search_failure(monkeypatch, failure):
    monkeypatch.setenv("FIRECRAWL_API_KEY", "fc-secret")

    def boom(*a, **k):
        raise failure

    monkeypatch.setattr(watch, "http_request", boom)
    sources, note = search_problems("Algebra", "Grade 7")
    assert sources == [] and "without search references" in note
    assert "fc-secret" not in note


def test_references_and_privacy(monkeypatch, tmp_path):
    monkeypatch.setenv("FIRECRAWL_API_KEY", "fc-secret")
    monkeypatch.setenv("OPENAI_API_KEY", "tutor-secret")
    monkeypatch.setenv("CC_BUDDY_LEARNING_PROVIDER", "openai")
    ref = {"url": "https://openstax.org/books/algebra", "title": "Algebra", "markdown": "Equation exercises"}
    search = firecrawl([ref, {**ref, "url": "javascript:alert(1)"}, {**ref, "url": "http://plain.example/x"}])
    monkeypatch.setattr(watch, "http_request", search)
    app = LearningApp(tmp_path)
    s = app.dispatch({"action": "create", "topic": "Algebra", "level": "Grade 7"})
    app.dispatch({"action": "save", "work": {"ideas": "private learner work"}})
    reply = {"problem": "Solve 3x = 12.", "feedback": "Try this.", "step": "", "status": "continue"}
    with patch("urllib.request.urlopen", side_effect=[
        response({"status": "completed", "output": [{"content": [{"type": "output_text", "text": json.dumps(reply)}]}]}),
    ]) as call:
        app.dispatch({"action": "generate"})
    sent = json.dumps(search.sent[0]["body"])
    assert "Grade 7" in sent and "private learner work" not in sent
    assert "Equation exercises" in call.call_args_list[0].args[0].data.decode()
    event = Store(tmp_path).get(s["id"])["events"][-1]
    assert event["sources"] == [{"title": "Algebra", "url": ref["url"]}]       # https only


def test_search_is_subject_neutral(monkeypatch):
    """Any topic at any level: no site allowlist, only topic and level sent, three pages read."""
    monkeypatch.setenv("FIRECRAWL_API_KEY", "fc-secret")
    search = firecrawl([])
    monkeypatch.setattr(watch, "http_request", search)
    sources, note = search_problems("Rust ownership and borrowing", "senior backend engineer, new to Rust")
    body = search.sent[0]["body"]
    assert sources == [] and "no usable references" in note
    assert "includeDomains" not in body and body["limit"] == 3 and body["scrapeOptions"]["formats"] == ["markdown"]
    assert "Rust ownership" in body["query"] and "senior backend engineer" in body["query"]
    assert "Math" not in body["query"]


def test_demo_never_searches(monkeypatch, tmp_path):
    monkeypatch.setenv("FIRECRAWL_API_KEY", "fc-secret")
    monkeypatch.setattr(watch, "http_request", lambda *a, **k: pytest.fail("the demo never searches"))
    app = LearningApp(tmp_path, demo=True)
    app.dispatch({"action": "create", "topic": "Counting"})
    with patch("urllib.request.urlopen") as call:
        app.dispatch({"action": "generate"})
    call.assert_not_called()


def test_env_file_supplies_the_firecrawl_key_to_standalone_launcher(monkeypatch, tmp_path):
    """tools/start_learning.py and `python -m cc_buddy_bridge.learning` go through server.main,
    which loads the env file before the server starts and before any search runs."""
    from cc_buddy_bridge.learning import server

    env_file = tmp_path / "env"
    env_file.write_text("FIRECRAWL_API_KEY=fc-from-file\n")
    # delenv records the keys as absent, so teardown removes what load_env_file adds.
    monkeypatch.delenv("FIRECRAWL_API_KEY", raising=False)
    monkeypatch.delenv("CC_BUDDY_ENV_FILE", raising=False)
    seen = {}

    def fake_start(*args, **kwargs):
        seen["key"] = os.environ.get("FIRECRAWL_API_KEY")
        raise OSError(errno.EACCES, "stop before serving")

    with patch.object(server, "start", side_effect=fake_start):
        with pytest.raises(OSError):
            server.main(["--env-file", str(env_file), "--data-dir", str(tmp_path), "--no-open"])
    assert seen["key"] == "fc-from-file"
    search = firecrawl([])
    monkeypatch.setattr(watch, "http_request", search)
    search_problems("Fractions", "Grade 4")
    assert search.sent[0]["headers"]["Authorization"] == "Bearer fc-from-file"


def test_daemon_cli_loads_env_before_subcommands(monkeypatch):
    """The launchd daemon and `cc-buddy-bridge learning` both start in cli.main, which loads the env file."""
    from cc_buddy_bridge import cli

    loader = Mock()
    monkeypatch.setattr(cli, "load_env_file", loader)
    with patch("cc_buddy_bridge.learning.server.main", return_value=0) as learning_main:
        assert cli.main(["learning", "--no-open"]) == 0
    loader.assert_called_once()
    learning_main.assert_called_once()
