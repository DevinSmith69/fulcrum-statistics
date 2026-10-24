"""Deterministic moment detection (MODEL_SPEC §4) with cooldowns and fact packs.

`MomentDetector` is fed every applied event / frame (with the WP before and after it) and returns the
`Moment`s that fired. Fact values follow the formatting contract so the fact-checker can match strings:
percentages are integers 0–100 (unit `%`), xG two decimals, speeds integers, distances one decimal,
minutes as `"67'"`. Derived deltas are always included (computed from the *rounded* values, so an agent
quoting before/after/delta is always arithmetically consistent).

Cooldown policy (see docs/HANDOFFS.md): goal, red_card, half_time, full_time and goal milestones (brace /
hat-trick) are exempt; big_chance skips only the global cooldown; every other moment needs ≥ 45 s since the
previous moment of any type and ≥ 180 s since the previous moment of the same type for the same team
(elite_pass / phase_change: any team; player_milestone: same player).

wp_swing only fires on a shot (`MomentParams.wp_swing_cause_types`): it compares the WP now with the WP just
before each cause event in the last 90 s, so the swing is always attributable to on-pitch actions.
"""

from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass, field

from fulcrum.contracts import (
    Fact,
    MatchEvent,
    MatchPhase,
    Moment,
    MomentType,
    TrackingFrame,
    WinProbability,
)

from .params import MomentParams
from .state import EventEffects, FrameEffects, MatchTracker

MT = MomentType


def pct(p: float) -> int:
    """Probability 0..1 → integer percent (round half up, never banker's rounding)."""
    return int(math.floor(p * 100 + 0.5))


def r1(x: float) -> float:
    return math.floor(x * 10 + 0.5) / 10


def r2(x: float) -> float:
    return math.floor(x * 100 + 0.5) / 100


def ri(x: float) -> int:
    return int(math.floor(x + 0.5))


@dataclass
class _Candidate:
    type: MomentType
    significance: float
    trigger: str
    facts: list[Fact]
    team_id: str | None = None
    player_ids: list[str] = field(default_factory=list)
    seq_start: int | None = None
    wp_before: WinProbability | None = None
    exempt: bool = False
    cooldown_team: str | None = None  # key for the same-type cooldown (defaults to team_id)


class MomentDetector:
    """Stateful rule engine over a `MatchTracker`."""

    def __init__(self, tracker: MatchTracker, params: MomentParams | None = None) -> None:
        self.t = tracker
        self.p = params or tracker.params.moments
        self.emitted: list[Moment] = []
        self._last_any_ms: int | None = None
        self._last_type: dict[tuple[str, str | None], int] = {}
        self._wp_hist: deque[tuple[int, int, WinProbability]] = deque()
        self._pressure_hist: deque[tuple[int, int, float, float]] = deque()
        self._dominant: tuple[str, int, int, float, float] | None = None  # side, clock, seq, p_home, p_away
        self._tempo_since: int | None = None
        self._tempo_dir = 0
        self._tempo_fired = False
        self._max_shot_kmh = 0.0
        self._max_sprint_kmh = 0.0
        self._milestones: set[tuple[str, str]] = set()
        self._ft_counted: set[int] = set()
        self._shot_wp_before: dict[str, tuple[int, WinProbability]] = {}
        self._ht_done = False
        self._ft_done = False
        self._run_spell: dict[str, bool] = {}

    # ------------------------------------------------------------------ fact helpers

    def _base_facts(self) -> list[Fact]:
        t = self.t
        return [
            Fact(key="minute", label="Match minute", value=t.minute_label),
            Fact(key="score", label=f"Score ({t.team_name(t.home_id)} first)", value=t.score_label),
            Fact(key="home_team", label="Home team", value=t.team_name(t.home_id)),
            Fact(key="away_team", label="Away team", value=t.team_name(t.away_id)),
        ]

    @staticmethod
    def _wp_facts(before: WinProbability, after: WinProbability) -> list[Fact]:
        out = []
        for side, label in (("home", "Home"), ("draw", "Draw"), ("away", "Away")):
            b, a = pct(getattr(before, side)), pct(getattr(after, side))
            noun = "draw probability" if side == "draw" else "win probability"
            out += [
                Fact(key=f"wp_{side}_before", label=f"{label} {noun} before", value=b, unit="%"),
                Fact(key=f"wp_{side}_after", label=f"{label} {noun} after", value=a, unit="%"),
                Fact(key=f"wp_{side}_delta", label=f"Change in {label.lower()} {noun}", value=a - b, unit="pp"),
            ]
        return out

    def _pressure_facts(self, before: tuple[float, float]) -> list[Fact]:
        t = self.t
        out = []
        for side, team_id, b in (("home", t.home_id, before[0]), ("away", t.away_id, before[1])):
            bi, ai = ri(b), ri(t.pressure_index(team_id))
            name = t.team_name(team_id)
            out += [
                Fact(key=f"pressure_index_{side}_before", label=f"{name} pressure index before", value=bi),
                Fact(key=f"pressure_index_{side}_after", label=f"{name} pressure index now", value=ai),
                Fact(key=f"pressure_index_{side}_delta", label=f"Change in {name} pressure index", value=ai - bi),
            ]
        return out

    def _team_fact(self, team_id: str | None) -> Fact:
        return Fact(key="team", label="Team", value=self.t.team_name(team_id))

    def _player_fact(self, key: str, label: str, player_id: str | None) -> Fact:
        return Fact(key=key, label=label, value=self.t.player_name(player_id))

    # ------------------------------------------------------------------ emission

    def _emit(self, c: _Candidate, wp_after: WinProbability) -> Moment | None:
        now = self.t.clock_ms
        key = (str(c.type), c.cooldown_team if c.cooldown_team is not None else c.team_id)
        if not (c.exempt or str(c.type) in self.p.fully_exempt):
            in_global = self._last_any_ms is not None and now - self._last_any_ms < self.p.global_cooldown_s * 1000
            if in_global and str(c.type) not in self.p.global_exempt:
                return None
            last = self._last_type.get(key)
            cooldown_s = dict(self.p.type_cooldowns_s).get(str(c.type), self.p.same_type_cooldown_s)
            if last is not None and now - last < cooldown_s * 1000:
                return None
        self._last_any_ms = now
        self._last_type[key] = now
        seq = max(self.t.last_seq, 0)
        m = Moment(
            match_id=self.t.match_id,
            type=c.type,
            significance=max(0.0, min(1.0, round(c.significance, 3))),
            period=self.t.period,
            clock_ms=now,
            seq_start=min(c.seq_start if c.seq_start is not None else seq, seq),
            seq_end=seq,
            team_id=c.team_id,
            player_ids=[p for p in c.player_ids if p],
            wp_before=c.wp_before or wp_after,
            wp_after=wp_after,
            facts=self._base_facts() + c.facts,
            trigger=c.trigger,
        )
        self.emitted.append(m)
        return m

    def _run(self, candidates: list[_Candidate | None], wp_after: WinProbability) -> list[Moment]:
        out = []
        for c in candidates:
            if c is not None and (m := self._emit(c, wp_after)) is not None:
                out.append(m)
        return out

    # ------------------------------------------------------------------ entry points

    def on_event(
        self, ev: MatchEvent, fx: EventEffects, wp_before: WinProbability, wp_after: WinProbability
    ) -> list[Moment]:
        """Evaluate every rule for one applied event, in priority order."""
        if fx.duplicate:
            return []
        if fx.shot is not None:
            self._shot_wp_before[fx.shot.team_id] = (fx.shot.seq, wp_before)
        goalish = fx.goal is not None or (fx.shot is not None and fx.shot.outcome == "goal")
        is_cause = str(ev.type) in self.p.wp_swing_cause_types
        if is_cause and not goalish:
            self._wp_hist.append((self.t.clock_ms, ev.seq, wp_before))

        candidates: list[_Candidate | None] = [
            self._goal(fx, wp_before, wp_after),
            self._goal_milestone(fx),
            self._red_card(ev, fx, wp_before, wp_after),
            self._big_chance(fx),
            self._elite_pass(ev, fx),
            self._shot_speed(fx),
        ]
        if is_cause and fx.period_ended is None and not fx.match_ended and not goalish:
            candidates.append(self._wp_swing(ev, wp_after))
        candidates += [
            self._momentum_run(fx),
            self._pressure_shift(),
            self._pass_milestone(fx),
            self._phase_change(fx),
            self._tempo_change(),
        ]
        if fx.period_ended == 1:
            candidates += [self._ht_accuracy_milestone(), self._half_time(wp_after)]
        if (fx.match_ended or (fx.period_ended or 0) >= 2) and not self._ft_done:
            candidates.append(self._full_time(wp_after))

        moments = self._run(candidates, wp_after)
        for m in moments:  # spells fire once; only mark them once the moment actually got through the cooldowns
            if m.type == MT.TEMPO_CHANGE:
                self._tempo_fired = True
            elif m.type == MT.MOMENTUM_RUN and m.team_id:
                self._run_spell[m.team_id] = True
        # WP history: reset after anything that legitimately moved WP, so a goal/red isn't re-reported as a swing.
        if goalish or fx.red_card_player_id is not None or any(m.type in (MT.BIG_CHANCE, MT.WP_SWING) for m in moments):
            self._wp_hist.clear()
        return moments

    def on_frame(self, frame: TrackingFrame, fx: FrameEffects, wp: WinProbability) -> list[Moment]:
        """Tracking-driven rules: sprint speed records and distance milestones."""
        if fx.duplicate:
            return []
        candidates: list[_Candidate | None] = []
        best = max(fx.top_speeds, key=lambda x: x[1], default=None)
        if best is not None:
            pid, speed, _ = best
            prev = self._max_sprint_kmh
            if speed > prev:
                self._max_sprint_kmh = speed
                settled = self.t.clock_ms >= self.p.sprint_record_min_elapsed_s * 1000
                gain_ok = speed - prev >= self.p.sprint_record_min_gain_kmh
                if settled and gain_ok and speed >= self.p.sprint_record_kmh:
                    candidates.append(self._speed_candidate(pid, speed, prev, "sprint"))
        for pid, km in fx.distance_crossings:
            if (pid, "distance") in self._milestones:
                continue
            first = not any(kind == "distance" for _, kind in self._milestones)
            self._milestones.add((pid, "distance"))
            if self.p.milestone_distance_first_only and not first:
                continue
            candidates.append(
                self._milestone_candidate(
                    pid,
                    "distance",
                    f"{r1(self.p.milestone_distance_km)} km covered",
                    r1(km),
                    "km",
                    self.p.milestone_sig_distance,
                    f"distance_km >= {self.p.milestone_distance_km}",
                )
            )
        return self._run(candidates, wp)

    # ------------------------------------------------------------------ rules

    def _goal(self, fx: EventEffects, wp_before: WinProbability, wp_after: WinProbability) -> _Candidate | None:
        g = fx.goal
        if g is None:
            return None
        t = self.t
        side = t.side(g.team_id)
        shot = g.shot
        seq_start, before = g.seq, wp_before
        if shot is not None and g.team_id in self._shot_wp_before:
            shot_seq, shot_wp = self._shot_wp_before[g.team_id]
            if shot_seq == shot.seq:
                seq_start, before = shot_seq, shot_wp
        b, a = pct(getattr(before, side)), pct(getattr(wp_after, side))
        facts = [
            self._team_fact(g.team_id),
            self._player_fact("scorer", "Goalscorer", g.scorer_id),
            Fact(key="score_after", label="Score after the goal", value=t.score_label),
            Fact(key="wp_scorer_before", label="Scoring team win probability before", value=b, unit="%"),
            Fact(key="wp_scorer_after", label="Scoring team win probability after", value=a, unit="%"),
            Fact(key="wp_scorer_delta", label="Change in scoring team win probability", value=a - b, unit="pp"),
        ]
        if shot is not None:
            facts += [
                Fact(key="xg", label="Expected goals of the shot", value=r2(shot.xg), unit="xG"),
                Fact(key="shot_speed_kmh", label="Shot speed", value=ri(shot.speed_kmh), unit="km/h"),
                Fact(key="distance_m", label="Shot distance", value=r1(shot.distance_m), unit="m"),
            ]
        if g.assister_id:
            facts.append(self._player_fact("assist", "Assist by", g.assister_id))
            if g.assist_difficulty is not None:
                facts.append(
                    Fact(
                        key="pass_difficulty_of_assist",
                        label="Difficulty of the assist (0-10)",
                        value=r1(g.assist_difficulty),
                    )
                )
        if g.scorer_id and g.scorer_id in t.players:
            facts.append(
                Fact(key="scorer_goals_in_match", label="Scorer's goals this match", value=t.players[g.scorer_id].goals)
            )
        facts += self._wp_facts(before, wp_after)
        sig = self.p.goal_base + min(self.p.goal_wp_cap, abs(getattr(wp_after, side) - getattr(before, side)))
        return _Candidate(
            MT.GOAL,
            sig,
            "every goal",
            facts,
            g.team_id,
            [g.scorer_id or "", g.assister_id or ""],
            seq_start,
            before,
            exempt=True,
        )

    def _goal_milestone(self, fx: EventEffects) -> _Candidate | None:
        g = fx.goal
        if g is None or not g.scorer_id or g.scorer_id not in self.t.players:
            return None
        goals = self.t.players[g.scorer_id].goals
        kind = {2: "brace", 3: "hat-trick"}.get(goals)
        if kind is None or (g.scorer_id, kind) in self._milestones:
            return None
        self._milestones.add((g.scorer_id, kind))
        sig = self.p.milestone_sig_brace if goals == 2 else self.p.milestone_sig_hat_trick
        c = self._milestone_candidate(g.scorer_id, kind, kind, goals, "goals", sig, f"player goals == {goals}")
        c.exempt = True
        return c

    def _milestone_candidate(
        self, player_id: str, kind: str, label: str, value: float | int, unit: str | None, sig: float, trigger: str
    ) -> _Candidate:
        team_id = self.t.players[player_id].team_id if player_id in self.t.players else None
        facts = [
            self._player_fact("player", "Player", player_id),
            self._team_fact(team_id),
            Fact(key="milestone", label="Milestone", value=label),
            Fact(key="value", label="Milestone value", value=value, unit=unit),
        ]
        return _Candidate(MT.PLAYER_MILESTONE, sig, trigger, facts, team_id, [player_id], cooldown_team=player_id)

    def _red_card(
        self, ev: MatchEvent, fx: EventEffects, wp_before: WinProbability, wp_after: WinProbability
    ) -> _Candidate | None:
        if fx.red_card_player_id is None:
            return None
        colour = ev.card.colour if ev.card else "red"
        team_id = ev.team_id
        reds = self.t.team(team_id).red_cards if team_id else 1
        facts = [
            self._player_fact("player", "Player sent off", ev.player_id),
            self._team_fact(team_id),
            Fact(key="card", label="Card", value="second yellow" if colour == "second_yellow" else "straight red"),
            Fact(key="team_red_cards", label="Team red cards this match", value=reds),
        ] + self._wp_facts(wp_before, wp_after)
        return _Candidate(
            MT.RED_CARD,
            self.p.red_card_sig,
            "red card",
            facts,
            team_id,
            [ev.player_id or ""],
            ev.seq,
            wp_before,
            exempt=True,
        )

    def _big_chance(self, fx: EventEffects) -> _Candidate | None:
        s = fx.shot
        if s is None or s.xg < self.p.big_chance_xg or s.outcome == "goal":
            return None
        team_xg = self.t.team(s.team_id).xg
        facts = [
            self._team_fact(s.team_id),
            self._player_fact("shooter", "Shooter", s.player_id),
            Fact(key="xg", label="Expected goals of the chance", value=r2(s.xg), unit="xG"),
            Fact(key="outcome", label="Outcome", value=s.outcome.replace("_", " ")),
            Fact(key="distance_m", label="Shot distance", value=r1(s.distance_m), unit="m"),
            Fact(key="team_xg_total", label="Team xG so far", value=r2(team_xg), unit="xG"),
        ]
        if s.speed_kmh:
            facts.append(Fact(key="shot_speed_kmh", label="Shot speed", value=ri(s.speed_kmh), unit="km/h"))
        if s.assister_id:
            facts.append(self._player_fact("assist", "Chance created by", s.assister_id))
        before = self._shot_wp_before.get(s.team_id, (s.seq, None))[1]
        return _Candidate(
            MT.BIG_CHANCE,
            self.p.big_chance_base + self.p.big_chance_per_xg * s.xg,
            f"shot xG >= {self.p.big_chance_xg:.2f} not scored",
            facts,
            s.team_id,
            [s.player_id or "", s.assister_id or ""],
            s.seq,
            before,
        )

    def _elite_pass(self, ev: MatchEvent, fx: EventEffects) -> _Candidate | None:
        pa = ev.pass_
        if fx.completed_pass is None or pa is None:
            return None
        if pa.difficulty < self.p.elite_pass_difficulty or pa.progressive_m < self.p.elite_pass_progressive_m:
            return None
        span = self.p.elite_pass_sig_max - self.p.elite_pass_sig_min
        sig = self.p.elite_pass_sig_min + span * min(1.0, (pa.difficulty - self.p.elite_pass_difficulty) / 2.5)
        facts = [
            self._team_fact(ev.team_id),
            self._player_fact("passer", "Passer", ev.player_id),
            self._player_fact("recipient", "Recipient", pa.recipient_id),
            Fact(key="difficulty", label="Pass difficulty (0-10)", value=r1(pa.difficulty)),
            Fact(
                key="expected_completion_pct", label="Expected completion", value=pct(pa.expected_completion), unit="%"
            ),
            Fact(key="length_m", label="Pass length", value=r1(pa.length_m), unit="m"),
            Fact(key="progressive_m", label="Metres gained towards goal", value=r1(pa.progressive_m), unit="m"),
            Fact(key="ball_speed_kmh", label="Ball speed", value=ri(pa.ball_speed_kmh), unit="km/h"),
        ]
        return _Candidate(
            MT.ELITE_PASS,
            sig,
            f"completed pass difficulty >= {self.p.elite_pass_difficulty} and progressive >= "
            f"{self.p.elite_pass_progressive_m:.0f} m",
            facts,
            ev.team_id,
            [ev.player_id or "", pa.recipient_id or ""],
            ev.seq,
            cooldown_team="*",
        )

    def _speed_candidate(self, player_id: str | None, speed: float, previous: float, kind: str) -> _Candidate:
        team_id = self.t.players[player_id].team_id if player_id in self.t.players else None
        label = "Shot speed" if kind == "shot" else "Sprint speed"
        facts = [
            self._player_fact("player", "Player", player_id),
            self._team_fact(team_id),
            Fact(key="record_type", label="Record type", value=kind),
            Fact(key="speed_kmh", label=label, value=ri(speed), unit="km/h"),
            Fact(
                key="previous_match_high", label=f"Previous match-high {label.lower()}", value=ri(previous), unit="km/h"
            ),
        ]
        threshold = self.p.shot_speed_record_kmh if kind == "shot" else self.p.sprint_record_kmh
        return _Candidate(
            MT.SPEED_RECORD,
            self.p.speed_record_sig,
            f"match-high {kind} speed >= {threshold:.0f} km/h",
            facts,
            team_id,
            [player_id or ""],
        )

    def _shot_speed(self, fx: EventEffects) -> _Candidate | None:
        s = fx.shot
        if s is None or s.speed_kmh <= self._max_shot_kmh:
            return None
        prev, self._max_shot_kmh = self._max_shot_kmh, s.speed_kmh
        if s.speed_kmh < self.p.shot_speed_record_kmh or s.outcome == "goal":
            return None  # a goal's own speed is already in the goal fact pack
        c = self._speed_candidate(s.player_id, s.speed_kmh, prev, "shot")
        c.seq_start = s.seq
        return c

    def _wp_swing(self, ev: MatchEvent, wp_after: WinProbability) -> _Candidate | None:
        lo = self.t.clock_ms - int(self.p.wp_swing_window_s * 1000)
        while self._wp_hist and self._wp_hist[0][0] < lo:
            self._wp_hist.popleft()
        best: tuple[float, int, WinProbability] | None = None
        for _, seq, wp in self._wp_hist:
            d = max(abs(wp_after.home - wp.home), abs(wp_after.away - wp.away))
            if best is None or d > best[0]:
                best = (d, seq, wp)
        if best is None or best[0] < self.p.wp_swing_delta:
            return None
        delta, seq_start, before = best
        dh, da = wp_after.home - before.home, wp_after.away - before.away
        team_id = (self.t.home_id if dh >= da else self.t.away_id) if max(dh, da) > 0 else None
        cause = str(ev.type).replace("_", " ")
        facts = self._wp_facts(before, wp_after) + [
            Fact(key="cause_event", label="Event that tipped the swing", value=cause),
            Fact(
                key="pressure_index_home",
                label=f"{self.t.team_name(self.t.home_id)} pressure index",
                value=ri(self.t.pressure_index(self.t.home_id)),
            ),
            Fact(
                key="pressure_index_away",
                label=f"{self.t.team_name(self.t.away_id)} pressure index",
                value=ri(self.t.pressure_index(self.t.away_id)),
            ),
        ]
        if ev.player_id:
            facts.append(self._player_fact("cause_player", "Player involved", ev.player_id))
        if team_id:
            facts.append(self._team_fact(team_id))
        return _Candidate(
            MT.WP_SWING,
            min(1.0, self.p.wp_swing_sig_per_delta * delta),
            f"abs(dWP) >= {self.p.wp_swing_delta:.2f} within {self.p.wp_swing_window_s:.0f}s",
            facts,
            team_id,
            [ev.player_id or ""],
            seq_start,
            before,
        )

    def _pressure_shift(self) -> _Candidate | None:
        t = self.t
        ph, pa = t.pressure_index(t.home_id), t.pressure_index(t.away_id)
        now, seq = t.clock_ms, max(t.last_seq, 0)
        lo = now - int(self.p.pressure_rise_window_s * 1000)
        while self._pressure_hist and self._pressure_hist[0][0] < lo:
            self._pressure_hist.popleft()
        self._pressure_hist.append((now, seq, ph, pa))

        cand: tuple[str, float, int, tuple[float, float], str] | None = None  # side, magnitude, seq, before, kind
        diff = ph - pa
        if abs(diff) >= self.p.pressure_flip_diff:
            side = "home" if diff > 0 else "away"
            prev = self._dominant
            if prev is not None and prev[0] != side:
                magnitude = abs(diff - (prev[3] - prev[4]))
                cand = (side, magnitude, prev[2], (prev[3], prev[4]), "flip")
            self._dominant = (side, now, seq, ph, pa)
        if cand is None:
            for side, idx in (("home", 2), ("away", 3)):
                low = min(self._pressure_hist, key=lambda h: h[idx])
                rise = (ph if side == "home" else pa) - low[idx]
                if rise >= self.p.pressure_rise and (ph if side == "home" else pa) >= self.p.pressure_surge_min_after:
                    cand = (side, rise, low[1], (low[2], low[3]), "surge")
                    break
        if cand is None:
            return None
        side, magnitude, seq_start, before, kind = cand
        team_id = t.home_id if side == "home" else t.away_id
        window = self.p.momentum_window_s
        facts = [
            self._team_fact(team_id),
            Fact(
                key="shift_kind", label="Kind of shift", value="pressure flip" if kind == "flip" else "pressure surge"
            ),
            Fact(
                key="shots_last_5", label="Team shots in the last 5 minutes", value=len(t.recent_shots(team_id, window))
            ),
            Fact(
                key="box_entries_last_5",
                label="Team box entries in the last 5 minutes",
                value=t.box_entries_last_window(team_id),
            ),
        ] + self._pressure_facts(before)
        span = self.p.pressure_sig_max - self.p.pressure_sig_min
        sig = self.p.pressure_sig_min + span * min(1.0, magnitude / 60.0)
        trigger = (
            f"sign flip of pressure diff with |diff| >= {self.p.pressure_flip_diff:.0f}"
            if kind == "flip"
            else f"pressure +{self.p.pressure_rise:.0f} within {self.p.pressure_rise_window_s:.0f}s"
        )
        return _Candidate(MT.PRESSURE_SHIFT, sig, trigger, facts, team_id, seq_start=seq_start)

    def _phase_change(self, fx: EventEffects) -> _Candidate | None:
        if fx.phase_before is None:
            return None
        t = self.t
        after = t.phase.phase
        if after == MatchPhase.BALANCED and not self.p.phase_change_report_balanced:
            return None
        team_id = {MatchPhase.CONTROL_HOME: t.home_id, MatchPhase.CONTROL_AWAY: t.away_id}.get(after)
        facts = [
            Fact(key="phase_before", label="Match phase before", value=str(fx.phase_before)),
            Fact(key="phase_after", label="Match phase now", value=str(after)),
            Fact(
                key="control_index_home",
                label=f"{t.team_name(t.home_id)} control index",
                value=ri(t.control_index(t.home_id)),
            ),
            Fact(
                key="control_index_away",
                label=f"{t.team_name(t.away_id)} control index",
                value=ri(t.control_index(t.away_id)),
            ),
            Fact(key="chaos_index", label="Chaos index", value=ri(t.chaos_index())),
            Fact(key="turnovers_last_5", label="Turnovers in the last 5 minutes", value=t.turnovers_last_window()),
        ]
        if team_id:
            facts.append(self._team_fact(team_id))
        return _Candidate(
            MT.PHASE_CHANGE,
            self.p.phase_change_sig,
            f"phase {fx.phase_before} -> {after} (persisted)",
            facts,
            team_id,
            cooldown_team="*",
        )

    def _tempo_change(self) -> _Candidate | None:
        t = self.t
        if t.clock_ms < self.p.tempo_min_elapsed_s * 1000:
            return None
        tempo, avg = t.tempo_ppm(), t.tempo_match_avg()
        rel = (tempo - avg) / avg if avg else 0.0
        direction = (rel > 0) - (rel < 0)
        if abs(rel) <= self.p.tempo_change_pct:
            self._tempo_since, self._tempo_fired = None, False
            return None
        if self._tempo_since is None or direction != self._tempo_dir:
            self._tempo_since, self._tempo_dir, self._tempo_fired = t.clock_ms, direction, False
            return None
        if self._tempo_fired or t.clock_ms - self._tempo_since < self.p.tempo_hold_s * 1000:
            return None
        tempo_r, avg_r = r1(tempo), r1(avg)
        facts = [
            Fact(key="tempo_ppm", label="Passes per minute, last 5 minutes", value=tempo_r),
            Fact(key="tempo_match_avg", label="Passes per minute, match average", value=avg_r),
            Fact(
                key="tempo_delta_pct",
                label="Tempo vs match average",
                value=ri(100 * (tempo_r - avg_r) / avg_r),
                unit="%",
            ),
            Fact(key="direction", label="Direction", value="faster" if direction > 0 else "slower"),
        ]
        c = _Candidate(
            MT.TEMPO_CHANGE,
            self.p.tempo_change_sig,
            f"|tempo - match avg| > {100 * self.p.tempo_change_pct:.0f}% for >= {self.p.tempo_hold_s:.0f}s",
            facts,
        )
        return c

    def _momentum_run(self, fx: EventEffects) -> _Candidate | None:
        t = self.t
        team_id = fx.possessing_team
        if team_id is None or (fx.shot is None and not fx.final_third_entry):
            return None
        opp = t.other(team_id)
        window = self.p.momentum_window_s
        shots, opp_shots = t.recent_shots(team_id, window), t.recent_shots(opp, window)
        own = [p for p in t.possessions if p.team_id == team_id][-self.p.momentum_final_third_possessions :]
        in_row = 0
        for p in reversed([p for p in t.possessions if p.team_id == team_id]):
            if not p.reached_final_third:
                break
            in_row += 1
        by_shots = len(shots) >= self.p.momentum_shots and not opp_shots
        by_entries = len(own) == self.p.momentum_final_third_possessions and all(p.reached_final_third for p in own)
        active = by_shots or by_entries
        if not active:
            self._run_spell[team_id] = False
            return None
        if self._run_spell.get(team_id):
            return None
        c = _Candidate(
            MT.MOMENTUM_RUN,
            self.p.momentum_run_sig,
            f">= {self.p.momentum_shots} shots in 5 min with 0 opponent shots"
            if by_shots
            else f">= {self.p.momentum_final_third_possessions} consecutive possessions reaching the final third",
            [
                self._team_fact(team_id),
                Fact(key="shots_last_5", label="Team shots in the last 5 minutes", value=len(shots)),
                Fact(key="opp_shots_last_5", label="Opponent shots in the last 5 minutes", value=len(opp_shots)),
                Fact(
                    key="xg_last_5",
                    label="Team xG in the last 5 minutes",
                    value=r2(sum(s.xg for s in shots)),
                    unit="xG",
                ),
                Fact(
                    key="final_third_possessions_in_row",
                    label="Consecutive possessions reaching the final third",
                    value=in_row,
                ),
            ],
            team_id,
        )
        return c

    def _pass_milestone(self, fx: EventEffects) -> _Candidate | None:
        rec = fx.completed_pass
        if rec is None or not rec.passer_id:
            return None
        pl = self.t.players.get(rec.passer_id)
        n = self.p.milestone_passes
        if pl is None or pl.passes_completed != n or (pl.player_id, "passes") in self._milestones:
            return None
        self._milestones.add((pl.player_id, "passes"))
        return self._milestone_candidate(
            pl.player_id,
            "passes",
            f"{n} completed passes",
            n,
            "passes",
            self.p.milestone_sig_passes,
            f"completed passes == {n}",
        )

    def _ht_accuracy_milestone(self) -> _Candidate | None:
        best = None
        for pl in self.t.players.values():
            if pl.passes < self.p.milestone_ht_min_passes:
                continue
            acc = 100.0 * pl.passes_completed / pl.passes
            if acc >= self.p.milestone_ht_accuracy and (best is None or acc > best[1]):
                best = (pl, acc)
        if best is None:
            return None
        pl, acc = best
        c = self._milestone_candidate(
            pl.player_id,
            "ht_accuracy",
            "pass accuracy at half-time",
            ri(acc),
            "%",
            self.p.milestone_sig_accuracy,
            f"pass accuracy >= {self.p.milestone_ht_accuracy:.0f}% on >= {self.p.milestone_ht_min_passes} passes at HT",
        )
        c.facts.append(Fact(key="passes", label="Passes attempted", value=pl.passes))
        return c

    def _summary_facts(self, wp: WinProbability) -> list[Fact]:
        t = self.t
        out = []
        poss_home = ri(t.possession_pct(t.home_id))
        possession = {"home": poss_home, "away": 100 - poss_home}  # always sums to 100
        for side, team_id in (("home", t.home_id), ("away", t.away_id)):
            acc = t.team(team_id) if team_id else None
            name = t.team_name(team_id)
            out += [
                Fact(key=f"xg_{side}", label=f"{name} xG", value=r2(acc.xg if acc else 0.0), unit="xG"),
                Fact(key=f"shots_{side}", label=f"{name} shots", value=acc.shots if acc else 0),
                Fact(key=f"possession_{side}", label=f"{name} possession", value=possession[side], unit="%"),
            ]
        out += [
            Fact(key="wp_home", label="Home win probability", value=pct(wp.home), unit="%"),
            Fact(key="wp_draw", label="Draw probability", value=pct(wp.draw), unit="%"),
            Fact(key="wp_away", label="Away win probability", value=pct(wp.away), unit="%"),
        ]
        ranked = sorted(t.players.values(), key=lambda p: (-t.player_rating(p), p.player_id))
        ranked = [p for p in ranked if t.player_minutes(p) > 0 or p.touches > 0][:3]
        for i, p in enumerate(ranked, 1):
            out += [
                self._player_fact(f"top_player_{i}", f"Top-rated player #{i}", p.player_id),
                Fact(
                    key=f"top_player_{i}_rating", label=f"Rating of top-rated player #{i}", value=r1(t.player_rating(p))
                ),
            ]
        return out

    def _half_time(self, wp: WinProbability) -> _Candidate | None:
        if self._ht_done:
            return None
        self._ht_done = True
        return _Candidate(MT.HALF_TIME, self.p.half_time_sig, "period 1 ended", self._summary_facts(wp), exempt=True)

    def _full_time(self, wp: WinProbability) -> _Candidate | None:
        self._ft_done = True
        return _Candidate(MT.FULL_TIME, self.p.full_time_sig, "match ended", self._summary_facts(wp), exempt=True)
