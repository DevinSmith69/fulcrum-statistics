"""Team loading, 4-3-3 formation anchors and the 1 Hz tracking model (MODEL_SPEC §1.8).

The tracking model keeps every on-pitch player's ABSOLUTE position (home attacks towards x = 105 in
period 1). Each step a player's target is its anchor shifted towards the ball plus smooth OU noise; the
ball carrier and the two nearest players of each team chase the ball. Movement towards the target is
capped by a pace-dependent top speed, so speeds and distances come out physically plausible.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import numpy as np

from fulcrum.contracts import PlayerFrame, Position, Team

from .params import DEFAULT_PARAMS, SimParams
from .pitch import LENGTH, WIDTH

DEFAULT_TEAMS_PATH = Path(__file__).resolve().parents[3] / "data" / "teams.json"

# Team frame (attacking towards x = 105) with the ball on the centre spot. "Right" is the low-y flank.
# The block is compact on purpose: with the §1.8 shift (0.5 m per metre of ball travel) the back line
# then sits ~10–15 m from goal when defending deep and the striker stays level with the opposing line.
ANCHORS_433: dict[str, tuple[float, float]] = {
    "GK": (5.0, 34.0),
    "RB": (36.0, 10.0),
    "RCB": (32.0, 28.0),
    "LCB": (32.0, 40.0),
    "LB": (36.0, 58.0),
    "CDM": (44.0, 34.0),
    "RCM": (52.0, 22.0),
    "LCM": (52.0, 46.0),
    "RW": (64.0, 10.0),
    "ST": (68.0, 34.0),
    "LW": (64.0, 58.0),
}
STARTING_ROLES = list(ANCHORS_433)
SLOTS_PER_TEAM = 11


def load_teams(path: Path | str | None = None) -> dict[str, Team]:
    """Load the fictional clubs, keyed by team_id. Path: arg → $FULCRUM_TEAMS_PATH → data/teams.json."""
    src = Path(path or os.getenv("FULCRUM_TEAMS_PATH") or DEFAULT_TEAMS_PATH)
    teams = [Team.model_validate(t) for t in json.loads(src.read_text(encoding="utf-8"))]
    return {t.team_id: t for t in teams}


def starting_xi(team: Team) -> list:
    """The 11 starters ordered by STARTING_ROLES (GK first). Raises if the XI doesn't fit a 4-3-3."""
    by_role = {p.role: p for p in team.players[:SLOTS_PER_TEAM]}
    missing = [r for r in STARTING_ROLES if r not in by_role]
    if missing:
        raise ValueError(f"{team.team_id}: starting XI lacks 4-3-3 roles {missing}")
    return [by_role[r] for r in STARTING_ROLES]


def top_speed_kmh(pace: int, p: SimParams = DEFAULT_PARAMS) -> float:
    """Pace-dependent top speed: pace 60 → 29 km/h, pace 90 → 34 km/h, clamped to [27, 35]."""
    v = p.top_speed_base_kmh + (pace - 60) * p.top_speed_per_pace_kmh
    return min(max(v, p.top_speed_min_kmh), p.top_speed_max_kmh)


def mean_rating(player) -> float:
    """Average outfield rating, used to decide who is substituted."""
    r = player.ratings
    return (r.passing + r.finishing + r.pace + r.tackling + r.dribbling) / 5.0


class Tracking:
    """Positions of all 22 slots (home 0–10, away 11–21), stepped once per tracking frame."""

    def __init__(self, home: Team, away: Team, rng: np.random.Generator, p: SimParams = DEFAULT_PARAMS) -> None:
        self.p = p
        self.rng = rng
        self.team_ids = (home.team_id, away.team_id)
        self.players = starting_xi(home) + starting_xi(away)
        self.team = np.repeat([0, 1], SLOTS_PER_TEAM)
        self.anchor = np.array([ANCHORS_433[r] for r in STARTING_ROLES] * 2, dtype=float)
        self.is_gk = np.array([pl.position == Position.GK for pl in self.players])
        self.shift = np.where(
            self.is_gk[:, None],
            [p.tracking_gk_shift_x, p.tracking_gk_shift_y],
            [p.tracking_shift_x, p.tracking_shift_y],
        )
        self.is_front = self.anchor[:, 0] >= p.tracking_front_line_x
        self.noise_scale = np.where(self.is_gk, p.tracking_gk_noise_factor, 1.0)[:, None]
        self.active = np.ones(2 * SLOTS_PER_TEAM, dtype=bool)
        self.vmax = np.array([top_speed_kmh(pl.ratings.pace, p) / 3.6 for pl in self.players])
        self.pos = np.zeros((2 * SLOTS_PER_TEAM, 2))
        self.noise = np.zeros((2 * SLOTS_PER_TEAM, 2))
        self.speed_kmh = np.zeros(2 * SLOTS_PER_TEAM)
        self.distance_m = np.zeros(2 * SLOTS_PER_TEAM)
        self.top_kmh = np.zeros(2 * SLOTS_PER_TEAM)
        self.retired: dict[str, tuple[float, float]] = {}  # player_id -> (distance_m, top_kmh) for subbed-off

    # ------------------------------------------------------------------ slots
    def slots(self, team: int) -> range:
        """Global slot indices of a team."""
        return range(team * SLOTS_PER_TEAM, (team + 1) * SLOTS_PER_TEAM)

    def active_slots(self, team: int, *, outfield: bool = False) -> list[int]:
        """On-pitch slots of a team, optionally without the keeper."""
        return [s for s in self.slots(team) if self.active[s] and not (outfield and self.is_gk[s])]

    def gk_slot(self, team: int) -> int:
        """The keeper's slot (slot 0 of the team; keepers are never substituted or sent off)."""
        return team * SLOTS_PER_TEAM

    def substitute(self, slot: int, player) -> None:
        """Replace the player in `slot`; the newcomer inherits the slot's position and anchor."""
        old = self.players[slot]
        self.retired[old.player_id] = (float(self.distance_m[slot]), float(self.top_kmh[slot]))
        self.players[slot] = player
        self.vmax[slot] = top_speed_kmh(player.ratings.pace, self.p) / 3.6
        self.distance_m[slot] = 0.0
        self.top_kmh[slot] = 0.0
        self.noise[slot] = 0.0

    def send_off(self, slot: int) -> None:
        """Remove a player from the pitch (red card)."""
        self.active[slot] = False
        self.speed_kmh[slot] = 0.0

    # ------------------------------------------------------------------ geometry
    def team_frame(self, team: int, dirs: tuple[int, int]) -> np.ndarray:
        """All slot positions expressed in `team`'s attacking frame."""
        if dirs[team] > 0:
            return self.pos.copy()
        return np.column_stack((LENGTH - self.pos[:, 0], WIDTH - self.pos[:, 1]))

    def nearest(self, team: int, point: tuple[float, float], *, outfield: bool = False, exclude: int = -1) -> int:
        """Slot of `team`'s on-pitch player closest to an absolute point."""
        cand = [s for s in self.active_slots(team, outfield=outfield) if s != exclude]
        d = np.hypot(self.pos[cand, 0] - point[0], self.pos[cand, 1] - point[1])
        return cand[int(np.argmin(d))]

    def player_distance_km(self, slot: int) -> float:
        """Distance covered so far by the player currently in `slot`."""
        return float(self.distance_m[slot]) / 1000.0

    # ------------------------------------------------------------------ dynamics
    def _targets(self, ball: tuple[float, float], poss: int | None, dirs: tuple[int, int]) -> np.ndarray:
        p = self.p
        out = np.empty_like(self.pos)
        for team in (0, 1):
            sl = slice(team * SLOTS_PER_TEAM, (team + 1) * SLOTS_PER_TEAM)
            bx, by = ball if dirs[team] > 0 else (LENGTH - ball[0], WIDTH - ball[1])
            offset = (
                0.0
                if poss is None
                else (p.tracking_possession_offset_m if poss == team else -p.tracking_possession_offset_m)
            )
            tx = self.anchor[sl, 0] + self.shift[sl, 0] * (bx - LENGTH / 2) + np.where(self.is_gk[sl], 0.0, offset)
            ty = self.anchor[sl, 1] + self.shift[sl, 1] * (by - WIDTH / 2)
            if poss == team and bx >= p.tracking_narrow_from_x:  # front three tuck inside near goal
                ty = ty + np.where(self.is_front[sl], p.tracking_attack_narrowing, 0.0) * (WIDTH / 2 - ty)
            tx = np.clip(tx, 1.0, LENGTH - 1.0)
            ty = np.clip(ty, 1.0, WIDTH - 1.0)
            if dirs[team] < 0:
                tx, ty = LENGTH - tx, WIDTH - ty
            out[sl, 0], out[sl, 1] = tx, ty
        return out

    def reset(self, ball: tuple[float, float], dirs: tuple[int, int]) -> None:
        """Put everyone on their anchors (start of a period)."""
        self.pos = self._targets(ball, None, dirs)
        self.noise[:] = 0.0
        self.speed_kmh[:] = 0.0

    def step(
        self,
        dt: float,
        ball: tuple[float, float],
        poss: int | None,
        carrier: int | None,
        dirs: tuple[int, int],
        receiver: int | None = None,
        receiver_target: tuple[float, float] | None = None,
    ) -> None:
        """Advance every player by `dt` seconds towards their target, capped by top speed."""
        p = self.p
        target = self._targets(ball, poss, dirs)
        a = np.exp(-dt / p.tracking_noise_tau_s)
        self.noise = self.noise * a + p.tracking_noise_sd_m * np.sqrt(1 - a * a) * self.rng.standard_normal(
            self.noise.shape
        )
        target += self.noise * self.noise_scale

        cap = self.vmax * dt * p.tracking_jog_factor
        d_ball = np.hypot(self.pos[:, 0] - ball[0], self.pos[:, 1] - ball[1])
        for team in (0, 1):
            cand = [s for s in self.active_slots(team, outfield=True) if s != carrier and s != receiver]
            if cand:
                order = np.argsort(d_ball[cand], kind="stable")[: p.tracking_chasers_per_team]
                for i in order:
                    s = cand[int(i)]
                    target[s] = ball
                    cap[s] = self.vmax[s] * dt
        if carrier is not None:
            target[carrier] = ball
            cap[carrier] = self.vmax[carrier] * dt
        if receiver is not None and receiver_target is not None:
            target[receiver] = receiver_target
            cap[receiver] = self.vmax[receiver] * dt

        delta = target - self.pos
        dist = np.hypot(delta[:, 0], delta[:, 1])
        step = np.minimum(dist, cap)
        step[~self.active] = 0.0
        scale = np.divide(step, dist, out=np.zeros_like(step), where=dist > 1e-9)
        self.pos += delta * scale[:, None]
        np.clip(self.pos[:, 0], 0.0, LENGTH, out=self.pos[:, 0])
        np.clip(self.pos[:, 1], 0.0, WIDTH, out=self.pos[:, 1])
        self.speed_kmh = step / dt * 3.6 if dt > 0 else np.zeros_like(step)
        self.distance_m += step
        np.maximum(self.top_kmh, self.speed_kmh, out=self.top_kmh)

    def frame_players(self) -> list[PlayerFrame]:
        """PlayerFrame for every on-pitch player (positions 2 dp, speed 1 dp)."""
        xs = np.round(self.pos[:, 0], 2).tolist()
        ys = np.round(self.pos[:, 1], 2).tolist()
        vs = np.round(self.speed_kmh, 1).tolist()
        return [
            PlayerFrame(
                player_id=self.players[s].player_id,
                team_id=self.team_ids[self.team[s]],
                x=xs[s],
                y=ys[s],
                speed_kmh=vs[s],
            )
            for s in range(2 * SLOTS_PER_TEAM)
            if self.active[s]
        ]

    def totals(self) -> dict[str, tuple[float, float]]:
        """player_id -> (distance_m, top_speed_kmh) for everyone who played."""
        out = dict(self.retired)
        for s, pl in enumerate(self.players):
            out[pl.player_id] = (float(self.distance_m[s]), float(self.top_kmh[s]))
        return out
