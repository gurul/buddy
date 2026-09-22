"""PostToolUse hook — fire-and-forget notice that a tool call finished."""

from __future__ import annotations

from ._client import post, read_hook_input


def _tail(response: object, limit: int = 400) -> str:
    """The last `limit` characters of a tool's output, whatever shape Claude Code gave it."""
    text = ""
    if isinstance(response, dict):
        for key in ("stdout", "output", "content", "result", "text"):
            v = response.get(key)
            if isinstance(v, str) and v.strip():
                text = v
                break
        if not text:
            text = " ".join(str(v) for v in response.values() if isinstance(v, str))
    elif isinstance(response, list):
        text = " ".join(str(x.get("text", "")) if isinstance(x, dict) else str(x) for x in response)
    elif isinstance(response, str):
        text = response
    text = text.strip()
    return ("…" + text[-limit:]) if len(text) > limit else text


def main() -> int:
    payload = read_hook_input()
    post({
        "evt": "posttooluse",
        "session_id": payload.get("session_id", ""),
        "tool_use_id": payload.get("tool_use_id", ""),
        "tool_name": payload.get("tool_name", ""),
        # The terminal shows a tool's result; the phone relay (telegram.py, "claude on") shows the same
        # tail. Kept short here so a large result never blocks the hook.
        "result_tail": _tail(payload.get("tool_response")),
    })
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
