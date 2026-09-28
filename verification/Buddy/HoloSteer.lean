/-
  Correcting and stopping a Holo desktop task (bridge/src/cc_buddy_bridge/holo_computer.py, holo_driver.py).

  The owner's "steer_task" (Telegram, voice) calls `HoloComputerAgent.steer(text)`; "stop_task" calls
  `cancel()`. The lane before 2026-09-28 was one `holo run` subprocess per task, and `holo run` takes no
  message mid-run, so `steer` returned False for every correction: the owner's words were refused while
  the task went on without them. The client driver sends a correction to the running agent-API session
  (`send_message`); the driver queues one that arrives before the session exists and delivers it as soon
  as the session id is known (holo_driver.py `Turn.announce`). The properties:

    (1) a correction given while the task runs and no stop was asked is never refused;
    (2) once a stop was asked, no result is certified: the task ends "stopped" (`cancelled` event), even
        when Holo finished the turn before the stop landed — the same rule as TaskStop.lean;
    (3) a task that reports a result has no correction still waiting: a `final` comes from the session,
        and the session's creation delivered the queue.

  Abstraction. `start` is `run()` starting the driver; `session` is the driver's `{"ev": "session"}` line;
  `steer` is `steer(text)` with non-blank text; `cancel` is `cancel()` (`_stop_asked` set before the driver
  is asked); `finalOk` is the driver's final with status completed/idle; `finalStopped` is a final with
  status cancelled/interrupted, or the driver exiting after a stop (a queued correction dies with it).
  `sent` and `lost` are ghosts: `sent` that some correction reached the session; `lost` that one was
  refused while it should have been taken. `verified` is a `final` event having been emitted.
-/
namespace Buddy.HoloSteer

inductive Phase where
  | idle
  | running
  | over
  deriving DecidableEq, Repr

inductive Event where
  | start
  | session
  | steer
  | cancel
  | finalOk
  | finalStopped
  deriving DecidableEq, Repr

structure S where
  phase : Phase := .idle
  session : Bool := false      -- the driver has said the session id (thread_id set)
  stopping : Bool := false     -- _stop_asked
  queued : Bool := false       -- a correction waits in the driver for the session
  sent : Bool := false         -- ghost: a correction reached the session
  lost : Bool := false         -- ghost: a correction was refused while the task ran and no stop was asked
  cancelled : Bool := false    -- ghost: a stop was asked while the task ran
  verified : Bool := false     -- a `final` event was emitted (the result certified)
  deriving DecidableEq, Repr

def running (s : S) : Bool := s.phase == .running

def Spec (s : S) : Bool :=
  !s.lost && (!s.cancelled || !s.verified) && (!s.verified || !s.queued)

/-! ## The cli driver (the lane before the client driver) -/

def Cur.step (s : S) : Event → S
  | .start => if s.phase == .idle then { s with phase := .running } else s
  | .session => s                                           -- `holo run` has no session to speak of
  -- holo_computer.py before: `def steer(self, text): return False`
  | .steer => if running s && !s.stopping then { s with lost := true } else s
  | .cancel => if running s then { s with stopping := true, cancelled := true } else s
  | .finalOk => if running s then { s with phase := .over, verified := !s.cancelled } else s
  | .finalStopped => if running s then { s with phase := .over } else s

def Cur.run (evs : List Event) : S := evs.foldl Cur.step {}

/-- The task runs; the owner corrects it; the correction is refused. -/
theorem current_violates :
    let s := Cur.run [.start, .steer]
    s.lost = true ∧ Spec s = false := by
  decide

/-! ## The client driver -/

def Fix.step (s : S) : Event → S
  | .start => if s.phase == .idle then { s with phase := .running } else s
  -- holo_driver.py `announce`: the id is said and the queue delivered, in that order, before anything else.
  | .session => if running s then { s with session := true, sent := s.sent || s.queued, queued := false } else s
  -- holo_computer.py `steer`: taken while `steerable` (running, not stopping); the driver sends it to the
  -- session, or queues it until the session exists.
  | .steer => if running s && !s.stopping then
      (if s.session then { s with sent := true } else { s with queued := true }) else s
  | .cancel => if running s then { s with stopping := true, cancelled := true } else s
  -- `_run_client`: `_stop_asked` is checked before the status; a completed final after a stop is "stopped".
  -- A final only comes from a session, so `session` is required.
  | .finalOk => if running s && s.session then
      { s with phase := .over, verified := !s.stopping } else s
  | .finalStopped => if running s then { s with phase := .over, queued := false } else s

def Fix.run (evs : List Event) : S := evs.foldl Fix.step {}

/-! ### Proof: an inductive invariant, checked on every state and event. -/

def Inv (s : S) : Bool :=
  Spec s &&
  (!s.queued || (s.phase == .running && !s.session)) &&
  (!s.verified || s.phase == .over) &&
  (s.stopping == s.cancelled) &&
  (s.phase != .idle || (!s.session && !s.stopping && !s.verified && !s.queued))

theorem inv_step : ∀ (s : S) (e : Event), Inv s = true → Inv (Fix.step s e) = true := by
  intro s e
  rcases s with ⟨ph, a, b, c, d, f, g, h⟩
  cases ph <;> cases a <;> cases b <;> cases c <;> cases d <;> cases f <;> cases g <;> cases h <;>
    cases e <;> decide

theorem inv_run (evs : List Event) : ∀ s : S, Inv s = true → Inv (evs.foldl Fix.step s) = true := by
  induction evs with
  | nil => intro s h; exact h
  | cons e rest ih => intro s h; exact ih _ (inv_step s e h)

/-- For every trace of starts, session announcements, corrections, stops and finals: no correction given
while the task ran is refused; a stop that was asked never lets a result be certified; a certified result
has no correction still waiting. -/
theorem fixed_invariant (evs : List Event) : Spec (Fix.run evs) = true := by
  have h := inv_run evs {} (by decide)
  have spec_of_inv : ∀ s : S, Inv s = true → Spec s = true := by
    intro s; unfold Inv; cases Spec s <;> simp
  exact spec_of_inv _ h

/-- The refused correction, on the client driver: it reaches the session. -/
theorem fixed_replays_counterexample :
    let s := Fix.run [.start, .session, .steer]
    s.sent = true ∧ s.lost = false := by
  decide

/-- A correction before the session exists is delivered when the session is announced. -/
theorem early_correction_is_delivered :
    let s := Fix.run [.start, .steer, .session, .finalOk]
    s.sent = true ∧ s.queued = false ∧ s.verified = true := by
  decide

/-- Holo finishes the turn after a stop was asked: the task is reported stopped, not as a result. -/
theorem stop_then_finish_is_stopped :
    let s := Fix.run [.start, .session, .cancel, .finalOk]
    s.phase = .over ∧ s.verified = false := by
  decide

end Buddy.HoloSteer
