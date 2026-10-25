import { useCallback, useEffect, useRef, useState } from "react";
import { fetchControl, postControl, startDemoMatch } from "./api";
import { DEFAULT_DEMO_SPEED, matchUrl, startError, type ControlAction, type ControlState } from "./director";

const POLL_MS = 2000;
const POLL_SKIPPING_MS = 500;
const NOTICE_MS = 8000;

export interface Director {
  control: ControlState | null;
  /** Why the controls are disabled (e.g. the match is simulated out of process), or null. */
  unavailable: string | null;
  /** A short-lived message (e.g. why a new match couldn't start), shown ahead of the control status. */
  notice: string | null;
  /** The speed shown / used for a new demo match. */
  speed: number;
  busy: boolean;
  act: (body: ControlAction) => void;
  setSpeed: (speed: number) => void;
  togglePause: () => void;
  nextMoment: () => void;
  newDemoMatch: () => void;
}

/** Live match controls for `matchId` (polled only while `enabled`). */
export function useDirector(matchId: string | null, enabled: boolean): Director {
  const [control, setControl] = useState<ControlState | null>(null);
  const [unavailable, setUnavailable] = useState<string | null>(null);
  const [localSpeed, setLocalSpeed] = useState(DEFAULT_DEMO_SPEED);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const skipping = control?.skipping ?? false;
  const controlRef = useRef(control);
  controlRef.current = control;

  const onError = useCallback((e: unknown) => {
    const msg = e instanceof Error ? e.message : String(e);
    if (msg.startsWith("501")) setUnavailable("Match controls need the in-process API");
    else if (msg.startsWith("409")) setUnavailable("Full time");
    else if (!msg.startsWith("404")) setUnavailable("Match controls unavailable");
  }, []);

  const accept = useCallback((c: ControlState) => {
    setControl(c);
    setUnavailable(c.finished ? "Full time" : null);
    setLocalSpeed(c.speed);
  }, []);

  useEffect(() => {
    if (!enabled || !matchId) return;
    let stopped = false;
    let timer: number | undefined;
    const poll = () =>
      fetchControl(matchId)
        .then((c) => !stopped && accept(c))
        .catch((e) => !stopped && onError(e))
        .finally(() => {
          if (!stopped) timer = window.setTimeout(poll, controlRef.current?.skipping ? POLL_SKIPPING_MS : POLL_MS);
        });
    poll();
    return () => {
      stopped = true;
      window.clearTimeout(timer);
    };
  }, [enabled, matchId, skipping, accept, onError]);

  const act = useCallback(
    (body: ControlAction) => {
      if (!matchId) return;
      setBusy(true);
      postControl(matchId, body)
        .then(accept)
        .catch(onError)
        .finally(() => setBusy(false));
    },
    [matchId, accept, onError],
  );

  const setSpeed = useCallback(
    (speed: number) => {
      setLocalSpeed(speed);
      if (controlRef.current && !controlRef.current.finished) act({ action: "set_speed", speed });
    },
    [act],
  );
  const togglePause = useCallback(() => act({ action: controlRef.current?.paused ? "resume" : "pause" }), [act]);
  const nextMoment = useCallback(() => act({ action: "next_moment" }), [act]);

  const speed = control?.speed ?? localSpeed;
  const newDemoMatch = useCallback(() => {
    setBusy(true);
    startDemoMatch(speed)
      .then(({ match_id }) => window.location.assign(matchUrl(window.location.href, match_id)))
      .catch((e) => {
        setBusy(false);
        setNotice(startError(e));
      });
  }, [speed]);

  useEffect(() => {
    if (!notice) return;
    const t = window.setTimeout(() => setNotice(null), NOTICE_MS);
    return () => window.clearTimeout(t);
  }, [notice]);

  return { control, unavailable, notice, speed, busy, act, setSpeed, togglePause, nextMoment, newDemoMatch };
}
