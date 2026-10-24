// Fulcrum mock API: REST + WebSocket on :8000, speaking docs/API_CONTRACT.md §2–3.
//
// Replays a believable synthetic match (mock/match.mjs) with moments, persona/language insights and
// overlay cues (mock/text.mjs) so the web app can be built and demoed without the Python pipeline.
//
//   npm run mock                       # NBA v RVR, seed 7, 10× speed
//   npm run mock -- --from 80          # start at 80' (handy for recording the finish)
//   npm run mock -- --home HLC --away MDU --speed 20 --loop
//
// Every viewer shares one match clock, so split view shows the same match with two prefs side by side.
import { createServer } from "node:http";
import { readFileSync } from "node:fs";
import { parseArgs } from "node:util";
import { WebSocketServer } from "ws";
import { buildMatch, H1_LEN, minuteLabel } from "./match.mjs";
import { buildCues, buildInsight, buildRecap, LANGUAGES, pickVariant, playerTagCue, variant } from "./text.mjs";

const { values: args } = parseArgs({
  options: {
    port: { type: "string", default: process.env.PORT ?? "8000" },
    home: { type: "string", default: "NBA" },
    away: { type: "string", default: "RVR" },
    seed: { type: "string", default: "7" },
    speed: { type: "string", default: "10" },
    from: { type: "string", default: "0" },
    "ht-break": { type: "string", default: "12" },
    loop: { type: "boolean", default: false },
  },
});

const TEAMS = JSON.parse(readFileSync(new URL("../../data/teams.json", import.meta.url), "utf8"));
const teamById = (id) => TEAMS.find((t) => t.team_id === id);
const HT_BREAK_WALL = Number(args["ht-break"]) * 1000;
const TICK_MS = 50;

let rngState = 0x9e3779b9;
const rand = () => {
  rngState ^= rngState << 13; rngState ^= rngState >>> 17; rngState ^= rngState << 5;
  return ((rngState >>> 0) % 1_000_000) / 1_000_000;
};
const hexId = () => Array.from({ length: 32 }, () => Math.floor(rand() * 16).toString(16)).join("");

// ------------------------------------------------------------------ match session
class Session {
  constructor({ homeId, awayId, seed, speed, fromMinute = 0 }) {
    this.home = teamById(homeId) ?? TEAMS[0];
    this.away = teamById(awayId) ?? TEAMS[1];
    this.seed = seed;
    this.speed = speed;
    this.matchId = `demo-${this.home.team_id}-${this.away.team_id}-s${seed}`.toLowerCase();
    const built = buildMatch({ home: this.home, away: this.away, seed, matchId: this.matchId });
    this.periodStart = built.periodStart;
    this.ctx = makeCtx(this.home, this.away);
    this.items = this.schedule(built);
    this.store = { events: [], winprob: [], moments: [], insights: [], state: null, recapFor: null, finished: false };
    this.insightByMoment = new Map();
    const startWall = this.wallOf(fromMinute <= 45 ? fromMinute * 60_000 : H1_LEN + (fromMinute - 45) * 60_000, fromMinute > 45 ? 2 : 1);
    this.t0 = Date.now() - startWall;
    this.cursor = 0;
    // Fast-forward silently to --from: everything earlier lands in the catch-up snapshot only.
    while (this.cursor < this.items.length && this.items[this.cursor].wall <= startWall) this.deliver(this.items[this.cursor++], false);
    const goals = built.moments.filter((m) => m.type === "goal").length;
    console.log(`[mock] ${this.home.name} v ${this.away.name} · seed ${seed} · ${speed}× · ${built.moments.length} moments, ${goals} goals · match_id ${this.matchId}`);
  }

  wallOf(clock, period) {
    return clock / this.speed + (period === 2 ? HT_BREAK_WALL : 0);
  }

  clockAtWall(w) {
    const h1End = H1_LEN / this.speed;
    if (w <= h1End) return Math.round(w * this.speed);
    if (w <= h1End + HT_BREAK_WALL) return H1_LEN;
    return Math.round((w - HT_BREAK_WALL) * this.speed);
  }

  /** Give every item a wall-clock offset; add insight + recap items at "agent latency" after each moment. */
  schedule(built) {
    const out = [];
    let mi = 0;
    for (const it of built.items) {
      const wall = this.wallOf(it.clock, it.period) + (it.kind === "moment" ? 200 : 0);
      out.push({ ...it, wall });
      if (it.kind !== "moment") continue;
      const m = it.data;
      const status = mi % 9 === 4 ? "repaired" : mi % 13 === 7 ? "fallback" : "passed";
      const latency = Math.round(1600 + rand() * 2600 + (status === "repaired" ? 1900 : status === "fallback" ? 2800 : 0));
      const arrival = wall + latency;
      out.push({ kind: "insight", wall: arrival, moment: m, latency, status, seqAtMoment: m.seq_end });
      if (m.type === "full_time") out.push({ kind: "recap", wall: arrival + 1500, moment: m });
      mi++;
    }
    return out.sort((a, b) => a.wall - b.wall);
  }

  tick() {
    const now = Date.now() - this.t0;
    while (this.cursor < this.items.length && this.items[this.cursor].wall <= now) this.deliver(this.items[this.cursor++], true);
  }

  get clockNow() {
    return this.clockAtWall(Date.now() - this.t0);
  }

  deliver(item, live) {
    const s = this.store;
    switch (item.kind) {
      case "event": {
        const ev = { ...item.data, ts: new Date().toISOString() };
        s.events.push(ev);
        if (!live) break;
        broadcast(this, (sub) => {
          sub.send("event", ev);
          const tag = this.playerTag(sub, ev);
          if (tag) sub.send("cue", tag);
        });
        if (ev.type === "match_end") broadcast(this, (sub) => sub.send("match_end", { score_home: ev.score_home, score_away: ev.score_away }));
        break;
      }
      case "winprob":
        s.winprob.push(item.data);
        break;
      case "state":
        s.state = item.data;
        if (live) broadcast(this, (sub) => sub.send("state", item.data));
        break;
      case "moment":
        s.moments.push(item.data);
        if (live) broadcast(this, (sub) => sub.send("moment", item.data));
        break;
      case "insight": {
        const m = item.moment;
        const insight = buildInsight(m, this.ctx, { id: hexId(), latencyMs: item.latency, status: item.status });
        insight.showAt = this.clockAtWall(item.wall);
        insight.syncSeq = item.seqAtMoment;
        s.insights.push(insight);
        this.insightByMoment.set(m.moment_id, insight);
        if (live) broadcast(this, (sub) => this.sendInsight(sub, insight, m));
        break;
      }
      case "recap":
        s.finished = true;
        s.ftMoment = item.moment;
        if (live) broadcast(this, (sub) => sub.send("recap", this.recap(sub.prefs)));
        break;
      default:
    }
  }

  momentById(id) {
    return this.store.moments.find((m) => m.moment_id === id);
  }

  filteredInsight(insight, prefs) {
    const m = this.momentById(insight.moment_id);
    const v = pickVariant(insight, m, prefs, this.ctx);
    const { showAt, syncSeq, ...pub } = insight;
    return { ...pub, variants: [v] };
  }

  cuesFor(insight, prefs) {
    const m = this.momentById(insight.moment_id);
    const v = pickVariant(insight, m, prefs, this.ctx);
    // show_at = moment clock + agent latency (at this match speed), rounded up to the next second
    const showAt = Math.ceil(insight.showAt / 1000) * 1000;
    return buildCues(m, insight, v, this.ctx, { showAtClockMs: showAt, syncSeq: insight.syncSeq, newId: hexId });
  }

  sendInsight(sub, insight, m) {
    void m;
    sub.send("insight", this.filteredInsight(insight, sub.prefs));
    for (const cue of this.cuesFor(insight, sub.prefs)) sub.send("cue", cue);
  }

  playerTag(sub, ev) {
    const pid = sub.prefs.followed_player_id;
    if (!pid || ev.player_id !== pid) return null;
    if (ev.clock_ms - (sub.lastTagClock ?? -Infinity) < 30_000) return null;
    const p = this.ctx.player(pid);
    const cue = p && playerTagCue(ev, p, sub.prefs, { newId: hexId });
    if (cue) sub.lastTagClock = ev.clock_ms;
    return cue;
  }

  recap(prefs) {
    return buildRecap(this.store.ftMoment, this.store.moments, this.ctx, prefs);
  }

  hello(prefs) {
    const s = this.store;
    return {
      match_id: this.matchId, home: this.home, away: this.away, state: s.state, winprob: s.winprob,
      moments: s.moments, insights: s.insights.map((i) => this.filteredInsight(i, prefs)),
      // Extras beyond API_CONTRACT §3 (see docs/HANDOFFS.md): clock display + playback info.
      period_start_clock_ms: this.periodStart, speed: this.speed,
      status: s.finished ? "finished" : s.state ? "live" : "scheduled",
    };
  }

  summary() {
    const st = this.store.state;
    return {
      match_id: this.matchId, home_team_id: this.home.team_id, away_team_id: this.away.team_id,
      status: this.store.finished ? "finished" : "live", clock_ms: st?.clock_ms ?? 0,
      score: { home: st?.score_home ?? 0, away: st?.score_away ?? 0 },
    };
  }
}

function makeCtx(home, away) {
  const all = [...home.players, ...away.players];
  const byTeam = { [home.team_id]: home, [away.team_id]: away };
  return {
    home, away,
    teamName: (id) => byTeam[id]?.name ?? null,
    opponentId: (id) => (id === home.team_id ? away.team_id : id === away.team_id ? home.team_id : null),
    opponentName: (id) => (id === home.team_id ? away.name : id === away.team_id ? home.name : null),
    player: (id) => all.find((p) => p.player_id === id) ?? null,
  };
}

// ------------------------------------------------------------------ subscribers
const subscribers = new Set();
let firstSession = true;
let session = newSession({});

function newSession({ home_team_id, away_team_id, seed, speed }) {
  const s = new Session({
    homeId: home_team_id ?? args.home, awayId: away_team_id ?? args.away,
    seed: Number(seed ?? args.seed), speed: Number(speed ?? args.speed),
    fromMinute: firstSession ? Number(args.from) : 0,
  });
  firstSession = false;
  return s;
}

function broadcast(sess, fn) {
  for (const sub of subscribers) if (sub.matchId === sess.matchId) fn(sub);
}

function normPrefs(p = {}) {
  return {
    persona: ["analyst", "casual", "club_fan", "player_lens"].includes(p.persona) ? p.persona : "casual",
    language: LANGUAGES.includes(p.language) ? p.language : "en",
    favourite_team_id: p.favourite_team_id ?? null,
    followed_player_id: p.followed_player_id ?? null,
  };
}

function sendHello(sub) {
  sub.send("hello", session.hello(sub.prefs));
  if (session.store.finished) sub.send("recap", session.recap(sub.prefs));
}

setInterval(() => {
  session.tick();
  if (args.loop && session.store.finished && !session.loopAt) session.loopAt = Date.now() + 60_000;
  if (session.loopAt && Date.now() > session.loopAt) restart({});
}, TICK_MS);

function restart(cfg) {
  session = newSession(cfg);
  for (const sub of subscribers) {
    sub.matchId = session.matchId;
    sendHello(sub);
  }
}

// ------------------------------------------------------------------ HTTP
function send(res, status, body) {
  res.writeHead(status, {
    "content-type": status >= 400 ? "application/problem+json" : "application/json",
    "access-control-allow-origin": "*",
    "access-control-allow-headers": "content-type",
    "access-control-allow-methods": "GET,POST,OPTIONS",
  });
  res.end(JSON.stringify(body));
}
const problem = (res, status, title, detail) => send(res, status, { type: "about:blank", title, status, detail });

/** GET /api/eval/latest: the committed offline reports in ../../data/eval (the real API picks the newest). */
function latestEval() {
  const read = (name) => {
    try {
      const { per_item: _p, records: _r, ...rest } = JSON.parse(readFileSync(new URL(`../../data/eval/${name}`, import.meta.url), "utf8"));
      return { file: name, ...rest };
    } catch {
      return null;
    }
  };
  return { live: read("sample_seed11.json"), foundry: read("foundry_sample_seed11.json") };
}

async function readJson(req) {
  let body = "";
  for await (const chunk of req) body += chunk;
  try { return body ? JSON.parse(body) : {}; } catch { return null; }
}

const server = createServer(async (req, res) => {
  const url = new URL(req.url, "http://localhost");
  const parts = url.pathname.split("/").filter(Boolean);
  const q = Object.fromEntries(url.searchParams);
  if (req.method === "OPTIONS") return send(res, 204, {});
  if (parts[0] !== "api") return problem(res, 404, "Not found", `${url.pathname} is not an API route`);
  const [, resource, id, sub] = parts;

  if (resource === "health") return send(res, 200, { status: "ok", bus: "mock", llm: "mock" });
  if (resource === "teams") return send(res, 200, TEAMS);
  if (resource === "eval" && id === "latest") return send(res, 200, latestEval());
  if (resource === "matches" && !id) {
    if (req.method === "POST") {
      const cfg = await readJson(req);
      if (!cfg) return problem(res, 400, "Invalid JSON", "Body must be a MatchConfig object");
      restart(cfg);
      return send(res, 200, { match_id: session.matchId });
    }
    return send(res, 200, [session.summary()]);
  }
  if (resource === "matches" && id) {
    if (id !== session.matchId) return problem(res, 404, "Unknown match", `No match ${id}; current is ${session.matchId}`);
    const s = session.store;
    const prefs = normPrefs(q);
    switch (sub) {
      case "state": return send(res, 200, s.state);
      case "events": return send(res, 200, s.events.filter((e) => e.seq > Number(q.since_seq ?? -1)));
      case "winprob": return send(res, 200, s.winprob);
      case "moments": return send(res, 200, s.moments);
      case "insights": return send(res, 200, s.insights.map((i) => session.filteredInsight(i, prefs)));
      case "overlay-cues": {
        const since = Number(q.since_clock_ms ?? -1);
        return send(res, 200, s.insights.flatMap((i) => session.cuesFor(i, prefs)).filter((c) => c.show_at_clock_ms > since));
      }
      case "recap":
        if (!s.finished) return problem(res, 404, "Recap not ready", "The recap is generated at full time");
        return send(res, 200, session.recap(prefs));
      case "translate": {
        const body = await readJson(req);
        const ins = s.insights.find((i) => i.insight_id === body?.insight_id);
        if (!ins) return problem(res, 404, "Unknown insight", "insight_id not found");
        const lang = LANGUAGES.includes(body.language) ? body.language : "en";
        const m = session.momentById(ins.moment_id);
        return send(res, 200, ["analyst", "casual"].map((p) => variant(m, p, lang, session.ctx)));
      }
      case "ask": {
        // Ask the studio (mock): echo the latest moment's headline as the answer, citing that moment.
        const body = await readJson(req);
        const question = String(body?.question ?? "").trim();
        if (!question) return problem(res, 422, "Unprocessable Entity", "question is required");
        const last = s.insights[s.insights.length - 1];
        return send(res, 200, {
          match_id: session.matchId,
          question,
          answer: last ? `${last.explanation.headline}.` : "Nothing has happened yet: ask again after the first moment.",
          answerable: Boolean(last),
          check_status: last ? "passed" : "fallback",
          cited_moment_ids: last ? [last.moment_id] : [],
          fact_keys: [],
          persona: body?.persona ?? "casual",
          language: "en",
          latency_ms: 0,
        });
      }
      default:
        return problem(res, 404, "Not found", `Unknown resource ${sub}`);
    }
  }
  return problem(res, 404, "Not found", `${url.pathname} is not an API route`);
});

// ------------------------------------------------------------------ WebSocket
const wss = new WebSocketServer({ server, path: "/ws" });
wss.on("connection", (ws) => {
  const sub = {
    matchId: null, prefs: normPrefs(),
    send(type, payload) {
      if (ws.readyState !== ws.OPEN) return;
      ws.send(JSON.stringify({ type, match_id: this.matchId, payload, ts: new Date().toISOString() }));
    },
  };
  subscribers.add(sub);
  ws.on("message", (raw) => {
    let msg;
    try { msg = JSON.parse(raw.toString()); } catch { return sub.send("error", { code: "bad_json", message: "Message is not JSON" }); }
    if (msg.type === "ping") return sub.send("pong", {});
    if (msg.type === "subscribe") {
      if (msg.match_id && msg.match_id !== session.matchId) {
        sub.send("error", { code: "unknown_match", message: `No match ${msg.match_id}; subscribed to ${session.matchId}` });
      }
      sub.matchId = session.matchId;
      sub.prefs = normPrefs(msg.prefs);
      return sendHello(sub);
    }
    if (msg.type === "update_prefs") {
      sub.prefs = normPrefs({ ...sub.prefs, ...msg.prefs });
      // The mock re-sends a hello so the client's feed switches persona/language immediately.
      return sendHello(sub);
    }
    sub.send("error", { code: "bad_type", message: `Unknown message type ${msg.type}` });
  });
  ws.on("close", () => subscribers.delete(sub));
});

server.listen(Number(args.port), () => {
  console.log(`[mock] Fulcrum mock API on http://localhost:${args.port}  (REST /api, WebSocket /ws)`);
  console.log(`[mock] clock ${minuteLabel(session.clockNow > H1_LEN ? 2 : 1, session.clockNow)} · Ctrl+C to stop`);
});
