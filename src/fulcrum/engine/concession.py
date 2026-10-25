"""Concession analysis (MODEL_SPEC §4.1): the deterministic evidence behind the "Coach's view".

For every shot the engine knows how the attacking move started, how long it took, how much pressure the shooter
was under and how good the chance was. `ConcessionTracker` follows the event stream (and the last few tracking
frames) and turns that into `coach_` facts for goal and big_chance moments, written from the DEFENDING team's
side. Nothing here judges anyone: a misplaced pass is reported with its own difficulty and expected completion,
so "a 78%-completion pass was misplaced" is a computed fact, not an opinion. A fact that cannot be computed
reliably is left out, never guessed.

Coordinates: events are team-relative (the acting team attacks towards x = 105); tracking frames are absolute
with the home team attacking towards x = 105 in odd periods (MODEL_SPEC §1.8).
"""

from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass

from fulcrum.contracts import EventType, Fact, MatchEvent, Outcome, Position, TrackingFrame

from .params import CoachParams
from .state import ON_BALL, PERIOD_START_MIN, RESTARTS, EventEffects, MatchTracker, ShotRecord, minute_label

E = EventType
PITCH_X = 105.0
PITCH_Y = 68.0

# Readable values for `coach_move_origin` (agents quote fact values verbatim, so never raw enum strings).
RESTART_ORIGIN = {
    E.KICK_OFF: "kick-off",
    E.GOAL_KICK: "goal kick",
    E.THROW_IN: "throw-in",
    E.CORNER: "corner",
    E.FREE_KICK: "free kick",
}
REGAIN_TYPES = {E.INTERCEPTION, E.TACKLE, E.BALL_RECOVERY, E.CLEARANCE}
ORIGIN_MISPLACED_PASS = "misplaced pass"
ORIGIN_TACKLE = "tackle"
ORIGIN_INTERCEPTION = "interception"
ORIGIN_CLEARANCE = "clearance"
ORIGIN_CLEARANCE_RECOVERED = "loose ball after a clearance"
ORIGIN_SAVE = "goalkeeper save"
ORIGIN_BLOCK = "loose ball after a blocked shot"
ORIGIN_RECOVERY = "loose-ball recovery"


def r1(x: float) -> float:
    return math.floor(x * 10 + 0.5) / 10


def r2(x: float) -> float:
    return math.floor(x * 100 + 0.5) / 100


def ri(x: float) -> int:
    return int(math.floor(x + 0.5))


def pct(p: float) -> int:
    return int(math.floor(p * 100 + 0.5))


def zone_label(x_own_frame: float, params: CoachParams) -> str:
    """Pitch third of a point, in the frame of the team it is described for (x = 0 is that team's own goal)."""
    if x_own_frame < params.own_third_x:
        return "own third"
    if x_own_frame < params.final_third_x:
        return "middle third"
    return "final third"


def chance_quality(xg: float, params: CoachParams) -> str:
    """Deterministic chance-quality band from a shot's xG (MODEL_SPEC §4.1)."""
    if xg < params.chance_low_xg:
        return "low-quality chance"
    if xg < params.chance_clear_xg:
        return "half-chance"
    return "clear chance"


def keeper_shot_profile(on_target: bool, xg: float, distance_m: float, params: CoachParams) -> str | None:
    """A keeper-facing label only where it is defensible from the numbers; None otherwise (never a verdict)."""
    if on_target and xg < params.low_probability_finish_xg and distance_m > params.low_probability_finish_min_m:
        return "low-probability finish"
    return None


def attacks_positive_x(team_is_home: bool, period: int) -> bool:
    """True if the team attacks towards x = 105 in the absolute (tracking) frame in `period`."""
    return team_is_home == (period % 2 == 1)


def defenders_goal_side(
    frame: TrackingFrame,
    *,
    defending_team_id: str,
    goalkeeper_ids: set[str],
    shot_x: float,
    shot_y: float,
    shooter_attacks_positive_x: bool,
    max_ball_gap_m: float,
) -> int | None:
    """Outfield defenders closer to their own goal line than the ball at the shot, from one tracking frame.

    `shot_x/shot_y` are in the shooting team's frame (it attacks towards x = 105, the defenders' goal). The
    frame is converted into that frame; if its ball is more than `max_ball_gap_m` from the shot location the
    frame does not show the shot (wrong time or orientation) and None is returned.
    """

    def rel(x: float, y: float) -> tuple[float, float]:
        return (x, y) if shooter_attacks_positive_x else (PITCH_X - x, PITCH_Y - y)

    bx, by = rel(frame.ball_x, frame.ball_y)
    if math.hypot(bx - shot_x, by - shot_y) > max_ball_gap_m:
        return None
    return sum(
        1
        for p in frame.players
        if p.team_id == defending_team_id and p.player_id not in goalkeeper_ids and rel(p.x, p.y)[0] > shot_x
    )


@dataclass
class _Action:
    """The bits of an on-ball event the origin of a move is classified from."""

    seq: int
    type: EventType
    outcome: Outcome
    team_id: str | None
    player_id: str | None
    difficulty: float | None = None
    expected_completion: float | None = None

    @classmethod
    def of(cls, ev: MatchEvent) -> _Action:
        pa = ev.pass_
        return cls(
            ev.seq,
            ev.type,
            ev.outcome,
            ev.team_id,
            ev.player_id,
            pa.difficulty if pa else None,
            pa.expected_completion if pa else None,
        )


@dataclass
class Move:
    """The attacking move in progress: from a regain or restart to (eventually) a shot."""

    team_id: str
    period: int
    start_clock_ms: int
    start_x: float | None
    origin: str | None = None
    won_by: str | None = None  # attacking player who won the ball
    lost_by: str | None = None  # defending player who lost it (misplaced pass / tackled dribble)
    lost_pass: _Action | None = None
    foul_by: str | None = None  # defending player whose foul gave the free kick
    passes: int = 0  # completed passes by the attacking team since the start


class ConcessionTracker:
    """Follows the stream and produces `coach_` facts for a shot. Feed every applied event and frame, in order."""

    def __init__(self, tracker: MatchTracker, params: CoachParams | None = None) -> None:
        self.t = tracker
        self.p = params or tracker.params.coach
        self.move: Move | None = None
        self._last_action: _Action | None = None
        self._last_foul: _Action | None = None
        self._frames: deque[TrackingFrame] = deque(maxlen=4)

    # ------------------------------------------------------------------ input

    def on_frame(self, frame: TrackingFrame) -> None:
        """Remember the last few tracking frames (the shot's frame is at most ~1 s old when the shot arrives)."""
        self._frames.append(frame)

    def on_event(self, ev: MatchEvent, fx: EventEffects) -> None:
        """Update the current move after `MatchTracker.apply_event(ev)` returned `fx`."""
        if fx.duplicate or not ev.team_id:
            return
        if fx.new_possession is not None and fx.new_possession.team_id == ev.team_id:
            self.move = self._start(ev, self._last_action)
        elif ev.type in RESTARTS and self.move is not None and self.move.team_id == ev.team_id:
            self.move = self._start(ev, None)  # a set piece inside the same possession starts a new move
        if ev.type == E.FOUL:
            self._last_foul = _Action.of(ev)
        if ev.type in ON_BALL:
            self._last_action = _Action.of(ev)
        mv = self.move
        if mv is not None and ev.type == E.PASS and ev.outcome == Outcome.SUCCESS and ev.team_id == mv.team_id:
            mv.passes += 1

    def _start(self, ev: MatchEvent, lost: _Action | None) -> Move:
        mv = Move(ev.team_id or "", ev.period, ev.clock_ms, ev.x)
        lost = lost if lost is not None and lost.team_id not in (None, ev.team_id) else None
        if ev.type in RESTARTS:
            mv.origin = RESTART_ORIGIN[ev.type]
            foul, last = self._last_foul, self._last_action
            fresh = foul is not None and (last is None or foul.seq > last.seq)  # the foul right before this restart
            if ev.type == E.FREE_KICK and fresh and foul.team_id not in (None, ev.team_id):
                mv.foul_by = foul.player_id
            return mv
        if ev.type in REGAIN_TYPES:  # the player on a regain event won the ball (not e.g. a first pass)
            mv.won_by = ev.player_id
        if lost is not None and lost.type == E.PASS and lost.outcome == Outcome.FAIL:
            mv.origin, mv.lost_by, mv.lost_pass = ORIGIN_MISPLACED_PASS, lost.player_id, lost
        elif (lost is not None and lost.type == E.DRIBBLE and lost.outcome == Outcome.FAIL) or ev.type == E.TACKLE:
            mv.origin = ORIGIN_TACKLE
            mv.lost_by = lost.player_id if lost is not None and lost.type == E.DRIBBLE else None
        elif ev.type == E.INTERCEPTION:
            mv.origin = ORIGIN_INTERCEPTION
        elif ev.type == E.CLEARANCE:
            mv.origin = ORIGIN_CLEARANCE
        elif lost is not None and lost.type == E.CLEARANCE:
            mv.origin = ORIGIN_CLEARANCE_RECOVERED
        elif lost is not None and lost.type == E.SHOT and lost.outcome == Outcome.SAVED:
            mv.origin = ORIGIN_SAVE
        elif lost is not None and lost.type == E.SHOT and lost.outcome == Outcome.BLOCKED:
            mv.origin = ORIGIN_BLOCK
        elif ev.type == E.BALL_RECOVERY:
            mv.origin = ORIGIN_RECOVERY
        # anything else (e.g. a bare possession_change): origin unknown, so no origin facts
        return mv

    # ------------------------------------------------------------------ output

    def _minute_label_at(self, period: int, clock_ms: int) -> str:
        start = self.t.period_start_ms.get(period, 0)
        return minute_label(period, PERIOD_START_MIN.get(period, 0.0) + (clock_ms - start) / 60000.0)

    def _goalkeepers(self, team_id: str) -> set[str]:
        team = next((x for x in (self.t.home, self.t.away) if x is not None and x.team_id == team_id), None)
        return {p.player_id for p in team.players if p.position == Position.GK} if team else set()

    def _keeper_on_pitch(self, team_id: str) -> str | None:
        on = [
            pid
            for pid in self._goalkeepers(team_id)
            if (acc := self.t.players.get(pid)) is not None and acc.on_since_ms is not None and not acc.sent_off
        ]
        return on[0] if len(on) == 1 else None

    def _frame_for(self, ev: MatchEvent) -> TrackingFrame | None:
        """The latest frame at or before the shot, in the same period and at most `frame_max_age_ms` old."""
        ok = [
            f
            for f in self._frames
            if f.period == ev.period and 0 <= ev.clock_ms - f.clock_ms <= self.p.frame_max_age_ms
        ]
        return max(ok, key=lambda f: f.clock_ms, default=None)

    def facts_for_shot(self, ev: MatchEvent, shot: ShotRecord) -> list[Fact]:
        """`coach_` facts for the shot `ev` (call right after it was applied), from the defending team's side."""
        t = self.t
        defending = t.other(shot.team_id)
        if defending is None:
            return []
        dname, aname = t.team_name(defending), t.team_name(shot.team_id)
        name = t.player_name
        facts = [Fact(key="coach_defending_team", label="Defending team", value=dname)]

        mv = self.move if self.move is not None and self.move.team_id == shot.team_id else None
        if mv is not None and mv.period == ev.period:
            if mv.origin is not None:
                facts.append(Fact(key="coach_move_origin", label="How the attacking move started", value=mv.origin))
            if mv.won_by:
                facts.append(
                    Fact(key="coach_ball_won_by", label=f"{aname} player who won the ball", value=name(mv.won_by))
                )
            if mv.lost_by:
                facts.append(
                    Fact(key="coach_ball_lost_by", label=f"{dname} player who lost the ball", value=name(mv.lost_by))
                )
            lp = mv.lost_pass
            if lp is not None and lp.difficulty is not None and lp.expected_completion is not None:
                facts += [
                    Fact(
                        key="coach_misplaced_pass_difficulty",
                        label="Difficulty of the misplaced pass (0-10)",
                        value=r1(lp.difficulty),
                    ),
                    Fact(
                        key="coach_misplaced_pass_expected_completion_pct",
                        label="Expected completion of the misplaced pass",
                        value=pct(lp.expected_completion),
                        unit="%",
                    ),
                ]
            if mv.foul_by:
                facts.append(
                    Fact(
                        key="coach_free_kick_foul_by",
                        label=f"{dname} player whose foul gave away the free kick",
                        value=name(mv.foul_by),
                    )
                )
            facts.append(
                Fact(
                    key="coach_move_start_minute",
                    label="Minute the attacking move started",
                    value=self._minute_label_at(mv.period, mv.start_clock_ms),
                )
            )
            if mv.start_x is not None:
                facts.append(
                    Fact(
                        key="coach_move_start_zone",
                        label=f"Where the move started, seen from {dname}",
                        value=zone_label(PITCH_X - mv.start_x, self.p),
                    )
                )
            if mv.passes:  # "needed 0 passes" reads badly; a set piece is named by the move's origin instead
                facts.append(
                    Fact(
                        key="coach_move_passes",
                        label="Number of completed passes in the move (not its length in seconds)",
                        value=mv.passes,
                    )
                )
            facts.append(
                Fact(
                    key="coach_move_seconds",
                    label="Length of the move in seconds (not a pass count)",
                    value=ri(max(0, ev.clock_ms - mv.start_clock_ms) / 1000),
                    unit="s",
                )
            )

        # Pressure on the shooter. The nearest-opponent distance is the simulator's own measurement on the shot
        # event (MODEL_SPEC §1.2); tracking frames are not used for it because the two nearest players of each
        # team chase the ball (§1.8) and would read ~0 m on most shots.
        if ev.nearest_opponent_m is not None:
            facts.append(
                Fact(
                    key="coach_nearest_defender_m",
                    label=f"Distance from the shooter to the nearest {dname} player",
                    value=r1(ev.nearest_opponent_m),
                    unit="m",
                )
            )
        frame = self._frame_for(ev)
        if frame is not None and ev.x is not None and ev.y is not None:
            n = defenders_goal_side(
                frame,
                defending_team_id=defending,
                goalkeeper_ids=self._goalkeepers(defending),
                shot_x=ev.x,
                shot_y=ev.y,
                shooter_attacks_positive_x=attacks_positive_x(shot.team_id == t.home_id, ev.period),
                max_ball_gap_m=self.p.frame_max_ball_gap_m,
            )
            if n is not None:
                facts.append(
                    Fact(
                        key="coach_defenders_goal_side",
                        label=f"{dname} outfield players goal-side of the ball at the shot",
                        value=n,
                    )
                )

        # Chance quality conceded.
        facts += [
            Fact(key="coach_chance_xg", label="Expected goals of the chance conceded", value=r2(shot.xg), unit="xG"),
            Fact(key="coach_shot_distance_m", label="Shot distance", value=r1(shot.distance_m), unit="m"),
            Fact(key="coach_chance_quality", label="Chance quality band", value=chance_quality(shot.xg, self.p)),
        ]

        # The goalkeeper's view: only when the shot was on target.
        if shot.on_target:
            if (gk := self._keeper_on_pitch(defending)) is not None:
                facts.append(Fact(key="coach_goalkeeper", label=f"{dname} goalkeeper", value=name(gk)))
            facts.append(Fact(key="coach_shot_on_target", label="Shot on target", value="yes"))
            if shot.speed_kmh:
                facts.append(
                    Fact(key="coach_shot_speed_kmh", label="Shot speed", value=ri(shot.speed_kmh), unit="km/h")
                )
            if (profile := keeper_shot_profile(shot.on_target, shot.xg, shot.distance_m, self.p)) is not None:
                facts.append(
                    Fact(key="coach_keeper_shot_profile", label="Shot profile for the goalkeeper", value=profile)
                )
        return facts
