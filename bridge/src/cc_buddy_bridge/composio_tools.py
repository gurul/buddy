"""Composio: the text buddy's "app hands".

Owner's decision, 2026-09-21: Gmail, Calendar, GitHub, Notion, Slack and the rest are reachable by API in
seconds through Composio, where driving the Mac's screen for the same job takes minutes. So the Telegram
brain gets Composio's session tools beside its own, and a question like "any mail from Sam today?" is one
API call, not a screen task.

How it fits:

* One Composio *session* per owner. The session's user id is derived from the Telegram owner ids
  (``telegram-<id>[-<id>...]``), so the connected accounts belong to the owner, never to a chat. The
  session id is persisted to ``~/.config/cc-buddy-bridge/composio.json`` and resumed on restart; a stale or
  foreign id falls back to a fresh session.
* The session exposes Composio's six meta tools (search, schemas, multi-execute, connections, remote bash,
  remote workbench) already shaped as Responses-API function tools, so they slot into ``telegram.TOOLS``.
* ``execute`` is blocking and never raises: the caller runs it on a thread and hands the dict straight back
  to the model. Errors come back as ``{"ok": False, "reason": "<Type>: message"}``.
* Consequence gating is pure and lives here: ``consequential_slugs`` names the slugs in a call that do
  something (send, create, delete...), by the verb in the slug; ``describe_for_confirmation`` writes the
  yes/no line the owner sees before those run. Read-only verbs (get, list, fetch, search...) run at once.

The SDK is imported lazily inside methods: the daemon must start without ``composio`` installed and with the
feature off. The API key is read from the environment by the SDK only; this module never holds, logs or
repr's it. Tool arguments may hold mail text, so logs carry names, counts and exception types only.
"""

from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, Optional

log = logging.getLogger(__name__)

COMPOSIO_DEFAULT: bool = False
DEFAULT_STATE_PATH = Path("~/.config/cc-buddy-bridge/composio.json")
DEFAULT_TIMEOUT_SECS = 60.0

_ON = frozenset({"1", "true", "yes", "on"})

MULTI_EXECUTE = "COMPOSIO_MULTI_EXECUTE_TOOL"
RUNS_CODE = frozenset({"COMPOSIO_REMOTE_BASH_TOOL", "COMPOSIO_REMOTE_WORKBENCH"})

# A slug is TOOLKIT_WORDS. A call only looks when one of its words is a reading verb and none is a writing
# verb: GMAIL_FETCH_EMAILS reads, GOOGLECALENDAR_EVENTS_LIST_ALL_CALENDARS reads (the verb is not always the
# first word), GMAIL_SEND_EMAIL writes, GETTY_UPLOAD writes (a verb-like toolkit name proves nothing). What
# happens to a writing call is the toolkit's POLICY below.
READ_VERBS = frozenset({"GET", "LIST", "FETCH", "SEARCH", "FIND", "READ", "RETRIEVE", "LOOKUP", "CHECK", "COUNT",
                        "DESCRIBE", "QUERY", "VIEW", "DOWNLOAD", "SHOW", "PREVIEW"})
WRITE_VERBS = frozenset({"SEND", "CREATE", "DELETE", "REMOVE", "UPDATE", "EDIT", "MODIFY", "POST", "PUBLISH", "PAY",
                         "MERGE", "TRANSFER", "SUBMIT", "CANCEL", "ARCHIVE", "TRASH", "REPLY", "FORWARD", "INVITE",
                         "SHARE", "ADD", "SET", "MOVE", "UPLOAD", "INSERT", "WRITE", "PATCH", "PUT", "EXECUTE", "RUN",
                         "TRIGGER", "MARK", "LABEL", "ASSIGN", "CLOSE", "REVOKE", "GRANT", "RENAME", "COPY", "IMPORT",
                         "SYNC", "RESET", "CLEAR", "PURGE", "BATCH", "DRAFT", "COMPOSE", "SCHEDULE", "BOOK", "ORDER",
                         "ACCEPT", "DECLINE", "APPROVE", "REJECT", "DISABLE", "ENABLE", "STAR", "UNSTAR", "PIN"})
# Words that are also plain nouns: a write only in verb position (the first word after the toolkit). Live,
# 2026-09-23: GMAIL_GET_LABEL (read the INBOX label, i.e. the unread count) was refused as a write under the
# owner's "gmail: read" policy. Tools that change labels carry a real verb too: ADD_LABEL, MODIFY_…_LABELS.
NOUN_TOO = frozenset({"LABEL", "DRAFT", "ORDER", "RUN", "SCHEDULE"})   # GET_DRAFT, GET_ORDER, GET_A_WORKFLOW_RUN…
READ_ONLY_SLUG = re.compile(r"^[A-Za-z0-9]+_[A-Za-z0-9_]+$")     # the shape; the words decide (is_read_only)

# Per-toolkit policy for a call that WRITES (owner, 2026-09-21: "make gmail read only, allow write for
# calendar"). read: a writing call is refused, never asked; write: it runs without asking; ask: the owner's
# yes/no in the chat first (the default for every other toolkit). Reading calls always run.
# CC_BUDDY_COMPOSIO_POLICY="gmail=read,googlecalendar=write,slack=ask" overrides or extends this.
DEFAULT_TOOLKIT_POLICY: dict[str, str] = {"gmail": "read", "googlecalendar": "write",
                                          "googledrive": "ask"}       # Drive (owner, 2026-09-21): reads run, writes ask
POLICIES = ("read", "write", "ask")

VALUE_CLIP = 60
LINE_CLIP = 300


@dataclass(frozen=True)
class ComposioConfig:
    """What the bridge needs. No key here: the SDK reads ``COMPOSIO_API_KEY`` from the environment itself."""

    enabled: bool
    user_id: str
    state_path: Path = DEFAULT_STATE_PATH
    timeout_secs: float = DEFAULT_TIMEOUT_SECS


def _truthy(value: Optional[str]) -> bool:
    return (value or "").strip().lower() in _ON


def configured(environ: Optional[Mapping[str, str]] = None, owner_ids: frozenset[int] = frozenset()) -> ComposioConfig:
    """Read the switch and the surroundings. On only when the switch, the key and an owner are all there."""
    env = os.environ if environ is None else environ
    asked = _truthy(env.get("CC_BUDDY_COMPOSIO")) if "CC_BUDDY_COMPOSIO" in env else COMPOSIO_DEFAULT
    has_key = bool((env.get("COMPOSIO_API_KEY") or "").strip())
    has_owner = bool(owner_ids)
    enabled = asked and has_key and has_owner
    if asked and not enabled:
        missing = [name for name, ok in (("COMPOSIO_API_KEY", has_key), ("a Telegram owner id", has_owner)) if not ok]
        log.warning("composio: asked for (CC_BUDDY_COMPOSIO) but missing %s; staying off", " and ".join(missing))
    user_id = "telegram-" + "-".join(str(i) for i in sorted(owner_ids))
    state_path = Path(env.get("CC_BUDDY_COMPOSIO_STATE") or DEFAULT_STATE_PATH).expanduser()
    try:
        timeout = float(env.get("CC_BUDDY_COMPOSIO_TIMEOUT_SECS") or DEFAULT_TIMEOUT_SECS)
    except ValueError:
        timeout = DEFAULT_TIMEOUT_SECS
    if timeout <= 0:
        timeout = DEFAULT_TIMEOUT_SECS
    return ComposioConfig(enabled=enabled, user_id=user_id, state_path=state_path, timeout_secs=timeout)


# -- pure: which calls ask first, and how the question reads --
def slug_words(slug: str) -> tuple[str, list[str]]:
    """("gmail", ["FETCH", "EMAILS"]) from GMAIL_FETCH_EMAILS: the toolkit and the words after it."""
    parts = [w for w in (slug or "").strip().upper().split("_") if w]
    if len(parts) < 2:
        return (parts[0].lower() if parts else ""), []
    return parts[0].lower(), parts[1:]


def is_read_only(slug: str) -> bool:
    if not READ_ONLY_SLUG.match(slug or ""):
        return False
    _toolkit, words = slug_words(slug)
    writes = [w for i, w in enumerate(words) if w in WRITE_VERBS and (w not in NOUN_TOO or i == 0)]
    return any(w in READ_VERBS for w in words) and not writes


def toolkit_policy(environ: Optional[Mapping[str, str]] = None) -> dict[str, str]:
    """The per-toolkit policy for writing calls: the defaults, then CC_BUDDY_COMPOSIO_POLICY on top."""
    env = os.environ if environ is None else environ
    policy = dict(DEFAULT_TOOLKIT_POLICY)
    for piece in (env.get("CC_BUDDY_COMPOSIO_POLICY") or "").split(","):
        toolkit, sep, value = piece.strip().lower().partition("=")
        if not sep or not toolkit:
            continue
        if value not in POLICIES:
            log.warning("composio: CC_BUDDY_COMPOSIO_POLICY has %r for %s; one of %s", value, toolkit, POLICIES)
            continue
        policy[toolkit] = value
    return policy


def _multi_execute_tools(args: Mapping[str, Any]) -> list[dict[str, Any]]:
    tools = args.get("tools") if isinstance(args, Mapping) else None
    return [t for t in tools if isinstance(t, Mapping)] if isinstance(tools, list) else []


def consequential_slugs(name: str, args: Mapping[str, Any]) -> list[str]:
    """The slugs in this call that change something, in call order. Empty means the call only looks."""
    if name in RUNS_CODE:
        return [name]
    if name == MULTI_EXECUTE:
        out: list[str] = []
        for tool in _multi_execute_tools(args):
            slug = str(tool.get("tool_slug") or "").strip()
            if slug and not is_read_only(slug):
                out.append(slug)
        return out
    return []


@dataclass(frozen=True)
class Decision:
    action: str                      # run | ask | refuse
    slugs: list[str]                 # the writing slugs that decided it (empty for run)
    why: str = ""                    # one line for the owner or the log


def decide(name: str, args: Mapping[str, Any], policy: Optional[Mapping[str, str]] = None) -> Decision:
    """What happens to this call before it runs. Reading calls run. A writing call follows its toolkit's
    policy; a call that mixes toolkits takes the strictest: refuse beats ask beats write. The remote code
    tools have no toolkit and always ask."""
    slugs = consequential_slugs(name, args)
    if not slugs:
        return Decision("run", [])
    pol = dict(DEFAULT_TOOLKIT_POLICY if policy is None else policy)
    verdicts: list[tuple[str, str]] = []
    for slug in slugs:
        toolkit, _words = slug_words(slug)
        rule = "ask" if slug in RUNS_CODE else pol.get(toolkit, "ask")
        verdicts.append((slug, {"read": "refuse", "write": "run", "ask": "ask"}[rule]))
    refused = [s for s, v in verdicts if v == "refuse"]
    if refused:
        kits = sorted({slug_words(s)[0] for s in refused})
        return Decision("refuse", refused, f"{', '.join(kits)} is read only here; {', '.join(refused)} will not run")
    asked = [s for s, v in verdicts if v == "ask"]
    if asked:
        return Decision("ask", asked)
    return Decision("run", [], "allowed to write: " + ", ".join(slugs))


def _clip(text: str, limit: int) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _flat(value: Any) -> str:
    if isinstance(value, str):
        return value
    if value is None or isinstance(value, (bool, int, float)):
        return json.dumps(value)
    try:
        return json.dumps(value, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        return str(value)


def _pairs(arguments: Any) -> str:
    if not isinstance(arguments, Mapping) or not arguments:
        return ""
    return ", ".join(f"{k}: {_clip(_flat(v), VALUE_CLIP)}" for k, v in arguments.items())


def describe_for_confirmation(name: str, args: Mapping[str, Any]) -> str:
    """One plain line the owner can answer yes or no to. Values clipped, the line clipped, no emoji."""
    parts: list[str] = []
    if name == MULTI_EXECUTE:
        tools = _multi_execute_tools(args)
        asked = [t for t in tools if not is_read_only(str(t.get("tool_slug") or ""))] or tools
        for tool in asked:
            slug = str(tool.get("tool_slug") or "a tool")
            pairs = _pairs(tool.get("arguments"))
            parts.append(f"{slug} with {pairs}" if pairs else slug)
    else:
        pairs = _pairs(args)
        parts.append(f"{name} with {pairs}" if pairs else name)
    line = "Run " + "; and ".join(parts) + "?"
    if len(line) > LINE_CLIP:
        line = _clip(line[:-1], LINE_CLIP - 1) + "?"
    return line


# -- the bridge: one session, resumed across restarts --
def _read_state(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _write_state(path: Path, state: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(dict(state), f, indent=2)
        f.write("\n")
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)


def _as_dict(result: Any) -> dict[str, Any]:
    """A SDK response (pydantic-ish), a mapping or anything else, as a JSON-serialisable dict."""
    dump = getattr(result, "model_dump", None)
    if callable(dump):
        data = dump()
    elif isinstance(result, Mapping):
        data = dict(result)
    else:
        data = {"data": result}
    return json.loads(json.dumps(data, default=str))


class ComposioBridge:
    """The owner's Composio session, wrapped so nothing in here raises into the chat loop."""

    def __init__(self, config: ComposioConfig, client_factory: Optional[Callable[[], Any]] = None) -> None:
        self.config = config
        self._client_factory = client_factory
        self._client: Any = None
        self._session: Any = None
        self._tools: list[dict[str, Any]] = []
        self._requests: dict[str, Any] = {}

    @property
    def started(self) -> bool:
        return self._session is not None

    @property
    def session_id(self) -> str:
        return str(getattr(self._session, "session_id", "") or "") if self._session is not None else ""

    def _make_client(self) -> Any:
        if self._client_factory is not None:
            return self._client_factory()
        from composio import Composio
        from composio_openai import OpenAIResponsesProvider

        return Composio(provider=OpenAIResponsesProvider(strict=True), timeout=int(self.config.timeout_secs))

    def start(self) -> None:
        """Resume the persisted session for this owner, or create one and persist it. Raises only if the
        SDK cannot create a session at all (no key, no network): the caller decides what that means."""
        self._client = self._make_client()
        sessions = self._client.sessions
        state = _read_state(self.config.state_path)
        stored = str(state.get("session_id") or "")
        session = None
        if stored and state.get("user_id") == self.config.user_id:
            try:
                session = sessions.use(stored)
                log.info("composio: resumed the session for %s", self.config.user_id)
            except Exception as e:  # noqa: BLE001 — the type only
                log.warning("composio: stored session unusable (%s); creating a fresh one", type(e).__name__)
        if session is None:
            session = sessions.create(user_id=self.config.user_id)
            _write_state(self.config.state_path, {"user_id": self.config.user_id,
                                                  "session_id": str(getattr(session, "session_id", ""))})
            log.info("composio: created a session for %s", self.config.user_id)
        self._session = session
        self._tools = self._load_tools()

    def _load_tools(self) -> list[dict[str, Any]]:
        raw = self._session.tools()
        out: list[dict[str, Any]] = []
        for tool in raw or []:
            if not isinstance(tool, Mapping):
                tool = _as_dict(tool)
            if tool.get("type") == "function" and tool.get("name"):
                out.append(dict(tool))
        log.info("composio: %d tool%s", len(out), "" if len(out) == 1 else "s")
        return out

    def tools(self) -> list[dict[str, Any]]:
        return list(self._tools)

    @property
    def names(self) -> frozenset[str]:
        return frozenset(str(t["name"]) for t in self._tools)

    def execute(self, name: str, args: Mapping[str, Any]) -> dict[str, Any]:
        """Blocking. Never raises: a failure is a dict the model can read and tell the owner about."""
        if self._session is None:
            return {"ok": False, "reason": "composio is not started"}
        try:
            result = self._session.execute(name, arguments=dict(args or {}))
            out = _as_dict(result)
        except Exception as e:  # noqa: BLE001 — the type only: arguments may hold mail text
            log.warning("composio: %s failed: %s", name, type(e).__name__)
            return {"ok": False, "reason": f"{type(e).__name__}: {_clip(str(e), 200)}"}
        out.setdefault("ok", not out.get("error"))
        return out

    def toolkits(self) -> list[tuple[str, bool]]:
        """(toolkit slug, connected) for the toolkits the session knows. The slug is what connect_link takes."""
        if self._session is None:
            return []
        page = self._session.toolkits()
        out: list[tuple[str, bool]] = []
        for item in getattr(page, "items", None) or []:
            slug = str(getattr(item, "slug", None) or getattr(item, "name", "") or "").lower()
            connection = getattr(item, "connection", None)
            active = bool(getattr(connection, "is_active", False)) if connection is not None else False
            if slug:
                out.append((slug, active))
        return out

    def connect_link(self, toolkit: str) -> str:
        """The Connect Link for an app: the owner opens it, signs in, and the session gains the account."""
        if self._session is None:
            raise RuntimeError("composio is not started")
        req = self._session.authorize(toolkit)
        self._requests[toolkit] = req
        return str(getattr(req, "redirect_url", "") or "")

    def wait_for(self, toolkit: str, timeout: Optional[float] = None) -> bool:
        """Block until the owner finishes connecting the app, or the timeout passes. Never raises."""
        req = self._requests.get(toolkit)
        try:
            if req is None:
                if self._session is None:
                    return False
                req = self._session.authorize(toolkit)
                self._requests[toolkit] = req
            req.wait_for_connection(timeout=timeout if timeout is not None else self.config.timeout_secs)
        except Exception as e:  # noqa: BLE001
            log.warning("composio: waiting for %s: %s", toolkit, type(e).__name__)
            return False
        return True
