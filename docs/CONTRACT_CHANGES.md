# Contract changes log
Append one entry per change to `src/fulcrum/contracts/models.py`, then `make schemas`.

| Date | Author/WS | Change | Breaking? |
|---|---|---|---|
| 2026-10-23 | plan | v1.0.0 initial contracts (+ perspective_team_id / perspective_player_id on InsightVariant & OverlayCue) | – |
| 2026-10-23 | plan | Add `InsightsEnvelope` (`kind` + `data`) for the `insights` topic, with `InsightsEnvelope.wrap(item)` | No (additive) |
| 2026-10-24 | WS3 | `Recap.social_posts: list[str] = []` — 2-3 fact-checked share posts written by the Match Reporter | No (additive) |
| 2026-10-24 | WS3 (Ask the studio) | Add `AskRequest` / `AskAnswer` for `POST /api/matches/{id}/ask` (Studio Analyst answers a viewer question from match data only) | No (additive) |
| 2026-10-24 | WS2/WS3 (Producer console) | `OverlayCue.origin: "agents" \| "producer" \| None = None` (producer graphics are persona-neutral). Add `ProducerState` (+ `HeldItem`, `ProducerLogEntry`), `ProducerSettings`, `ProducerEdit`, `ProducerDecision`, `GraphicRequest`, `GraphicProposal` for `/api/matches/{id}/producer/...` | No (additive) |
