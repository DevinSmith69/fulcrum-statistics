# Cross-workstream requests
Format: `- [ ] (WSx → WSy) what you need, why, which contract/fixture you coded against`

- [ ] (WS4 → WS2) Add `period_start_clock_ms: {"1": 0, "2": <clock at H2 kick-off>}` to the WS `hello` payload.
  The scoreboard clock and minute labels need it to show 46:00 (not 47:00) after first-half stoppage. The web app
  falls back to `period_start` events and then to 45:00, so this is cosmetic, not blocking. The mock sends it.
  Coded against: API_CONTRACT §3 `hello` + `web/mock/server.mjs`.
- [ ] (WS4 → WS2) On `update_prefs`, please re-send a `hello` snapshot (with insights re-filtered for the new
  persona/language). The web app treats `hello` as "replace the feed", so the moment list switches language and
  persona immediately; without it only *new* insights change. The mock does this.
- [ ] (WS4 → WS2) Confirm units for `TeamLiveStats.pass_accuracy`, `possession_pct` and `PlayerLiveStats.pass_accuracy`
  (0–1 or 0–100?). The web app accepts either (`as100()` in `web/src/lib/format.ts`); the mock sends 0–100.
- [ ] (WS4 → WS2/WS3) `OverlayCue.show_at_clock_ms`: the web app shows a cue once the latest match clock is ≥ this
  value, and shows late cues immediately. At 10× sim speed agent latency is ~30 s of match clock, so the mock sets
  `show_at = moment clock + latency × speed`, rounded up to the next second. Please do the same (or tell WS4 if you
  intend wall-clock latency only).
- [ ] (WS4 → WS3) `OverlayCue.duration_ms` is applied in **wall-clock** time by the renderer (a 6 s card stays 6 s
  on screen even at 10× speed). Keep durations in the 4–15 s range per API_CONTRACT §4.
- [ ] (WS4 → WS3) Cue mapping beyond the API_CONTRACT §4 list, as implemented in the mock (`web/mock/text.mjs`
  `buildCues`) — please match or tell WS4: `big_chance` → `stat_card` p3 bottom_right; `red_card` → `lower_third`
  p4 + `wp_swing` p4 (for the opponent); `tempo_change` → `pressure_bar`; `half_time` → `stat_card` p3.
  Anchors used by the web overlay: `lower_third`/`recap_card` bottom_center, `wp_swing` top_right,
  `pressure_bar` top_left, `stat_card` bottom_right, `player_tag` bottom_left.
- [ ] (WS4 → WS2) `wp_swing` copy wants to say "inside 90 seconds"; please include the window in the fact pack
  (the mock uses `window_s = 90`, unit `s`) so the fact-checker allows the number.
- [ ] (WS4 → WS5) Static Web App build: `cd web && npm ci && npm run build`, output `web/dist`, env
  `VITE_API_URL=https://<api-container-app>` at build time. WebSocket path is `/ws` on the same host.
- [ ] (WS4 → WS6) Recording tips: `npm run mock -- --from 82` lands just before the winning goal; `#/split` with
  Analyst·EN v Casual·ES is the money shot; `#/broadcast` then `I` shows the cue inspector.

## WS4 decisions (made overnight, unattended)
- Hash routing (`#/`, `#/split`, `#/broadcast`, `#/recap`), no router dependency. Static Web Apps needs no rewrite rules.
- Studio, broadcast and recap share one subscription; split view opens two of its own (the brief's "two independent
  WS subscriptions").
- Player positions are inferred client-side from events (4-3-3 anchors around the ball; out-of-possession side drops
  into a block). Tracking frames are not on the WS protocol; if WS2 adds them later the pitch can use them directly.
- Prefs persist per view in `localStorage` (`fulcrum.prefs.*`). Default studio viewer: Casual · EN following NBA-10.
- Team colours are used as data only (kit stripes, dots, chart series via OKLCH re-derivation). The single UI accent
  is signal cyan `#3DE0FF`. Overlays have no backdrop blur: it forced a GPU blur over the animating canvas every frame.
