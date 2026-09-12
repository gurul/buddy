"""Loopback-only learning workspace. Run with python -m cc_buddy_bridge.learning --demo."""
from __future__ import annotations

import argparse
import base64
import errno
import json
import logging
import secrets
import sys
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

from .store import Store, default_dir
from .tutor import DemoTutor, LiveTutor, live_settings

log = logging.getLogger(__name__)
DEFAULT_PORT = 48766
MAX_BODY = 12 * 1024 * 1024
ASSETS = Path(__file__).parent / "web"


def validate_work(data):
    result = {}
    for key in ("ideas", "problem"):
        if key in data:
            if not isinstance(data[key], str) or len(data[key]) > 20000:
                raise ValueError("Text is too long.")
            result[key] = data[key]
    if "stuck" in data:
        result["stuck"] = bool(data["stuck"])
    for key in ("source_image", "board_image"):
        if key in data:
            value = data[key]
            if not isinstance(value, str) or len(value) > 6 * 1024 * 1024:
                raise ValueError("Image must be smaller than 4 MB.")
            if value:
                prefix, sep, encoded = value.partition(",")
                if not sep or prefix not in ("data:image/png;base64", "data:image/jpeg;base64", "data:image/webp;base64"):
                    raise ValueError("Use a PNG, JPEG or WebP image.")
                try:
                    raw = base64.b64decode(encoded, validate=True)
                except ValueError:
                    raise ValueError("Invalid image data.") from None
                if not (raw.startswith(b"\x89PNG\r\n\x1a\n") or raw.startswith(b"\xff\xd8\xff") or raw.startswith(b"RIFF")):
                    raise ValueError("Invalid image file.")
            result[key] = value
    if "strokes" in data:
        strokes = data["strokes"]
        if not isinstance(strokes, list) or len(strokes) > 10000:
            raise ValueError("Whiteboard is too large. Start another problem.")
        for stroke in strokes:
            if not isinstance(stroke, dict) or stroke.get("tool") not in ("pen", "eraser"):
                raise ValueError("Invalid drawing stroke.")
            points = stroke.get("points")
            if not isinstance(points, list) or len(points) > 10000:
                raise ValueError("Invalid drawing points.")
            for p in points:
                if not isinstance(p, list) or len(p) != 2 or any(type(v) not in (int, float) or not 0 <= v <= 1 for v in p):
                    raise ValueError("Invalid drawing coordinates.")
        result["strokes"] = strokes
    return result


class LearningApp:
    def __init__(self, directory=None, demo=False, tutor=None, notify=None):
        self.store = Store(directory or default_dir())
        self.tutor = tutor or (DemoTutor() if demo else LiveTutor())
        self.demo = self.tutor.demo
        self.notify = notify
        self.lock = threading.RLock()
        self.active = None
        self.url = ""

    def event(self, s, action, result):
        s["events"].append({"action": action, "time": time.time(), **result})

    def dispatch(self, data):
        with self.lock:
            action = data.get("action")
            if action == "create":
                mode = data.get("mode", "learn")
                topic, level = str(data.get("topic", ""))[:200], str(data.get("level", ""))[:100]
                if mode == "learn" and not topic.strip():
                    raise ValueError("What topic would you like to learn?")
                s = self.store.create(mode, topic, level, self.demo)
                self.active = s["id"]
                return s
            key = data.get("id") or self.active
            if not key:
                raise ValueError("Open a lesson first.")
            s = self.store.get(key)
            if action == "select":
                self.active = key
                return s
            if action == "delete":
                self.store.delete(key)
                if self.active == key:
                    self.active = None
                return {"deleted": True}
            if data.get("revision", s["revision"]) != s["revision"]:
                raise ValueError("This lesson changed in another window. Reload it before continuing.")
            if s["demo"] != self.demo:
                raise ValueError("Open this lesson using the same demo/live mode it was created in.")
            if s["stage"] == "ended" and action != "resume":
                raise ValueError("Resume this saved lesson before editing it.")
            if action == "save":
                work = validate_work(data.get("work", {}))
                if s["stage"] not in ("input", "confirm"):
                    work.pop("problem", None)
                    work.pop("source_image", None)
                s.update(work)
            elif action == "generate":
                if s["stage"] != "setup":
                    raise ValueError("Start a new lesson to generate another problem.")
                result = self.tutor("generate", s)
                if not result["problem"].strip():
                    raise ValueError("Buddy did not generate a problem. Try again.")
                s.update(problem=result["problem"], stage="working")
                self.event(s, action, result)
            elif action == "recognize":
                if s["stage"] not in ("input", "confirm"):
                    raise ValueError("Start a new lesson to change the problem.")
                if not (s["problem"].strip() or s["source_image"] or s["strokes"]):
                    raise ValueError("Write a problem or add a screenshot first.")
                result = self.tutor(action, s)
                s.update(problem=result["problem"], stage="confirm")
                self.event(s, action, result)
            elif action == "confirm":
                if s["stage"] != "confirm" or not s["problem"].strip():
                    raise ValueError("Confirm the problem text first.")
                s["stage"] = "working"
                s["problem_stroke_count"] = len(s["strokes"])
                self.event(s, action, {"feedback": "Write your ideas or show where you are stuck. If you don't know how to start, tell me."})
            elif action in ("hint", "step", "check", "recap"):
                if s["stage"] not in ("working", "complete"):
                    raise ValueError("Confirm your problem before asking for help.")
                if s["stage"] == "complete" and action != "recap":
                    raise ValueError("This problem is complete. Try another problem or review the recap.")
                if s["mode"] == "help" and not (s["ideas"].strip() or s["stuck"] or len(s["strokes"]) > s.get("problem_stroke_count", 0)):
                    raise ValueError("Put down your ideas first, or select I don't know how to start.")
                result = self.tutor(action, s)
                self.event(s, action, result)
                if result["status"] == "complete":
                    s["stage"] = "complete"
            elif action == "end":
                s["resume_stage"] = s["stage"]
                s["stage"] = "ended"
                self.event(s, action, {"feedback": "Your lesson and all your work are saved. Come back whenever you're ready."})
            elif action == "resume":
                if s["stage"] == "ended":
                    s["stage"] = s.pop("resume_stage", "working")
            else:
                raise ValueError("Unknown lesson action.")
            s = self.store.save(s, expected=s["revision"])
            self.active = s["id"]
            if self.notify and action != "save" and s["events"]:
                try:
                    self.notify(s["events"][-1].get("feedback", ""), s["stage"])
                except Exception:
                    log.exception("learning: robot notification failed; lesson is saved")
            return s

    def voice(self, action="open", mode="", topic="", level="", text=""):
        if action == "open":
            webbrowser.open(self.url)
            return {"ok": True, "answer": "Would you like to learn a topic or work on a problem you already have?"}
        if action == "start":
            s = self.dispatch({"action": "create", "mode": mode or "learn", "topic": topic, "level": level})
            if s["mode"] == "learn":
                s = self.dispatch({"action": "generate", "id": s["id"]})
            webbrowser.open(self.url + "#lesson/" + s["id"])
            return {"ok": True, "answer": s["problem"] or "Add your problem to the whiteboard, then show me your ideas."}
        if action == "status":
            s = self.store.get(self.active) if self.active else None
            return {"ok": True, "lesson": self.store.summary(s) if s else None,
                    "answer": s["events"][-1].get("feedback", "") if s and s["events"] else "Choose a lesson in the learning window."}
        if action == "ideas":
            s = self.dispatch({"action": "save", "work": {"ideas": text, "stuck": not text.strip()}})
            return {"ok": True, "answer": "I've saved your thinking. Would you like a hint or a check?"}
        s = self.dispatch({"action": action})
        event = s["events"][-1]
        return {"ok": True, "answer": " ".join(filter(None, [event.get("step"), event.get("feedback")]))}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def reply(self, status, data, mime="application/json"):
        encoded = json.dumps(data).encode() if mime == "application/json" else data
        self.send_response(status)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(encoded)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", "default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; frame-ancestors 'none'")
        self.end_headers()
        self.wfile.write(encoded)

    def allowed(self):
        expected = urlsplit(self.server.app.url).netloc
        return self.headers.get("Host") == expected and self.headers.get("Origin", self.server.app.url.rstrip("/")) == self.server.app.url.rstrip("/")

    def do_GET(self):
        if not self.allowed():
            return self.reply(403, {"error": "Local access only."})
        path = urlsplit(self.path).path
        app = self.server.app
        try:
            if path == "/api/config":
                settings = {"provider": "demo", "model": "offline examples", "ready": True, "key_name": ""} if app.demo else live_settings()
                return self.reply(200, {**settings, "token": self.server.token, "demo": app.demo, "active": app.active})
            if path == "/api/lessons":
                return self.reply(200, app.store.list())
            if path.startswith("/api/lessons/"):
                parts = path.split("/")
                key = parts[3]
                return self.reply(200, app.store.history(key) if len(parts) > 4 and parts[4] == "history" else app.store.get(key))
            files = {"/": ("index.html", "text/html; charset=utf-8"), "/app.js": ("app.js", "text/javascript; charset=utf-8"),
                     "/style.css": ("style.css", "text/css; charset=utf-8")}
            if path in files:
                name, mime = files[path]
                return self.reply(200, (ASSETS / name).read_bytes(), mime)
            return self.reply(404, {"error": "Not found"})
        except KeyError:
            return self.reply(404, {"error": "Lesson not found"})

    def do_POST(self):
        if not self.allowed() or self.headers.get("X-Buddy-Token") != self.server.token:
            return self.reply(403, {"error": "Reload this local page before saving."})
        if self.path != "/api/action":
            return self.reply(404, {"error": "Not found"})
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= MAX_BODY:
                return self.reply(413, {"error": "Request is too large."})
            data = json.loads(self.rfile.read(length))
            if not isinstance(data, dict):
                raise ValueError("Invalid request.")
            return self.reply(200, self.server.app.dispatch(data))
        except KeyError:
            return self.reply(404, {"error": "Lesson not found"})
        except (ValueError, TypeError) as exc:
            return self.reply(400, {"error": str(exc)})
        except Exception:
            log.exception("learning request failed")
            return self.reply(500, {"error": "Buddy could not finish this request. Your previously saved work is safe."})


def start(directory=None, demo=False, port=DEFAULT_PORT, notify=None):
    app = LearningApp(directory, demo, notify=notify)
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    server.app = app
    server.token = secrets.token_urlsafe(32)
    app.url = f"http://127.0.0.1:{server.server_port}/"
    thread = threading.Thread(target=server.serve_forever, name="buddy-learning", daemon=True)
    thread.start()
    return server


def main(argv=None):
    parser = argparse.ArgumentParser(description="Buddy's saved math lessons and whiteboard")
    parser.add_argument("--demo", action="store_true", help="Offline examples; no API calls or handwriting recognition")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--data-dir", type=Path)
    parser.add_argument("--env-file", type=Path, help="Explicit environment file (use a /mnt/c/... path from WSL)")
    parser.add_argument("--no-open", action="store_true")
    args = parser.parse_args(argv)
    from ..envfile import load_env_file
    load_env_file(args.env_file)
    directory = args.data_dir or (default_dir() / "demo" if args.demo else default_dir())
    try:
        server = start(directory, args.demo, args.port)
    except OSError as exc:
        if exc.errno != errno.EADDRINUSE and getattr(exc, "winerror", None) != 10048:
            raise
        print(f"Port {args.port} is already in use. If Buddy is already running, open "
              f"http://127.0.0.1:{args.port}/ instead of starting another instance.\n"
              "To load changed settings, stop the existing Buddy process first, then restart it. "
              "Or choose a different port with --port.", file=sys.stderr)
        return 2
    print(f"Buddy learning: {server.app.url} ({'offline demo' if args.demo else 'live tutor'})", flush=True)
    if not args.no_open:
        webbrowser.open(server.app.url)
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        server.shutdown()
        server.server_close()
    return 0
