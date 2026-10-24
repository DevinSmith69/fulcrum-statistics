// Fulcrum mock match generator.
//
// Builds an entire synthetic match up front (deterministic for a seed) as a timeline of contract
// payloads: MatchEvent, MatchState, WinProbPoint and Moment. Stats, the win-probability model and
// moment rules follow docs/MODEL_SPEC.md closely enough to look believable on screen; this is a stand-in
// for WS1/WS2 until the real pipeline is up, not a replacement for them.
//
// Coordinates: events are TEAM-RELATIVE (acting team attacks towards x = 105), as in the contracts.

export const CONTRACT_VERSION = "1.0.0";
export const ROLES = ["GK", "RB", "RCB", "LCB", "LB", "CDM", "RCM", "LCM", "RW", "ST", "LW"];
const MIN = 60_000;
export const H1_LEN = 47 * MIN; // 45' + 2' stoppage
export const H2_LEN = 49 * MIN; // 45' + 4' stoppage

// Team-frame formation anchors (attacking towards x = 105; right side = high y).
const ANCHORS = {
  GK: [5, 34], RB: [34, 60], RCB: [24, 44], LCB: [24, 24], LB: [34, 8], CDM: [40, 34],
  RCM: [52, 48], LCM: [52, 20], RW: [68, 60], ST: [72, 34], LW: [68, 8],
};

const clamp = (v, lo, hi) => Math.max(lo, Math.min(hi, v));
const sigmoid = (z) => 1 / (1 + Math.exp(-z));
const r1 = (v) => Math.round(v * 10) / 10;
const r2 = (v) => Math.round(v * 100) / 100;
const pct = (v) => Math.round(v * 100);

function mulberry32(seed) {
  let a = seed >>> 0;
  return () => {
    a = (a + 0x6d2b79f5) | 0;
    let t = Math.imul(a ^ (a >>> 15), 1 | a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

/** Football minute label for a clock value, e.g. "67'" or "45+2'". */
export function minuteLabel(period, clockMs) {
  if (period === 1) {
    const m = Math.max(1, Math.floor((clockMs - 1) / MIN) + 1);
    return m > 45 ? `45+${m - 45}'` : `${m}'`;
  }
  const m = 45 + Math.max(1, Math.floor((clockMs - H1_LEN - 1) / MIN) + 1);
  return m > 90 ? `90+${m - 90}'` : `${m}'`;
}

function anchorPos(role, bx, by) {
  const [ax, ay] = ANCHORS[role] ?? [50, 34];
  if (role === "GK") return [clamp(ax + 0.08 * (bx - 52.5), 2, 16), clamp(ay + 0.15 * (by - 34), 26, 42)];
  return [clamp(ax + 0.45 * (bx - 52.5), 3, 102), clamp(ay + 0.3 * (by - 34), 2, 66)];
}

const mirror = (x, y) => [105 - x, 68 - y];
const inBox = (x, y) => x > 88.5 && y > 13.85 && y < 54.15;

function xgAt(x, y, header = false) {
  const dx = 105 - x;
  const dy = Math.abs(34 - y);
  const dist = Math.hypot(dx, dy);
  let angle = Math.atan2(7.32 * dx, dx * dx + dy * dy - (7.32 / 2) ** 2);
  if (angle < 0) angle += Math.PI;
  let xg = sigmoid(-0.4 - 0.1 * dist + 1.5 * angle);
  if (header) xg *= 0.6;
  return { xg: clamp(xg, 0.01, 0.95), dist, angle };
}

function poissonPmf(lambda, kMax = 10) {
  const out = [Math.exp(-lambda)];
  for (let k = 1; k <= kMax; k++) out.push((out[k - 1] * lambda) / k);
  return out;
}

// Scripted narrative beats (minute = football minute). Everything between them is generated play.
// `edge` windows tilt the generated play so the indices tell the same story as the beats.
const BEATS = [
  { m: 6, kind: "elite_pass", side: "home", passer: "RCM", recipient: "RW", len: 34.2, prog: 24.6, difficulty: 7.8 },
  { m: 12, kind: "shot", side: "away", shooter: "ST", assist: "RCM", xg: 0.41, outcome: "saved", dist: 9.8, speed: 104 },
  { m: 27, kind: "shot", side: "home", shooter: "ST", xg: 0.09, outcome: "woodwork", dist: 24.6, speed: 118 },
  { m: 29, kind: "card", side: "away", player: "LCB", colour: "yellow" },
  { m: 34, kind: "goal", side: "away", shooter: "ST", assist: "RW", xg: 0.38, dist: 11.2, speed: 96, body: "left_foot", assistDifficulty: 5.6 },
  { m: 37, kind: "shot", side: "away", shooter: "LW", xg: 0.07, outcome: "off_target", dist: 22.4, speed: 92 },
  { m: 39, kind: "shot", side: "away", shooter: "RCM", xg: 0.11, outcome: "blocked", dist: 18.9, speed: 88 },
  { m: 55, kind: "goal", side: "home", shooter: "ST", assist: "LW", xg: 0.29, dist: 7.4, speed: 58, body: "head", cross: true, assistDifficulty: 6.1 },
  { m: 58, kind: "milestone", side: "home" },
  { m: 61, kind: "card", side: "away", player: "LCB", colour: "second_yellow" },
  { m: 63, kind: "sub", side: "away", off: "RW", on: 11 },
  { m: 68, kind: "sub", side: "home", off: "LCM", on: 11 },
  { m: 72, kind: "shot", side: "home", shooter: "RW", assist: "ST", xg: 0.52, outcome: "saved", dist: 8.1, speed: 101 },
  { m: 74, kind: "sub", side: "away", off: "LW", on: 12 },
  { m: 84, kind: "goal", side: "home", shooter: "RW", assist: "RCM", xg: 0.44, dist: 10.3, speed: 109, body: "right_foot", assistDifficulty: 7.9, through: true },
  { m: 88, kind: "sprint", side: "home", player: "LB", speed: 34.1 },
  { m: 92, kind: "shot", side: "away", shooter: "ST", xg: 0.34, outcome: "off_target", dist: 8.9, speed: 61, body: "head" },
];
const EDGES = [
  { from: 14, to: 41, side: "away", v: 0.55 },
  { from: 48, to: 60, side: "home", v: 0.45 },
  { from: 64, to: 90, side: "home", v: 0.7 },
];

/**
 * Build a full match timeline.
 * @returns {{ matchId, home, away, items: Array<{clock, period, kind, data}>, periodStart, summary }}
 */
export function buildMatch({ home, away, seed = 7, matchId }) {
  return new Sim(home, away, seed, matchId).run();
}

class Sim {
  constructor(home, away, seed, matchId) {
    this.rng = mulberry32(seed * 7919 + 13);
    this.matchId = matchId;
    this.teams = { home, away };
    this.items = [];
    this.seq = 0;
    this.clock = 0;
    this.period = 1;
    this.possId = 0;
    this.score = { home: 0, away: 0 };
    this.onPitch = {
      home: home.players.slice(0, 11).map((p, i) => ({ p, role: ROLES[i], on: 0 })),
      away: away.players.slice(0, 11).map((p, i) => ({ p, role: ROLES[i], on: 0 })),
    };
    this.sentOff = { home: 0, away: 0 };
    this.yellows = {};
    this.players = {};
    for (const side of ["home", "away"]) for (const e of this.onPitch[side]) this.ensurePlayer(e.p, side, 0);
    this.team = { home: this.blankTeam(home), away: this.blankTeam(away) };
    this.pressRaw = { home: 0, away: 0 };
    this.lastPressClock = 0;
    this.momentum = 0;
    this.passLog = []; // {clock, side, ok, len}
    this.shotLog = []; // {clock, side, xg}
    this.possLog = []; // {id, side, start, passes}
    this.possTime = { home: 0, away: 0 };
    this.lastEventClock = 0;
    this.wpHistory = [];
    this.moments = [];
    this.lastMomentClock = -Infinity;
    this.lastByType = {};
    this.phase = "balanced";
    this.phaseCandidate = { phase: "balanced", since: 0 };
    this.lastSnapshot = -Infinity;
    this.periodStart = { 1: 0, 2: H1_LEN };
    this.pressSign = 0;
    this.pressHistory = [];
    this.tempoHigh = { since: null };
    this.tempoSamples = [];
    this.topSpeed = { kmh: 0 };
    this.matchHighShot = 0;
  }

  // ------------------------------------------------------------------ helpers
  r(lo = 0, hi = 1) { return lo + (hi - lo) * this.rng(); }
  chance(p) { return this.rng() < p; }
  id() { let s = ""; for (let i = 0; i < 32; i++) s += Math.floor(this.rng() * 16).toString(16); return s; }
  other(side) { return side === "home" ? "away" : "home"; }
  minuteNow() { return this.period === 1 ? Math.min(45, this.clock / MIN) : 45 + (this.clock - H1_LEN) / MIN; }
  edge(side) {
    const m = this.minuteNow();
    let v = 0;
    for (const e of EDGES) if (m >= e.from && m < e.to) v += e.side === side ? e.v : -e.v * 0.6;
    if (this.sentOff[this.other(side)]) v += 0.25 * this.sentOff[this.other(side)];
    return v;
  }
  entry(side, role) { return this.onPitch[side].find((e) => e.role === role); }
  roleOf(side, playerId) { return this.onPitch[side].find((e) => e.p.player_id === playerId)?.role; }

  blankTeam(t) {
    return {
      team_id: t.team_id, goals: 0, xg: 0, shots: 0, shots_on_target: 0, passes: 0, passes_completed: 0,
      pass_accuracy: 0, avg_pass_difficulty: 0, possession_pct: 50, final_third_entries: 0, box_entries: 0,
      corners: 0, fouls: 0, yellow_cards: 0, red_cards: 0, pressure_index: 0, control_index: 0,
      max_shot_speed_kmh: 0, _diffSum: 0,
    };
  }

  ensurePlayer(p, side, onClock) {
    if (!this.players[p.player_id]) {
      this.players[p.player_id] = {
        player_id: p.player_id, team_id: this.teams[side].team_id, minutes: 0, touches: 0, passes: 0,
        passes_completed: 0, pass_accuracy: 0, avg_pass_difficulty: 0, key_passes: 0, shots: 0, xg: 0, goals: 0,
        tackles_won: 0, interceptions: 0, distance_km: 0, top_speed_kmh: 0, sprints: 0, rating: 6.0,
        _diffSum: 0, _on: onClock, _off: null, _pace: p.ratings.pace, _gk: p.position === "GK",
      };
    }
    return this.players[p.player_id];
  }

  nearest(side, x, y, { exclude = null, gk = false } = {}) {
    const cands = this.onPitch[side]
      .filter((e) => e.p.player_id !== exclude && (gk || e.role !== "GK"))
      .map((e) => {
        const [px, py] = anchorPos(e.role, x, y);
        return { e, d: Math.hypot(px - x, py - y) };
      })
      .sort((a, b) => a.d - b.d);
    if (!cands.length) return this.onPitch[side][0];
    return (cands.length > 1 && this.chance(0.3) ? cands[1] : cands[0]).e;
  }

  // ------------------------------------------------------------------ emission
  emit(type, side, player, f = {}) {
    const ev = {
      contract_version: CONTRACT_VERSION, event_id: this.id(), match_id: this.matchId, seq: this.seq++,
      period: this.period, clock_ms: Math.round(this.clock), type,
      team_id: side ? this.teams[side].team_id : null, player_id: player ? player.player_id : null,
      possession_id: this.possId, outcome: "na", under_pressure: false, extra: {}, ...f,
      score_home: this.score.home, score_away: this.score.away,
    };
    for (const k of ["x", "end_x"]) if (ev[k] != null) ev[k] = r1(clamp(ev[k], 0, 105));
    for (const k of ["y", "end_y"]) if (ev[k] != null) ev[k] = r1(clamp(ev[k], 0, 68));
    this.apply(ev, side);
    this.items.push({ clock: ev.clock_ms, period: this.period, kind: "event", data: ev });
    const wp = this.winProb();
    const point = { clock_ms: ev.clock_ms, seq: ev.seq, home: r2(wp.home), draw: r2(wp.draw), away: r2(wp.away) };
    this.wpHistory.push(point);
    this.items.push({ clock: ev.clock_ms, period: this.period, kind: "winprob", data: point });
    this.detect(ev);
    if (this.clock - this.lastSnapshot >= 2000) this.snapshot();
    return ev;
  }

  advance(seconds) {
    this.clock += seconds * 1000;
  }

  // ------------------------------------------------------------------ stats
  apply(ev, side) {
    const dt = Math.max(0, ev.clock_ms - this.lastEventClock);
    this.lastEventClock = ev.clock_ms;
    if (this.possSide) this.possTime[this.possSide] += dt;
    // pressure decay (half-life 180 s)
    const decay = Math.exp((-Math.LN2 * dt) / 180_000);
    this.pressRaw.home *= decay;
    this.pressRaw.away *= decay;
    const t = side ? this.team[side] : null;
    const pl = ev.player_id ? this.players[ev.player_id] : null;
    if (pl && !["substitution", "card", "foul"].includes(ev.type)) pl.touches++;
    const addR = (p, v) => { if (p) p.rating = clamp(p.rating + v, 0, 14); };

    switch (ev.type) {
      case "pass": {
        const a = ev.pass;
        t.passes++;
        t._diffSum += a.difficulty;
        t.avg_pass_difficulty = r1(t._diffSum / t.passes);
        pl.passes++;
        pl._diffSum += a.difficulty;
        pl.avg_pass_difficulty = r1(pl._diffSum / pl.passes);
        const ok = ev.outcome === "success";
        this.passLog.push({ clock: ev.clock_ms, side, ok, len: a.length_m });
        const cur = this.possLog[this.possLog.length - 1];
        if (cur && ok) cur.passes++;
        if (ok) {
          t.passes_completed++;
          pl.passes_completed++;
          addR(pl, 0.002 * (1 + a.difficulty));
          if (ev.x < 70 && ev.end_x >= 70) { t.final_third_entries++; this.pressRaw[side] += 1; }
          if (!inBox(ev.x, ev.y) && inBox(ev.end_x, ev.end_y)) { t.box_entries++; this.pressRaw[side] += 3.5; }
        } else addR(pl, -0.03);
        t.pass_accuracy = r1((100 * t.passes_completed) / t.passes);
        pl.pass_accuracy = r1((100 * pl.passes_completed) / pl.passes);
        break;
      }
      case "carry":
        if (ev.x < 70 && ev.end_x >= 70) { t.final_third_entries++; this.pressRaw[side] += 1; }
        if (!inBox(ev.x, ev.y) && inBox(ev.end_x, ev.end_y)) { t.box_entries++; this.pressRaw[side] += 2; }
        break;
      case "dribble":
        if (ev.outcome === "success") addR(pl, 0.1);
        break;
      case "shot": {
        const s = ev.shot;
        t.shots++;
        t.xg = r2(t.xg + s.xg);
        pl.shots++;
        pl.xg = r2(pl.xg + s.xg);
        addR(pl, s.xg);
        if (s.on_target) { t.shots_on_target++; addR(pl, 0.1); }
        t.max_shot_speed_kmh = Math.max(t.max_shot_speed_kmh, s.shot_speed_kmh);
        this.shotLog.push({ clock: ev.clock_ms, side, xg: s.xg });
        this.pressRaw[side] += 2 + 10 * s.xg;
        if (s.assist_player_id) {
          const ap = this.players[s.assist_player_id];
          if (ap) { ap.key_passes++; addR(ap, 0.2); }
        }
        break;
      }
      case "goal":
        t.goals++;
        pl.goals++;
        addR(pl, 1.0);
        if (ev.extra.assist_player_id) addR(this.players[ev.extra.assist_player_id], 0.6);
        break;
      case "corner":
        t.corners++;
        this.pressRaw[side] += 1.5;
        break;
      case "foul":
        t.fouls++;
        break;
      case "card":
        if (ev.card.colour === "yellow") { t.yellow_cards++; addR(pl, -0.3); }
        else { t.red_cards++; addR(pl, -1.5); }
        break;
      case "tackle":
      case "interception":
      case "ball_recovery":
        if (ev.type === "tackle") pl.tackles_won++;
        if (ev.type === "interception") pl.interceptions++;
        addR(pl, 0.1);
        if (ev.x > 52.5) this.pressRaw[side] += 1.5;
        break;
      case "save":
        addR(pl, 0.15);
        break;
      default:
    }
    this.team.home.pressure_index = r1(100 * (1 - Math.exp(-this.pressRaw.home / 8)));
    this.team.away.pressure_index = r1(100 * (1 - Math.exp(-this.pressRaw.away / 8)));
    this.momentum = 0.8 * this.momentum + 0.2 * (this.team.home.pressure_index - this.team.away.pressure_index);
  }

  window5() {
    const since = this.clock - 5 * MIN;
    const passes = this.passLog.filter((p) => p.clock >= since);
    const poss = this.possLog.filter((p) => p.start >= since);
    return { since, passes, poss };
  }

  indices() {
    const { since, passes, poss } = this.window5();
    const spanMin = Math.max(1, Math.min(5, (this.clock - this.periodStart[this.period]) / MIN));
    // possession share in window from segments
    const share = { home: 0, away: 0 };
    for (let i = 0; i < this.possLog.length; i++) {
      const p = this.possLog[i];
      const end = this.possLog[i + 1]?.start ?? this.clock;
      const s = Math.max(p.start, since);
      if (end > s) share[p.side] += end - s;
    }
    const tot = share.home + share.away || 1;
    const control = {};
    for (const side of ["home", "away"]) {
      const mine = passes.filter((p) => p.side === side);
      const comp = mine.length ? mine.filter((p) => p.ok).length / mine.length : 0;
      const myPoss = poss.filter((p) => p.side === side);
      const ppp = myPoss.length ? myPoss.reduce((a, p) => a + p.passes, 0) / myPoss.length : 0;
      control[side] = r1(100 * (share[side] / tot) * comp * Math.min(1, ppp / 5));
    }
    const turnovers = poss.length;
    const short = poss.length ? poss.filter((p) => p.passes < 3).length / poss.length : 0;
    const long = passes.length ? passes.filter((p) => p.len > 35).length / passes.length : 0;
    const chaos = r1(100 * (0.5 * Math.min(1, turnovers / spanMin / 2.5) + 0.3 * short + 0.2 * long));
    const tempo = r1(passes.length / spanMin);
    return { control, chaos, tempo, turnovers };
  }

  winProb() {
    const { home, away } = this.teams;
    let rh = (home.pre_match_goal_rate * (75 / away.defence_rating) * 1.08) / 90;
    let ra = (away.pre_match_goal_rate * (75 / home.defence_rating) * 0.95) / 90;
    const m = this.minuteNow();
    if (m > 15) {
      const since = this.clock - 15 * MIN;
      const xg = (s) => this.shotLog.filter((x) => x.clock >= since && x.side === s).reduce((a, x) => a + x.xg, 0);
      rh = 0.7 * rh + 0.3 * (xg("home") / 15);
      ra = 0.7 * ra + 0.3 * (xg("away") / 15);
    }
    for (let i = 0; i < this.sentOff.home; i++) { rh *= 0.65; ra *= 1.25; }
    for (let i = 0; i < this.sentOff.away; i++) { ra *= 0.65; rh *= 1.25; }
    const diff = this.score.home - this.score.away;
    if (m > 60 && diff !== 0) {
      if (diff > 0) { rh *= 0.9; ra *= 1.15; } else { rh *= 1.15; ra *= 0.9; }
    }
    const T = Math.max(0, 94 - m);
    const ph = poissonPmf(rh * T);
    const pa = poissonPmf(ra * T);
    let H = 0, D = 0, A = 0;
    for (let i = 0; i <= 10; i++) for (let j = 0; j <= 10; j++) {
      const p = ph[i] * pa[j];
      const d = this.score.home + i - (this.score.away + j);
      if (d > 0) H += p; else if (d < 0) A += p; else D += p;
    }
    const s = H + D + A;
    return { home: H / s, draw: D / s, away: A / s };
  }

  snapshot() {
    this.lastSnapshot = this.clock;
    const idx = this.indices();
    this.team.home.control_index = idx.control.home;
    this.team.away.control_index = idx.control.away;
    const pt = this.possTime.home + this.possTime.away || 1;
    this.team.home.possession_pct = r1((100 * this.possTime.home) / pt);
    this.team.away.possession_pct = r1(100 - this.team.home.possession_pct);
    // phase with 60 s persistence
    let raw = "balanced";
    if (idx.chaos >= 60) raw = "chaos";
    else if (idx.control.home - idx.control.away >= 20) raw = "control_home";
    else if (idx.control.home - idx.control.away <= -20) raw = "control_away";
    if (raw !== this.phaseCandidate.phase) this.phaseCandidate = { phase: raw, since: this.clock };
    const prevPhase = this.phase;
    if (raw !== this.phase && this.clock - this.phaseCandidate.since >= MIN) this.phase = raw;
    this.idx = idx;
    if (this.minuteNow() > 15) this.tempoSamples.push(idx.tempo);

    const wp = this.winProb();
    const players = {};
    for (const [id, p] of Object.entries(this.players)) {
      const until = p._off ?? this.clock;
      const minutes = Math.max(0, (until - p._on) / MIN);
      const maxSpeed = p._gk ? 24 : 25 + 0.095 * p._pace;
      p.minutes = r1(minutes);
      p.distance_km = r2((minutes / 90) * (p._gk ? 5.1 : 9.8 + p._pace / 60));
      if (!p._fixedTop) p.top_speed_kmh = r1(maxSpeed * (1 - Math.exp(-minutes / 10)));
      p.sprints = Math.floor(minutes * (p._gk ? 0.02 : 0.3));
      const { _diffSum, _on, _off, _pace, _gk, _fixedTop, ...pub } = p;
      // raw rating deltas follow MODEL_SPEC §2; compressed so a 90-minute mock stays in a believable 5–9 band
      players[id] = { ...pub, rating: r1(clamp(6 + 0.42 * (p.rating - 6), 3, 10)) };
    }
    const pubTeam = (t) => { const { _diffSum, ...rest } = t; return { ...rest }; };
    const state = {
      match_id: this.matchId, seq: this.seq - 1, period: this.period, clock_ms: Math.round(this.clock),
      score_home: this.score.home, score_away: this.score.away,
      home: pubTeam(this.team.home), away: pubTeam(this.team.away),
      win_prob: { home: r2(wp.home), draw: r2(wp.draw), away: r2(wp.away) },
      phase: this.phase, chaos_index: idx.chaos, tempo_ppm: idx.tempo,
      momentum: r1(clamp(this.momentum, -100, 100)), players,
    };
    this.lastState = state;
    this.items.push({ clock: state.clock_ms, period: this.period, kind: "state", data: state });
    if (prevPhase !== this.phase) this.onPhaseChange(prevPhase, this.phase);
    this.pressHistory.push({ clock: this.clock, home: this.team.home.pressure_index, away: this.team.away.pressure_index });
    this.detectPeriodic();
  }

  // ------------------------------------------------------------------ moments
  baseFacts() {
    return [
      { key: "minute", label: "Minute", value: minuteLabel(this.period, this.clock) },
      { key: "score", label: "Score", value: `${this.score.home}-${this.score.away}` },
      { key: "home_team", label: "Home team", value: this.teams.home.name },
      { key: "away_team", label: "Away team", value: this.teams.away.name },
    ];
  }

  canFire(type, side, scripted) {
    if (type === "goal" || type === "half_time" || type === "full_time") return true;
    if (!scripted && this.clock - this.lastMomentClock < 45_000) return false;
    const key = `${type}:${side ?? "-"}`;
    // Same-type cooldowns: 180 s per spec, stretched for the noisier data-driven rules.
    const cooldown = { momentum_run: 600_000, wp_swing: 300_000, pressure_shift: 420_000, phase_change: 420_000 }[type] ?? 180_000;
    return this.clock - (this.lastByType[key] ?? -Infinity) >= cooldown;
  }

  moment(type, { side = null, players = [], significance, facts, trigger, wpBefore, scripted = false }) {
    if (!this.canFire(type, side, scripted)) return null;
    this.lastMomentClock = this.clock;
    this.lastByType[`${type}:${side ?? "-"}`] = this.clock;
    const wpNow = this.winProb();
    const round = (w) => ({ home: r2(w.home), draw: r2(w.draw), away: r2(w.away) });
    const m = {
      contract_version: CONTRACT_VERSION, moment_id: this.id(), match_id: this.matchId, type,
      significance: r2(clamp(significance, 0, 1)), period: this.period, clock_ms: Math.round(this.clock),
      seq_start: Math.max(0, this.seq - 6), seq_end: this.seq - 1,
      team_id: side ? this.teams[side].team_id : null, player_ids: players,
      wp_before: round(wpBefore ?? wpNow), wp_after: round(wpNow),
      facts: [...this.baseFacts(), ...facts], trigger,
    };
    this.moments.push(m);
    this.snapshot();
    this.items.push({ clock: m.clock_ms, period: this.period, kind: "moment", data: m });
    return m;
  }

  wpAt(clock) {
    let best = this.wpHistory[0];
    for (const p of this.wpHistory) { if (p.clock_ms <= clock) best = p; else break; }
    return best ?? { home: 0.33, draw: 0.34, away: 0.33 };
  }

  name(id) {
    for (const side of ["home", "away"]) {
      const p = this.teams[side].players.find((x) => x.player_id === id);
      if (p) return p.name;
    }
    return id;
  }

  wpFacts(before, after, keys = ["home", "away"]) {
    const out = [];
    for (const k of keys) {
      out.push({ key: `wp_${k}_before`, label: `${k === "home" ? "Home" : "Away"} win probability before`, value: pct(before[k]), unit: "%" });
      out.push({ key: `wp_${k}_after`, label: `${k === "home" ? "Home" : "Away"} win probability after`, value: pct(after[k]), unit: "%" });
      out.push({ key: `wp_${k}_delta`, label: `${k === "home" ? "Home" : "Away"} win probability change`, value: pct(after[k]) - pct(before[k]), unit: "pts" });
    }
    return out;
  }

  detect(ev) {
    // wp_swing: |ΔP| ≥ 0.06 within 90 s, not caused by a goal or red card
    if (this.clock < 3 * MIN) return;
    const recentDecisive = ev.type === "goal" || ev.outcome === "goal" || ev.type === "card" ||
      this.moments.some((m) => (m.type === "goal" || m.type === "red_card") && this.clock - m.clock_ms < 120_000);
    if (recentDecisive) return;
    const before = this.wpAt(this.clock - 90_000);
    const now = this.winProb();
    const dh = now.home - before.home;
    const da = now.away - before.away;
    const big = Math.abs(dh) >= Math.abs(da) ? "home" : "away";
    const d = big === "home" ? dh : da;
    if (Math.abs(d) >= 0.06) {
      const side = d > 0 ? big : this.other(big);
      this.moment("wp_swing", {
        side, significance: Math.min(1, 5 * Math.abs(d)), wpBefore: before,
        facts: [
          ...this.wpFacts(before, now),
          { key: "cause_event", label: "Cause", value: ev.type.replace("_", " ") },
          { key: "window_s", label: "Window", value: 90, unit: "s" },
          { key: "pressure_index_home", label: "Home pressure index", value: Math.round(this.team.home.pressure_index) },
          { key: "pressure_index_away", label: "Away pressure index", value: Math.round(this.team.away.pressure_index) },
        ],
        trigger: "abs(dWP) >= 0.06 within 90s",
      });
    }
  }

  detectPeriodic() {
    if (this.minuteNow() < 4) return;
    const H = this.team.home.pressure_index;
    const A = this.team.away.pressure_index;
    const diff = H - A;
    // pressure_shift: sign flip with |diff| >= 25
    if (Math.abs(diff) >= 25) {
      const sign = Math.sign(diff);
      if (this.pressSign !== 0 && sign !== this.pressSign) {
        const old = this.pressHistory.find((p) => p.clock >= this.clock - 3 * MIN) ?? { home: H, away: A };
        const side = sign > 0 ? "home" : "away";
        const since = this.clock - 5 * MIN;
        const m = this.moment("pressure_shift", {
          side, significance: 0.4 + Math.min(0.2, Math.abs(diff) / 250),
          facts: [
            { key: "pressure_index_home_before", label: "Home pressure index before", value: Math.round(old.home) },
            { key: "pressure_index_home_after", label: "Home pressure index now", value: Math.round(H) },
            { key: "pressure_index_away_before", label: "Away pressure index before", value: Math.round(old.away) },
            { key: "pressure_index_away_after", label: "Away pressure index now", value: Math.round(A) },
            { key: "shots_last_5", label: `${this.teams[side].name} shots, last 5 min`, value: this.shotLog.filter((s) => s.clock >= since && s.side === side).length },
            { key: "box_entries_last_5", label: `${this.teams[side].name} box entries (match)`, value: this.team[side].box_entries },
          ],
          trigger: "sign flip of pressure difference with |diff| >= 25",
        });
        if (m) this.pressSign = sign;
      } else if (this.pressSign === 0) this.pressSign = sign;
    }
    // momentum_run: ≥ 3 shots in 5 min, 0 opponent shots
    const since = this.clock - 5 * MIN;
    for (const side of ["home", "away"]) {
      const mine = this.shotLog.filter((s) => s.clock >= since && s.side === side);
      const theirs = this.shotLog.filter((s) => s.clock >= since && s.side !== side);
      if (mine.length >= 3 && theirs.length === 0) {
        this.moment("momentum_run", {
          side, significance: 0.4,
          facts: [
            { key: "team", label: "Team", value: this.teams[side].name },
            { key: "shots_last_5", label: "Shots, last 5 min", value: mine.length },
            { key: "opp_shots_last_5", label: "Opponent shots, last 5 min", value: 0 },
            { key: "xg_last_5", label: "xG, last 5 min", value: r2(mine.reduce((a, s) => a + s.xg, 0)), unit: "xG" },
          ],
          trigger: ">= 3 shots in 5 min with 0 opponent shots",
        });
      }
    }
    // tempo_change: |tempo − match avg| > 35 % for ≥ 2 min
    if (this.tempoSamples.length > 60 && this.idx) {
      const avg = this.tempoSamples.reduce((a, v) => a + v, 0) / this.tempoSamples.length;
      const off = Math.abs(this.idx.tempo - avg) / avg > 0.35;
      if (off && this.tempoHigh.since == null) this.tempoHigh.since = this.clock;
      if (!off) this.tempoHigh.since = null;
      if (off && this.clock - this.tempoHigh.since >= 2 * MIN) {
        this.tempoHigh.since = null;
        this.moment("tempo_change", {
          significance: 0.25,
          facts: [
            { key: "tempo_ppm", label: "Passes per minute, last 5 min", value: r1(this.idx.tempo) },
            { key: "tempo_match_avg", label: "Match average passes per minute", value: r1(avg) },
            { key: "tempo_direction", label: "Direction", value: this.idx.tempo > avg ? "up" : "down" },
          ],
          trigger: "|tempo - match avg| > 35% for >= 2 min",
        });
      }
    }
  }

  onPhaseChange(before, after) {
    if (this.minuteNow() < 8) return;
    const side = after === "control_home" ? "home" : after === "control_away" ? "away" : null;
    this.moment("phase_change", {
      side, significance: 0.35,
      facts: [
        { key: "phase_before", label: "Phase before", value: before },
        { key: "phase_after", label: "Phase now", value: after },
        { key: "control_index_home", label: "Home control index", value: Math.round(this.idx.control.home) },
        { key: "control_index_away", label: "Away control index", value: Math.round(this.idx.control.away) },
        { key: "chaos_index", label: "Chaos index", value: Math.round(this.idx.chaos) },
        { key: "turnovers_last_5", label: "Turnovers, last 5 min", value: this.idx.turnovers },
      ],
      trigger: "phase transition persisted 60s",
    });
  }

  // ------------------------------------------------------------------ play
  newPossession(side, x, y, carrierEntry, emitChange = true) {
    this.possId++;
    this.possSide = side;
    this.ball = [x, y];
    this.carrier = carrierEntry ?? this.nearest(side, x, y);
    this.possLog.push({ id: this.possId, side, start: this.clock, passes: 0 });
    if (emitChange) this.emit("possession_change", side, this.carrier.p, { x, y });
  }

  turnover(type, x, y) {
    // x, y in the current possessing side's frame
    const def = this.other(this.possSide);
    const [mx, my] = mirror(x, y);
    const winner = this.nearest(def, mx, my);
    this.emit(type, def, winner.p, { x: mx, y: my, outcome: "success" });
    this.newPossession(def, mx, my, winner);
  }

  restart(type, side, x, y, gap, carrierEntry) {
    this.advance(gap);
    this.newPossession(side, x, y, carrierEntry, false);
    this.emit(type, side, this.carrier.p, { x, y });
  }

  pressure(side) {
    if (!this.chance(0.22)) return { under: false, near: r1(this.r(3, 9)) };
    const [mx, my] = mirror(...this.ball);
    const presser = this.nearest(this.other(side), mx, my);
    this.emit("pressure", this.other(side), presser.p, { x: mx, y: my });
    this.advance(this.r(0.3, 0.8));
    return { under: true, near: r1(this.r(0.8, 2.9)) };
  }

  pass(side, opts = {}) {
    const [x, y] = this.ball;
    const passer = this.carrier;
    // Pick the receiver first (MODEL_SPEC §1.3: weight ∝ exp(0.04·progressive) · openness), then pass to them.
    let target = opts.recipient ?? null;
    let to = opts.to ?? null;
    if (!to) {
      const mates = this.onPitch[side]
        .filter((e) => e.p.player_id !== passer.p.player_id && (e.role !== "GK" || x < 25))
        .map((e) => {
          const [px, py] = anchorPos(e.role, x, y);
          const d = Math.hypot(px - x, py - y);
          const roleW = { MF: 1.6, DF: 1.0, FW: 0.55, GK: 0.5 }[e.p.position] ?? 1;
          const w = roleW * Math.exp(0.04 * clamp(px - x, -20, 40)) * Math.exp(-Math.abs(d - 16) / 14) * (0.6 + this.rng());
          return { e, px, py, w };
        });
      let pick = this.rng() * mates.reduce((a, m) => a + m.w, 0);
      const m = mates.find((c) => (pick -= c.w) <= 0) ?? mates[0];
      target = m.e;
      to = [m.px + this.r(-4, 4), m.py + this.r(-4, 4)];
    }
    let ex = to[0];
    let ey = to[1];
    ex = clamp(ex, 1, 104);
    ey = clamp(ey, 1, 67);
    const len = Math.hypot(ex - x, ey - y);
    const prog = ex - x;
    const lineBreaking = opts.lineBreaking ?? (prog >= 15 && len >= 20 && this.chance(0.5));
    const intoBox = !inBox(x, y) && inBox(ex, ey);
    const isCross = opts.cross ?? ((y < 14 || y > 54) && x > 70 && intoBox);
    const pr = opts.under ?? false;
    const t = this.teams[side];
    const o = this.teams[this.other(side)];
    const z = 3.2 - 0.045 * len - 0.04 * Math.max(prog, 0) - 0.9 * pr - 0.8 * lineBreaking - 0.6 * intoBox -
      0.7 * isCross + 0.03 * (passer.p.ratings.passing - 70) - 0.02 * (o.defence_rating - 70) +
      1.5 * ((t.attack_rating - o.defence_rating) / 100) + (side === "home" ? 0.15 : 0) + this.edge(side);
    let pc = clamp(sigmoid(z), 0.2, 0.98);
    if (opts.difficulty != null) pc = clamp(1 - opts.difficulty / 10, 0.2, 0.98);
    const difficulty = opts.difficulty ?? r1(10 * (1 - pc));
    const ok = opts.force != null ? opts.force : this.chance(pc);
    const height = isCross || len > 38 ? "high" : len > 25 && this.chance(0.4) ? "low" : "ground";
    const speed = height === "high" ? clamp(70 + 10 * (this.rng() * 2 - 1), 20, 110) : clamp(45 + 0.6 * len + 6 * (this.rng() * 2 - 1), 20, 110);
    const recipient = target ?? this.nearest(side, ex, ey, { exclude: passer.p.player_id });
    const ev = this.emit("pass", side, passer.p, {
      x, y, end_x: ex, end_y: ey, outcome: ok ? "success" : "fail", under_pressure: pr,
      nearest_opponent_m: opts.near ?? null,
      pass: {
        length_m: r1(len), progressive_m: r1(prog), height, ball_speed_kmh: Math.round(opts.ballSpeed ?? speed),
        expected_completion: r2(pc), difficulty, line_breaking: lineBreaking, into_box: intoBox, is_cross: isCross,
        is_through_ball: opts.through ?? false, recipient_id: ok ? recipient.p.player_id : null,
      },
    });
    this.advance(len / (speed / 3.6) + 0.6);
    if (ok) {
      this.ball = [ex, ey];
      this.carrier = recipient;
    } else {
      const f = this.r(0.4, 0.9);
      const tx = x + (ex - x) * f;
      const ty = y + (ey - y) * f;
      if ((ey < 3 || ey > 65) && this.chance(0.5)) {
        const [mx, my] = mirror(ex, ey < 34 ? 0 : 68);
        this.restart("throw_in", this.other(side), mx, my, this.r(10, 20));
      } else this.turnover(this.chance(0.6) ? "interception" : "ball_recovery", tx, ty);
    }
    return { ev, ok };
  }

  shot(side, { x, y, header = false, forced = null, xg = null, speed = null, assist = null, situation = "open_play", shooter = null }) {
    const sh = shooter ?? this.carrier;
    const geo = xgAt(x, y, header);
    const xgv = r2(xg ?? geo.xg);
    let outcome = forced;
    if (!outcome) {
      if (this.chance(0.15)) outcome = "blocked";
      else if (this.chance(0.3 + 0.6 * xgv)) outcome = "saved";
      else outcome = this.chance(0.02) ? "woodwork" : "off_target";
    }
    const fin = sh.p.ratings.finishing;
    const kmh = Math.round(speed ?? (header ? clamp(55 + 8 * (this.rng() * 2 - 1), 40, 75) : clamp(98 + 12 * (this.rng() * 2 - 1) + 0.2 * Math.max(0, fin - 70), 55, 135)));
    const onTarget = outcome === "saved" || outcome === "goal";
    this.emit("shot", side, sh.p, {
      x, y, end_x: 105, end_y: clamp(34 + this.r(-3, 3), 0, 68), outcome,
      shot: {
        distance_m: r1(geo.dist), angle_rad: r2(geo.angle), body_part: header ? "head" : this.chance(0.7) ? "right_foot" : "left_foot",
        xg: xgv, shot_speed_kmh: kmh, on_target: onTarget, big_chance: xgv >= 0.3, situation,
        assist_player_id: assist?.p.player_id ?? null,
      },
    });
    this.advance(this.r(0.8, 1.6));
    const def = this.other(side);
    const gk = this.entry(def, "GK") ?? this.onPitch[def][0];
    if (outcome === "goal") return { outcome, xg: xgv, kmh, dist: geo.dist, shooter: sh };
    if (outcome === "saved") {
      this.emit("save", def, gk.p, { x: 2, y: 34, outcome: "success" });
      this.advance(this.r(1, 3));
      if (this.chance(0.3)) this.corner(side);
      else this.newPossession(def, 5, 34, gk);
    } else if (outcome === "blocked") {
      const [mx, my] = mirror(x + 2, y);
      const blocker = this.nearest(def, mx, my);
      this.emit("clearance", def, blocker.p, { x: mx, y: my, outcome: "success" });
      if (this.chance(0.35)) this.corner(side);
      else this.newPossession(def, mx, my, blocker);
    } else {
      this.restart("goal_kick", def, 6, 34, this.r(15, 25), gk);
    }
    return { outcome, xg: xgv, kmh, dist: geo.dist, shooter: sh };
  }

  corner(side) {
    const top = this.chance(0.5);
    this.restart("corner", side, 104.5, top ? 0.5 : 67.5, this.r(25, 40), this.nearest(side, 100, top ? 5 : 63));
    const target = [this.r(94, 100), this.r(28, 40)];
    const { ok } = this.pass(side, { to: target, cross: true });
    if (ok && this.chance(0.22)) this.shot(side, { x: target[0], y: target[1], header: true, situation: "corner" });
  }

  foul(side) {
    const def = this.other(side);
    const [mx, my] = mirror(...this.ball);
    const fouler = this.nearest(def, mx, my);
    this.emit("foul", def, fouler.p, { x: mx, y: my });
    if (this.chance(0.15) && !this.yellows[fouler.p.player_id]) this.card(def, fouler, "yellow");
    this.restart("free_kick", side, this.ball[0], this.ball[1], this.r(15, 30), this.carrier);
  }

  card(side, entry, colour) {
    const pid = entry.p.player_id;
    if (colour === "yellow") this.yellows[pid] = true;
    this.emit("card", side, entry.p, { outcome: colour === "yellow" ? "yellow" : "red", card: { colour, reason: "foul" } });
    if (colour !== "yellow") {
      this.onPitch[side] = this.onPitch[side].filter((e) => e !== entry);
      this.sentOff[side]++;
      this.players[pid]._off = this.clock;
    }
  }

  step() {
    const side = this.possSide;
    const [x, y] = this.ball;
    this.advance(this.r(0.6, 2.2)); // time on the ball
    const { under, near } = this.pressure(side);
    if (this.chance(0.011)) return this.foul(this.chance(0.35) ? this.other(side) : side);
    if (this.chance(0.003) && x > 60) {
      const [mx, my] = mirror(x + 10, y);
      this.emit("offside", side, this.carrier.p, { x: Math.min(104, x + 10), y });
      return this.restart("free_kick", this.other(side), mx, my, this.r(10, 20));
    }
    const w = {};
    const geo = xgAt(x, y);
    const edge = this.edge(side);
    if (x < 35) Object.assign(w, { pass: 0.78, carry: 0.15, dribble: 0.02, clearance: under ? 0.05 : 0 });
    else if (x < 70) Object.assign(w, { pass: 0.72, carry: 0.18, dribble: 0.07, shot: x > 60 ? 0.003 : 0 });
    else Object.assign(w, {
      pass: 0.5, carry: 0.12, dribble: 0.13, cross: y < 14 || y > 54 ? 0.1 : 0,
      // random shots come from the edge of the box; clear-cut chances are scripted beats
      shot: (x > 76 && x < 96 ? 0.045 : 0.004) * Math.max(0.2, 1 + 1.5 * edge),
    });
    const total = Object.values(w).reduce((a, v) => a + v, 0);
    let pick = this.rng() * total;
    let action = "pass";
    for (const [k, v] of Object.entries(w)) { if ((pick -= v) <= 0) { action = k; break; } }

    if (action === "pass") return this.pass(side, { under, near });
    if (action === "cross") return this.pass(side, { to: [this.r(92, 101), this.r(24, 44)], cross: true, under });
    if (action === "shot") return this.shot(side, { x, y });
    if (action === "clearance") {
      this.emit("clearance", side, this.carrier.p, { x, y, outcome: "success", under_pressure: under });
      this.advance(this.r(2, 3));
      const lx = clamp(x + this.r(35, 55), 40, 100);
      const ly = this.r(8, 60);
      if (this.chance(0.55)) {
        const [mx, my] = mirror(lx, ly);
        const w2 = this.nearest(this.other(side), mx, my);
        this.emit("ball_recovery", this.other(side), w2.p, { x: mx, y: my, outcome: "success" });
        this.newPossession(this.other(side), mx, my, w2);
      } else {
        this.ball = [lx, ly];
        this.carrier = this.nearest(side, lx, ly);
      }
      return;
    }
    if (action === "carry") {
      const d = this.r(4, 15);
      const ex = clamp(x + d * (0.6 + 0.4 * this.rng()), 1, 104);
      const ey = clamp(y + this.r(-5, 5), 1, 67);
      this.emit("carry", side, this.carrier.p, { x, y, end_x: ex, end_y: ey, under_pressure: under });
      this.advance(this.r(2, 4));
      this.ball = [ex, ey];
      return;
    }
    // dribble
    const [mx, my] = mirror(x, y);
    const defender = this.nearest(this.other(side), mx, my);
    const ok = this.chance(sigmoid(0.05 * (this.carrier.p.ratings.dribbling - defender.p.ratings.tackling) + 0.3 + edge));
    this.emit("dribble", side, this.carrier.p, { x, y, end_x: x + 3, end_y: y, outcome: ok ? "success" : "fail", under_pressure: under });
    this.advance(this.r(1.5, 3));
    if (ok) this.ball = [Math.min(104, x + 3), y];
    else this.turnover("tackle", x, y);
  }

  // ------------------------------------------------------------------ beats
  beatClock(m) {
    return m <= 45 ? (m - 1) * MIN + 20_000 : H1_LEN + (m - 46) * MIN + 20_000;
  }

  buildUp(side, n = 3) {
    this.newPossession(side, this.r(45, 58), this.r(20, 48), null, true);
    for (let i = 0; i < n; i++) {
      const [x, y] = this.ball;
      this.pass(side, { to: [clamp(x + this.r(6, 14), 1, 85), clamp(y + this.r(-10, 10), 6, 62)], force: true });
    }
  }

  runBeat(b) {
    const side = b.side;
    const T = this.teams[side];
    if (b.kind === "elite_pass") {
      this.buildUp(side, 2);
      const passer = this.entry(side, b.passer);
      const rec = this.entry(side, b.recipient);
      this.carrier = passer;
      this.ball = [44, 40];
      const to = [44 + b.prog, 40 + Math.sqrt(b.len ** 2 - b.prog ** 2)];
      this.pass(side, { to, recipient: rec, difficulty: b.difficulty, force: true, lineBreaking: true, through: true, ballSpeed: 71 });
      const ev = this.items.filter((i) => i.kind === "event").at(-1).data;
      this.moment("elite_pass", {
        side, players: [passer.p.player_id, rec.p.player_id], significance: 0.45, scripted: true,
        facts: [
          { key: "passer", label: "Passer", value: passer.p.name },
          { key: "recipient", label: "Recipient", value: rec.p.name },
          { key: "difficulty", label: "Pass difficulty (0-10)", value: ev.pass.difficulty },
          { key: "expected_completion_pct", label: "Expected completion", value: pct(ev.pass.expected_completion), unit: "%" },
          { key: "length_m", label: "Pass length", value: ev.pass.length_m, unit: "m" },
          { key: "progressive_m", label: "Metres gained", value: ev.pass.progressive_m, unit: "m" },
          { key: "ball_speed_kmh", label: "Ball speed", value: ev.pass.ball_speed_kmh, unit: "km/h" },
        ],
        trigger: "completed pass, difficulty >= 7.5 and progressive >= 15 m",
      });
      return;
    }
    if (b.kind === "card") {
      const entry = this.entry(side, b.player);
      if (!entry) return;
      const before = this.winProb();
      const [mx, my] = mirror(this.r(40, 60), this.r(10, 58));
      this.emit("foul", side, entry.p, { x: mx, y: my });
      this.card(side, entry, b.colour);
      if (b.colour !== "yellow") {
        const after = this.winProb();
        this.moment("red_card", {
          side, players: [entry.p.player_id], significance: 0.8, wpBefore: before, scripted: true,
          facts: [
            { key: "player", label: "Player sent off", value: entry.p.name },
            { key: "team", label: "Team", value: T.name },
            { key: "card", label: "Card", value: b.colour === "second_yellow" ? "second yellow" : "straight red" },
            ...this.wpFacts(before, after),
          ],
          trigger: "red card",
        });
      }
      this.restart("free_kick", this.other(side), ...mirror(mx, my), this.r(30, 45));
      return;
    }
    if (b.kind === "sub") {
      const idx = this.onPitch[side].findIndex((e) => e.role === b.off);
      if (idx < 0) return;
      const off = this.onPitch[side][idx];
      const onP = this.teams[side].players[b.on];
      this.emit("substitution", side, onP, { substitution: { player_off_id: off.p.player_id, player_on_id: onP.player_id } });
      this.players[off.p.player_id]._off = this.clock;
      this.ensurePlayer(onP, side, this.clock);
      this.onPitch[side][idx] = { p: onP, role: off.role, on: this.clock };
      this.advance(this.r(20, 35));
      return;
    }
    if (b.kind === "milestone") {
      const best = Object.values(this.players)
        .filter((p) => p.team_id === T.team_id && this.teams[side].players.find((x) => x.player_id === p.player_id)?.position === "MF")
        .sort((a, c) => c.passes_completed - a.passes_completed)[0];
      this.moment("player_milestone", {
        side, players: [best.player_id], significance: 0.3, scripted: true,
        facts: [
          { key: "player", label: "Player", value: this.name(best.player_id) },
          { key: "milestone", label: "Milestone", value: "most completed passes of any midfielder" },
          { key: "value", label: "Completed passes", value: best.passes_completed },
          { key: "pass_accuracy", label: "Pass accuracy", value: Math.round(best.pass_accuracy), unit: "%" },
        ],
        trigger: "player completed-pass leader after 55'",
      });
      return;
    }
    if (b.kind === "sprint") {
      const entry = this.entry(side, b.player);
      const p = this.players[entry.p.player_id];
      const prev = Math.max(...Object.values(this.players).map((x) => x.top_speed_kmh));
      p.top_speed_kmh = b.speed;
      p._fixedTop = true;
      this.newPossession(side, 30, 10, entry, true);
      this.emit("carry", side, entry.p, { x: 30, y: 10, end_x: 66, end_y: 8, extra: { sprint_kmh: b.speed } });
      this.advance(5);
      this.ball = [66, 8];
      this.moment("speed_record", {
        side, players: [entry.p.player_id], significance: 0.3, scripted: true,
        facts: [
          { key: "player", label: "Player", value: entry.p.name },
          { key: "speed_kmh", label: "Top speed", value: Math.round(b.speed), unit: "km/h" },
          { key: "previous_match_high", label: "Previous match high", value: Math.round(prev), unit: "km/h" },
          { key: "record_kind", label: "Record", value: "fastest sprint of the match" },
        ],
        trigger: "player first >= 33 km/h",
      });
      return;
    }
    // shot / goal beats
    const shooter = this.entry(side, b.shooter);
    const assist = b.assist ? this.entry(side, b.assist) : null;
    if (!shooter) return;
    const before = this.winProb();
    const sx = 105 - b.dist * 0.97;
    const sy = 34 + (this.chance(0.5) ? 1 : -1) * b.dist * 0.24;
    this.buildUp(side, 3);
    if (assist) {
      this.carrier = assist;
      this.ball = b.cross ? [94, 6] : [clamp(sx - 16, 50, 85), clamp(sy + 9, 8, 60)];
      this.pass(side, { to: [sx, sy], recipient: shooter, force: true, difficulty: b.assistDifficulty ?? null, cross: !!b.cross, through: !!b.through });
    } else {
      this.carrier = shooter;
      this.ball = [sx, sy];
    }
    const isGoal = b.kind === "goal";
    const header = b.body === "head";
    const res = this.shot(side, { x: sx, y: sy, header, forced: isGoal ? "goal" : b.outcome, xg: b.xg, speed: b.speed, assist, shooter });
    const name = shooter.p.name;
    if (isGoal) {
      this.score[side]++;
      this.emit("goal", side, shooter.p, { x: sx, y: sy, outcome: "goal", extra: { assist_player_id: assist?.p.player_id ?? null } });
      const after = this.winProb();
      const k = side;
      const passEv = assist ? this.items.filter((i) => i.kind === "event" && i.data.type === "pass").at(-1).data : null;
      this.moment("goal", {
        side, players: [shooter.p.player_id, ...(assist ? [assist.p.player_id] : [])],
        significance: 0.6 + Math.min(0.4, Math.abs(after[k] - before[k])), wpBefore: before, scripted: true,
        facts: [
          { key: "scorer", label: "Scorer", value: name },
          { key: "team", label: "Scoring team", value: T.name },
          { key: "xg", label: "Shot xG", value: res.xg, unit: "xG" },
          { key: "shot_speed_kmh", label: "Shot speed", value: res.kmh, unit: "km/h" },
          { key: "distance_m", label: "Distance", value: r1(res.dist), unit: "m" },
          { key: "body_part", label: "Body part", value: header ? "header" : b.body === "left_foot" ? "left foot" : "right foot" },
          { key: "wp_scorer_before", label: "Scorer win probability before", value: pct(before[k]), unit: "%" },
          { key: "wp_scorer_after", label: "Scorer win probability after", value: pct(after[k]), unit: "%" },
          { key: "wp_scorer_delta", label: "Scorer win probability change", value: pct(after[k]) - pct(before[k]), unit: "pts" },
          { key: "score_after", label: "Score after", value: `${this.score.home}-${this.score.away}` },
          ...(assist ? [
            { key: "assist", label: "Assist", value: assist.p.name },
            { key: "pass_difficulty_of_assist", label: "Assist pass difficulty (0-10)", value: passEv.pass.difficulty },
          ] : []),
          { key: "team_xg_total", label: `${T.name} xG total`, value: this.team[side].xg, unit: "xG" },
        ],
        trigger: "goal",
      });
      this.advance(this.r(60, 90));
      const conceding = this.other(side);
      this.newPossession(conceding, 52.5, 34, this.entry(conceding, "ST") ?? this.onPitch[conceding][5], false);
      this.emit("kick_off", conceding, this.carrier.p, { x: 52.5, y: 34 });
      return;
    }
    if (b.xg >= 0.3) {
      this.moment("big_chance", {
        side, players: [shooter.p.player_id], significance: 0.3 + 0.5 * b.xg, scripted: true,
        facts: [
          { key: "shooter", label: "Shooter", value: name },
          { key: "team", label: "Team", value: T.name },
          { key: "xg", label: "Shot xG", value: res.xg, unit: "xG" },
          { key: "outcome", label: "Outcome", value: res.outcome.replace("_", " ") },
          { key: "distance_m", label: "Distance", value: r1(res.dist), unit: "m" },
          { key: "team_xg_total", label: `${T.name} xG total`, value: this.team[side].xg, unit: "xG" },
        ],
        trigger: "shot xG >= 0.30 not scored",
      });
    } else if (res.kmh >= 110 && res.kmh > this.matchHighShot) {
      this.moment("speed_record", {
        side, players: [shooter.p.player_id], significance: 0.3, scripted: true,
        facts: [
          { key: "player", label: "Player", value: name },
          { key: "speed_kmh", label: "Shot speed", value: res.kmh, unit: "km/h" },
          { key: "previous_match_high", label: "Previous match high", value: this.matchHighShot, unit: "km/h" },
          { key: "record_kind", label: "Record", value: res.outcome === "woodwork" ? "hardest shot of the match, off the woodwork" : "hardest shot of the match" },
        ],
        trigger: "match-high shot >= 110 km/h",
      });
    }
    this.matchHighShot = Math.max(this.matchHighShot, res.kmh);
  }

  periodSummaryFacts() {
    const h = this.team.home;
    const a = this.team.away;
    const wp = this.winProb();
    const top = Object.values(this.players).sort((x, y) => y.rating - x.rating).slice(0, 3);
    const pubRating = (p) => clamp(6 + 0.42 * (p.rating - 6), 3, 10);
    return [
      { key: "xg_home", label: "Home xG", value: h.xg, unit: "xG" },
      { key: "xg_away", label: "Away xG", value: a.xg, unit: "xG" },
      { key: "shots_home", label: "Home shots", value: h.shots },
      { key: "shots_away", label: "Away shots", value: a.shots },
      { key: "possession_home", label: "Home possession", value: Math.round(h.possession_pct), unit: "%" },
      { key: "possession_away", label: "Away possession", value: Math.round(a.possession_pct), unit: "%" },
      { key: "wp_home", label: "Home win probability", value: pct(wp.home), unit: "%" },
      { key: "wp_draw", label: "Draw probability", value: pct(wp.draw), unit: "%" },
      { key: "wp_away", label: "Away win probability", value: pct(wp.away), unit: "%" },
      ...top.map((p, i) => ({ key: `top_player_${i + 1}`, label: `Top rated #${i + 1}`, value: `${this.name(p.player_id)} ${r1(pubRating(p)).toFixed(1)}` })),
      { key: "top_player_1_id", label: "Top rated player id", value: top[0].player_id },
    ];
  }

  runPeriod(period) {
    this.period = period;
    const start = period === 1 ? 0 : H1_LEN;
    const end = start + (period === 1 ? H1_LEN : H2_LEN);
    this.clock = start;
    this.lastEventClock = start;
    this.emit("period_start", null, null, {});
    const ko = period === 1 ? "home" : "away";
    this.newPossession(ko, 52.5, 34, this.entry(ko, "ST"), false);
    this.emit("kick_off", ko, this.carrier.p, { x: 52.5, y: 34 });
    const beats = BEATS.filter((b) => (period === 1 ? b.m <= 45 : b.m > 45));
    let bi = 0;
    while (this.clock < end) {
      if (bi < beats.length && this.clock >= this.beatClock(beats[bi].m)) {
        this.runBeat(beats[bi++]);
        continue;
      }
      this.step();
    }
    this.clock = end;
    this.emit("period_end", null, null, {});
    this.snapshot();
  }

  run() {
    this.emit("match_start", null, null, {});
    this.runPeriod(1);
    const ht = this.moment("half_time", { significance: 0.5, facts: this.periodSummaryFacts(), trigger: "period end" });
    void ht;
    this.runPeriod(2);
    this.emit("match_end", null, null, {});
    this.moment("full_time", { significance: 1.0, facts: this.periodSummaryFacts(), trigger: "period end" });
    this.snapshot();
    return {
      matchId: this.matchId, home: this.teams.home, away: this.teams.away, items: this.items,
      periodStart: this.periodStart, moments: this.moments, finalState: this.lastState,
      wpHistory: this.wpHistory,
    };
  }
}
