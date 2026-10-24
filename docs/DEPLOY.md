# DEPLOY: Fulcrum on Azure

One command, `azd up`, deploys into the existing resource group `rg-fulcrum` (Sweden Central). It does not
create a new resource group. It adopts the hand-made Event Hubs namespace and only references the Foundry
project.

```
                ┌──────────── Container Apps environment (cae-fulcrum-ds534) ────────────┐
 browser ──▶ SWA (web/)        ca-fulcrum-sim ──▶ match-events / tracking-frames ──▶ ca-fulcrum-engine
   │                              ▲  (internal)                                         │
   └──── HTTPS + WSS ──▶ ca-fulcrum-api ◀── match-state / moments / insights ◀──────────┤
                          (external)            ▲                                       ▼
                                                └──── insights ◀── ca-fulcrum-agents ◀── moments
                └────────────────────────────────────────────────────────────────────────┘
   Event Hubs evh-fulcrum-ds534 (Kafka 9093)   Key Vault (connection string)   Foundry fulcrum-ds534/fulcrum
   id-fulcrum-ds534 (user-assigned MI: AcrPull, KV Secrets User, EH Data Sender/Receiver, Foundry User)
   Log Analytics + App Insights          Container Registry (remote builds; no local Docker needed)
```

## 1. Deploy

Prerequisites: `az login`, `azd auth login`, Node 20 (the SWA deploy uses `npx`), and Git Bash on `PATH`
(the web hooks run with `sh`). Docker is **not** needed because images build in ACR (`remoteBuild: true`).

```bash
azd env new fulcrum --subscription <subscription-id> --location swedencentral
azd env set AZURE_RESOURCE_GROUP rg-fulcrum      # resource-group-scoped deployment into the existing RG
azd provision --preview                          # free what-if; review before spending anything
azd up                                           # provision + build 4 images in ACR + deploy api/sim/engine/agents/web
```

`azd up` prints `API_URL` and `WEB_URL`. Check them:

```bash
curl "$(azd env get-value API_URL)/api/health"   # {"status":"ok","bus":"eventhubs","llm":"foundry","in_process":false,...}
                                                  # (in the demo's in-process mode, §4: "bus":"memory","in_process":true)
open "$(azd env get-value WEB_URL)"              # the overlay; the sim auto-starts NBA v RVR at 10x on boot
```

Redeploy code only: `azd deploy` (or `azd deploy api`). Re-provision after a Bicep change: `azd provision`.
The `SERVICE_*_RESOURCE_EXISTS` flags that azd sets keep the deployed images (`infra/modules/fetch-image.bicep`).

The `.azure/` folder (azd env state) is gitignored and local to the checkout you run `azd` from. If you
prepared the env in another worktree, run the `azd env new` and `azd env set` lines again in the checkout you
deploy from.

## 2. Parameters (`azd env set NAME value`, then `azd provision`)

| azd env var | Bicep param | Default | Meaning |
|---|---|---|---|
| `AZURE_RESOURCE_GROUP` | (scope) | – | must be `rg-fulcrum` |
| `AZURE_LOCATION` | `location` | `swedencentral` | everything except the SWA |
| `FULCRUM_SUFFIX` | `suffix` | `ds534` | naming suffix (`kv-fulcrum-ds534`, `crfulcrumds534`, …) |
| `EVENTHUBS_NAMESPACE` | `eventHubsNamespaceName` | `evh-fulcrum-ds534` | namespace to adopt (created if missing) |
| `FOUNDRY_ACCOUNT_NAME` / `FOUNDRY_PROJECT_NAME` | `foundryAccountName` / `foundryProjectName` | `fulcrum-ds534` / `fulcrum` | existing Foundry project (never modified) |
| `FOUNDRY_MODEL_DEPLOYMENT` / `_RECAP` | `foundryModelDeployment` / `foundryRecapDeployment` | `fast` / `strong` | model deployment names |
| `FULCRUM_LLM` | `llm` | `foundry` | `fake` = template copy, no Foundry calls (use this if `llm/foundry.py` is not merged yet) |
| `FULCRUM_INPROCESS` | `inProcess` | `false` | **cut-list fallback**, see §4 |
| `FULCRUM_SIM_AUTOSTART` | `simAutostart` | `true` | sim app starts one match on boot |
| `FULCRUM_SIM_SPEED` | `simSpeed` | `10` | sim-time multiplier for the boot match |
| `FULCRUM_EXTRA_ORIGINS` | `extraAllowedOrigins` | empty | extra CORS origins, e.g. `http://localhost:5173` |
| `FULCRUM_ENABLE_SPEECH` | `enableSpeech` | `false` | create an Azure AI Speech resource for neural voice commentary, see §5a |
| `FULCRUM_SPEECH_SKU` | `speechSku` | `F0` | `F0` (free, one per subscription) or `S0` |
| – | `staticWebAppLocation` | `eastus2` | SWA is not offered in Sweden Central, and West Europe refused new SWAs in what-if |

Environment variables the containers get (all four): `FULCRUM_SERVICE`, `FULCRUM_BUS=eventhubs`,
`EVENTHUBS_NAMESPACE`, `EVENTHUBS_CONNECTION_STRING` (Key Vault reference), `FULCRUM_LLM`,
`FOUNDRY_PROJECT_ENDPOINT`, `FOUNDRY_MODEL_DEPLOYMENT=fast`, `FOUNDRY_MODEL_DEPLOYMENT_RECAP=strong`,
`APPLICATIONINSIGHTS_CONNECTION_STRING`, `AZURE_CLIENT_ID` (the MI, for `DefaultAzureCredential`) and
`FULCRUM_TEAMS_PATH`. The api also gets `FULCRUM_ALLOWED_ORIGINS` (the SWA origin) and `FULCRUM_SIM_URL=http://ca-fulcrum-sim`
(plus `AZURE_SPEECH_ENDPOINT` / `_RESOURCE_ID` / `_REGION` when `enableSpeech` is on, §5a).
The web build gets `VITE_API_URL=$API_URL` through the `prepackage` hook in `azure.yaml`.

## 3. What maps to what

| Resource | Name | Created / adopted / referenced |
|---|---|---|
| Event Hubs Standard (Kafka) + 5 hubs (2 partitions, 7-day retention) + all consumer groups | `evh-fulcrum-ds534` | **adopted**. What-if shows hubs and consumer groups as *NoChange*. The namespace gets tags. `geoDataReplication` shows as "Delete", which is what-if noise for a read-only status property on Standard |
| SAS rule `fulcrum-apps` (Send, Listen; no Manage) | on the namespace | created; its connection string → Key Vault |
| Foundry account + project | `fulcrum-ds534` / `fulcrum` | **referenced** (what-if: *Ignore*); MI gets **Foundry User** on the project and on the account |
| Log Analytics (1 GB/day cap) + App Insights | `log-` / `appi-fulcrum-ds534` | created |
| Container Registry Basic | `crfulcrumds534` | created; MI has AcrPull |
| User-assigned identity | `id-fulcrum-ds534` | created |
| Key Vault (RBAC) | `kv-fulcrum-ds534` | created; secret `eventhubs-connection-string`; MI has Secrets User |
| Container Apps env + 4 apps | `cae-fulcrum-ds534`, `ca-fulcrum-{api,sim,engine,agents}` | created. api: external ingress :8000 (WebSockets); sim: internal ingress :8001; engine/agents: no ingress. 1 replica each |
| Static Web App (Free) | `swa-fulcrum-ds534` | created in `eastus2` |

Consumer groups (API_CONTRACT §1): match-events → `engine`, `agents`, `api-feed`; tracking-frames →
`engine`; match-state → `api`; moments → `agents`, `api`; insights → `api`. The Kafka client uses the same
names as its `group_id`.

**Bus auth.** The tested path is SASL PLAIN with the Key Vault connection string. The MI already has Event
Hubs Data Sender and Data Receiver, so switching to Entra ID (OAUTHBEARER) only means removing
`EVENTHUBS_CONNECTION_STRING` from the apps (or setting `FULCRUM_EVENTHUBS_AUTH=managed_identity`). The code
path exists in `bus/eventhubs.py` but has not been run live: the local user has no Event Hubs data role.

**Why one replica.** The API keeps read models and WebSocket fan-out in memory, the engine keeps per-match
state, and a running match lives inside the sim process. Never scale these to zero mid-match.

## 4. Cut-list fallback: one container (`FULCRUM_INPROCESS`)

```bash
azd env set FULCRUM_INPROCESS true && azd provision
```

The api app then runs with `FULCRUM_BUS=memory FULCRUM_INPROCESS=1`, which puts sim, engine and agents inside
the API process, the same as `make dev`. **This is how the hackathon demo is deployed (24 Oct):** the on-demand agent
features (translation, Player lens, per-persona recaps, Ask the studio) call the `AgentService` in the API's
process, so in the split topology they would fall back to English / 503. In this mode the api app gets 1 vCPU /
2 GiB (it carries the other three apps' work). sim, engine and agents scale to 0 replicas. Event Hubs stays
provisioned, and the architecture diagram can call it the production topology. Undo with
`azd env set FULCRUM_INPROCESS false && azd provision`.

Running the image locally (any Docker host):

```bash
docker build -t fulcrum .
docker run --rm -p 8000:8000 -e FULCRUM_SERVICE=api fulcrum                    # in-process on the memory bus
docker run --rm --env-file .env -e FULCRUM_BUS=eventhubs -e FULCRUM_SERVICE=engine fulcrum
```

Without Docker, the same processes run from the venv: `FULCRUM_SERVICE=engine uv run --env-file .env python -m fulcrum`.

## 5. Cost (rough, PAYG, per day while running)

| Item | ≈ per day |
|---|---|
| Event Hubs Standard, 1 TU (already running) | £0.55 |
| Container Apps: 4 always-on replicas (1.75 vCPU, 3.5 GiB in total); idle replicas bill at the lower idle rate; the monthly free grant covers about the first day | £1.50–4 |
| Container Registry Basic | £0.13 |
| Log Analytics (capped at 1 GB/day) | ≤ £2.20, typically pennies |
| Key Vault, managed identity, SWA Free | ~£0 |
| Foundry tokens | < £1 per full match (`simAutostart` runs **one** match per sim restart; looping is off) |

Weekend total, including the deploy and a few test matches: **about £10–15**. Judging may run for days, so after
recording the video consider the single-container fallback (§4), which cuts Container Apps cost by about 3/4.

## 5a. Voice commentary (optional Azure AI Speech)

The overlay's **Voice** and **Audio description** toggles work without Azure: `POST /api/speech` answers 503 and
the browser's `speechSynthesis` speaks. To use Azure AI Speech neural voices instead (en-GB Ryan, es-ES Alvaro,
fr-FR Henri, pt-BR Antonio, de-DE Conrad):

**Keyless, via Bicep (recommended).** Done for the hackathon on 24 Oct: `speech-fulcrum-ds534` (F0, custom domain, key auth off) was created by hand with exactly the Bicep's name and settings, so `azd provision` adopts it and adds the identity's role; the `fulcrum` azd env already has `FULCRUM_ENABLE_SPEECH=true`.

```bash
azd env set FULCRUM_ENABLE_SPEECH true      # optional: azd env set FULCRUM_SPEECH_SKU S0
azd provision && azd deploy api
```

This creates `speech-fulcrum-<suffix>` (kind `SpeechServices`, F0, custom domain
`https://speech-fulcrum-<suffix>.cognitiveservices.azure.com`, local/key auth **disabled**), grants the apps'
user-assigned identity **Cognitive Services Speech User** on it, and gives the api container
`AZURE_SPEECH_ENDPOINT`, `AZURE_SPEECH_RESOURCE_ID` and `AZURE_SPEECH_REGION`. The api gets an Entra token for
`https://cognitiveservices.azure.com/.default` from `DefaultAzureCredential` (`AZURE_CLIENT_ID` = the identity) and
calls `<custom domain>/tts/cognitiveservices/v1` with `Authorization: Bearer aad#<resource id>#<token>`.
Check with `curl $API_URL/api/speech` → `"enabled": true, "auth": "entra"`. A 502 from `POST /api/speech` right
after provisioning is usually the role assignment still propagating (wait a few minutes).

Notes: F0 allows one free Speech resource per subscription (use `S0` if one exists already; neural TTS is billed
per character, and a 90-minute match produces only a few thousand characters). Audio is cached in memory per
(voice, text).

**Key, for local development.** In `.env` (never commit it): `AZURE_SPEECH_REGION=<region>` and
`AZURE_SPEECH_KEY=<key>`. Keyless locally works too: `AZURE_SPEECH_ENDPOINT` + `AZURE_SPEECH_RESOURCE_ID` with
`az login` as a user who has Cognitive Services Speech User on the resource. Override a voice with
`AZURE_SPEECH_VOICE_<LANG>` (for example `AZURE_SPEECH_VOICE_EN=en-GB-SoniaNeural`).

## 6. Teardown

> **Do not run `azd down` while you still need Foundry.** This deployment is resource-group scoped, and
> `azd down` deletes the whole resource group. That includes the Foundry account and project, which this
> template only references.

After judging, delete everything with `az group delete -n rg-fulcrum --yes`. Then purge the soft-deleted
Key Vault (`az keyvault purge -n kv-fulcrum-ds534`) and the Foundry account
(`az cognitiveservices account purge -g rg-fulcrum -n fulcrum-ds534 -l swedencentral`).
To remove only what azd created and keep Foundry and Event Hubs:

```bash
for r in ca-fulcrum-api ca-fulcrum-sim ca-fulcrum-engine ca-fulcrum-agents; do az containerapp delete -g rg-fulcrum -n $r --yes; done
az containerapp env delete -g rg-fulcrum -n cae-fulcrum-ds534 --yes
az staticwebapp delete -g rg-fulcrum -n swa-fulcrum-ds534 --yes
az acr delete -g rg-fulcrum -n crfulcrumds534 --yes
az keyvault delete -g rg-fulcrum -n kv-fulcrum-ds534 && az keyvault purge -n kv-fulcrum-ds534
az monitor app-insights component delete -g rg-fulcrum -a appi-fulcrum-ds534
az monitor log-analytics workspace delete -g rg-fulcrum -n log-fulcrum-ds534 --yes --force
az identity delete -g rg-fulcrum -n id-fulcrum-ds534
```

## 7. Troubleshooting

- **First `azd provision` fails with a Key Vault secret or registry pull error.** The role assignments take a
  minute or two to propagate. Run `azd provision` again.
- **The api revision is unhealthy right after the first provision.** Expected. The placeholder image listens on
  :80, not :8000, and `azd deploy` replaces it.
- **The web shows "Retrying…".** `VITE_API_URL` was empty at build time. Run `azd env get-value API_URL`, then `azd deploy web`.
- **CORS errors.** `FULCRUM_ALLOWED_ORIGINS` must equal the SWA origin exactly (it is set from Bicep). Add
  local dev origins with `FULCRUM_EXTRA_ORIGINS`.
- **Bus.** `az containerapp logs show -g rg-fulcrum -n ca-fulcrum-engine --follow` should show
  `Authenticated as $ConnectionString via PLAIN` and `eventhubs consumer match-events/engine started`.
- **Old "live" matches after an API restart.** Kafka consumer groups resume from their committed offsets.
  The API therefore picks up where it stopped and can show a half-seen match as live. Start a fresh match.
- **Check the bus from a laptop:** `FULCRUM_LIVE_EVENTHUBS=1 uv run pytest -q -s tests/bus/test_eventhubs_live.py`.
