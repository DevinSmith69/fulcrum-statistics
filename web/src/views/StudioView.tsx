import { useCallback, useEffect, useRef, useState } from "react";
import { AskStudio } from "../components/AskStudio";
import { EventTicker } from "../components/EventTicker";
import { InsightFeed, momentAnchorId } from "../components/InsightFeed";
import { Gauges, MomentumBar } from "../components/Meters";
import { OverlayLayer } from "../components/OverlayLayer";
import { Pitch } from "../components/Pitch";
import { Scoreboard } from "../components/Scoreboard";
import { WinProbChart } from "../components/WinProbChart";
import type { MatchFeed } from "../lib/useMatchFeed";
import type { ViewerPrefs } from "../types";

const HIGHLIGHT_MS = 2500;

/** Match Studio: scoreboard, live pitch with overlay cues, and the analysis rail. */
export function StudioView({ feed, prefs }: { feed: MatchFeed; prefs: ViewerPrefs }) {
  const [highlight, setHighlight] = useState<string | null>(null);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  // "Ask the studio" citation chip → scroll the moment into view in the feed and ring it briefly.
  const cite = useCallback((momentId: string) => {
    document.getElementById(momentAnchorId(momentId))?.scrollIntoView({ behavior: "smooth", block: "center" });
    setHighlight(momentId);
    if (timer.current) clearTimeout(timer.current);
    timer.current = setTimeout(() => setHighlight(null), HIGHLIGHT_MS);
  }, []);
  useEffect(() => () => {
    if (timer.current) clearTimeout(timer.current);
  }, []);

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="shrink-0 border-b border-ink-700 bg-ink-900/60 px-6 py-3">
        <Scoreboard feed={feed} />
      </div>
      <main className="grid min-h-0 flex-1 grid-cols-1 gap-4 p-4 lg:grid-cols-[minmax(0,1fr)_440px]">
        <section className="panel relative flex min-h-[420px] flex-col overflow-hidden" aria-label="Pitch">
          <div className="relative min-h-0 flex-1">
            <Pitch model={feed.pitch} followedPlayerId={prefs.followed_player_id} />
            <OverlayLayer feed={feed} />
          </div>
          <div className="border-t border-ink-700 bg-ink-950/40">
            <EventTicker feed={feed} />
          </div>
        </section>
        <aside className="flex min-h-0 flex-col gap-4" aria-label="Analysis">
          <div className="panel p-4">
            <WinProbChart feed={feed} height={170} />
          </div>
          <div className="panel flex flex-col gap-4 p-4">
            <MomentumBar feed={feed} />
            <Gauges feed={feed} />
          </div>
          <AskStudio feed={feed} prefs={prefs} onCite={cite} />
          <div className="panel flex min-h-[220px] flex-1 flex-col">
            <div className="flex items-baseline justify-between px-4 pb-2 pt-3">
              <h2 className="text-sm font-medium text-ink-100">Moments, explained</h2>
              <span className="text-xs text-ink-500">{feed.moments.length} so far</span>
            </div>
            <div className="scroll-thin min-h-0 flex-1 overflow-y-auto px-4 pb-4">
              <InsightFeed feed={feed} lang={prefs.language ?? "en"} anchored highlightId={highlight} />
            </div>
          </div>
        </aside>
      </main>
    </div>
  );
}
