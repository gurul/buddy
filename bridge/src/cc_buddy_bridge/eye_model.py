"""Conversation-aware eye choice using the original local Laya weights.

Separate from the frozen six-label research experiment. No weights are trained
here: eleven binary questions specialize the checkpoint to eye expression.
"""

from __future__ import annotations

import time

CRITERIA = {
    "calm": "neutral facts, routine questions or instructions",
    "happy": "pleased, content or gently celebrating",
    "curious": "wondering, exploring or wanting to learn",
    "affection": "love, gratitude, friendship or reassurance",
    "surprised": "shocked or amazed by something unexpected",
    "sad": "sadness, grief, loss or disappointment",
    "worried": "fear, anxiety, concern or danger",
    "skeptical": "doubt, disbelief or questioning a claim",
    "frustrated": "annoyed or fed up with a problem",
    "excited": "very enthusiastic, thrilled or eagerly anticipating",
    "wink": "playful teasing, a joke or asking for a wink",
}
LABELS = tuple(CRITERIA)
QUESTION = {
    label: {
        "type": "noul",
        "instructions": f"Does the last message express {description}?",
        "criteria": {"false": "No", "true": "Yes"},
    }
    for label, description in CRITERIA.items()
}


class ConversationContext:
    def __init__(self):
        self.turns = {}

    def state(self, who, text, now):
        # Bounded chronological context: previous turn first, current speaker last.
        speaker = "Buddy" if who == "assistant" else "Buddy's observation" if who == "diary" else "User"
        parts = []
        for role, (at, previous) in self.turns.items():
            if now - at <= 90 and role != who:
                parts.append(f"{'User' if role == 'user' else 'Buddy'}: {previous[-400:]}")
        parts.append(f"{speaker}: {text}")
        if who in ("user", "assistant"):
            self.turns[who] = (now, text)
        return "\n".join(parts)


class LiveEyeModel:
    def __init__(self, path):
        import laya_mlx
        import mlx.core as mx

        self.agent = laya_mlx.Agent(str(path), dtype="float16", device="gpu", compile=False)
        self.agent.model.scorer.set_dtype(mx.float32)
        mx.eval(self.agent.model.scorer.parameters())

    def predict(self, state):
        import numpy as np
        from laya_mlx.agent import collate_items

        start = time.perf_counter()
        items, _ = self.agent.prepare(state, QUESTION)
        if any(len(item["ids"]) >= self.agent.cfg["max_len"] for item in items):
            raise ValueError("eye context exceeds token budget")
        logits, _ = self.agent.forward(collate_items(items, self.agent.tok.pad_token_id))
        logits = np.asarray(logits).astype(np.float64)
        scores = logits[:, 1] - logits[:, 0]
        if not np.isfinite(scores).all():
            raise ValueError("nonfinite eye scores")
        p = np.exp(scores - scores.max())
        p /= p.sum()
        return {
            "label": LABELS[int(p.argmax())],
            "probabilities": p.tolist(),
            "ms": (time.perf_counter() - start) * 1000,
        }
