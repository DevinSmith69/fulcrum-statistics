"""Applies `MatchEvent` / `TrackingFrame` to in-memory match state (MODEL_SPEC §2).

`MatchTracker` is pure and synchronous: feed it events in seq order (duplicates and stale seqs are ignored,
so redelivery from the bus is harmless) and read `snapshot()` for a contract `MatchState`. Each
`apply_event` returns an `EventEffects` describing what the event did, which `moments.py` consumes.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from fulcrum.contracts import (
    EventType,
    MatchEvent,
    MatchPhase,
    MatchState,
    Outcome,
    PlayerLiveStats,
    Team,
    TeamLiveStats,
    TrackingFrame,
    WinProbability,
)

from . import metrics as mx
from .params import BOX_X, BOX_Y_MAX, BOX_Y_MIN, DEFAULT_PARAMS, FINAL_THIRD_X, HALF_X, EngineParams
from .winprob import TeamStrength, win_probability

E = EventType
ON_BALL = {E.PASS, E.CARRY, E.DRIBBLE, E.SHOT, E.CLEARANCE}
RESTARTS = {E.KICK_OFF, E.GOAL_KICK, E.THROW_IN, E.CORNER, E.FREE_KICK}
GAINS = {E.POSSESSION_CHANGE, E.BALL_RECOVERY, E.INTERCEPTION}
POSSESSION_TYPES = ON_BALL | RESTARTS | GAINS
PERIOD_START_MIN = {1: 0.0, 2: 45.0, 3: 90.0, 4: 105.0}
PERIOD_END_MIN = {1: 45.0, 2: 90.0, 3: 105.0, 4: 120.0}


def in_box(x: float, y: float) -> bool:
    """True if a team-relative point is inside the attacked penalty box."""
    return x >= BOX_X and BOX_Y_MIN <= y <= BOX_Y_MAX


def minute_label(period: int, minute: float) -> str:
    """Football minute string as quoted in fact packs: `"67'"`, `"45+2'"`."""
    m = int(math.floor(minute)) + 1
    end = int(PERIOD_END_MIN.get(period, 120.0))
    return f"{end}+{m - end}'" if m > end else f"{m}'"


@dataclass
class TeamAcc:
    team_id: str
    pressure: mx.DecayingScore
    goals: int = 0
    xg: float = 0.0
    shots: int = 0
    shots_on_target: int = 0
    passes: int = 0
    passes_completed: int = 0
    difficulty_sum: float = 0.0
    final_third_entries: int = 0
    box_entries: int = 0
    corners: int = 0
    fouls: int = 0
    yellow_cards: int = 0
    red_cards: int = 0
    max_shot_speed_kmh: float = 0.0
    possession_ms: int = 0


@dataclass
class PlayerAcc:
    player_id: str
    team_id: str
    touches: int = 0
    passes: int = 0
    passes_completed: int = 0
    difficulty_sum: float = 0.0
    key_passes: int = 0
    shots: int = 0
    xg: float = 0.0
    goals: int = 0
    assists: int = 0
    tackles_won: int = 0
    interceptions: int = 0
    rating_delta: float = 0.0
    distance_m: float = 0.0
    top_speed_kmh: float = 0.0
    sprints: int = 0
    in_sprint: bool = False
    last_pos: tuple[float, float, int, int] | None = None  # x, y, clock_ms, period
    played_ms: int = 0
    on_since_ms: int | None = None
    sent_off: bool = False


@dataclass
class PassRecord:
    clock_ms: int
    seq: int
    team_id: str
    passer_id: str | None
    recipient_id: str | None
    completed: bool
    length_m: float
    difficulty: float
    possession_index: int


@dataclass
class ShotRecord:
    clock_ms: int
    seq: int
    team_id: str
    player_id: str | None
    xg: float
    speed_kmh: float
    distance_m: float
    on_target: bool
    outcome: str
    big_chance: bool
    assister_id: str | None
    assist_difficulty: float | None
    wp_before: WinProbability | None = None


@dataclass
class GoalRecord:
    seq: int
    clock_ms: int
    team_id: str
    scorer_id: str | None
    shot: ShotRecord | None
    assister_id: str | None
    assist_difficulty: float | None


@dataclass
class EventEffects:
    """What one event did — the input to moment detection."""

    duplicate: bool = False
    possessing_team: str | None = None
    turnover: bool = False
    new_possession: mx.Possession | None = None
    final_third_entry: bool = False
    box_entry: bool = False
    completed_pass: PassRecord | None = None
    shot: ShotRecord | None = None
    goal: GoalRecord | None = None
    red_card_player_id: str | None = None
    yellow_card_player_id: str | None = None
    phase_before: MatchPhase | None = None
    period_ended: int | None = None
    match_ended: bool = False


@dataclass
class FrameEffects:
    """What one tracking frame did."""

    duplicate: bool = False
    top_speeds: list[tuple[str, float, float]] = field(default_factory=list)  # player, new top, previous top
    distance_crossings: list[tuple[str, float]] = field(default_factory=list)  # player, km


class MatchTracker:
    """In-memory live state for one match."""

    def __init__(
        self,
        match_id: str,
        home: Team | None = None,
        away: Team | None = None,
        teams_by_id: dict[str, Team] | None = None,
        params: EngineParams = DEFAULT_PARAMS,
    ) -> None:
        self.match_id = match_id
        self.params = params
        self.teams_by_id = dict(teams_by_id or {})
        self.home: Team | None = home
        self.away: Team | None = away
        self.home_id: str | None = home.team_id if home else None
        self.away_id: str | None = away.team_id if away else None

        self.last_seq = -1
        self.last_frame_id = -1
        self.period = 1
        self.clock_ms = 0
        self.period_start_ms: dict[int, int] = {1: 0}
        self.score_home = 0
        self.score_away = 0
        self.finished = False
        self.started = False

        self.teams: dict[str, TeamAcc] = {}
        self.players: dict[str, PlayerAcc] = {}
        self.player_names: dict[str, str] = {}
        self.possessions: list[mx.Possession] = []
        self.current: mx.Possession | None = None

        mp = params.metrics
        self.pass_window: mx.Window[PassRecord] = mx.Window(mp.window_s)
        self.turnover_window: mx.Window[str] = mx.Window(mp.window_s)  # team that LOST the ball
        self.shot_window: mx.Window[ShotRecord] = mx.Window(max(mp.window_s, params.winprob.xg_window_min * 60))
        self.box_entry_window: mx.Window[str] = mx.Window(mp.window_s)
        self.phase = mx.PhaseTracker(mp)
        self.momentum = 0.0
        self.total_passes = 0
        self.max_sprint_kmh = 0.0

        self._last_completed: dict[str, PassRecord] = {}
        self._last_shot: dict[str, ShotRecord] = {}
        self._pending_error: tuple[str, int] | None = None  # (player who lost it, possession index)
        self._error_charged: set[int] = set()
        self._counted_goals = {"home": 0, "away": 0}

        if home or away:
            self._init_rosters()

    # ------------------------------------------------------------------ teams / players

    def _init_rosters(self) -> None:
        for team in (self.home, self.away):
            if team is None:
                continue
            self.team(team.team_id)
            for i, p in enumerate(team.players):
                self.player_names[p.player_id] = p.short_name
                acc = self.player(p.player_id, team.team_id)
                if i < 11:
                    acc.on_since_ms = 0

    def _learn_teams(self, ev: MatchEvent) -> None:
        """Identify home/away: `extra.home_team_id/away_team_id` on any event, else first two teams seen."""
        if self.home_id and self.away_id:
            return
        h, a = ev.extra.get("home_team_id"), ev.extra.get("away_team_id")
        if h and a:
            self.home_id, self.away_id = str(h), str(a)
        elif ev.team_id and ev.team_id not in (self.home_id, self.away_id):
            if self.home_id is None:
                self.home_id = ev.team_id
            else:
                self.away_id = ev.team_id
        else:
            return
        if self.home is None and self.home_id in self.teams_by_id:
            self.home = self.teams_by_id[self.home_id]
        if self.away is None and self.away_id in self.teams_by_id:
            self.away = self.teams_by_id[self.away_id]
        if self.home_id and self.away_id:
            self._init_rosters()

    def team(self, team_id: str) -> TeamAcc:
        if team_id not in self.teams:
            self.teams[team_id] = TeamAcc(team_id, mx.DecayingScore(self.params.metrics.pressure_half_life_s))
        return self.teams[team_id]

    def player(self, player_id: str, team_id: str | None) -> PlayerAcc:
        if player_id not in self.players:
            acc = PlayerAcc(player_id, team_id or "")
            acc.on_since_ms = self.clock_ms
            self.players[player_id] = acc
        return self.players[player_id]

    def other(self, team_id: str | None) -> str | None:
        if team_id == self.home_id:
            return self.away_id
        if team_id == self.away_id:
            return self.home_id
        return None

    def team_name(self, team_id: str | None) -> str:
        t = self.teams_by_id.get(team_id or "") or next(
            (x for x in (self.home, self.away) if x and x.team_id == team_id), None
        )
        return t.name if t else (team_id or "")

    def player_name(self, player_id: str | None) -> str:
        return self.player_names.get(player_id or "", player_id or "")

    def side(self, team_id: str | None) -> str:
        """'home' / 'away' for a team id."""
        return "home" if team_id == self.home_id else "away"

    # ------------------------------------------------------------------ time

    @property
    def minute(self) -> float:
        """Football minute (float). P2 restarts at 45 regardless of first-half stoppage."""
        start = self.period_start_ms.get(self.period, 0)
        return PERIOD_START_MIN.get(self.period, 0.0) + (self.clock_ms - start) / 60000.0

    @property
    def minute_label(self) -> str:
        return minute_label(self.period, self.minute)

    @property
    def score_label(self) -> str:
        return f"{self.score_home}-{self.score_away}"

    def _advance(self, clock_ms: int) -> None:
        dt = max(0, clock_ms - self.clock_ms)
        if dt and self.current is not None and self.started and not self.finished:
            self.team(self.current.team_id).possession_ms += dt
        self.clock_ms = max(self.clock_ms, clock_ms)

    # ------------------------------------------------------------------ events

    def apply_event(self, ev: MatchEvent) -> EventEffects:
        """Apply one event. Idempotent: events with seq <= last applied seq are ignored."""
        if ev.match_id != self.match_id or ev.seq <= self.last_seq:
            return EventEffects(duplicate=True)
        self.last_seq = ev.seq
        fx = EventEffects()
        self._learn_teams(ev)
        if ev.period != self.period:
            self.period = ev.period
            self.period_start_ms.setdefault(ev.period, ev.clock_ms)
        self._advance(ev.clock_ms)
        t = ev.type

        if t == E.MATCH_START or t == E.KICK_OFF or t == E.PERIOD_START:
            self.started = True
        if t == E.PERIOD_START:
            self.period_start_ms[ev.period] = ev.clock_ms

        self._track_possession(ev, fx)
        if ev.player_id and ev.type in POSSESSION_TYPES | {E.TACKLE, E.SAVE}:
            self.player(ev.player_id, ev.team_id).touches += 1

        handler = _HANDLERS.get(t)
        if handler:
            handler(self, ev, fx)
        self._track_entries(ev, fx)
        self._sync_score(ev)

        # metric updates that depend on the event clock
        self.momentum = mx.clamp(
            mx.ema(
                self.momentum,
                self.pressure_index(self.home_id) - self.pressure_index(self.away_id),
                self.params.metrics.momentum_alpha,
            ),
            -100.0,
            100.0,
        )
        if self.clock_ms >= self.params.metrics.phase_min_elapsed_s * 1000:
            fx.phase_before = self.phase.update(self._instant_phase(), self.clock_ms)
        return fx

    def _track_possession(self, ev: MatchEvent, fx: EventEffects) -> None:
        gains = ev.type in POSSESSION_TYPES or (ev.type == E.TACKLE and ev.outcome == Outcome.SUCCESS)
        if not gains or not ev.team_id:
            return
        cur = self.current
        changed_team = cur is None or cur.team_id != ev.team_id
        changed_id = (
            cur is not None
            and ev.possession_id is not None
            and cur.possession_id is not None
            and ev.possession_id != cur.possession_id
        )
        if cur is not None and cur.possession_id is None and ev.possession_id is not None and not changed_team:
            cur.possession_id = ev.possession_id
        if not (changed_team or changed_id):
            fx.possessing_team = ev.team_id
            if self._pending_error is not None and self._pending_error[1] == -1:
                self._pending_error = None  # the failed action did not cost the ball after all
            return
        if cur is not None:
            cur.end_ms = ev.clock_ms
            if changed_team and ev.type not in RESTARTS:
                fx.turnover = True
                self.turnover_window.add(ev.clock_ms, cur.team_id)
        new = mx.Possession(len(self.possessions), ev.team_id, ev.clock_ms, ev.possession_id)
        self.possessions.append(new)
        self.current = new
        fx.new_possession = new
        fx.possessing_team = ev.team_id
        if fx.turnover and self._pending_error is not None:
            self._pending_error = (self._pending_error[0], new.index)
        elif changed_team:
            self._pending_error = None

    def _track_entries(self, ev: MatchEvent, fx: EventEffects) -> None:
        """Final-third / box entries from completed on-ball moves (team-relative coordinates)."""
        if ev.type not in (E.PASS, E.CARRY, E.DRIBBLE) or ev.outcome != Outcome.SUCCESS or not ev.team_id:
            return
        if ev.x is None or ev.end_x is None:
            return
        team = self.team(ev.team_id)
        y, ey = ev.y if ev.y is not None else 34.0, ev.end_y if ev.end_y is not None else 34.0
        p = self.params.metrics
        if ev.x < FINAL_THIRD_X <= ev.end_x:
            team.final_third_entries += 1
            team.pressure.add(p.pts_final_third_entry, ev.clock_ms)
            fx.final_third_entry = True
        if self.current is not None and ev.end_x >= FINAL_THIRD_X:
            self.current.reached_final_third = True
        if not in_box(ev.x, y) and in_box(ev.end_x, ey):
            team.box_entries += 1
            team.pressure.add(p.pts_box_entry, ev.clock_ms)
            self.box_entry_window.add(ev.clock_ms, ev.team_id)
            fx.box_entry = True

    def _sync_score(self, ev: MatchEvent) -> None:
        """Score = max(goals counted from goal events, score carried on the event)."""
        self.score_home = max(self.score_home, self._counted_goals["home"], ev.score_home)
        self.score_away = max(self.score_away, self._counted_goals["away"], ev.score_away)
        if self.home_id:
            self.team(self.home_id).goals = self.score_home
        if self.away_id:
            self.team(self.away_id).goals = self.score_away

    # -- handlers

    def _on_pass(self, ev: MatchEvent, fx: EventEffects) -> None:
        if not ev.team_id:
            return
        pa = ev.pass_
        completed = ev.outcome == Outcome.SUCCESS
        difficulty = pa.difficulty if pa else 0.0
        team = self.team(ev.team_id)
        team.passes += 1
        team.difficulty_sum += difficulty
        self.total_passes += 1
        rp = self.params.ratings
        rec = PassRecord(
            ev.clock_ms,
            ev.seq,
            ev.team_id,
            ev.player_id,
            pa.recipient_id if pa else None,
            completed,
            pa.length_m if pa else 0.0,
            difficulty,
            self.current.index if self.current else -1,
        )
        self.pass_window.add(ev.clock_ms, rec)
        if self.current is not None and self.current.team_id == ev.team_id:
            self.current.passes += 1
        pl = self.player(ev.player_id, ev.team_id) if ev.player_id else None
        if pl:
            pl.passes += 1
            pl.difficulty_sum += difficulty
        if completed:
            team.passes_completed += 1
            self._last_completed[ev.team_id] = rec
            fx.completed_pass = rec
            if pl:
                pl.passes_completed += 1
                pl.rating_delta += mx.completed_pass_rating(difficulty, rp)
            if pa and pa.into_box:
                team.pressure.add(self.params.metrics.pts_pass_into_box, ev.clock_ms)
        else:
            if pl:
                pl.rating_delta += rp.failed_pass
                self._pending_error = (pl.player_id, -1)

    def _on_dribble(self, ev: MatchEvent, fx: EventEffects) -> None:
        if not ev.player_id:
            return
        pl = self.player(ev.player_id, ev.team_id)
        if ev.outcome == Outcome.SUCCESS:
            pl.rating_delta += self.params.ratings.dribble_won
        elif ev.outcome == Outcome.FAIL:
            self._pending_error = (pl.player_id, -1)

    def _on_shot(self, ev: MatchEvent, fx: EventEffects) -> None:
        if not ev.team_id:
            return
        sh = ev.shot
        xg = sh.xg if sh else 0.0
        on_target = (sh.on_target if sh else False) or ev.outcome in (Outcome.GOAL, Outcome.SAVED)
        team = self.team(ev.team_id)
        team.shots += 1
        team.xg += xg
        team.shots_on_target += int(on_target)
        speed = sh.shot_speed_kmh if sh else 0.0
        team.max_shot_speed_kmh = max(team.max_shot_speed_kmh, speed)
        p = self.params.metrics
        team.pressure.add(p.pts_shot_base + p.pts_shot_per_xg * xg, ev.clock_ms)
        if self.current is not None:
            self.current.shots += 1

        assister = sh.assist_player_id if sh else None
        assist_difficulty = None
        last = self._last_completed.get(ev.team_id)
        if (
            last
            and self.current
            and last.possession_index == self.current.index
            and (assister == last.passer_id or (assister is None and last.recipient_id == ev.player_id))
        ):
            assister = last.passer_id
            assist_difficulty = last.difficulty
            self._last_completed.pop(ev.team_id, None)
            if assister:
                ap = self.player(assister, ev.team_id)
                ap.key_passes += 1
                ap.rating_delta += self.params.ratings.key_pass
        elif assister:
            ap = self.player(assister, ev.team_id)
            ap.key_passes += 1
            ap.rating_delta += self.params.ratings.key_pass

        if ev.player_id:
            pl = self.player(ev.player_id, ev.team_id)
            pl.shots += 1
            pl.xg += xg
            pl.rating_delta += self.params.ratings.per_xg * xg
            if on_target:
                pl.rating_delta += self.params.ratings.shot_on_target

        # own error leading to a shot: the player who lost the ball that started this possession
        if self._pending_error and self.current and self._pending_error[1] == self.current.index:
            culprit, idx = self._pending_error
            if idx not in self._error_charged and culprit in self.players:
                self.players[culprit].rating_delta += self.params.ratings.error_leading_to_shot
                self._error_charged.add(idx)
            self._pending_error = None

        rec = ShotRecord(
            ev.clock_ms,
            ev.seq,
            ev.team_id,
            ev.player_id,
            xg,
            speed,
            sh.distance_m if sh else 0.0,
            on_target,
            str(ev.outcome),
            sh.big_chance if sh else False,
            assister,
            assist_difficulty,
        )
        self.shot_window.add(ev.clock_ms, rec)
        self._last_shot[ev.team_id] = rec
        fx.shot = rec

    def _on_goal(self, ev: MatchEvent, fx: EventEffects) -> None:
        if not ev.team_id:
            return
        self._counted_goals[self.side(ev.team_id)] += 1
        shot = self._last_shot.get(ev.team_id)
        scorer = ev.player_id or (shot.player_id if shot else None)
        assister = shot.assister_id if shot else None
        if scorer:
            pl = self.player(scorer, ev.team_id)
            pl.goals += 1
            pl.rating_delta += self.params.ratings.goal
        if assister:
            ap = self.player(assister, ev.team_id)
            ap.assists += 1
            ap.rating_delta += self.params.ratings.assist
        fx.goal = GoalRecord(
            ev.seq, ev.clock_ms, ev.team_id, scorer, shot, assister, shot.assist_difficulty if shot else None
        )

    def _on_defensive(self, ev: MatchEvent, fx: EventEffects) -> None:
        if not ev.team_id:
            return
        won = ev.type != E.TACKLE or ev.outcome == Outcome.SUCCESS
        if ev.player_id and won and ev.type in (E.TACKLE, E.INTERCEPTION):
            pl = self.player(ev.player_id, ev.team_id)
            if ev.type == E.TACKLE:
                pl.tackles_won += 1
            else:
                pl.interceptions += 1
            pl.rating_delta += self.params.ratings.tackle_or_interception
        if won and ev.x is not None and ev.x >= HALF_X:
            self.team(ev.team_id).pressure.add(self.params.metrics.pts_recovery_opp_half, ev.clock_ms)

    def _on_corner(self, ev: MatchEvent, fx: EventEffects) -> None:
        if ev.team_id:
            team = self.team(ev.team_id)
            team.corners += 1
            team.pressure.add(self.params.metrics.pts_corner, ev.clock_ms)

    def _on_foul(self, ev: MatchEvent, fx: EventEffects) -> None:
        if ev.team_id:
            self.team(ev.team_id).fouls += 1

    def _on_card(self, ev: MatchEvent, fx: EventEffects) -> None:
        colour = ev.card.colour if ev.card else ("red" if ev.outcome == Outcome.RED else "yellow")
        team = self.team(ev.team_id) if ev.team_id else None
        pl = self.player(ev.player_id, ev.team_id) if ev.player_id else None
        if colour in ("yellow", "second_yellow"):
            if team:
                team.yellow_cards += 1
            if pl:
                pl.rating_delta += self.params.ratings.yellow
            fx.yellow_card_player_id = ev.player_id
        if colour in ("red", "second_yellow"):
            if team:
                team.red_cards += 1
            if pl:
                pl.rating_delta += self.params.ratings.red
                self._take_off(pl)
                pl.sent_off = True
            fx.red_card_player_id = ev.player_id or ""

    def _on_substitution(self, ev: MatchEvent, fx: EventEffects) -> None:
        sub = ev.substitution
        if not sub:
            return
        self._take_off(self.player(sub.player_off_id, ev.team_id))
        on = self.player(sub.player_on_id, ev.team_id)
        on.on_since_ms = ev.clock_ms

    def _on_period_end(self, ev: MatchEvent, fx: EventEffects) -> None:
        fx.period_ended = ev.period
        if ev.period >= 2:  # no extra time in the simulator: end of P2 is full time
            self._on_match_end(ev, fx)

    def _on_match_end(self, ev: MatchEvent, fx: EventEffects) -> None:
        if self.finished:
            return
        for pl in self.players.values():
            self._take_off(pl)
        self.finished = True
        fx.match_ended = True
        if self.current is not None:
            self.current.end_ms = ev.clock_ms

    def _take_off(self, pl: PlayerAcc) -> None:
        if pl.on_since_ms is not None:
            pl.played_ms += max(0, self.clock_ms - pl.on_since_ms)
            pl.on_since_ms = None

    # ------------------------------------------------------------------ tracking

    def apply_frame(self, frame: TrackingFrame) -> FrameEffects:
        """Accumulate distance, top speed and sprints from a tracking frame. Idempotent on frame_id."""
        if frame.match_id != self.match_id or frame.frame_id <= self.last_frame_id:
            return FrameEffects(duplicate=True)
        self.last_frame_id = frame.frame_id
        if frame.period == self.period and not self.finished:
            self._advance(frame.clock_ms)  # frame-driven moments carry the frame's sim time
        fx = FrameEffects()
        mp = self.params.metrics
        milestone_m = self.params.moments.milestone_distance_km * 1000
        for pf in frame.players:
            pl = self.player(pf.player_id, pf.team_id)
            if pl.last_pos is not None:
                x0, y0, t0, period0 = pl.last_pos
                dt_s = (frame.clock_ms - t0) / 1000.0
                if period0 == frame.period and 0 < dt_s <= mp.max_tracking_gap_s:
                    d = math.hypot(pf.x - x0, pf.y - y0)
                    if d / dt_s * 3.6 <= mp.max_plausible_kmh:
                        before = pl.distance_m
                        pl.distance_m += d
                        if before < milestone_m <= pl.distance_m:
                            fx.distance_crossings.append((pl.player_id, pl.distance_m / 1000))
            pl.last_pos = (pf.x, pf.y, frame.clock_ms, frame.period)
            speed = min(pf.speed_kmh, mp.max_plausible_kmh)
            if speed > pl.top_speed_kmh:
                fx.top_speeds.append((pl.player_id, speed, pl.top_speed_kmh))
                pl.top_speed_kmh = speed
            sprinting = speed >= mp.sprint_kmh
            if sprinting and not pl.in_sprint:
                pl.sprints += 1
            pl.in_sprint = sprinting
        return fx

    # ------------------------------------------------------------------ derived metrics

    def pressure_index(self, team_id: str | None) -> float:
        if not team_id:
            return 0.0
        return mx.pressure_index(self.team(team_id).pressure.value_at(self.clock_ms), self.params.metrics)

    def _window_lo(self) -> int:
        return self.clock_ms - int(self.params.metrics.window_s * 1000)

    def _window_possessions(self) -> list[mx.Possession]:
        lo = self._window_lo()
        out = []
        for p in reversed(self.possessions):
            if p.end_ms is not None and p.end_ms < lo:
                break
            out.append(p)
        return out

    def control_index(self, team_id: str | None) -> float:
        if not team_id or self.home_id is None or self.away_id is None:
            return 0.0
        poss = self._window_possessions()
        shares = mx.possession_shares(poss, (self.home_id, self.away_id), self._window_lo(), self.clock_ms)
        passes = [p for p in self.pass_window.items(self.clock_ms) if p.team_id == team_id]
        completion = sum(p.completed for p in passes) / len(passes) if passes else 0.0
        own = [p for p in poss if p.team_id == team_id]
        mean_passes = sum(p.passes for p in own) / len(own) if own else 0.0
        return mx.control_index(shares[team_id], completion, mean_passes, self.params.metrics)

    def turnovers_last_window(self) -> int:
        return len(self.turnover_window.items(self.clock_ms))

    def chaos_index(self) -> float:
        mp = self.params.metrics
        per_min = self.turnovers_last_window() / mx.rate_window_min(self.clock_ms, mp)
        closed = [p for p in self._window_possessions() if p.end_ms is not None]
        short = sum(p.passes < mp.chaos_short_possession_passes for p in closed) / len(closed) if closed else 0.0
        passes = self.pass_window.items(self.clock_ms)
        long_share = sum(p.length_m > mp.chaos_long_pass_m for p in passes) / len(passes) if passes else 0.0
        return mx.chaos_index(per_min, short, long_share, mp)

    def tempo_ppm(self) -> float:
        return len(self.pass_window.items(self.clock_ms)) / mx.rate_window_min(self.clock_ms, self.params.metrics)

    def tempo_match_avg(self) -> float:
        elapsed_min = max(1.0, self.clock_ms / 60000.0)
        return self.total_passes / elapsed_min

    def _instant_phase(self) -> MatchPhase:
        return mx.classify_phase(
            self.chaos_index(), self.control_index(self.home_id), self.control_index(self.away_id), self.params.metrics
        )

    def recent_shots(self, team_id: str | None, window_s: float) -> list[ShotRecord]:
        lo = self.clock_ms - int(window_s * 1000)
        return [s for t, s in self.shot_window.stamped(self.clock_ms) if t >= lo and s.team_id == team_id]

    def box_entries_last_window(self, team_id: str | None) -> int:
        return sum(t == team_id for t in self.box_entry_window.items(self.clock_ms))

    def win_prob(self) -> WinProbability:
        """Live outcome probability for the current state (MODEL_SPEC §3)."""
        wpp = self.params.winprob
        xg_h = sum(s.xg for s in self.recent_shots(self.home_id, wpp.xg_window_min * 60))
        xg_a = sum(s.xg for s in self.recent_shots(self.away_id, wpp.xg_window_min * 60))
        return win_probability(
            home=TeamStrength.from_team(self.home, wpp),
            away=TeamStrength.from_team(self.away, wpp),
            period=self.period,
            minute=self.minute,
            score_home=self.score_home,
            score_away=self.score_away,
            xg_last_window_home=xg_h,
            xg_last_window_away=xg_a,
            reds_home=self.team(self.home_id).red_cards if self.home_id else 0,
            reds_away=self.team(self.away_id).red_cards if self.away_id else 0,
            finished=self.finished,
            params=wpp,
        )

    # ------------------------------------------------------------------ snapshot

    def possession_pct(self, team_id: str | None) -> float:
        total = sum(t.possession_ms for t in self.teams.values())
        if not team_id or total == 0:
            return 50.0
        return 100.0 * self.team(team_id).possession_ms / total

    def _team_stats(self, team_id: str | None) -> TeamLiveStats:
        if not team_id:
            return TeamLiveStats(team_id="")
        t = self.team(team_id)
        return TeamLiveStats(
            team_id=team_id,
            goals=t.goals,
            xg=round(t.xg, 2),
            shots=t.shots,
            shots_on_target=t.shots_on_target,
            passes=t.passes,
            passes_completed=t.passes_completed,
            pass_accuracy=round(100.0 * t.passes_completed / t.passes, 1) if t.passes else 0.0,
            avg_pass_difficulty=round(t.difficulty_sum / t.passes, 2) if t.passes else 0.0,
            possession_pct=round(self.possession_pct(team_id), 1),
            final_third_entries=t.final_third_entries,
            box_entries=t.box_entries,
            corners=t.corners,
            fouls=t.fouls,
            yellow_cards=t.yellow_cards,
            red_cards=t.red_cards,
            pressure_index=round(self.pressure_index(team_id), 1),
            control_index=round(self.control_index(team_id), 1),
            max_shot_speed_kmh=round(t.max_shot_speed_kmh, 1),
        )

    def player_minutes(self, pl: PlayerAcc) -> float:
        live = self.clock_ms - pl.on_since_ms if pl.on_since_ms is not None else 0
        return (pl.played_ms + max(0, live)) / 60000.0

    def player_rating(self, pl: PlayerAcc) -> float:
        return mx.player_rating(pl.rating_delta, self.params.ratings)

    def _player_stats(self, pl: PlayerAcc) -> PlayerLiveStats:
        return PlayerLiveStats(
            player_id=pl.player_id,
            team_id=pl.team_id,
            minutes=round(self.player_minutes(pl), 1),
            touches=pl.touches,
            passes=pl.passes,
            passes_completed=pl.passes_completed,
            pass_accuracy=round(100.0 * pl.passes_completed / pl.passes, 1) if pl.passes else 0.0,
            avg_pass_difficulty=round(pl.difficulty_sum / pl.passes, 2) if pl.passes else 0.0,
            key_passes=pl.key_passes,
            shots=pl.shots,
            xg=round(pl.xg, 2),
            goals=pl.goals,
            tackles_won=pl.tackles_won,
            interceptions=pl.interceptions,
            distance_km=round(pl.distance_m / 1000.0, 2),
            top_speed_kmh=round(pl.top_speed_kmh, 1),
            sprints=pl.sprints,
            rating=round(self.player_rating(pl), 2),
        )

    def snapshot(self, win_prob: WinProbability | None = None) -> MatchState:
        """Contract `MatchState` for the current moment."""
        return MatchState(
            match_id=self.match_id,
            seq=max(self.last_seq, 0),
            period=self.period,
            clock_ms=self.clock_ms,
            score_home=self.score_home,
            score_away=self.score_away,
            home=self._team_stats(self.home_id),
            away=self._team_stats(self.away_id),
            win_prob=win_prob or self.win_prob(),
            phase=self.phase.phase,
            chaos_index=round(self.chaos_index(), 1),
            tempo_ppm=round(self.tempo_ppm(), 1),
            momentum=round(self.momentum, 1),
            players={pid: self._player_stats(pl) for pid, pl in self.players.items()},
        )


_HANDLERS = {
    E.PASS: MatchTracker._on_pass,
    E.DRIBBLE: MatchTracker._on_dribble,
    E.SHOT: MatchTracker._on_shot,
    E.GOAL: MatchTracker._on_goal,
    E.TACKLE: MatchTracker._on_defensive,
    E.INTERCEPTION: MatchTracker._on_defensive,
    E.BALL_RECOVERY: MatchTracker._on_defensive,
    E.CORNER: MatchTracker._on_corner,
    E.FOUL: MatchTracker._on_foul,
    E.CARD: MatchTracker._on_card,
    E.SUBSTITUTION: MatchTracker._on_substitution,
    E.PERIOD_END: MatchTracker._on_period_end,
    E.MATCH_END: MatchTracker._on_match_end,
}
