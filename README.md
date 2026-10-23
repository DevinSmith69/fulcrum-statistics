# Fulcrum

**Match intelligence that explains the moments a game turns on.**

Fulcrum ingests a live stream of synthetic football events, quantifies how each moment changes the match
(live outcome probability, pressure, control, tempo), and uses a team of AI agents on Microsoft Foundry to
explain *why it matters* — personalised for an analyst, a casual fan, a club supporter or a player-follower,
in their language, rendered as broadcast-ready overlays.

> Built for the Microsoft *Inside the Game* Developer Hackathon — challenge: *Synthetic Match Insights Engine*.
> All clubs, players and data are fictional and synthetic.

| Stage | What Fulcrum does | Azure |
|---|---|---|
| Ingest | Seeded possession-chain simulator streams events + tracking | Event Hubs (Kafka) |
| Interpret | xG, pass difficulty, pressure/control/chaos, live outcome probability | Container Apps |
| Explain | Moment Analyst → Persona Editor → Fact-Checker (repair loop, fallback) | Foundry + Agent Framework |
| Render | Live overlay studio + machine-readable overlay cue feed | Static Web Apps |
| Personalise | 4 personas × 5 languages, favourite club, follow a player | Foundry |

_Quick start, architecture, evaluation results and path to production: coming in WS6._

```bash
make install && make test      # offline: memory bus + fake LLM
make dev                       # API + whole pipeline on :8000
make web                       # overlay on :5173
```
