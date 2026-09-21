#!/usr/bin/env python3
"""Control the live Laya worker; --check exercises model -> USB -> eye/speaker ACKs."""

import argparse
import json
import time
from pathlib import Path

from cc_buddy_bridge.ipc import make_transport


def request(evt="expressions", **kwargs):
    with make_transport().sync_connect(5) as sock:
        sock.sendall((json.dumps({"evt": evt, **kwargs}) + "\n").encode())
        result = json.loads(sock.makefile("rb").readline())
    if not result.get("ok"):
        raise RuntimeError(result)
    return result


def wait_for(predicate, seconds=20):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        result = request()
        if predicate(result):
            return result
        time.sleep(0.15)
    raise AssertionError("timed out waiting for live device condition: " + json.dumps(result))


def check():
    request(action="on")
    wait_for(lambda r: r.get("connected") and r["expressions"]["ready"], 60)
    original = request("sound", action="status")["sound"]
    evidence = []
    try:
        for muted, phase, text in [
            (False, "speaking", "I passed my final exam! This is wonderful news!"),
            (True, "speaking", "I am curious about that strange new object on the desk."),
            (False, "listening", "Thank you for being my friend. I really appreciate you."),
        ]:
            request("sound", action="off" if muted else "on")
            time.sleep(0.5)
            event = request(action="audition", phase=phase, text=text)["id"]
            applied = phase != "listening"
            result = wait_for(
                lambda r, event=event, applied=applied, muted=muted: any(
                    b["id"] == event and b["active"] and b["applied"] == applied and b["muted"] == muted
                    for b in r["expressions"]["board_history"]
                )
            )
            result = wait_for(
                lambda r, event=event: any(
                    b["id"] == event and not b["active"] for b in r["expressions"]["board_history"]
                )
            )
            rows = [b for b in result["expressions"]["board_history"] if b["id"] == event]
            assert result["expressions"]["last"]["id"] == event
            assert (
                all(not b["chirp"] for b in rows) if muted or not applied else any(b["chirp"] for b in rows)
            )
            assert any(b["applied"] for b in rows) == applied
            evidence.append(
                {
                    "case": phase + ("-muted" if muted else ""),
                    "model": result["expressions"]["last"],
                    "board": rows,
                }
            )
            time.sleep(4.5)  # finish audition and eight-second sound cooldown
    finally:
        request("sound", action=original)
    path = Path(".test-artifacts/laya-live-device.json")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(evidence, indent=2) + "\n")
    print(json.dumps(evidence, indent=2))
    print("LIVE_LAYA_DEVICE_OK")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "action", nargs="?", default="status", choices=["status", "on", "off", "react", "audition"]
    )
    parser.add_argument("text", nargs="?", default="")
    parser.add_argument("--phase", choices=["speaking", "listening", "idle"], default="speaking")
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    if args.check:
        check()
    else:
        print(json.dumps(request(action=args.action, text=args.text, phase=args.phase), indent=2))


if __name__ == "__main__":
    main()
