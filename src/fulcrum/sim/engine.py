"""Possession-chain match simulator (MODEL_SPEC §1).

Pure and seeded: `simulate(config, home, away)` yields `MatchEvent` and `TrackingFrame` models in
non-decreasing `clock_ms` order, with no wall-clock, I/O or bus access. The same config (incl. seed)
always yields an identical stream — ids and `ts` are derived from the seed and sim time, not uuid4/now().

Coordinates: events are in the acting team's frame (attacks towards x = 105); the ball and tracking
are kept in absolute coordinates (home attacks towards x = 105 in period 1).
"""

from __future__ import annotations

import hashlib
import math
import random
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import numpy as np

from fulcrum.contracts import (
    BodyPart,
    CardAttributes,
    EventType,
    MatchConfig,
    MatchEvent,
    Outcome,
    PassAttributes,
    Player,
    Position,
    ShotAttributes,
    SubstitutionAttributes,
    Team,
    TrackingFrame,
)

from . import pitch
from .params import DEFAULT_PARAMS, SimParams
from .players import SLOTS_PER_TEAM, Tracking, mean_rating

SimOutput = MatchEvent | TrackingFrame

# Synthetic wall-clock anchor for `ts` so fixtures are byte-identical. The runner overwrites `ts`
# with the real time when pacing a live match.
KICKOFF_TS = datetime(2026, 10, 24, 15, 0, tzinfo=UTC)
HALF_TIME_BREAK_S = 15 * 60
PERIOD_S = 45 * 60

_RESTART_EVENT = {
    "kick_off": EventType.KICK_OFF,
    "throw_in": EventType.THROW_IN,
    "goal_kick": EventType.GOAL_KICK,
    "corner": EventType.CORNER,
    "free_kick": EventType.FREE_KICK,
}
_DEAD_BALL = {"kick_off", "throw_in", "goal_kick", "corner", "free_kick"}  # subs allowed here


@dataclass
class Restart:
    """A pending restart: `team` takes `kind` at (x, y) in its own frame after `gap_s` of dead time."""

    kind: str  # kick_off | throw_in | goal_kick | corner | free_kick | penalty | keeper
    team: int
    x: float
    y: float
    gap_s: float


@dataclass
class LastPass:
    """Most recent completed pass of the current possession (assists and the xG bonus)."""

    passer: int
    recipient: int
    bonus: bool  # through ball or cutback
    cross: bool
    touches: int = 0  # carries/dribbles by the recipient since receiving


def simulate(config: MatchConfig, home: Team, away: Team, params: SimParams = DEFAULT_PARAMS) -> Iterator[SimOutput]:
    """Yield the full match as contract models, in order. Pure function of its arguments."""
    return MatchSimulator(config, home, away, params).run()


class MatchSimulator:
    """One match. Call `run()` once; afterwards `score` and `tracking.totals()` hold the result."""

    def __init__(self, config: MatchConfig, home: Team, away: Team, params: SimParams = DEFAULT_PARAMS) -> None:
        if home.team_id != config.home_team_id or away.team_id != config.away_team_id:
            raise ValueError("home/away teams do not match the MatchConfig")
        self.cfg = config
        self.p = params
        self.teams = (home, away)
        self.rng = random.Random(config.seed)
        self.tracking = Tracking(home, away, np.random.default_rng(config.seed), params)
        self.frame_dt = 1.0 / config.tracking_hz
        self.out: list[SimOutput] = []
        self.seq = 0
        self.frame_id = 0
        self.t = 0.0  # seconds since kick-off, continuous across halves
        self.period = 1
        self.period_start = 0.0
        self.period_end = float(PERIOD_S)
        self.stoppage_min = 0
        self.next_frame_t = 0.0
        self.last_frame_ball: tuple[float, float] | None = None
        self.ball = pitch.CENTRE  # absolute
        self.score = [0, 0]
        self.possession_id = 0
        self.poss: int | None = None
        self.carrier = 0
        self.poss_start_t = 0.0
        self.poss_start_x = 50.0
        self.last_pass: LastPass | None = None
        self.restart: Restart | None = None
        self.yellows: set[str] = set()
        self.used_bench: set[str] = set()
        self.sub_times = [self._plan_subs(), self._plan_subs()]

    # ================================================================== driver
    def run(self) -> Iterator[SimOutput]:
        """Generate the whole match."""
        self._emit(EventType.MATCH_START, extra={"seed": self.cfg.seed, "venue": self.cfg.venue})
        yield from self._flush()
        for period in (1, 2):
            self._start_period(period)
            yield from self._flush()
            while self.t < self.period_end:
                self._step()
                yield from self._flush()
            self._end_period()
            yield from self._flush()
        self._emit(EventType.MATCH_END)
        yield from self._flush()

    def _flush(self) -> Iterator[SimOutput]:
        out, self.out = self.out, []
        yield from out

    def _start_period(self, period: int) -> None:
        self.period = period
        self.period_start = self.t
        lo, hi = self.p.stoppage_h1_min if period == 1 else self.p.stoppage_h2_min
        self.stoppage_min = self.rng.randint(lo, hi)
        self.period_end = self.period_start + PERIOD_S + 60 * self.stoppage_min
        self.ball = pitch.CENTRE
        self.tracking.reset(self.ball, self._dirs())
        self.next_frame_t = self.t
        self.last_frame_ball = None
        self._emit(EventType.PERIOD_START)
        self._advance(0.0)
        kicker = 0 if period == 1 else 1
        self.restart = Restart("kick_off", kicker, *pitch.CENTRE, 0.0)

    def _end_period(self) -> None:
        added_ms = int(round((self.t - self.period_start - PERIOD_S) * 1000))
        self._emit(EventType.PERIOD_END, extra={"stoppage_min": self.stoppage_min, "added_time_ms": added_ms})
        self.restart = None
        self.last_pass = None

    def _step(self) -> None:
        if self.restart is not None:
            self._take_restart()
        else:
            self._carrier_action()

    # ================================================================== frames & time
    def _dirs(self) -> tuple[int, int]:
        home_dir = 1 if self.period == 1 else -1
        return home_dir, -home_dir

    def _abs(self, team: int, x: float, y: float) -> tuple[float, float]:
        """Team frame → absolute (and, being a 180° rotation, absolute → team frame)."""
        return (x, y) if self._dirs()[team] > 0 else pitch.flip(x, y)

    _rel = _abs

    def _ball_rel(self, team: int) -> tuple[float, float]:
        return self._rel(team, *self.ball)

    def _advance(
        self,
        dt: float,
        ball_to: tuple[float, float] | None = None,
        receiver: int | None = None,
    ) -> None:
        """Move sim time on by `dt` s, the ball linearly to `ball_to`, emitting tracking frames on the grid."""
        t0, t1 = self.t, self.t + dt
        b0 = self.ball
        b1 = ball_to if ball_to is not None else b0
        while self.next_frame_t <= t1 + 1e-9:
            ft = self.next_frame_t
            frac = 1.0 if dt <= 0 else min(max((ft - t0) / dt, 0.0), 1.0)
            ball = (b0[0] + frac * (b1[0] - b0[0]), b0[1] + frac * (b1[1] - b0[1]))
            self.tracking.step(
                self.frame_dt, ball, self.poss, self.carrier if receiver is None else None, self._dirs(), receiver, b1
            )
            if self.cfg.emit_tracking:
                prev = self.last_frame_ball or ball
                ball_speed = math.hypot(ball[0] - prev[0], ball[1] - prev[1]) / self.frame_dt * 3.6
                self.out.append(
                    TrackingFrame(
                        match_id=self.cfg.match_id,
                        frame_id=self.frame_id,
                        period=self.period,
                        clock_ms=int(round(ft * 1000)),
                        ball_x=round(ball[0], 2),
                        ball_y=round(ball[1], 2),
                        ball_speed_kmh=round(ball_speed, 1),
                        players=self.tracking.frame_players(),
                    )
                )
            self.last_frame_ball = ball
            self.frame_id += 1
            self.next_frame_t += self.frame_dt
        self.t = t1
        self.ball = b1

    def _minute(self) -> float:
        return (self.t - self.period_start) / 60.0 + (0.0 if self.period == 1 else 45.0)

    # ================================================================== emission
    def _player(self, slot: int) -> Player:
        return self.tracking.players[slot]

    def _team_of(self, slot: int) -> int:
        return slot // SLOTS_PER_TEAM

    def _emit(
        self,
        etype: EventType,
        team: int | None = None,
        slot: int | None = None,
        xy: tuple[float, float] | None = None,
        end: tuple[float, float] | None = None,
        **fields,
    ) -> MatchEvent:
        """Append a MatchEvent. `xy`/`end` are in the acting team's frame."""
        if xy is not None:
            xy = pitch.clamp_point(*xy)
        if end is not None:
            end = pitch.clamp_point(*end)
        ev = MatchEvent(
            match_id=self.cfg.match_id,
            event_id=hashlib.md5(f"{self.cfg.match_id}:{self.seq}".encode()).hexdigest(),
            seq=self.seq,
            ts=KICKOFF_TS + timedelta(seconds=self.t + (HALF_TIME_BREAK_S if self.period == 2 else 0)),
            period=self.period,
            clock_ms=int(round(self.t * 1000)),
            type=etype,
            team_id=None if team is None else self.teams[team].team_id,
            player_id=None if slot is None else self._player(slot).player_id,
            possession_id=self.possession_id or None,
            x=None if xy is None else round(xy[0], 2),
            y=None if xy is None else round(xy[1], 2),
            end_x=None if end is None else round(end[0], 2),
            end_y=None if end is None else round(end[1], 2),
            score_home=self.score[0],
            score_away=self.score[1],
            **fields,
        )
        self.seq += 1
        self.out.append(ev)
        return ev

    def _take_possession(self, team: int, slot: int) -> None:
        """Start a new possession for `team` (emits possession_change unless it's the first)."""
        first = self.poss is None
        self.possession_id += 1
        self.poss = team
        self.carrier = slot
        self.poss_start_t = self.t
        self.poss_start_x = self._ball_rel(team)[0]
        self.last_pass = None
        if not first:
            self._emit(EventType.POSSESSION_CHANGE, team, slot, self._ball_rel(team))

    def _gain(self, team: int, slot: int, etype: EventType, at: tuple[float, float]) -> None:
        """`slot` wins the ball at absolute point `at` with a `etype` event, then possession changes."""
        self.ball = at
        if team == self.poss:
            self.carrier = slot
            self._emit(etype, team, slot, self._ball_rel(team), outcome=Outcome.SUCCESS)
            return
        self.possession_id += 1  # the winning event already belongs to the new possession
        self._emit(etype, team, slot, self._rel(team, *at), outcome=Outcome.SUCCESS)
        self.possession_id -= 1
        self._take_possession(team, slot)

    # ================================================================== helpers
    def _uniform(self, rng_pair: tuple[float, float]) -> float:
        return self.rng.uniform(*rng_pair)

    def _strength(self, team: int) -> float:
        return (self.teams[team].attack_rating - self.teams[1 - team].defence_rating) / 100.0

    def _trailing(self, team: int) -> bool:
        return self._minute() >= self.p.trailing_after_min and self.score[team] < self.score[1 - team]

    def _best(self, team: int, key, *, outfield: bool = True) -> int:
        slots = self.tracking.active_slots(team, outfield=outfield)
        return max(slots, key=lambda s: (key(self._player(s)), -s))

    def _restart_at(self, kind: str, team: int, at_abs: tuple[float, float], gap: tuple[float, float] | float) -> None:
        """Schedule a restart for `team` at an absolute point."""
        x, y = self._rel(team, *at_abs)
        if kind == "throw_in":
            y = 0.0 if y < pitch.WIDTH / 2 else pitch.WIDTH
            x = pitch.clamp(x, 1.0, pitch.LENGTH - 1.0)
        elif kind == "corner":
            x, y = pitch.LENGTH - 0.5, 0.5 if y < pitch.WIDTH / 2 else pitch.WIDTH - 0.5
        elif kind == "goal_kick":
            x, y = 5.5, pitch.GOAL_Y + self.rng.uniform(-6.0, 6.0)
        elif kind == "penalty":
            x, y = pitch.PENALTY_SPOT
        gap_s = gap if isinstance(gap, float) else self._uniform(gap)
        self.restart = Restart(kind, team, x, y, gap_s)

    # ================================================================== restarts
    def _take_restart(self) -> None:
        r = self.restart
        assert r is not None
        self.restart = None
        self.ball = self._abs(r.team, r.x, r.y)
        self._advance(r.gap_s)
        if r.kind in _DEAD_BALL and self._run_due_subs():
            self._advance(self.p.gap_substitution)
        if self.t >= self.period_end and r.kind not in ("kick_off", "penalty"):
            return  # the whistle goes during the stoppage

        if r.kind == "kick_off":
            taker = self.tracking.nearest(r.team, self.ball, outfield=True)
        elif r.kind in ("goal_kick", "keeper"):
            taker = self.tracking.gk_slot(r.team)
        elif r.kind == "corner":
            taker = self._best(r.team, lambda pl: pl.ratings.passing)
        elif r.kind == "penalty":
            taker = self._best(r.team, lambda pl: pl.ratings.finishing)
        else:
            taker = self.tracking.nearest(r.team, self.ball, outfield=True)

        if r.team != self.poss:
            self._take_possession(r.team, taker)
        self.carrier = taker
        self.last_pass = None

        if r.kind in _RESTART_EVENT:
            self._emit(_RESTART_EVENT[r.kind], r.team, taker, (r.x, r.y))

        if r.kind == "kick_off":
            self._pass(restart="kick_off")
        elif r.kind == "throw_in":
            self._pass(restart="throw_in")
        elif r.kind in ("goal_kick", "keeper"):
            self._pass(restart="goal_kick")
        elif r.kind == "corner":
            self._cross(corner=True)
        elif r.kind == "penalty":
            self._shot(situation="penalty")
        else:
            self._free_kick(r)

    def _free_kick(self, r: Restart) -> None:
        p = self.p
        dist = pitch.shot_distance(r.x, r.y)
        if (
            r.x >= p.final_third_x
            and dist <= p.free_kick_shot_max_dist
            and abs(r.y - pitch.GOAL_Y) <= p.free_kick_shot_max_dy
            and self.rng.random() < p.free_kick_shot_prob
        ):
            self.carrier = self._best(r.team, lambda pl: pl.ratings.finishing)
            self._shot(situation="set_piece")
        elif r.x >= p.final_third_x and pitch.is_wide(r.y, p):
            self._cross()
        else:
            self._pass(restart="free_kick")

    # ================================================================== subs
    def _plan_subs(self) -> list[float]:
        """Sorted sim-minute times of this team's substitutions (executed at the next dead ball)."""
        n = self.rng.randint(self.p.subs_min, self.p.subs_max)
        return sorted(self.rng.uniform(self.p.subs_window_start_min, self.p.subs_window_end_min) for _ in range(n))

    def _run_due_subs(self) -> bool:
        if self.period != 2:
            return False
        made = False
        for team in (0, 1):
            while self.sub_times[team] and self.sub_times[team][0] <= self._minute():
                self.sub_times[team].pop(0)
                made |= self._substitute(team)
        return made

    def _substitute(self, team: int) -> bool:
        tr = self.tracking
        off_slot = min(
            tr.active_slots(team, outfield=True),
            key=lambda s: (mean_rating(tr.players[s]) - self.p.sub_fatigue_per_km * tr.player_distance_km(s), s),
        )
        off = tr.players[off_slot]
        bench = [
            pl
            for pl in self.teams[team].players[SLOTS_PER_TEAM:]
            if pl.position != Position.GK and pl.player_id not in self.used_bench
        ]
        if not bench:
            return False
        same = [pl for pl in bench if pl.position == off.position]
        on = (same or bench)[0]
        self.used_bench.add(on.player_id)
        tr.substitute(off_slot, on)
        self._emit(
            EventType.SUBSTITUTION,
            team,
            off_slot,
            substitution=SubstitutionAttributes(player_off_id=off.player_id, player_on_id=on.player_id),
        )
        return True

    # ================================================================== open play
    def _carrier_action(self) -> None:
        p = self.p
        team, slot = self.poss, self.carrier
        assert team is not None
        touch = self.rng.uniform(p.touch_min_s, p.touch_max_s)

        # §1.2 pressure: nearest opponent distance ~ Gamma, shifted by the defence's pressing.
        shift = (self.teams[1 - team].defence_rating - p.pressure_def_ref) * p.pressure_def_shift_per_point
        d_opp = max(self.rng.gammavariate(p.pressure_gamma_k, p.pressure_gamma_theta) - shift, p.pressure_min_m)
        under = d_opp < p.pressure_threshold_m
        self._advance(touch * (p.touch_pressure_factor if under else 1.0))
        if under:
            presser = self.tracking.nearest(1 - team, self.ball)
            self._emit(EventType.PRESSURE, 1 - team, presser, self._ball_rel(1 - team), nearest_opponent_m=None)

        action = self._choose_action(team, under)
        p_foul = p.foul_dribble_prob if action == "dribble" else p.foul_prob * (p.foul_pressure_mult if under else 1.0)
        if pitch.in_box(*self._ball_rel(team)):
            p_foul *= p.box_foul_factor
        fouler = self.tracking.nearest(1 - team, self.ball, outfield=True)
        if self._player(fouler).player_id in self.yellows:
            p_foul *= p.booked_foul_factor
        if self.rng.random() < p_foul:
            self._foul(team, slot, fouler)
            return

        ctx = {"under_pressure": under, "nearest_opponent_m": round(d_opp, 1)}
        if action == "pass":
            self._pass(**ctx)
        elif action == "carry":
            self._carry(**ctx)
        elif action == "dribble":
            self._dribble(**ctx)
        elif action == "cross":
            self._cross(**ctx)
        elif action == "shot":
            self._shot(**ctx)
        else:
            self._clearance(**ctx)

    def _choose_action(self, team: int, under: bool) -> str:
        p = self.p
        x, y = self._ball_rel(team)
        if x < p.own_third_x:
            w = {
                "pass": p.own_pass + (0.0 if under else p.own_clearance),
                "carry": p.own_carry,
                "dribble": p.own_dribble,
                "clearance": p.own_clearance if under else 0.0,
            }
        elif x < p.final_third_x:
            w = {
                "pass": p.mid_pass,
                "carry": p.mid_carry,
                "dribble": p.mid_dribble,
                "shot": p.mid_shot if x > p.middle_shot_min_x else 0.0,
            }
        else:
            xg_here = pitch.xg(x, y, p=p)
            w = {
                "pass": p.fin_pass,
                "carry": p.fin_carry,
                "dribble": p.fin_dribble,
                "cross": p.fin_cross if pitch.is_wide(y, p) else 0.0,
                "shot": pitch.clamp(p.fin_shot_base + p.fin_shot_xg_coef * xg_here, 0.0, p.fin_shot_max)
                * (1.0 + p.strength_shot_rel * self._strength(team)),
            }
        if "shot" in w and self._trailing(team):
            w["shot"] *= p.trailing_boost
        names = list(w)
        return self.rng.choices(names, weights=[w[n] for n in names])[0]

    # ------------------------------------------------------------------ passes
    def _opponent_lines(self, team: int, rel: np.ndarray) -> dict[Position, float]:
        """Mean x (in `team`'s frame) of each outfield line (DF / MF / FW) of the opponent's block."""
        lines: dict[Position, list[float]] = {}
        for s in self.tracking.active_slots(1 - team, outfield=True):
            lines.setdefault(self._player(s).position, []).append(float(rel[s, 0]))
        return {pos: sum(v) / len(v) for pos, v in lines.items()}

    def _pass(self, *, restart: str | None = None, under_pressure: bool = False, nearest_opponent_m=None) -> None:
        p, tr = self.p, self.tracking
        team, slot = self.poss, self.carrier
        assert team is not None
        sx, sy = self._ball_rel(team)
        rel = tr.team_frame(team, self._dirs())
        opp = tr.active_slots(1 - team)
        mates = [s for s in tr.active_slots(team) if s != slot]
        trailing = self._trailing(team)

        weights, targets = [], []
        for s in mates:
            ex, ey = float(rel[s, 0]), float(rel[s, 1])
            length = math.hypot(ex - sx, ey - sy)
            prog = ex - sx
            d_open = float(np.min(np.hypot(rel[opp, 0] - ex, rel[opp, 1] - ey)))
            w = (
                math.exp(p.recipient_prog_coef * prog - p.recipient_length_decay * length)
                * (1.0 - math.exp(-d_open / p.recipient_open_scale_m))
                * (p.recipient_gk_weight if tr.is_gk[s] else 1.0)
            )
            if trailing and prog > 0:
                w *= p.trailing_boost
            if restart in ("throw_in", "kick_off") and length > p.restart_max_len_m:
                w *= 0.01
            if restart == "kick_off" and prog > 0:
                w *= 0.01
            weights.append(w + 1e-12)
            targets.append((ex, ey))
        i = self.rng.choices(range(len(mates)), weights=weights)[0]
        recipient = mates[i]
        ex, ey = targets[i]

        prog = ex - sx
        lines = self._opponent_lines(team, rel)
        line_breaking = sum(1 for lx in lines.values() if sx < lx < ex) >= 2
        # A through ball splits the back line itself and gains real ground.
        back_line = lines.get(Position.DF, pitch.LENGTH)
        through = line_breaking and sx < back_line < ex and prog >= p.through_min_prog_m
        if through:
            ex = min(ex + p.through_lead_m, pitch.LENGTH - 1.0)
            prog = ex - sx
        ex, ey = pitch.clamp_point(ex, ey, 0.5)
        length = math.hypot(ex - sx, ey - sy)
        into_box = pitch.in_box(ex, ey) and not pitch.in_box(sx, sy)
        cutback = sx >= p.cutback_min_x and prog < 0 and pitch.in_box(ex, ey)
        self._deliver(
            recipient,
            (sx, sy),
            (ex, ey),
            length=length,
            prog=prog,
            under=under_pressure,
            line_breaking=line_breaking,
            into_box=into_box,
            cross=False,
            through=through,
            cutback=cutback,
            nearest_opponent_m=nearest_opponent_m,
        )

    def _deliver(
        self,
        recipient: int,
        start: tuple[float, float],
        end: tuple[float, float],
        *,
        length: float,
        prog: float,
        under: bool,
        line_breaking: bool,
        into_box: bool,
        cross: bool,
        through: bool,
        cutback: bool,
        nearest_opponent_m=None,
        force_success: bool | None = None,
    ) -> bool:
        """Emit a pass from `start` to `end` (team frame), resolve it, and move the ball. Returns success."""
        p = self.p
        team, slot = self.poss, self.carrier
        assert team is not None
        passer = self._player(slot)
        p_complete = pitch.pass_completion(
            length_m=length,
            progressive_m=prog,
            under_pressure=under,
            line_breaking=line_breaking,
            into_box=into_box,
            is_cross=cross,
            passing=passer.ratings.passing,
            opp_defence=self.teams[1 - team].defence_rating,
            strength=self._strength(team),
            home=team == 0,
            p=p,
        )
        success = self.rng.random() < p_complete if force_success is None else force_success
        offside = success and through and self.rng.random() < p.offside_prob

        high = cross or (length >= p.high_pass_min_len and self.rng.random() < p.high_pass_prob)
        if high:
            speed = self.rng.gauss(p.high_speed_mean, p.high_speed_sd)
        else:
            speed = self.rng.gauss(p.ground_speed_base + p.ground_speed_per_m * length, p.ground_speed_sd)
        speed = pitch.clamp(speed, p.ball_speed_min, p.ball_speed_max)
        duration = length / (speed / 3.6) + p.pass_reaction_s
        complete = success and not offside

        self._emit(
            EventType.PASS,
            team,
            slot,
            start,
            end,
            outcome=Outcome.SUCCESS if complete else Outcome.FAIL,
            under_pressure=under,
            nearest_opponent_m=nearest_opponent_m,
            pass_=PassAttributes(
                length_m=round(length, 1),
                progressive_m=round(prog, 1),
                height="high" if high else "ground",
                ball_speed_kmh=round(speed, 1),
                expected_completion=round(p_complete, 3),
                difficulty=pitch.pass_difficulty(p_complete),
                line_breaking=line_breaking,
                into_box=into_box,
                is_cross=cross,
                is_through_ball=through,
                recipient_id=self._player(recipient).player_id if complete else None,
            ),
            extra={"offside": True} if offside else ({"cutback": True} if cutback else {}),
        )
        end_abs = self._abs(team, *end)

        if complete:
            self._advance(duration, end_abs, receiver=recipient)
            self.carrier = recipient
            self.last_pass = LastPass(slot, recipient, through or cutback, cross)
            return True
        if offside:
            self._advance(duration, end_abs, receiver=recipient)
            self._emit(EventType.OFFSIDE, team, recipient, end)
            self._restart_at("free_kick", 1 - team, end_abs, p.gap_free_kick)
            return False

        # Failed pass.
        if cross and self.rng.random() < p.cross_fail_corner_prob:
            self._advance(duration, end_abs)
            self._restart_at("corner", team, end_abs, p.gap_corner)
            return False
        if not cross and self.rng.random() < p.pass_fail_out_prob:
            self._advance(duration, end_abs)
            if end[0] >= pitch.LENGTH - 2.0:
                self._restart_at("goal_kick", 1 - team, end_abs, p.gap_goal_kick)
            else:
                self._restart_at("throw_in", 1 - team, end_abs, p.gap_throw_in)
            return False
        f = self.rng.uniform(p.fail_point_min, p.fail_point_max)
        sx, sy = start
        point = self._abs(team, sx + f * (end[0] - sx), sy + f * (end[1] - sy))
        self._advance(duration * f, point)
        winner = self.tracking.nearest(1 - team, point)
        if cross:
            self._gain(1 - team, winner, EventType.CLEARANCE, point)
            self._clearance_flight()
        else:
            etype = EventType.INTERCEPTION if self.rng.random() < p.interception_share else EventType.BALL_RECOVERY
            self._gain(1 - team, winner, etype, point)
        return False

    def _cross(self, *, corner: bool = False, under_pressure: bool = False, nearest_opponent_m=None) -> None:
        p, tr = self.p, self.tracking
        team = self.poss
        assert team is not None
        start = self._ball_rel(team)
        end = (self.rng.uniform(*p.cross_target_x), self.rng.uniform(*p.cross_target_y))
        recipient = tr.nearest(team, self._abs(team, *end), outfield=True, exclude=self.carrier)
        length = math.hypot(end[0] - start[0], end[1] - start[1])
        success = self.rng.random() < p.corner_header_prob if corner else None
        ok = self._deliver(
            recipient,
            start,
            end,
            length=length,
            prog=end[0] - start[0],
            under=under_pressure,
            line_breaking=False,
            into_box=not pitch.in_box(*start),
            cross=True,
            through=False,
            cutback=False,
            nearest_opponent_m=nearest_opponent_m,
            force_success=success,
        )
        if not ok:
            return
        if corner:
            self._shot(situation="corner", header=True)
        elif self.rng.random() < p.cross_shot_prob:
            self._shot(header=self.rng.random() < p.cross_header_prob)

    # ------------------------------------------------------------------ carries, dribbles, clearances
    def _carry(self, *, under_pressure: bool = False, nearest_opponent_m=None) -> None:
        p = self.p
        team, slot = self.poss, self.carrier
        assert team is not None
        sx, sy = self._ball_rel(team)
        length = self.rng.uniform(p.carry_min_m, p.carry_max_m)
        ang = self.rng.gauss(0.0, p.carry_angle_sd)
        ex, ey = pitch.clamp_point(sx + length * math.cos(ang), sy + length * math.sin(ang), 1.0)
        ex = min(ex, max(sx, p.carry_max_x))
        if self.last_pass:
            self.last_pass.touches += 1
        self._emit(
            EventType.CARRY,
            team,
            slot,
            (sx, sy),
            (ex, ey),
            outcome=Outcome.SUCCESS,
            under_pressure=under_pressure,
            nearest_opponent_m=nearest_opponent_m,
        )
        self._advance(self.rng.uniform(p.carry_min_s, p.carry_max_s), self._abs(team, ex, ey))

    def _dribble(self, *, under_pressure: bool = False, nearest_opponent_m=None) -> None:
        p = self.p
        team, slot = self.poss, self.carrier
        assert team is not None
        defender = self.tracking.nearest(1 - team, self.ball, outfield=True)
        z = (
            p.dribble_rating_coef * (self._player(slot).ratings.dribbling - self._player(defender).ratings.tackling)
            + p.dribble_base
            + p.dribble_strength_coef * self._strength(team)
        )
        sx, sy = self._ball_rel(team)
        if self.rng.random() < pitch.sigmoid(z):
            gain = self.rng.uniform(p.dribble_min_m, p.dribble_max_m)
            ex, ey = pitch.clamp_point(sx + gain, sy + self.rng.uniform(-2.0, 2.0), 1.0)
            ex = min(ex, max(sx, p.carry_max_x))
            if self.last_pass:
                self.last_pass.touches += 2  # beating a man is the shooter's own work: no assist
            self._emit(
                EventType.DRIBBLE,
                team,
                slot,
                (sx, sy),
                (ex, ey),
                outcome=Outcome.SUCCESS,
                under_pressure=under_pressure,
                nearest_opponent_m=nearest_opponent_m,
                extra={"defender_id": self._player(defender).player_id},
            )
            self._advance(self.rng.uniform(p.dribble_min_s, p.dribble_max_s), self._abs(team, ex, ey))
        else:
            self._emit(
                EventType.DRIBBLE,
                team,
                slot,
                (sx, sy),
                outcome=Outcome.FAIL,
                under_pressure=under_pressure,
                nearest_opponent_m=nearest_opponent_m,
                extra={"defender_id": self._player(defender).player_id},
            )
            self._advance(self.rng.uniform(p.dribble_min_s, p.dribble_max_s) / 2)
            self._gain(1 - team, defender, EventType.TACKLE, self.ball)

    def _clearance(self, *, under_pressure: bool = False, nearest_opponent_m=None) -> None:
        team, slot = self.poss, self.carrier
        assert team is not None
        self._emit(
            EventType.CLEARANCE,
            team,
            slot,
            self._ball_rel(team),
            outcome=Outcome.NA,
            under_pressure=under_pressure,
            nearest_opponent_m=nearest_opponent_m,
        )
        self._clearance_flight()

    def _clearance_flight(self) -> None:
        """The possessing team has just hoofed it: out of play, or a contested second ball."""
        p = self.p
        team = self.poss
        assert team is not None
        sx, sy = self._ball_rel(team)
        ex = sx + self.rng.uniform(p.clearance_min_m, p.clearance_max_m)
        ey = sy + self.rng.uniform(-20.0, 20.0)
        out = self.rng.random() < p.clearance_out_prob
        end = pitch.clamp_point(ex, ey, 0.0 if out else 1.0)
        end_abs = self._abs(team, *end)
        self._advance(self.rng.uniform(2.0, 3.5), end_abs)
        if out:
            self._restart_at("throw_in", 1 - team, end_abs, p.gap_throw_in)
            return
        self._loose_ball(end_abs, keep_prob=p.clearance_keep_prob)

    def _loose_ball(self, at: tuple[float, float], keep_prob: float) -> None:
        """Ball loose at absolute `at`: the possessing team keeps it with `keep_prob`."""
        team = self.poss
        assert team is not None
        winner_team = team if self.rng.random() < keep_prob else 1 - team
        winner = self.tracking.nearest(winner_team, at, outfield=True)
        self._gain(winner_team, winner, EventType.BALL_RECOVERY, at)

    # ------------------------------------------------------------------ shots
    def _shot(
        self,
        *,
        situation: str = "open_play",
        header: bool = False,
        under_pressure: bool = False,
        nearest_opponent_m=None,
    ) -> None:
        p = self.p
        team, slot = self.poss, self.carrier
        assert team is not None
        shooter = self._player(slot)
        if situation == "penalty":
            self.ball = self._abs(team, *pitch.PENALTY_SPOT)
        sx, sy = self._ball_rel(team)
        distance = pitch.shot_distance(sx, sy)
        angle = pitch.shot_angle(sx, sy)

        lp = self.last_pass
        if lp is None or lp.recipient != slot or lp.touches > self.p.assist_max_touches:
            lp = None
        if situation == "penalty":
            xg = p.xg_penalty
        else:
            bonus = p.xg_assist_bonus if lp and lp.bonus and lp.touches == 0 else 0.0
            xg = pitch.xg(sx, sy, header=header, bonus=bonus, p=p)
            if situation == "set_piece":
                xg = pitch.clamp(xg * p.free_kick_xg_factor, p.xg_min, p.xg_max)
        xg = round(xg, 3)  # big_chance is judged on the published value
        if (
            situation == "open_play"
            and self.t - self.poss_start_t <= p.counter_max_s
            and self.poss_start_x < p.counter_start_max_x
        ):
            situation = "counter"

        gk_slot = self.tracking.gk_slot(1 - team)
        gk = self._player(gk_slot)
        if situation != "penalty" and self.rng.random() < p.shot_block_prob:
            outcome = Outcome.BLOCKED
        else:
            p_on = p.on_target_base + p.on_target_xg * xg
            if self.rng.random() < p_on:
                p_goal = pitch.clamp(
                    xg
                    / p_on
                    * (1 + p.finishing_coef * (shooter.ratings.finishing - 70))
                    * (1 - p.keeper_coef * (gk.ratings.goalkeeping - 70)),
                    p.p_goal_min,
                    p.p_goal_max,
                )
                outcome = Outcome.GOAL if self.rng.random() < p_goal else Outcome.SAVED
            else:
                outcome = Outcome.WOODWORK if self.rng.random() < p.woodwork_prob else Outcome.OFF_TARGET

        if header:
            speed = self.rng.gauss(p.shot_speed_head_mean, p.shot_speed_head_sd)
            body = BodyPart.HEAD
        else:
            speed = pitch.clamp(
                self.rng.gauss(p.shot_speed_foot_mean, p.shot_speed_foot_sd),
                p.shot_speed_foot_min,
                p.shot_speed_foot_max,
            ) + p.shot_speed_finishing_coef * max(shooter.ratings.finishing - 70, 0)
            body = BodyPart.RIGHT_FOOT if self.rng.random() < 0.7 else BodyPart.LEFT_FOOT

        if outcome in (Outcome.GOAL, Outcome.SAVED):
            end = (pitch.GOAL_X, self.rng.uniform(pitch.POST_LOW + 0.3, pitch.POST_HIGH - 0.3))
        elif outcome == Outcome.WOODWORK:
            end = (pitch.GOAL_X, pitch.POST_LOW if self.rng.random() < 0.5 else pitch.POST_HIGH)
        elif outcome == Outcome.OFF_TARGET:
            side = -1.0 if self.rng.random() < 0.5 else 1.0
            end = (pitch.GOAL_X, pitch.clamp(pitch.GOAL_Y + side * self.rng.uniform(2.0, 9.0), 0.0, pitch.WIDTH))
        else:
            f = self.rng.uniform(0.05, 0.25)
            end = (sx + f * (pitch.GOAL_X - sx), sy + f * (pitch.GOAL_Y - sy))

        shot_ev = self._emit(
            EventType.SHOT,
            team,
            slot,
            (sx, sy),
            end,
            outcome=outcome,
            under_pressure=under_pressure,
            nearest_opponent_m=nearest_opponent_m,
            shot=ShotAttributes(
                distance_m=round(distance, 1),
                angle_rad=round(angle, 3),
                body_part=body,
                xg=xg,
                shot_speed_kmh=round(speed, 1),
                on_target=outcome in (Outcome.GOAL, Outcome.SAVED),
                big_chance=xg >= p.big_chance_xg,
                situation=situation,
                assist_player_id=self._player(lp.passer).player_id if lp else None,
            ),
        )
        end_abs = self._abs(team, *end)
        self._advance(p.shot_flight_s, end_abs)
        self.last_pass = None

        if outcome == Outcome.GOAL:
            self.score[team] += 1
            self._emit(
                EventType.GOAL,
                team,
                slot,
                (sx, sy),
                outcome=Outcome.GOAL,
                extra={
                    "shot_event_id": shot_ev.event_id,
                    "shot_seq": shot_ev.seq,
                    "assist_player_id": shot_ev.shot.assist_player_id if shot_ev.shot else None,
                },
            )
            self._restart_at("kick_off", 1 - team, pitch.CENTRE, p.gap_goal)
        elif outcome == Outcome.SAVED:
            gk_xy = pitch.flip(pitch.GOAL_X - 1.0, end[1])
            if self.rng.random() < p.save_corner_prob:
                self._emit(EventType.SAVE, 1 - team, gk_slot, gk_xy, outcome=Outcome.SUCCESS)
                self._restart_at("corner", team, end_abs, p.gap_corner)
            else:
                self._gain(1 - team, gk_slot, EventType.SAVE, self._abs(1 - team, *gk_xy))
                self._restart_at("keeper", 1 - team, self.ball, p.gap_keeper_hold)
        elif outcome in (Outcome.OFF_TARGET, Outcome.WOODWORK):
            self._restart_at("goal_kick", 1 - team, end_abs, p.gap_goal_kick)
        else:  # blocked
            if self.rng.random() < p.blocked_corner_prob:
                self._restart_at("corner", team, end_abs, p.gap_corner)
            else:
                self._loose_ball(end_abs, keep_prob=1.0 - p.loose_ball_defender_prob)

    # ------------------------------------------------------------------ fouls & cards
    def _foul(self, team: int, fouled_slot: int, fouler: int) -> None:
        p = self.p
        opp = 1 - team
        at_rel = self._ball_rel(team)
        self._emit(
            EventType.FOUL,
            opp,
            fouler,
            self._ball_rel(opp),
            extra={"fouled_player_id": self._player(fouled_slot).player_id},
        )
        player = self._player(fouler)
        colour = None
        if self.rng.random() < p.straight_red_prob:
            colour = "red"
        elif self.rng.random() < p.yellow_prob:
            colour = "second_yellow" if player.player_id in self.yellows else "yellow"
        if colour:
            self.yellows.add(player.player_id)
            self._emit(
                EventType.CARD,
                opp,
                fouler,
                self._ball_rel(opp),
                outcome=Outcome.YELLOW if colour == "yellow" else Outcome.RED,
                card=CardAttributes(colour=colour),
            )
            if colour != "yellow":
                self.tracking.send_off(fouler)
        kind = "penalty" if pitch.in_box(*at_rel) else "free_kick"
        gap = p.gap_penalty if kind == "penalty" else p.gap_free_kick
        self._restart_at(kind, team, self.ball, gap)
