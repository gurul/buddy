/-
  The OpenAI seam's retry on a TLS drop (bridge/src/cc_buddy_bridge/computer_agent.py,
  `make_response_creator` and `make_stream_creator`).

  On 2026-09-28 three phone-call turns died on a bare `ssl.SSLError`: a TLS read that fails after the
  handshake comes out of anyio unmapped, and the SDK neither wraps nor retries it. One drop killed the
  turn. The obvious cure, "always retry", is wrong on the streamed call: a sentence already read out to
  the owner would be read out again. The properties:

    (1) a turn fails only because the connection dropped twice, or dropped after text was read out;
    (2) no text is read out twice.

  Abstraction. `drop` is `ssl.SSLError` raised by the call; `text` is a delta handed to `on_text` (read
  out at once by the call's SentenceStream); `done` is the response completing. `attempt` is which try is
  running. `spokeFirst` is text having been read out during the first try. `duplicated` is text read out
  in a second try after text was read out in the first (the same reply, twice). `droppedTwice` and
  `droppedAfterText` are why a failure is allowed.
-/
namespace Buddy.ResponseRetry

inductive Attempt where
  | first
  | second
  deriving DecidableEq, Repr

inductive Event where
  | drop
  | text
  | done
  deriving DecidableEq, Repr

structure S where
  attempt : Attempt := .first
  spoke : Bool := false            -- any text read out so far
  spokeFirst : Bool := false       -- text read out during the first try
  failed : Bool := false
  succeeded : Bool := false
  duplicated : Bool := false       -- ghost: text read out in the second try after text in the first
  dropped : Bool := false          -- a drop happened
  droppedTwice : Bool := false     -- a second drop happened
  droppedAfterText : Bool := false -- a drop happened after text was read out
  deriving DecidableEq, Repr

def active (s : S) : Bool := !s.failed && !s.succeeded

def Spec (s : S) : Bool :=
  !s.duplicated && (!s.failed || s.droppedTwice || s.droppedAfterText)

def onText (s : S) : S :=
  { s with spoke := true, spokeFirst := s.spokeFirst || s.attempt == .first,
           duplicated := s.duplicated || (s.attempt == .second && s.spokeFirst) }

def fail (s : S) : S :=
  { s with failed := true, dropped := true, droppedTwice := s.droppedTwice || s.dropped,
           droppedAfterText := s.droppedAfterText || s.spoke }

/-! ## The code before the fix: no retry -/

def Cur.step (s : S) : Event → S
  | .drop => if active s then fail s else s
  | .text => if active s then onText s else s
  | .done => if active s then { s with succeeded := true } else s

def Cur.run (evs : List Event) : S := evs.foldl Cur.step {}

/-- One drop, before any text, and the turn is dead. -/
theorem current_violates :
    let s := Cur.run [.drop]
    s.failed = true ∧ s.droppedTwice = false ∧ s.droppedAfterText = false ∧ Spec s = false := by
  decide

/-! ## The obvious alternative: always retry once -/

def Naive.step (s : S) : Event → S
  | .drop => if active s then
      (if s.attempt == .first then { s with attempt := .second, dropped := true } else fail s) else s
  | .text => if active s then onText s else s
  | .done => if active s then { s with succeeded := true } else s

def Naive.run (evs : List Event) : S := evs.foldl Naive.step {}

/-- A sentence is read out, the connection drops, the retry reads the same sentence out again. -/
theorem naive_violates :
    let s := Naive.run [.text, .drop, .text]
    s.duplicated = true ∧ Spec s = false := by
  decide

/-! ## The fix: retry once, only while nothing has been read out -/

def Fix.step (s : S) : Event → S
  -- computer_agent.py: `if attempt == TLS_RETRIES or spoke: raise` else `continue`
  | .drop => if active s then
      (if s.attempt == .first && !s.spoke then { s with attempt := .second, dropped := true } else fail s)
      else s
  | .text => if active s then onText s else s
  | .done => if active s then { s with succeeded := true } else s

def Fix.run (evs : List Event) : S := evs.foldl Fix.step {}

def Inv (s : S) : Bool :=
  Spec s &&
  (s.attempt != .second || (s.dropped && !s.spokeFirst)) &&
  (!s.spokeFirst || s.spoke) &&
  (!s.droppedTwice || s.dropped) &&
  (!s.droppedAfterText || (s.spoke && s.dropped))

theorem inv_step : ∀ (s : S) (e : Event), Inv s = true → Inv (Fix.step s e) = true := by
  intro s e
  rcases s with ⟨at_, a, b, c, d, f, g, h, i⟩
  cases at_ <;> cases a <;> cases b <;> cases c <;> cases d <;> cases f <;> cases g <;> cases h <;>
    cases i <;> cases e <;> decide

theorem inv_run (evs : List Event) : ∀ s : S, Inv s = true → Inv (evs.foldl Fix.step s) = true := by
  induction evs with
  | nil => intro s h; exact h
  | cons e rest ih => intro s h; exact ih _ (inv_step s e h)

/-- For every sequence of drops, deltas and completions: text is never read out twice, and a turn fails
only after two drops or a drop that came after text was read out. -/
theorem fixed_invariant (evs : List Event) : Spec (Fix.run evs) = true := by
  have h := inv_run evs {} (by decide)
  have spec_of_inv : ∀ s : S, Inv s = true → Spec s = true := by
    intro s; unfold Inv; cases Spec s <;> simp
  exact spec_of_inv _ h

/-- The drop that killed the turn, on the fixed code: the retry completes it. -/
theorem fixed_replays_counterexample :
    let s := Fix.run [.drop, .text, .done]
    s.succeeded = true ∧ s.failed = false ∧ s.duplicated = false := by
  decide

/-- The naive counterexample, on the fixed code: the turn fails rather than repeat the sentence. -/
theorem fixed_never_repeats :
    let s := Fix.run [.text, .drop, .text]
    s.failed = true ∧ s.duplicated = false := by
  decide

end Buddy.ResponseRetry
