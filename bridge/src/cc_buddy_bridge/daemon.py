"""Main daemon: wires IPC, BLE, state, and JSONL tailer together."""

from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import TYPE_CHECKING, Any, Optional

if TYPE_CHECKING:
    from .key_tap import KeyTapper

from . import voice_agent
from .audit import AuditLog
from .ble import BuddyBLE
from .caption_pager import CaptionPager, PagerConfig
from .computer_agent import ComputerAgent, log_desktop_grants, make_response_creator
from .computer_agent import configured as agent_configured
from .diary import DiaryTaker, Emote, Thought, build_emote_cmd, make_diary_client
from .ears import Ears
from .ears import configured as ears_configured
from .explore import (
    Action,
    ExploreRefused,
    Explorer,
    Look,
    Mode,
    Note,
    NoteTaker,
    Rest,
    build_look_cmd,
    build_mode_cmd,
)
from .explore import configured as explore_configured
from .identity import FaceIdentity, OwnerIdentity, configured_threshold, make_describer
from .identity import default_path as identity_default_path
from .ipc import IPCServer
from .jsonl_tailer import JSONLTailer
from .listen_key import Stopper, start_listen_key
from .matchers import MatcherConfig, classify_command
from .matchers import load_config as load_matcher_config
from .protocol import (
    ENTRY_MAX_BYTES,
    HEARTBEAT_KEEPALIVE,
    build_heartbeat,
    build_time_sync,
    truncate_utf8_bytes,
)
from .read_policy import is_within, read_scope
from .thought_screen import ThoughtScreen
from .state import State
from .version_check import check as version_check
from .vision import STATS_INTERVAL_SECS as VISION_STATS_SECS
from .vision import FaceTracker, Frame, build_cam_cmd, build_snap_cmd, configured_save_dir, decode_frame, make_detector

# Entry text is prefixed with a 2-byte marker ("> ", "@ ", "+ ") before being
# stored. Budget the user-supplied portion so the full entry stays within the
# firmware's line buffer without _format_entry needing to re-truncate.
_ENTRY_PAYLOAD_MAX_BYTES = ENTRY_MAX_BYTES - 2

log = logging.getLogger(__name__)

# PERMISSION_WAIT_SECS moved to protocol.py — the wire `prompt.ttl` field is
# derived from it, so it lives next to the serializer. Re-exported via the
# import above for callers/tests that referenced it here.

class Daemon:
    def __init__(
        self,
        socket_path: Optional[str] = None,
        device_name_prefix: str = "Claude",
        device_address: Optional[str] = None,
        matchers: Optional[MatcherConfig] = None,
        serial_port: Optional[str] = None,
        save_frames: Optional[str] = None,
    ) -> None:
        self.state = State()
        self.ipc = IPCServer(self._handle_ipc, socket_path=socket_path) if socket_path else IPCServer(self._handle_ipc)
        if serial_port:
            # USB CDC transport (Freenove FNK0104B port): same wire protocol,
            # different pipe. Kept on the .ble attribute so the rest of the
            # daemon doesn't care which transport is live.
            from .serial_transport import BuddySerial
            self.ble = BuddySerial(on_message=self._handle_ble, port=serial_port)
            # A board reboot under an unbroken CH340 link never fires the
            # on-connect resync — replay it when the boot banner scrolls past,
            # or the reborn board keeps "--:--" and "No Claude" indefinitely.
            self.ble.on_boot = self._resync_board
        else:
            self.ble = BuddyBLE(
                on_message=self._handle_ble,
                name_prefix=device_name_prefix,
                address=device_address,
            )
        self.jsonl = JSONLTailer(self._on_tokens, on_assistant_text=self._on_assistant_text)
        self.matchers = matchers if matchers is not None else load_matcher_config()
        # Per-decision append-only log; see audit.py
        self.audit = AuditLog()
        # tool_use_id → Future resolving to "allow" | "deny"
        # Read scopes (git-repo roots / parent dirs) the user has approved via
        # a card swipe. Daemon-lifetime by design — restart forgets all grants.
        self._read_scopes: set[str] = set()
        # Command shapes granted "always" from the stick (card held at the
        # approve edge). In-memory only — a bad grant dies with the daemon.
        # Hold-the-pet push-to-talk: holds Opt+Space (VoiceFlow) between the
        # stick's voice start/stop events. Lazily constructed on first use so
        # non-mac / Quartz-less hosts pay nothing.
        self._keys: Optional["KeyTapper"] = None
        # Listen key (held Option = dictation): a Quartz tap on its own
        # thread, started in run(). None when the tap could not be created.
        self._listen_stop: Optional[Stopper] = None
        # Last {"cmd":"listen"} state the board received. None until the
        # first send; reset to False on every connect/resync so a rebooted
        # board never keeps the listening pose.
        self._listen_sent: Optional[bool] = None
        # Host vision: the board streams camera frames, we answer with the
        # face position. The detector (macOS Vision) is resolved in run() so
        # constructing a Daemon never imports pyobjc; until then, and on
        # hosts without Vision, the tracker is disabled and we never ask the
        # board to stream — it keeps its on-board tracking.
        self._vision = FaceTracker(
            detect=None, send=self.ble.send, save_dir=configured_save_dir(save_frames))
        # Physical listen-key state, independent of what the board received:
        # identity.py enrols the owner only while it is down. Written on the
        # loop thread, read on the vision executor thread (a bool: no lock).
        self._listen_down = False
        # Owner identity: the prints live in memory; the file is loaded and
        # the Vision describer resolved in run(), like the detector.
        self._identity: Optional[FaceIdentity] = None
        # Idle explorer (explore.py): after a quiet stretch the board pans
        # the room and, per waypoint, may spend a note on what it sees. The
        # pure Explorer is ticked from _explore_loop; the note client is
        # resolved in run() so constructing a Daemon never imports openai.
        self._explore_cfg = explore_configured()
        self._explorer = Explorer(self._explore_cfg, now=time.monotonic())
        self._notes: Optional[NoteTaker] = None
        # "hey buddy": the wake word on the Mac mic (ears.py) opens a spoken
        # conversation (voice_agent.py) that can run a computer-use task
        # (computer_agent.py). One conversation at a time; the board mirrors
        # its phases through {"cmd":"agent","state":...}.
        self._ears_cfg = ears_configured()
        self._voice_cfg = voice_agent.configured()
        self._agent_cfg = agent_configured()
        self._ears: Optional[Ears] = None
        self._conversation: Optional[asyncio.Task[None]] = None
        self._agent_state = "idle"
        self._active_agent: Optional[ComputerAgent] = None
        # "hey buddy, go explore": the voice tool sets this; the explore
        # starts when the conversation has closed, so the board is never
        # asked to hold a conversation pose and pan the room at once.
        self._explore_after_conversation: Optional[str] = None
        # Thoughts on the robot's own screen while it explores: the same pager
        # the conversation uses (caption_pager.py), so a thought is wrapped to
        # 4 lines x 17 chars and held at reading pace. A conversation owns the
        # screen when one is open; this only ever draws between them.
        self._thought_pager = CaptionPager(PagerConfig(read_cps=self._voice_cfg.caption_cps))
        # ... and what is worth putting there. The diary writes down far more
        # than a person sitting beside the robot wants to read.
        self._screen = ThoughtScreen()
        # What the room sounds like (hearing.py). The board reports a loudness
        # reading while it explores; a frame cannot tell a silent afternoon
        # from one where a door just banged.
        # Monotonic time of the last thing a human or a session did: any hook
        # event, a board touch, the listen key. Idle time is measured from it.
        self._last_activity_at = time.monotonic()
        # The newest raw {"frame":...} object, kept only while exploring so a
        # waypoint sample never costs a decode on the 5 fps path.
        self._explore_raw_frame: Optional[dict[str, Any]] = None
        # A pending {"cmd":"snap"}: the diary asked for a photo and waits on
        # this future for the one frame line the board answers with.
        self._snap_waiter: Optional[asyncio.Future[Frame]] = None
        # Most recent {"diag":{...}} the board sent; served over IPC so
        # `cc-buddy-bridge diag` can show it without owning the serial port.
        self._last_diag: Optional[dict[str, Any]] = None
        # Status-ack liveness: proves host->board writes still land.
        self._status_sent_at: Optional[float] = None
        self._status_missed = 0
        self._ack_escalation = 0   # consecutive missed-ack episodes
        self._clean_polls = 0      # answered polls since the last escalation
        # tool_use_id -> session cwd, for the card's swipe-up focus action.
        # transcript_path → hash of the last assistant content we emitted as an
        # entry. Used to distinguish "fresh turn" from "re-read old content"
        # when the transcript file hasn't been flushed yet.
        self._last_emitted_turn_key: dict[str, str] = {}
        # session_id → task that'll flip running→0 after a grace window.
        # Delays the turn_end so the stick's HUD stays drawn long enough to
        # display the @-entry the tailer just emitted. See firmware's
        # drawHUD/clocking gate in main.cpp.
        self._pending_turn_ends: dict[str, asyncio.Task] = {}
        # Cached stick-side status fields from the most recent status ack.
        self._last_stick_sec: Optional[bool] = None
        self._last_stick_battery_pct: Optional[int] = None
        # Futures awaiting a specific ack type. Used by folder_push's
        # chunk-by-chunk flow control. Each entry: (ack_type, Future).
        self._ack_waiters: list[tuple[str, asyncio.Future]] = []
        # Track last heartbeat to dedupe (avoid spamming BLE with identical snapshots).
        self._last_hb_serialized: Optional[str] = None
        self._last_hb_sent_at: float = 0.0
        # Latest available version string (e.g. "v0.1.1") when an update is
        # available; None otherwise. Set by _update_check_loop.
        self._update_available: Optional[str] = None
        # Wire codec for heartbeat string fields. Set when the user has
        # flashed the fork-only CJK firmware variant (`m5stickc-plus-cjk-*`)
        # and tells the bridge which one via env var. None = stock firmware,
        # ASCII-only content. See protocol.CJK_CODECS for the mapping.
        from .protocol import CJK_CODECS
        _target = os.environ.get("CC_BUDDY_CJK_TARGET", "").strip()
        self._cjk_codec: Optional[str] = CJK_CODECS.get(_target) if _target else None
        if self._cjk_codec is not None:
            log.info("cjk firmware target=%s, wire codec=%s", _target, self._cjk_codec)
        self._shutdown = asyncio.Event()

    # ---- entry ----

    async def run(self) -> None:
        _log_permission_config_summary(self.matchers)
        await self.ipc.start()
        self._listen_stop = start_listen_key(self._on_listen_key, asyncio.get_running_loop())
        self._start_ears(asyncio.get_running_loop())
        self._vision.detect = make_detector()
        self._identity = self._make_identity()
        self._vision.identity = self._identity
        tasks = [
            asyncio.create_task(self.ipc.serve_forever(), name="ipc"),
            asyncio.create_task(self.ble.run(), name="ble"),
            asyncio.create_task(self.jsonl.run(), name="jsonl"),
            asyncio.create_task(self._heartbeat_loop(), name="heartbeat"),
            asyncio.create_task(self._on_ble_connected(), name="on-connect"),
            asyncio.create_task(self._status_poller(), name="status-poller"),
            asyncio.create_task(self._update_check_loop(), name="update-check"),
            asyncio.create_task(self._vision_stats_loop(), name="vision-stats"),
        ]
        # The diary (diary.py): memory-aware thoughts plus an appraisal
        # the board turns into a feeling. Same take() shape as NoteTaker.
        # The loop always runs so `cc-buddy-bridge explore` and "hey buddy,
        # go explore" work; CC_BUDDY_EXPLORE=0 only turns off the idle start.
        client = make_diary_client(self._explore_cfg.model)
        self._explorer.notes_enabled = client is not None
        if client is not None:
            self._notes = DiaryTaker(client, self._explore_cfg.notes_dir, send_emote=self._send_emote,
                                     snapshot=self._take_snapshot, on_thought=self._show_thought)
        tasks.append(asyncio.create_task(self._explore_loop(), name="explore"))
        tasks.append(asyncio.create_task(self._thought_caption_loop(), name="thought-captions"))
        if not self._explore_cfg.enabled:
            log.info("explore: idle start disabled (CC_BUDDY_EXPLORE=0); "
                     "`cc-buddy-bridge explore` and \"go explore\" still work")
        try:
            await self._shutdown.wait()
        finally:
            # Leave explore mode and stop the camera before the transport
            # task is cancelled — its reader owns the port close, so a send
            # after that goes nowhere.
            await self._stop_explore("daemon stopping")
            await self._send_cam(False)
            self._vision.stop()
            for t in tasks:
                t.cancel()
            for pend in list(self._pending_turn_ends.values()):
                if not pend.done():
                    pend.cancel()
            await asyncio.gather(*tasks, *self._pending_turn_ends.values(),
                                 return_exceptions=True)
            await self._cancel_active_task("the daemon is restarting")
            if self._conversation is not None and not self._conversation.done():
                self._conversation.cancel()
                await asyncio.gather(self._conversation, return_exceptions=True)
            if self._ears is not None:
                self._ears.stop()
            if self._listen_stop is not None:
                self._listen_stop()
            await self.ble.stop()
            await self.ipc.stop()

    async def shutdown(self) -> None:
        self._shutdown.set()

    # ---- heartbeat loop ----

    async def _heartbeat_loop(self) -> None:
        while not self._shutdown.is_set():
            await self._push_heartbeat()
            try:
                await asyncio.wait_for(self._shutdown.wait(), timeout=HEARTBEAT_KEEPALIVE)
            except asyncio.TimeoutError:
                continue

    async def _push_heartbeat(self, force: bool = False) -> None:
        import json

        snap = build_heartbeat(self.state, codec=self._cjk_codec)
        # Dedup key is the Python-side serialized snapshot, not the wire bytes
        # — same dict means same content regardless of wire codec.
        serialized = json.dumps(snap, sort_keys=True, ensure_ascii=False)
        now = time.monotonic()
        changed = serialized != self._last_hb_serialized
        stale = (now - self._last_hb_sent_at) >= HEARTBEAT_KEEPALIVE
        if not (force or changed or stale):
            return
        if self.ble.connected:
            # Monitor build: what actually went on the wire for the agent
            # table. Keep this — "the board shows 0 sessions" has to be
            # separable from "the daemon sent 0 sessions", and reading that
            # off a photo of an e-ink panel is not a diagnosis.
            log.debug(
                "hb->board: total=%d running=%d waiting=%d agents=%s",
                snap["total"], snap["running"], snap["waiting"],
                [f"{a['n']}:{a['s']}" for a in snap.get("agents", [])],
            )
            log.debug(
                "heartbeat: %d bytes, entries=%d (last=%r), force=%s, changed=%s",
                len(serialized), len(snap.get("entries", [])),
                snap["entries"][-1] if snap.get("entries") else None,
                force, changed,
            )
            ok = await self.ble.send(snap, codec=self._cjk_codec)
            if ok:
                self._last_hb_serialized = serialized
                self._last_hb_sent_at = now
            else:
                log.warning("heartbeat: ble.send returned failure")

    async def _resync_board(self) -> None:
        """Re-run the on-connect resync after a board reboot on a live link:
        time sync, forced heartbeat, status poll. The board buffers serial RX
        during its boot-time panel clear, so no settling delay is needed."""
        log.info("board rebooted under a live link — resyncing time + state")
        await self.ble.send(build_time_sync())
        await self._push_heartbeat(force=True)
        await self.ble.send({"cmd": "status"})
        await self._reset_listen()
        await self._resync_agent()
        await self._send_cam(True)

    async def _on_ble_connected(self) -> None:
        """On every (re)connect, emit time sync + force a heartbeat + kick
        a status poll so we learn the link's encryption state right away."""
        while not self._shutdown.is_set():
            await self.ble.wait_connected()
            # Guard against a stale connected-event: if we woke but the link is
            # already gone (the event was set but cleared a beat later, or a
            # teardown race), don't fire sends into a dead link and spin. Sleep
            # briefly and re-wait instead of looping with no delay.
            if not self.ble.connected:
                await asyncio.sleep(0.5)
                continue
            await self.ble.send(build_time_sync())
            await self._push_heartbeat(force=True)
            await self.ble.send({"cmd": "status"})
            await self._reset_listen()
            await self._resync_agent()
            await self._send_cam(True)
            # Wait for the connection to drop before waiting again.
            while self.ble.connected and not self._shutdown.is_set():
                await asyncio.sleep(1.0)

    async def _status_poller(self) -> None:
        """Poll the stick for status, and use the replies as a TX health check.

        The status ack is the only thing that proves host→board writes are
        landing. A half-dead link — reads fine, writes silently go nowhere —
        keeps RX_SILENCE_SECS happy (the board is still talking) while the board
        shows "No Claude connected" because no heartbeat ever reaches it.
        Missing acks are the only signal, so treat them as one.
        """
        POLL_INTERVAL = 60.0
        MISSED_LIMIT = 2          # ~2min of unanswered polls before reconnecting
        while not self._shutdown.is_set():
            try:
                await asyncio.wait_for(self._shutdown.wait(), timeout=POLL_INTERVAL)
                return
            except asyncio.TimeoutError:
                pass
            if not self.ble.connected:
                continue
            if self._status_sent_at is not None:
                self._status_missed += 1
                if self._status_missed >= MISSED_LIMIT:
                    self._status_missed = 0
                    self._status_sent_at = None
                    # Escalate: first a plain reconnect (which now holds the
                    # port closed for a while — the only host-side action that
                    # ever revived this wedge); if acks are STILL missing a
                    # cycle later, the wedge is board-side (its RX died while
                    # TX kept going) and we pulse RTS to reboot it. The
                    # escalation counter survives a lone post-reopen ack —
                    # the field pattern is a flap, not a clean recovery — and
                    # only a streak of clean polls (see the ack handler)
                    # resets it.
                    self._ack_escalation += 1
                    self._clean_polls = 0
                    log.info("ack escalation → %d", self._ack_escalation)
                    why = (f"no status ack for {MISSED_LIMIT} polls "
                           f"(~{int(MISSED_LIMIT * POLL_INTERVAL)}s) — "
                           "writes are not reaching the board")
                    if self._ack_escalation >= 2:
                        pulse = getattr(self.ble, "pulse_reset", None)
                        if pulse is not None:
                            self._ack_escalation = 0
                            self._clean_polls = 0
                            pulse("reconnect did not restore acks; " + why)
                            continue
                    force = getattr(self.ble, "force_reconnect", None)
                    if force is not None:
                        force(why)
                    continue
            self._status_sent_at = time.monotonic()
            # Re-sync time with every poll. The board's RTC-valid flag is
            # RAM-only, so any watchdog reset blanks the mini clock; a reboot
            # that slips past silence detection (fast reboot, half-dead link)
            # never refires the on-connect time sync, leaving the clock gone
            # until the next reconnect. Periodic sync makes it self-heal
            # within one poll and corrects RTC drift for free.
            await self.ble.send(build_time_sync())
            await self.ble.send({"cmd": "status"})

    # ---- host vision ----

    async def _send_cam(self, on: bool) -> None:
        """Ask the board to start/stop streaming frames. Only when a detector
        exists: a board streaming at a host that cannot look is wasted link
        time, and the board keeps its own tracking while no host asks."""
        if not self._vision.enabled or not self.ble.connected:
            return
        try:
            await asyncio.wait_for(self.ble.send(build_cam_cmd(on)), timeout=2.0)
        except asyncio.TimeoutError:
            log.warning("vision: cam %s command timed out", "on" if on else "off")

    async def _vision_stats_loop(self) -> None:
        """One summary line per window, and only when frames arrived. Never per-frame."""
        while not self._shutdown.is_set():
            await asyncio.sleep(VISION_STATS_SECS)
            line = self._vision.stats_line()
            if line is not None:
                log.info(line)

    # ---- owner identity ----

    def _make_identity(self) -> Optional[FaceIdentity]:
        """Load the owner prints and wire the Vision describer. None when
        this host cannot produce feature prints — faces stay 'unknown'."""
        if not self._vision.enabled:
            return None
        describe = make_describer()
        if describe is None:
            return None
        core = OwnerIdentity(path=identity_default_path(), threshold=configured_threshold())
        n = core.load()
        log.info("identity: %d owner print(s) loaded from %s (threshold %.2f)", n, core.path, core.threshold)
        return FaceIdentity(core, describe=describe, listen_down=lambda: self._listen_down)

    def _handle_identity(self, action: str) -> dict[str, Any]:
        """Loop thread. The core is lock-guarded, so this is safe against a
        classify/enrol in flight on the vision executor."""
        if action == "reset":
            if self._identity is None:
                core = OwnerIdentity(path=identity_default_path())
                core.load()
                removed = core.reset()
            else:
                removed = self._identity.core.reset()
                self._identity.last_who = None
            log.info("identity: reset — %d owner print(s) removed", removed)
            return {"ok": True, "removed": removed}
        if self._identity is None:
            core = OwnerIdentity(path=identity_default_path(), threshold=configured_threshold())
            core.load()
            st = FaceIdentity(core, describe=lambda f, r: None).status()
        else:
            st = self._identity.status()
        return {"ok": True, "enabled": self._identity is not None, "identity": st}

    # ---- "hey buddy" ----

    def _start_ears(self, loop: asyncio.AbstractEventLoop) -> None:
        if not self._ears_cfg.enabled:
            log.info("ears: disabled (CC_BUDDY_VOICE=0)")
            return
        self._ears = Ears(self._ears_cfg, self._on_wake, loop, suppressed=self._wake_suppressed)
        if not self._ears.start():
            self._ears = None
            return
        if not (os.environ.get("OPENAI_API_KEY") or "").strip():
            log.warning("voice: OPENAI_API_KEY not set — buddy will hear its name but cannot talk back "
                        "(put it in ~/.config/cc-buddy-bridge/env)")
        log.info("agent: computer control %s (model %s, %d steps / %.0f s max, %.0f s per step, reasoning %s/%s)",
                 "ready" if self._agent_cfg.enabled else "disabled (CC_BUDDY_COMPUTER_CONTROL=0)",
                 self._agent_cfg.model, self._agent_cfg.max_turns, self._agent_cfg.max_secs,
                 self._agent_cfg.exec_timeout_secs, self._agent_cfg.reasoning_effort,
                 self._agent_cfg.plan_reasoning_effort)
        if self._agent_cfg.enabled:
            log_desktop_grants(prompt=True)

    def _wake_suppressed(self) -> bool:
        """Reasons not to wake: the human is dictating, a conversation is already
        open, or a permission card is waiting on the board."""
        return (self._listen_down
                or (self._conversation is not None and not self._conversation.done())
                or self.state.pending_count > 0)

    def _on_wake(self, keyword: str) -> None:
        log.info("ears: heard %r", keyword)
        self._note_activity()          # a conversation is activity: the explorer stops
        if self._conversation is not None and not self._conversation.done():
            return
        # A manual explore ignores activity, so end it here: the board
        # cannot pan the room and hold the conversation pose at once.
        asyncio.create_task(self._dismiss_explore("wake word"))
        self._conversation = asyncio.create_task(self._converse(), name="voice-conversation")

    async def _resync_agent(self) -> None:
        """Tell a (re)connected or rebooted board which conversation phase is live —
        idle when none. The board drops a phase that goes 30 s without a word from
        us, so a lost 'idle' can no longer pin the listening pose (bench 2026-09-06)."""
        if self.ble.connected:
            await self.ble.send({"cmd": "agent", "state": self._agent_state})

    async def _agent_keepalive(self) -> None:
        """While a conversation is open, repeat the current phase every 10 s."""
        while True:
            await asyncio.sleep(10.0)
            await self._resync_agent()

    async def _converse(self) -> None:
        if self._ears is None:
            return
        mic = self._ears.subscribe()
        keepalive = asyncio.create_task(self._agent_keepalive(), name="agent-keepalive")
        try:
            await voice_agent.open_session(mic, self._on_agent_state, self._make_agent,
                                           config=self._voice_cfg, agent_enabled=self._agent_cfg.enabled,
                                           on_caption=self._on_caption, on_explore=self._on_voice_explore)
        except asyncio.CancelledError:
            self._explore_after_conversation = None      # hushed: stay put
            raise
        except Exception as e:  # noqa: BLE001
            log.warning("voice: conversation failed: %s: %s", type(e).__name__, e)
            self._on_agent_state("error")
        finally:
            keepalive.cancel()
            await asyncio.gather(keepalive, return_exceptions=True)
            self._ears.unsubscribe(mic)
            self._on_agent_state("idle")
            reason, self._explore_after_conversation = self._explore_after_conversation, None
            if reason is not None:
                await self._request_explore(reason)

    def _on_voice_explore(self) -> None:
        """The voice tool go_explore: remember the wish; it is granted in
        _converse once the conversation has closed."""
        self._explore_after_conversation = "requested by voice"

    def _make_agent(self, on_event: Any, ask_user: Any) -> ComputerAgent:
        agent = ComputerAgent(make_response_creator(), config=self._agent_cfg, on_event=on_event, ask_user=ask_user)
        self._active_agent = agent
        return agent

    async def _cancel_active_task(self, reason: str) -> None:
        """Stop a running desktop task with a reason the owner can read (log + caption),
        instead of letting a restart or a touch kill it silently (bench 2026-09-06 09:45)."""
        agent = getattr(self, "_active_agent", None)
        if agent is None or not agent.running:
            return
        log.warning("agent: cancelling the running task — %s", reason)
        agent.cancel(reason=reason)
        if self.ble.connected:
            await self.ble.send({"cmd": "caption", "lines": [f"stopped: {reason}"[:17]], "page": 0, "of": 1,
                                 "hold_ms": 4000, "final": True, "chirp": False})

    def _on_caption(self, msg: dict) -> None:
        """One page of buddy's reply (or a clear) onto the robot's screen; the pager owns the timing."""
        if self.ble.connected:
            asyncio.create_task(self.ble.send(msg))

    def _on_agent_state(self, state: str) -> None:
        """Mirror the conversation/task phase on the board."""
        if state == self._agent_state:
            return
        self._agent_state = state
        self._note_activity()
        log.info("agent: %s", state)
        if self.ble.connected:
            asyncio.create_task(self.ble.send({"cmd": "agent", "state": state}))

    # ---- idle explorer ----

    def _note_activity(self) -> None:
        """Something happened: a hook event, a board touch, the listen key."""
        self._last_activity_at = time.monotonic()

    def _idle_secs(self, now: float) -> float:
        """Seconds since the last activity. A running or waiting session is
        activity in itself, so the clock does not start until it is gone."""
        if self.state.running_count or self.state.waiting_count:
            self._last_activity_at = now
            return 0.0
        return max(0.0, now - self._last_activity_at)

    async def _explore_loop(self) -> None:
        while not self._shutdown.is_set():
            try:
                await asyncio.wait_for(self._shutdown.wait(), timeout=1.0)
                return
            except asyncio.TimeoutError:
                pass
            try:
                await self._explore_step(time.monotonic())
            except Exception:  # noqa: BLE001
                log.exception("explore: step failed")

    async def _explore_step(self, now: float) -> None:
        """One tick: gather the facts, run the schedule, execute its actions."""
        frame = None
        if self._explorer.wants_frame(now) and self._explore_raw_frame is not None:
            try:
                frame = decode_frame(self._explore_raw_frame)
            except ValueError:
                frame = None
            self._explore_raw_frame = None
        actions = self._explorer.tick(
            now,
            idle_secs=self._idle_secs(now),
            card_pending=self.state.pending_count > 0,
            listening=bool(self._listen_sent),
            frame=frame,
            connected=self.ble.connected,
        )
        for action in actions:
            await self._run_explore_action(action)

    async def _run_explore_action(self, action: Action) -> None:
        if isinstance(action, Mode):
            if action.explore:
                log.info("explore: start (%s)", action.reason)
            else:
                log.info("explore: stop (%s)", action.reason)
                self._explore_raw_frame = None
            if self.ble.connected:
                await self.ble.send(build_mode_cmd(action.explore))
        elif isinstance(action, Look):
            log.info("explore: look yaw=%+d pitch=%d", action.yaw, action.pitch)
            if self.ble.connected:
                await self.ble.send(build_look_cmd(action.yaw, action.pitch, action.hold_ms))
        elif isinstance(action, Note):
            if self._notes is not None:
                asyncio.create_task(self._notes.take(action))
        elif isinstance(action, Rest):
            # Nothing over the wire: the board stays in explore mode and
            # looks around on its own until the next pan cycle.
            log.info("explore: rest (%s)", action.reason)
            self._explore_raw_frame = None

    def _show_thought(self, thought: Thought) -> None:
        """Put a thought buddy just had on its own screen, if it earns it.

        The conversation owns the screen while one is open, and a pending
        permission card outranks anything buddy has to say to itself; in either
        case the thought is skipped rather than queued, because by the time the
        screen frees up buddy is looking somewhere else. Everything that gets
        past those is then judged on its own merits by thought_screen.py.
        """
        if not self._explorer.on_board or not self.ble.connected:
            return
        if self.state.pending_count > 0 or self._listen_down:
            return
        if self._conversation is not None and not self._conversation.done():
            return
        now = time.monotonic()
        text = " ".join(thought.text.split())
        judged = dict(importance=thought.importance, novelty=thought.novelty,
                      cool=1.0 if thought.photographed else thought.cool)
        why = self._screen.refusal(now, text, thought.tags, **judged)
        if why is not None:
            self._screen.refused += 1
            log.info("explore: thought kept to itself (%s) — %s", why, text[:50])
            return
        self._screen.offer(now, text, thought.tags, **judged)
        if thought.photographed:
            text = f"{text} [photo]"
        self._thought_pager.begin_reply(now)
        self._thought_pager.update(now, text, True)
        self._flush_thought_pager()
        log.info("explore: thought on screen — %s", text[:70])

    def _flush_thought_pager(self) -> None:
        for event in self._thought_pager.poll(time.monotonic()):
            self._on_caption(event.to_wire())

    async def _thought_caption_loop(self) -> None:
        """Turn the pages of whatever thought is on screen, and take it down
        the moment buddy stops exploring."""
        while not self._shutdown.is_set():
            try:
                await asyncio.wait_for(self._shutdown.wait(), timeout=0.1)
                return
            except asyncio.TimeoutError:
                pass
            try:
                if self._thought_pager.busy and not self._explorer.on_board:
                    self._clear_thought()
                else:
                    self._flush_thought_pager()
            except Exception:  # noqa: BLE001
                log.exception("thought captions: step failed")

    def _clear_thought(self) -> None:
        for event in self._thought_pager.reset(time.monotonic()):
            self._on_caption(event.to_wire())

    async def _send_emote(self, e: Emote) -> None:
        """The diary's appraisal of what the camera saw → the board's mood engine."""
        if self.ble.connected:
            await self.ble.send(build_emote_cmd(e))

    SNAP_TIMEOUT_SECS = 3.0

    async def _take_snapshot(self) -> Optional[Frame]:
        """The diary wants a photo: ask the board for one full-size frame and
        wait for it. None when the board is away, busy with another snap, or
        silent (older firmware) — the caller then keeps the stream frame."""
        if not self.ble.connected or (self._snap_waiter is not None and not self._snap_waiter.done()):
            return None
        loop = asyncio.get_running_loop()
        self._snap_waiter = loop.create_future()
        try:
            await self.ble.send(build_snap_cmd())
            return await asyncio.wait_for(asyncio.shield(self._snap_waiter), timeout=self.SNAP_TIMEOUT_SECS)
        except asyncio.TimeoutError:
            log.info("diary: snap not answered in %.0f s (older firmware?) — keeping the stream frame",
                     self.SNAP_TIMEOUT_SECS)
            return None
        except ValueError as e:
            log.warning("diary: snap frame undecodable: %s", e)
            return None
        finally:
            self._snap_waiter = None

    async def _request_explore(self, reason: str) -> None:
        """The owner asked (CLI or voice): start a manual explore now.
        Raises ExploreRefused when a card, the listen key or a disconnect
        stands in the way."""
        actions = self._explorer.request(
            time.monotonic(), reason,
            card_pending=self.state.pending_count > 0,
            listening=bool(self._listen_sent),
            connected=self.ble.connected,
        )
        for action in actions:
            await self._run_explore_action(action)

    async def _dismiss_explore(self, reason: str) -> None:
        """End an explore now (a touch, a wake word, `explore stop`)."""
        self._clear_thought()
        for action in self._explorer.dismiss(reason):
            await self._run_explore_action(action)

    async def _stop_explore(self, reason: str) -> None:
        """Shutdown path: leave explore mode on the board if we put it there."""
        await self._dismiss_explore(reason)
        if self._notes is not None:
            self._notes.stop()

    # ---- listen key ----

    def _on_listen_key(self, on: bool) -> None:
        """Debounced edge from listen_key.py, delivered on the loop thread.
        Dedupe here, synchronously, so two fast edges cannot both read the
        same last-sent state and put duplicates on the wire."""
        log.info("listen key: %s", "down" if on else "up")
        self._listen_down = on
        self._note_activity()
        if not self.ble.connected or on == self._listen_sent:
            return
        self._listen_sent = on
        asyncio.create_task(self.ble.send({"cmd": "listen", "on": on}))

    async def _reset_listen(self) -> None:
        """Board (re)connected or rebooted: tell it the key is up. Its listen
        state lives in RAM, and a reboot mid-hold would otherwise leave the
        pose stuck until the next key press."""
        await self.ble.send({"cmd": "listen", "on": False})
        self._listen_sent = False

    async def _update_check_loop(self) -> None:
        """Poll GitHub releases once at startup, then every 24 hours.

        Network calls happen on a thread so the asyncio loop isn't blocked by
        urllib's sync IO. Failures are logged at DEBUG and we just retry next
        cycle — never raise, never crash the daemon.
        """
        INTERVAL = 24 * 3600
        # First check is on startup (uses cache if fresh) so the hud has a
        # value to display immediately.
        first = True
        while not self._shutdown.is_set():
            info = await asyncio.to_thread(version_check, force=not first)
            self._update_available = info.latest if info.has_update else None
            if info.has_update and first:
                log.info(
                    "update available: %s → %s (you're on the latest cached info; "
                    "see https://github.com/SnowWarri0r/cc-buddy-bridge/releases)",
                    info.current, info.latest,
                )
            first = False
            try:
                await asyncio.wait_for(self._shutdown.wait(), timeout=INTERVAL)
                return
            except asyncio.TimeoutError:
                pass

    # ---- IPC handler ----

    async def _handle_ipc(self, req: dict[str, Any]) -> dict[str, Any]:
        evt = req.get("evt")
        # Drop pretooluse from the trace: it has its own dedicated INFO log,
        # and the volume would drown out everything else. get_state is the
        # hud polling — also too chatty to be useful here.
        if evt not in ("pretooluse", "get_state"):
            log.info("ipc evt=%r session=%s", evt, (req.get("session_id") or "?")[:8])
        # Every hook event is activity for the idle explorer, except the
        # polls that fire on their own (statusline, diag watch).
        if evt not in ("get_state", "diag", "explore"):
            self._note_activity()

        if evt == "explore":
            # `cc-buddy-bridge explore [start|stop|status]`: the owner sends
            # the robot off to look around (or calls it back) by hand.
            action = str(req.get("action") or "start")
            if action == "start":
                try:
                    await self._request_explore("requested by cli")
                except ExploreRefused as e:
                    return {"ok": False, "error": str(e), "connected": self.ble.connected,
                            "explore": self._explorer.status()}
            elif action == "stop":
                self._note_activity()   # the human is here
                await self._dismiss_explore("requested by cli")
            elif action != "status":
                return {"ok": False, "error": f"unknown explore action: {action!r}"}
            return {"ok": True, "connected": self.ble.connected, "explore": self._explorer.status()}

        if evt == "celebrate":
            # Host-triggered celebration. Reuses the same `completed: true`
            # heartbeat flag the pet already celebrates on, so no firmware
            # support is needed — the board cannot tell this from a finished
            # Claude turn, which is exactly the point.
            secs = float(req.get("secs") or 5.0)
            self.state.pulse_completed(secs)
            await self._push_heartbeat(force=True)
            log.info("celebrate: pulsed for %.0fs", secs)
            return {"ok": True, "connected": self.ble.connected}

        if evt == "species":
            # Replaces the retired on-device menu. Firmware persists the index
            # in NVS, so this survives reboots.
            idx = int(req.get("idx") or 0)
            if self.ble.connected:
                await self.ble.send({"cmd": "species", "idx": idx})
                log.info("species: set to index %d", idx)
            return {"ok": True, "connected": self.ble.connected}

        if evt == "diag":
            # `cc-buddy-bridge diag`: ask the board for a fresh report, then
            # return the last one we hold. The board's reply arrives
            # asynchronously over serial, so a request now shows up in the
            # NEXT call — hence returning both.
            if self.ble.connected:
                await self.ble.send({"cmd": "diag"})
            return {"ok": True, "connected": self.ble.connected,
                    "diag": self._last_diag}
        if evt == "identity":
            # `cc-buddy-bridge identity status|reset`. The daemon owns the
            # in-memory prints, so a reset must go through it while it runs.
            return self._handle_identity(str(req.get("action") or "status"))
        if evt == "session_start":
            self.state.session_start(
                req["session_id"],
                transcript_path=req.get("transcript_path"),
                cwd=req.get("cwd"),
            )
            await self._push_heartbeat()
            return {"ok": True}

        if evt == "session_end":
            self.state.session_end(req["session_id"])
            await self._push_heartbeat()
            return {"ok": True}

        if evt == "turn_begin":
            session_id = req["session_id"]
            # A new user prompt cancels any pending deferred turn_end.
            pending = self._pending_turn_ends.pop(session_id, None)
            if pending is not None and not pending.done():
                pending.cancel()
            # Also kill any active celebrate pulse — user moved on.
            self.state.completed_until = 0.0
            self.state.session_start(session_id)  # idempotent
            self.state.turn_begin(session_id)
            prompt = req.get("prompt")
            if isinstance(prompt, str) and prompt:
                self.state.add_entry(f"> {truncate_utf8_bytes(prompt, _ENTRY_PAYLOAD_MAX_BYTES)}")
            await self._push_heartbeat()
            return {"ok": True}

        if evt == "turn_end":
            # Don't flip running→0 immediately; the firmware enters clock mode
            # as soon as running+waiting both hit zero, which blanks the
            # transcript HUD before the user has a chance to read the entry
            # we just added. Schedule the flip 15s out — long enough to read,
            # short enough that idle really does clock. A new turn_begin
            # cancels the scheduled task.
            session_id = req["session_id"]
            previous = self._pending_turn_ends.get(session_id)
            if previous is not None and not previous.done():
                previous.cancel()
            self._pending_turn_ends[session_id] = asyncio.create_task(
                self._deferred_turn_end(session_id, delay=15.0)
            )
            # Trigger the firmware's celebrate animation for a few seconds.
            # Set the pulse state synchronously so the heartbeat snapshot is
            # correct before anything is pushed.
            CELEBRATE_SECS = 5.0
            self.state.pulse_completed(duration_secs=CELEBRATE_SECS)
            # Kick off the BLE push in the background so this coroutine can
            # return {"ok": True} immediately — the Stop hook caller must not
            # block on _push_heartbeat(force=True) or it surfaces as ETIMEDOUT
            # in the plugin's spawnSync call.
            asyncio.create_task(self._turn_end_side_effects(CELEBRATE_SECS))
            return {"ok": True}

        if evt == "pretooluse":
            return await self._handle_pretooluse(req)

        if evt == "push_character":
            path = req.get("path")
            if not isinstance(path, str) or not path:
                return {"ok": False, "error": "missing 'path'"}
            if not self.ble.connected:
                return {"ok": False, "error": "ble not connected"}
            try:
                from .folder_push import push_character
                result = await push_character(self, path)
            except Exception as e:  # noqa: BLE001
                log.exception("push_character failed")
                return {"ok": False, "error": f"{type(e).__name__}: {e}"}
            return {"ok": True, **result}

        if evt == "unpair":
            # Tell the stick to erase its stored bond so the next pairing
            # shows a fresh passkey (REFERENCE.md §Security and pairing).
            # Macos side still needs a manual 'Forget' from System Settings.
            if not self.ble.connected:
                return {"ok": False, "error": "ble not connected"}
            ok = await self.ble.send({"cmd": "unpair"})
            log.info("unpair: sent cmd:unpair to stick (ble write %s)",
                     "ok" if ok else "fail")
            return {"ok": bool(ok)}

        if evt == "get_state":
            # Queried by the `cc-buddy-bridge hud` subcommand (or anyone else
            # who wants a one-shot snapshot). Kept small on purpose.
            pending = self.state.first_pending()
            return {
                "ok": True,
                "state": {
                    "ble_connected": self.ble.connected,
                    "sec": self._last_stick_sec,
                    "battery_pct": self._last_stick_battery_pct,
                    "total": self.state.total,
                    "running": self.state.running_count,
                    "waiting": self.state.waiting_count,
                    "tokens_cumulative": self.state.tokens_cumulative,
                    "tokens_today": self.state.tokens_today,
                    "cost_cumulative": self.state.cost_cumulative,
                    "cost_today": self.state.cost_today,
                    "update_available": self._update_available,
                    "pending_tool": pending.tool_name if pending else None,
                    "last_entry": self.state.entries[0].text if self.state.entries else "",
                },
            }

        if evt == "posttooluse":
            # Clear any lingering pending (defensive; normally cleared in _handle_pretooluse).
            self.state.permission_resolved(req.get("tool_use_id", ""))
            # A tool ran → any terminal-side permission prompt was answered.
            self.state.input_received(req.get("session_id", ""))
            tool_name = req.get("tool_name")
            if isinstance(tool_name, str):
                self.state.add_entry(f"+ {tool_name}")
                self._ensure_session(req)
                self.state.note_tool(req.get("session_id", ""), tool_name)
            await self._push_heartbeat()
            return {"ok": True}

        if evt == "notification":
            # Claude is blocked on the user (permission prompt / waiting for
            # input). Mark the session so heartbeats carry waiting>0 — the
            # firmware's attention animation + LED pulse.
            self.state.needs_input(req.get("session_id", ""))
            msg = req.get("message")
            if isinstance(msg, str) and msg.strip():
                self.state.add_entry(f"! {msg.strip()}")
            log.info("notification: session=%s type=%s → attention",
                     (req.get("session_id") or "?")[:8],
                     req.get("notification_type") or "?")
            await self._push_heartbeat()
            return {"ok": True}

        return {"ok": False, "error": f"unknown evt: {evt!r}"}

    async def _handle_pretooluse(self, req: dict[str, Any]) -> dict[str, Any]:
        tool_use_id = req.get("tool_use_id")
        if not isinstance(tool_use_id, str) or not tool_use_id:
            return {"ok": False, "error": "missing tool_use_id"}
        session_id = req.get("session_id") or "unknown"
        tool_name = req.get("tool_name") or "tool"
        hint = req.get("hint") or ""
        self._ensure_session(req)
        self.state.note_tool(session_id, tool_name)

        # Read tool takes its own path: out-of-cwd reads card on the stick and
        # an approval grants the enclosing repo/dir. See read_policy.py.
        if tool_name == "Read":
            return await self._handle_read_pretooluse(req, tool_use_id, session_id, hint)

        # Smart matcher: classify trivial / risky commands before the BLE round-trip.
        # auto_allow → approve immediately, no stick prompt (keeps ls/cat fast).
        # always_ask → force stick prompt even if Claude Code would auto-approve.
        # default    → no decision, let Claude Code's native permission flow run.
        decision_class = classify_command(hint, self.matchers)
        audit_kwargs = dict(
            session_id=session_id, tool_name=tool_name, hint=hint, matcher=decision_class,
        )
        if decision_class == "allow":
            log.info("pretooluse for %s (%s): auto_allow match → allow", tool_name, hint[:60])
            self.audit.record(**audit_kwargs, decision="allow", source="auto_allow")
            return {"ok": True, "decision": "allow"}

        # If BLE isn't connected, skip the round-trip and return no decision so
        # Claude Code's normal flow runs (respects user's auto/allow settings).
        if not self.ble.connected:
            # "stick", not "ble": self.ble duck-types over serial too, and the
            # 2026-08-06 log read as a BLE problem when the board was simply
            # off the USB bus.
            log.info("pretooluse for %s: stick not connected, deferring to default flow", tool_name)
            self.audit.record(**audit_kwargs, decision=None, source="ble_disconnected")
            return {"ok": True}

        # The board has no decision surface any more — the swipe card is
        # gone, so nothing can answer a prompt. Always defer to Claude Code's
        # own permission flow rather than block the tool call waiting on a
        # button that no longer exists. Placed after auto_allow so the matcher
        # fast paths keep working.
        log.info("pretooluse for %s (%s): no card → defer to default", tool_name, hint[:60])
        self.audit.record(**audit_kwargs, decision=None, source="defer")
        return {"ok": True}

    def _ensure_session(self, req: dict[str, Any]) -> None:
        """Register the session behind a hook event if we've never seen it.

        SessionStart is the only hook that normally creates a session, so a
        daemon restart leaves every *already-running* session invisible until
        the human restarts it too — the monitor then shows an empty agent list
        while agents are plainly working. Tool hooks carry both ids we need,
        and session_start is create-if-absent, so adopting the session here is
        free and makes the list self-heal within one tool call.
        """
        session_id = req.get("session_id")
        if not (isinstance(session_id, str) and session_id):
            return
        cwd = req.get("cwd") or None
        self.state.session_start(session_id, cwd=cwd)
        # session_start is create-if-absent, so it will NOT fill in a cwd it
        # missed. posttooluse doesn't carry one, so whichever hook happens to
        # fire first decides the row's name forever — and losing the race
        # leaves the agent labelled with a session-id prefix instead of its
        # repo. Backfill the first cwd we actually see.
        sess = self.state.sessions.get(session_id)
        if sess is not None and cwd and not sess.cwd:
            sess.cwd = cwd

    async def _handle_read_pretooluse(
        self, req: dict[str, Any], tool_use_id: str, session_id: str, hint: str,
    ) -> dict[str, Any]:
        """Read-tool prompts: card out-of-cwd reads; approval grants the whole
        enclosing scope (git repo or parent dir) for the daemon's lifetime.
        See read_policy.py for why scope-not-file is the point."""
        path = hint
        cwd = req.get("cwd") or ""
        audit_kwargs = dict(
            session_id=session_id, tool_name="Read", hint=path, matcher="read",
        )
        # In-cwd reads never prompt anywhere; defer without noise.
        if not path or is_within(path, cwd):
            return {"ok": True}
        scope = read_scope(path)
        if scope is not None and scope in self._read_scopes:
            log.info("read under approved scope %s → allow (%s)", scope, path)
            self.audit.record(**audit_kwargs, decision="allow", source="read_scope")
            return {"ok": True, "decision": "allow"}
        if not self.ble.connected:
            self.audit.record(**audit_kwargs, decision=None, source="ble_disconnected")
            return {"ok": True}
        # No card to show it on — defer to Claude Code's own flow.
        self.audit.record(**audit_kwargs, decision=None, source="defer")
        return {"ok": True}

    # ---- BLE handler ----

    async def _handle_ble(self, obj: dict[str, Any]) -> None:
        frame = obj.get("frame")
        if isinstance(frame, dict):
            if frame.get("snap") is True:
                # The photo we asked for: hand it to the waiting diary, never
                # to the face tracker (it is 320x240, and it is not a stream
                # frame). An unasked-for snap is dropped.
                waiter, self._snap_waiter = self._snap_waiter, None
                if waiter is not None and not waiter.done():
                    try:
                        waiter.set_result(decode_frame(frame))
                    except ValueError as e:
                        waiter.set_exception(e)
                return
            # Camera frame: hottest object on the link (5/s). Hand it to the
            # tracker, which parks or runs it and replies with the face cmd.
            # While exploring, also keep the newest one for the next waypoint
            # sample (the explore loop decodes it, once, when it is due).
            if self._explorer.active:
                self._explore_raw_frame = frame
            await self._vision.on_frame(frame)
            return
        diag = obj.get("diag")
        if isinstance(diag, dict):
            # Crash/hang report from the board (see firmware diag.h). Logged at
            # WARNING when the previous run died abnormally, because that line
            # is the whole point: it says what the board was doing when it
            # froze, which nothing else on this link can tell us.
            reset = diag.get("reset", "?")
            abnormal = reset in ("PANIC", "TASK-WATCHDOG", "INT-WATCHDOG",
                                 "BROWNOUT", "other-watchdog")
            self._last_diag = diag
            log.log(
                logging.WARNING if abnormal else logging.INFO,
                "board diag: boot #%s after %s (up=%ss heap=%s min=%s psram=%s)",
                diag.get("boot"), reset, diag.get("up"),
                diag.get("heap"), diag.get("minheap"), diag.get("psram"),
            )
            # The single most valuable field after a watchdog reset: which
            # loop() phase never returned. The firmware has always sent it;
            # dropping it here is why a week of TASK-WATCHDOG resets never
            # named the hanging call.
            if "diedIn" in diag:
                log.log(
                    logging.WARNING if abnormal else logging.INFO,
                    "  died in: %s (entered %sms, %s loops)",
                    diag.get("diedIn"), diag.get("diedAtMs"), diag.get("loops"),
                )
            for ev in diag.get("last") or []:
                log.log(logging.WARNING if abnormal else logging.INFO,
                        "  pre-reset event: %s", ev)
            return
        cmd = obj.get("cmd")
        if cmd == "explore":
            # Two quick taps on the screen: come back. The board only sends
            # this while it is exploring, so it always means stop.
            self._note_activity()
            log.info("explore: called back by a double tap")
            await self._dismiss_explore("double tap")
            return
        if cmd in ("focus", "key"):
            # A touch on the board: the human is here, stop exploring — at
            # once, and even a manual explore, which ignores the idle clock.
            self._note_activity()
            await self._dismiss_explore(f"touch ({cmd})")
            # ... and a touch while buddy is in a conversation is "hush": the
            # conversation (and any task it is running) ends at once.
            if self._conversation is not None and not self._conversation.done():
                log.info("ears: hushed by a touch (%s)", cmd)
                await self._cancel_active_task("hushed by a touch")
                self._conversation.cancel()
        if cmd == "focus":
            # A tap on the pet in attention state: raise the terminal of the
            # session that is waiting on the human (oldest pending permission,
            # else newest needs-input session).
            from .focus_terminal import focus_session_terminal
            cwd = self.state.attention_cwd()
            log.info("focus: requested for %r", cwd or "(unknown session)")
            asyncio.create_task(focus_session_terminal(cwd))
            return
        if cmd == "key":
            # Swipe-down on the pet → Enter on the host.
            if self._keys is None:
                from .key_tap import KeyTapper
                self._keys = KeyTapper()
            self._keys.tap(str(obj.get("name") or ""))
            return
        # Status acks come back from the device after we poll with {"cmd":"status"}.
        # Shape per REFERENCE.md: {"ack":"status","ok":true,"data":{"name","sec","bat":{...},"sys":{...},"stats":{...}}}.
        ack = obj.get("ack")
        if ack == "status":
            # A status reply proves the write path works RIGHT NOW — but the
            # observed wedge pattern is flapping: a fresh reopen restores TX
            # for one exchange (63-183s) and then goes deaf again. Zeroing the
            # escalation on any single ack meant pulse_reset could never fire.
            # Only a streak of clean poll cycles (counted in the poller)
            # de-escalates; here we just mark the poll answered.
            self._status_sent_at = None
            self._status_missed = 0
            self._clean_polls += 1
            if self._ack_escalation and self._clean_polls >= 2:
                log.info("status acks stable for %d clean polls — "
                         "resetting ack escalation (was %d)",
                         self._clean_polls, self._ack_escalation)
                self._ack_escalation = 0
        if ack == "status" and obj.get("ok"):
            data = obj.get("data") or {}
            sec = data.get("sec")
            if sec is not None and sec != self._last_stick_sec:
                log.info(
                    "stick link: %s (was %s)",
                    "ENCRYPTED" if sec else "UNENCRYPTED — transcript sniffable!",
                    self._last_stick_sec,
                )
                self._last_stick_sec = bool(sec)
            bat = data.get("bat") or {}
            if isinstance(bat, dict) and bat:
                pct = bat.get("pct")
                ma = bat.get("mA")
                if isinstance(pct, int) and pct != self._last_stick_battery_pct:
                    charging = "+" if isinstance(ma, int) and ma < 0 else " "
                    log.info("stick battery: %d%% %s", pct, charging)
                    self._last_stick_battery_pct = pct
            sys_info = data.get("sys") or {}
            if isinstance(sys_info, dict):
                free = sys_info.get("fsFree")
                total = sys_info.get("fsTotal")
                if isinstance(free, int) and isinstance(total, int):
                    if total == 0:
                        # LittleFS isn't mounted. Firmware calls begin(false),
                        # so an un-formatted partition reports 0/0. push-character
                        # will fail with "have 0K" until the user factory-resets
                        # the stick (hold A → settings → reset → factory reset),
                        # which runs LittleFS.format().
                        log.error(
                            "stick LittleFS appears unformatted (fsTotal=0). "
                            "Run factory reset on the stick to format it; "
                            "push-character will reject until then."
                        )
                    else:
                        log.info("stick fs: %d/%d bytes free (%.0f%%)",
                                 free, total, 100.0 * free / total)
            return

        # Route any ack (status already handled above, but others — char_begin,
        # file, chunk, file_end, char_end, name, owner, unpair, etc.) to the
        # oldest waiter that registered for that ack type.
        if ack is not None:
            for waiter_type, fut in self._ack_waiters:
                if waiter_type == ack and not fut.done():
                    fut.set_result(obj)
                    break
            return

        if cmd in {"name", "owner", "unpair", "char_begin", "char_end", "file", "file_end", "chunk"}:
            # We're the central; we don't send these, but acknowledge defensively.
            return

        if obj.get("ack") is not None:
            return  # device acknowledging something we sent

        log.debug("ble: unhandled %r", obj)

    # ---- JSONL callback ----

    async def _on_tokens(
        self,
        cumulative: int,
        today: int,
        cost_cumulative: float,
        cost_today: float,
        _entries: list,
    ) -> None:
        self.state.set_tokens(cumulative, today, cost_cumulative, cost_today)
        await self._push_heartbeat()

    async def _on_assistant_text(self, _transcript_path: str, text: str, _uuid: str) -> None:
        """Fired by the JSONL tailer the moment a new assistant text record
        lands on disk (typically <500 ms after Claude Code finishes the
        message). Emitting here beats the Stop hook, so the stick receives
        the '@ ...' entry while the user is still looking at the terminal —
        before auto-off kicks in."""
        self.state.add_entry(f"@ {truncate_utf8_bytes(text, _ENTRY_PAYLOAD_MAX_BYTES)}")
        log.info("tailer: new assistant text → entry added (state.entries=%d)",
                 len(self.state.entries))
        await self._push_heartbeat(force=True)

    async def _turn_end_side_effects(self, celebrate_secs: float) -> None:
        """Post-response work for a turn_end event, run as a background task.

        Runs *after* the IPC ``{"ok": True}`` reply has been sent, so the Stop
        hook's spawnSync call never waits on a BLE write.

        1. Force-push the heartbeat carrying ``completed=true`` so the stick's
           celebrate animation fires within ~50 ms of the turn ending.
        2. Schedule a follow-up push at pulse end so ``completed`` flips back
           to false on time rather than waiting for the next keepalive.
        """
        try:
            await self._push_heartbeat(force=True)
        except Exception:  # noqa: BLE001
            log.exception("turn_end side effects: _push_heartbeat(force=True) failed")
        asyncio.create_task(self._heartbeat_after(celebrate_secs + 0.1))

    async def _heartbeat_after(self, delay: float) -> None:
        """Schedule one heartbeat push after ``delay`` seconds. Used by the
        celebrate-pulse logic to flush the completed=false transition right
        when the pulse expires, instead of waiting for the next keepalive."""
        try:
            await asyncio.sleep(delay)
            await self._push_heartbeat(force=True)
        except asyncio.CancelledError:
            return

    async def _deferred_turn_end(self, session_id: str, delay: float) -> None:
        """Delay the state.turn_end so that running>0 keeps the firmware out of
        clock mode long enough to render the @-entry the tailer just pushed."""
        try:
            await asyncio.sleep(delay)
            self.state.turn_end(session_id)
            await self._push_heartbeat()
        except asyncio.CancelledError:
            return
        finally:
            # Self-cleanup. If a new turn_begin already replaced the task, the
            # pop returns that task instead — ignore the mismatch.
            current = self._pending_turn_ends.get(session_id)
            if current is not None and current.done():
                self._pending_turn_ends.pop(session_id, None)

    # ---- turn event ----

    async def wait_for_ack(self, ack_type: str, timeout: float = 5.0) -> dict[str, Any]:
        """Block until we receive an ack matching ``ack_type``. Used by the
        folder-push flow — the firmware requires a per-chunk ack before we
        send the next chunk, since its UART RX buffer is only ~256 bytes."""
        fut: asyncio.Future[dict[str, Any]] = asyncio.get_running_loop().create_future()
        entry = (ack_type, fut)
        self._ack_waiters.append(entry)
        try:
            return await asyncio.wait_for(fut, timeout=timeout)
        finally:
            try:
                self._ack_waiters.remove(entry)
            except ValueError:
                pass

    async def _emit_turn_event(self, transcript_path: str) -> None:
        """On turn_end: mirror the latest assistant text into the heartbeat's
        ``entries`` list so the stick's transcript view shows it.

        The reference firmware silently drops {"evt":"turn"} events (its JSON
        parser only reads heartbeat fields), so the only thing that actually
        shows up for the user is the synthetic entry we add below.

        Polls for fresh content: Claude Code flushes assistant records to the
        transcript JSONL *after* the Stop hook fires, so a naive read grabs
        the PREVIOUS turn's content. We hash what we read and compare to the
        last content we emitted; if unchanged, wait 200 ms and retry, up to
        ~1.2 s total before giving up.
        """
        if not self.ble.connected:
            return

        # Claude Code's transcript writes are async w.r.t. the Stop hook — the
        # hook fires before the final assistant record hits disk. Sleep a beat
        # so our first read sees the just-finished turn; then poll for up to
        # another ~1.2s if that wasn't enough (e.g., long response still being
        # serialized). Dedupe by content hash so we never re-emit the same turn.
        await asyncio.sleep(1.0)

        import hashlib
        import json as _json
        last_key = self._last_emitted_turn_key.get(transcript_path)
        content: list | None = None
        content_key: str | None = None
        for attempt in range(6):
            if attempt > 0:
                await asyncio.sleep(0.2)
            try:
                self.jsonl._process_file(transcript_path)
            except Exception:  # noqa: BLE001
                log.debug("turn event: process_file failed", exc_info=True)
            candidate = self.jsonl.last_assistant_content(transcript_path)
            if not candidate:
                continue
            key = hashlib.md5(
                _json.dumps(candidate, sort_keys=True, ensure_ascii=False).encode("utf-8")
            ).hexdigest()
            if key != last_key:
                content = candidate
                content_key = key
                break
            log.debug("turn event: transcript content unchanged, retrying (attempt %d)", attempt + 1)

        if not content:
            log.info("turn end: no fresh content after 1s warmup + 1.2s polling")
            return

        text = _first_text_block(content)
        if text:
            log.info("turn end: adding entry '@ %s...' (state.entries len before=%d)",
                     text[:30], len(self.state.entries))
            self.state.add_entry(f"@ {truncate_utf8_bytes(text, _ENTRY_PAYLOAD_MAX_BYTES)}")
            await self._push_heartbeat(force=True)
            if content_key is not None:
                self._last_emitted_turn_key[transcript_path] = content_key
        else:
            log.info("turn end: content found but no text block, skipping entry add")


def _first_text_block(content: list) -> str:
    """Pull the first text block out of an SDK content array. Returns '' if
    the turn was purely tool_use / tool_result (no natural-language reply)."""
    for block in content:
        if not isinstance(block, dict):
            continue
        if block.get("type") == "text":
            text = block.get("text")
            if isinstance(text, str) and text.strip():
                return text.strip()
    return ""


def _log_permission_config_summary(matchers: MatcherConfig) -> None:
    """One-shot log at startup: how does the matcher interact with Claude Code's
    own permissions config? Flags the two most confusing misalignments:

    1. defaultMode == 'bypassPermissions' AND matcher is non-strict — the stick
       only gates always_ask patterns; everything else is silently bypassed.
    2. matcher.strict but defaultMode unsuitable — strict mode wants
       bypassPermissions, otherwise unmatched commands still go through Claude
       Code's normal prompt UI.
    """
    import json

    from .claude_home import claude_config_dirs
    # The daemon serves every config home it was pointed at, so summarize the
    # first one that actually exists rather than assuming ~/.claude.
    candidates = [d / "settings.json" for d in claude_config_dirs()]
    settings_path = next((p for p in candidates if p.exists()), candidates[0])
    default_mode: Optional[str] = None
    ask_count = 0
    if settings_path.exists():
        try:
            with settings_path.open() as f:
                data = json.load(f)
            perms = data.get("permissions") or {}
            default_mode = perms.get("defaultMode")
            ask_count = len(perms.get("ask") or [])
        except (OSError, ValueError) as e:
            log.debug("could not read settings.json for permission summary: %s", e)

    matcher_summary = (
        f"matcher: strict={matchers.strict} "
        f"auto_allow={len(matchers.auto_allow)} "
        f"always_ask={len(matchers.always_ask)}"
    )
    log.info("%s", matcher_summary)
    log.info(
        "settings.json (%s): permissions.defaultMode=%r ask=%d",
        settings_path, default_mode or "(unset)", ask_count,
    )

    if default_mode == "bypassPermissions" and not matchers.strict:
        log.warning(
            "permissions.defaultMode='bypassPermissions' + matcher.strict=false: "
            "the stick gates *only* always_ask patterns (%d defined); everything "
            "else is auto-approved without any human-in-the-loop. To put the "
            "stick in front of every un-vetted command, set `strict = true` in "
            "your matchers.toml.",
            len(matchers.always_ask),
        )
    elif matchers.strict and default_mode not in ("bypassPermissions", None):
        log.warning(
            "matcher.strict=true but permissions.defaultMode=%r: unmatched "
            "commands will route to the stick AND Claude Code may still surface "
            "its own terminal prompt depending on the mode. Strict mode is "
            "designed to pair with defaultMode='bypassPermissions'.",
            default_mode,
        )
