import { useEffect, useMemo, useReducer, useRef } from "react";
import { fetchEvents, resolveMatchId } from "./api";
import { WS_URL } from "./config";
import { PitchModel } from "./pitchModel";
import type {
  HelloPayload,
  Insight,
  MatchEvent,
  MatchState,
  Moment,
  OverlayCue,
  Recap,
  Team,
  ViewerPrefs,
  WinProbPoint,
} from "../types";

export type ConnStatus = "connecting" | "live" | "reconnecting" | "offline";

export interface FeedState {
  status: ConnStatus;
  error: string | null;
  matchId: string | null;
  home: Team | null;
  away: Team | null;
  state: MatchState | null;
  winprob: WinProbPoint[];
  moments: Moment[];
  insights: Record<string, Insight>;
  recentEvents: MatchEvent[];
  cues: OverlayCue[];
  recap: Recap | null;
  finished: boolean;
  periodStarts: Record<string, number>;
  /** Bumps on every hello so views can reset transient UI (overlay queue, etc.). */
  epoch: number;
}

type Action =
  | { type: "status"; status: ConnStatus; error?: string | null }
  | { type: "hello"; payload: HelloPayload }
  | { type: "event"; payload: MatchEvent }
  | { type: "state"; payload: MatchState }
  | { type: "moment"; payload: Moment }
  | { type: "insight"; payload: Insight }
  | { type: "cue"; payload: OverlayCue }
  | { type: "recap"; payload: Recap }
  | { type: "match_end" };

const initial: FeedState = {
  status: "connecting", error: null, matchId: null, home: null, away: null, state: null, winprob: [],
  moments: [], insights: {}, recentEvents: [], cues: [], recap: null, finished: false, periodStarts: {}, epoch: 0,
};

const CUE_LOG = 250;

function reducer(s: FeedState, a: Action): FeedState {
  switch (a.type) {
    case "status":
      return { ...s, status: a.status, error: a.error ?? null };
    case "hello": {
      const p = a.payload;
      const insights: Record<string, Insight> = {};
      for (const i of p.insights ?? []) insights[i.moment_id] = i;
      const sameMatch = s.matchId === p.match_id;
      return {
        ...s, status: "live", error: null, matchId: p.match_id, home: p.home, away: p.away, state: p.state ?? null,
        winprob: p.winprob ?? [], moments: p.moments ?? [], insights,
        recentEvents: sameMatch ? s.recentEvents : [], cues: sameMatch ? s.cues : [],
        recap: sameMatch ? s.recap : null, finished: p.status === "finished",
        periodStarts: p.period_start_clock_ms ?? s.periodStarts, epoch: s.epoch + 1,
      };
    }
    case "event": {
      const ev = a.payload;
      const periodStarts = ev.type === "period_start" ? { ...s.periodStarts, [String(ev.period)]: ev.clock_ms } : s.periodStarts;
      return { ...s, recentEvents: [...s.recentEvents.slice(-39), ev], periodStarts };
    }
    case "state": {
      const st = a.payload;
      const last = s.winprob[s.winprob.length - 1];
      const point = { clock_ms: st.clock_ms, seq: st.seq, home: st.win_prob.home, draw: st.win_prob.draw, away: st.win_prob.away };
      const winprob = !last || st.seq > last.seq ? [...s.winprob, point] : s.winprob;
      return { ...s, state: st, winprob };
    }
    case "moment":
      if (s.moments.some((m) => m.moment_id === a.payload.moment_id)) return s;
      return { ...s, moments: [...s.moments, a.payload] };
    case "insight":
      return { ...s, insights: { ...s.insights, [a.payload.moment_id]: a.payload } };
    case "cue":
      return { ...s, cues: [...s.cues.slice(-(CUE_LOG - 1)), a.payload] };
    case "recap":
      return { ...s, recap: a.payload, finished: true };
    case "match_end":
      return { ...s, finished: true };
    default:
      return s;
  }
}

/**
 * One WebSocket subscription to the live match with its own viewer prefs (API_CONTRACT §3).
 * Call it twice for split view — each call is fully independent.
 */
export function useMatchFeed(prefs: ViewerPrefs) {
  const [state, dispatch] = useReducer(reducer, initial);
  const pitch = useMemo(() => new PitchModel(), []);
  const wsRef = useRef<WebSocket | null>(null);
  const prefsRef = useRef(prefs);
  prefsRef.current = prefs;

  useEffect(() => {
    let closed = false;
    let retry = 0;
    let pingTimer: number | undefined;
    let retryTimer: number | undefined;
    let lineupFor: string | null = null;

    const connect = async () => {
      let matchId: string | null = null;
      try {
        matchId = await resolveMatchId();
      } catch (e) {
        scheduleRetry(`API unreachable at ${WS_URL.replace(/^ws/, "http").replace(/\/ws$/, "")} (${(e as Error).message})`);
        return;
      }
      if (closed) return;
      const ws = new WebSocket(WS_URL);
      wsRef.current = ws;
      ws.onopen = () => {
        retry = 0;
        ws.send(JSON.stringify({ type: "subscribe", match_id: matchId, prefs: prefsRef.current }));
        pingTimer = window.setInterval(() => ws.readyState === WebSocket.OPEN && ws.send(JSON.stringify({ type: "ping" })), 20_000);
      };
      ws.onmessage = (msg) => {
        let m: { type: string; payload: unknown };
        try {
          m = JSON.parse(msg.data as string);
        } catch {
          return;
        }
        switch (m.type) {
          case "hello": {
            const p = m.payload as HelloPayload;
            if (lineupFor !== p.match_id) {
              lineupFor = p.match_id;
              pitch.reset(p.home, p.away);
              // Rebuild line-ups (subs, red cards) and the ball position from history, without animation.
              fetchEvents(p.match_id)
                .then((evs) => evs.forEach((ev) => pitch.apply(ev, false)))
                .catch(() => undefined);
            }
            dispatch({ type: "hello", payload: p });
            break;
          }
          case "event":
            pitch.apply(m.payload as MatchEvent);
            dispatch({ type: "event", payload: m.payload as MatchEvent });
            break;
          case "state":
            dispatch({ type: "state", payload: m.payload as MatchState });
            break;
          case "moment":
            dispatch({ type: "moment", payload: m.payload as Moment });
            break;
          case "insight":
            dispatch({ type: "insight", payload: m.payload as Insight });
            break;
          case "cue":
            dispatch({ type: "cue", payload: m.payload as OverlayCue });
            break;
          case "recap":
            dispatch({ type: "recap", payload: m.payload as Recap });
            break;
          case "match_end":
            dispatch({ type: "match_end" });
            break;
          case "error": {
            const err = m.payload as { code?: string; message?: string };
            if (err.code === "fatal") ws.close();
            else console.warn("[fulcrum] server error", err);
            break;
          }
          default:
        }
      };
      ws.onclose = () => {
        window.clearInterval(pingTimer);
        if (!closed) scheduleRetry("Connection lost");
      };
      ws.onerror = () => undefined; // onclose handles reconnects
    };

    const scheduleRetry = (error: string) => {
      if (closed) return;
      dispatch({ type: "status", status: retry === 0 ? "reconnecting" : "offline", error });
      const delay = Math.min(8000, 800 * 2 ** retry++);
      retryTimer = window.setTimeout(connect, delay);
    };

    connect();
    return () => {
      closed = true;
      window.clearInterval(pingTimer);
      window.clearTimeout(retryTimer);
      wsRef.current?.close();
    };
  }, [pitch]);

  // Push preference changes to the server (it re-filters persona/language server-side).
  const prefsKey = JSON.stringify(prefs);
  useEffect(() => {
    const ws = wsRef.current;
    if (ws?.readyState === WebSocket.OPEN) ws.send(JSON.stringify({ type: "update_prefs", prefs }));
  }, [prefsKey]);

  return { ...state, pitch };
}

export type MatchFeed = ReturnType<typeof useMatchFeed>;
