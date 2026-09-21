"""Explainable heuristic points; scores are not calibrated probabilities."""
from __future__ import annotations

from dataclasses import dataclass, field

WEIGHTS = {'object_near_hand': 10, 'merchandise_interaction': 15,
           'object_disappearance': 20, 'hand_to_waist': 20,
           'concealment_candidate': 40, 'restricted_zone_interaction': 35,
           'excessive_dwell': 10, 'exit_zone_entry': 15,
           'specialized_model_activity': 70, 'unusual_bending': 5}


@dataclass
class TrackContext:
    signals: dict[str, float] = field(default_factory=dict)
    last_candidate: float | None = None
    last_activity: float = 0


class RiskEngine:
    def __init__(self, threshold: float = 65, window_seconds: float = 10,
                 quiet_seconds: float = 60):
        if not 0 < threshold <= 100 or min(window_seconds, quiet_seconds) <= 0:
            raise ValueError('Invalid risk configuration')
        self.threshold, self.window_seconds, self.quiet_seconds = threshold, window_seconds, quiet_seconds
        self.tracks: dict[int, TrackContext] = {}

    def observe(self, track_id: int, signals: list[str], now: float) -> dict | None:
        for key in list(self.tracks):
            if now - self.tracks[key].last_activity > max(120, self.quiet_seconds, self.window_seconds):
                del self.tracks[key]
        state = self.tracks.setdefault(track_id, TrackContext())
        if signals:
            if now - state.last_activity >= self.quiet_seconds:
                state.last_candidate = None
            state.last_activity = now
        for signal in signals:
            state.signals[signal] = now
        state.signals = {name: at for name, at in state.signals.items() if now - at <= self.window_seconds}
        score = min(100, sum(WEIGHTS.get(name, 0) for name in state.signals))
        if score < self.threshold or state.last_candidate is not None:
            return None
        state.last_candidate = now
        event = 'suspected_concealment' if 'concealment_candidate' in state.signals else 'high_risk_activity'
        return dict(event_type=event, risk_score=score / 100,
                    metadata={'score_kind': 'heuristic', 'signals': sorted(state.signals), 'track_id': track_id})
