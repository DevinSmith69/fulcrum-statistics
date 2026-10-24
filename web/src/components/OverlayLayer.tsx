import { useMemo } from "react";
import { useCueStage } from "../lib/cueStage";
import type { MatchFeed } from "../lib/useMatchFeed";
import type { OverlayAnchor, OverlayCue } from "../types";
import { TEMPLATES, type CueContext } from "./cues/CueTemplates";

const ANCHOR_POS: Record<OverlayAnchor, string> = {
  top_left: "top-0 left-0 items-start",
  top_right: "top-0 right-0 items-end",
  bottom_left: "bottom-0 left-0 items-start",
  bottom_right: "bottom-0 right-0 items-end",
  bottom_center: "bottom-0 inset-x-0 items-center",
};
// Outgoing graphics are taken out of flow and pinned to the anchor's edge while they fade.
const EXIT_POS: Record<OverlayAnchor, string> = {
  top_left: "top-0 left-0", top_right: "top-0 right-0", bottom_left: "bottom-0 left-0",
  bottom_right: "bottom-0 right-0", bottom_center: "bottom-0 left-1/2 -translate-x-1/2",
};
const ANCHOR_DY: Record<OverlayAnchor, string> = {
  top_left: "-14px", top_right: "-14px", bottom_left: "14px", bottom_right: "14px", bottom_center: "18px",
};

/**
 * Renders OverlayCues on top of the pitch, one per anchor, with priority pre-emption (API_CONTRACT §4).
 * Bottom-centre graphics push the bottom-corner ones up so they never collide.
 */
export function OverlayLayer({ feed, size = "lg" }: { feed: MatchFeed; size?: "lg" | "md" }) {
  const stage = useCueStage(feed.cues, feed.state?.clock_ms ?? 0);
  const momentsById = useMemo(() => new Map(feed.moments.map((m) => [m.moment_id, m])), [feed.moments]);
  const centreBusy = !!stage.active.bottom_center;

  const render = (cue: OverlayCue, anchor: OverlayAnchor, leaving: boolean) => {
    const Tpl = TEMPLATES[cue.template];
    if (!Tpl) return null;
    const ctx: CueContext = {
      home: feed.home, away: feed.away, state: feed.state, moment: cue.moment_id ? momentsById.get(cue.moment_id) : undefined,
      periodStarts: feed.periodStarts, size,
    };
    return (
      <div
        key={`${cue.cue_id}-${leaving ? "out" : "in"}`}
        className={leaving ? `cue-exit absolute ${EXIT_POS[anchor]}` : "cue-enter"}
        style={{ ["--cue-dy" as string]: ANCHOR_DY[anchor] }}
      >
        <Tpl cue={cue} ctx={ctx} />
      </div>
    );
  };

  const pad = size === "lg" ? "p-6" : "p-3";
  return (
    <div className="pointer-events-none absolute inset-0 overflow-hidden" aria-live="polite">
      {(Object.keys(ANCHOR_POS) as OverlayAnchor[]).map((anchor) => {
        const slot = stage.active[anchor];
        const leaving = stage.leaving.filter((l) => l.anchor === anchor);
        const lift = centreBusy && (anchor === "bottom_left" || anchor === "bottom_right") ? (size === "lg" ? "-translate-y-48" : "-translate-y-32") : "";
        return (
          <div key={anchor} className={`absolute flex flex-col transition-transform duration-500 ${pad} ${ANCHOR_POS[anchor]} ${lift}`}>
            <div className="relative">
              {leaving.map((l) => render(l.cue, anchor, true))}
              {slot && render(slot.cue, anchor, false)}
            </div>
          </div>
        );
      })}
    </div>
  );
}
