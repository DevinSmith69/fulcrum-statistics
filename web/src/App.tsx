import { useEffect, useState } from "react";
import { Header, type Route } from "./components/Header";
import { AudioSettings, isTyping, LiveRegion, useAudioShortcuts } from "./components/AudioControls";
import { DemoPanel } from "./components/DemoPanel";
import { HighlightsReel } from "./components/HighlightsReel";
import { ReplayStageProvider } from "./components/PitchReplay";
import { PrefsDrawer } from "./components/PrefsDrawer";
import { useAudioPrefs, useCommentary } from "./lib/useCommentary";
import { useMatchFeed } from "./lib/useMatchFeed";
import { usePrefs } from "./lib/usePrefs";
import { BroadcastView } from "./views/BroadcastView";
import { ProducerView } from "./views/ProducerView";
import { QualityView } from "./views/QualityView";
import { RecapView } from "./views/RecapView";
import { SplitView } from "./views/SplitView";
import { StudioView } from "./views/StudioView";

/** Match Studio shows the pitch beside the insight rail from `lg` up; below that, cards keep their mini replay. */
const STUDIO_ROOMY = "(min-width: 1024px)";

function parseRoute(hash: string): Route {
  const r = hash.replace(/^#\/?/, "").split("?")[0];
  return r === "split" || r === "broadcast" || r === "recap" || r === "quality" || r === "producer" ? r : "studio";
}

function useRoute() {
  const [route, setRoute] = useState<Route>(() => parseRoute(window.location.hash));
  useEffect(() => {
    const on = () => setRoute(parseRoute(window.location.hash));
    window.addEventListener("hashchange", on);
    return () => window.removeEventListener("hashchange", on);
  }, []);
  return route;
}

/** Studio, broadcast and recap share one subscription (and one set of prefs) so switching views is instant. */
function MainShell({ route }: { route: Exclude<Route, "split"> }) {
  const [prefs, setPrefs] = usePrefs("studio", { persona: "casual", language: "en", followed_player_id: "NBA-10" });
  const feed = useMatchFeed(prefs);
  const [drawer, setDrawer] = useState(false);
  // Voice commentary + audio description run across studio, broadcast and recap (one feed, one speech queue).
  const [audio, setAudio] = useAudioPrefs();
  const commentary = useCommentary(feed, prefs.language ?? "en", audio);
  useAudioShortcuts(audio, setAudio, commentary.stop);

  // "Your highlights": one-click reel at full time (H, or the buttons on the full-time banner / recap / broadcast).
  const [reel, setReel] = useState(false);
  const canReel = feed.finished && feed.moments.length > 0;
  const playHighlights = canReel ? () => setReel(true) : undefined;
  useEffect(() => {
    if (!canReel || reel) return;
    const onKey = (e: KeyboardEvent) => {
      if (isTyping(e.target) || e.metaKey || e.ctrlKey || e.altKey || e.repeat) return;
      if (e.key === "h" || e.key === "H") setReel(true);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [canReel, reel]);
  useEffect(() => setReel(false), [feed.matchId]);

  // When the match ends, offer the recap without forcing a navigation mid-celebration.
  return (
    <div className="flex h-full flex-col">
      {route !== "broadcast" && <Header route={route} status={feed.status} error={feed.error} prefs={prefs} onOpenPrefs={() => setDrawer(true)} />}
      {feed.status !== "live" && feed.error && route !== "broadcast" && (
        <div className="border-b border-ink-700 bg-ink-900 px-6 py-2 text-sm text-ink-300" role="status">
          {feed.error.startsWith("Waiting for") ? (
            <>{feed.error}…</>
          ) : (
            <>
              {feed.error}. Start the mock with <code className="text-ink-50">npm run mock</code> or set{" "}
              <code className="text-ink-50">VITE_API_URL</code>. Retrying…
            </>
          )}
        </div>
      )}
      {route === "studio" && (
        <ReplayStageProvider feed={feed} lang={prefs.language ?? "en"} roomyQuery={STUDIO_ROOMY}>
          <StudioView feed={feed} prefs={prefs} />
        </ReplayStageProvider>
      )}
      {route === "broadcast" && (
        <ReplayStageProvider feed={feed} lang={prefs.language ?? "en"}>
          <BroadcastView feed={feed} prefs={prefs} audio={audio} onAudioChange={setAudio} commentary={commentary} onPlayHighlights={playHighlights} />
        </ReplayStageProvider>
      )}
      {(route === "studio" || route === "broadcast") && (
        <DemoPanel feed={feed} lang={prefs.language ?? "en"} placement={route === "broadcast" ? "top-center" : "bottom-left"} />
      )}
      {route === "recap" && <RecapView feed={feed} prefs={prefs} onPlayHighlights={playHighlights} />}
      {route === "quality" && <QualityView feed={feed} />}
      {route === "producer" && <ProducerView feed={feed} />}
      {feed.finished && route === "studio" && (
        <div className="fixed bottom-6 left-1/2 z-30 flex -translate-x-1/2 items-center overflow-hidden rounded-full shadow-lg ring-1 ring-signal/60">
          {playHighlights && (
            <button type="button" onClick={playHighlights} className="flex items-center gap-2 bg-signal px-5 py-2.5 text-sm font-semibold text-ink-950 hover:bg-signal/90">
              <span aria-hidden>▶</span> Play your highlights <kbd className="text-ink-800">H</kbd>
            </button>
          )}
          <a
            href="#/recap"
            className={`px-5 py-2.5 text-sm font-semibold ${playHighlights ? "bg-ink-950/95 text-ink-50 hover:text-signal" : "bg-signal text-ink-950"}`}
          >
            Full time — read the recap
          </a>
        </div>
      )}
      {reel && <HighlightsReel feed={feed} prefs={prefs} commentary={commentary} onClose={() => setReel(false)} />}
      {route !== "recap" && <LiveRegion commentary={commentary} />}
      <PrefsDrawer
        open={drawer}
        onClose={() => setDrawer(false)}
        prefs={prefs}
        onChange={setPrefs}
        home={feed.home}
        away={feed.away}
        audioSettings={<AudioSettings audio={audio} onChange={setAudio} commentary={commentary} />}
      />
    </div>
  );
}

export default function App() {
  const route = useRoute();
  if (route === "split") {
    return (
      <div className="flex h-full flex-col">
        <Header route="split" />
        <SplitView />
      </div>
    );
  }
  return <MainShell route={route} />;
}
