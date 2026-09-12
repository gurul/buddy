"""Exa fallbacks and persisted references."""
import io
import json
from unittest.mock import patch

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
