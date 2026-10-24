import { useEffect, useState } from "react";
import { Header, type Route } from "./components/Header";
import { AudioSettings, LiveRegion, useAudioShortcuts } from "./components/AudioControls";
import { PrefsDrawer } from "./components/PrefsDrawer";
import { useAudioPrefs, useCommentary } from "./lib/useCommentary";
import { useMatchFeed } from "./lib/useMatchFeed";
import { usePrefs } from "./lib/usePrefs";
import { BroadcastView } from "./views/BroadcastView";
import { QualityView } from "./views/QualityView";
import { RecapView } from "./views/RecapView";
import { SplitView } from "./views/SplitView";
import { StudioView } from "./views/StudioView";

function parseRoute(hash: string): Route {
  const r = hash.replace(/^#\/?/, "").split("?")[0];
  return r === "split" || r === "broadcast" || r === "recap" || r === "quality" ? r : "studio";
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
      {route === "studio" && <StudioView feed={feed} prefs={prefs} />}
      {route === "broadcast" && <BroadcastView feed={feed} prefs={prefs} audio={audio} onAudioChange={setAudio} commentary={commentary} />}
      {route === "recap" && <RecapView feed={feed} prefs={prefs} />}
      {route === "quality" && <QualityView feed={feed} />}
      {feed.finished && route === "studio" && (
        <a href="#/recap" className="fixed bottom-6 left-1/2 z-30 -translate-x-1/2 rounded-full bg-signal px-5 py-2.5 text-sm font-semibold text-ink-950 shadow-lg">
          Full time — read the recap
        </a>
      )}
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
