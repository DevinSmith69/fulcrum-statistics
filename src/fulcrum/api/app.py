"""Fulcrum API gateway (API_CONTRACT §2–3): REST under `/api`, WebSocket at `/ws`.

    uvicorn fulcrum.api.app:app --port 8000          (module-level `app`)
    create_app(bus=MemoryBus(), run_sim=False)       (tests)

The hub always consumes the bus. What else runs in this process depends on the topology (`fulcrum.topology`):
inprocess (the default with FULCRUM_BUS=memory) runs sim + engine + agents here (see `pipeline.py`); hybrid runs
engine + agents here and forwards `POST /api/matches` to the sim service; split only forwards.
"""

from __future__ import annotations

import asyncio
import logging
import os
from contextlib import asynccontextmanager
from typing import Any, Literal

import httpx
from dotenv import load_dotenv
from fastapi import APIRouter, FastAPI, HTTPException, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field, ValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException

from fulcrum.bus import Bus, get_bus
from fulcrum.contracts import (
    AskAnswer,
    AskRequest,
    ClientMessage,
    Insight,
    InsightVariant,
    MatchConfig,
    MatchEvent,
    MatchState,
    Moment,
    MomentType,
    OverlayCue,
    Persona,
    Recap,
    Team,
    WinProbPoint,
)
from fulcrum.engine.service import EngineService
from fulcrum.engine.teams import load_teams
from fulcrum.sim.control import DEFAULT_SKIP_SIGNIFICANCE, MAX_SPEED, MIN_SPEED, MatchControl
from fulcrum.telemetry import configure_telemetry
from fulcrum.topology import Topology, parse_topology, resolve_topology

from . import personalise as ps
from .evalreports import latest_reports
from .hub import Connection, Hub
from .pipeline import Pipeline
from .readmodel import MatchReadModel, Registry
from .speech import MAX_TEXT_CHARS, SpeechError, SpeechSynth, voice_for
from .studio import DEFAULT_SPEECH_RATE, RateLimiter, client_key
from .translation import Translator

log = logging.getLogger("fulcrum.api")
DEFAULT_ORIGINS = "http://localhost:5173,http://127.0.0.1:5173"


class TranslateRequest(BaseModel):
    insight_id: str
    language: str


class SpeechRequest(BaseModel):
    text: str = Field(min_length=1, max_length=MAX_TEXT_CHARS)
    language: str = "en"


class ControlRequest(BaseModel):
    """`POST /api/matches/{id}/control` (demo director, API_CONTRACT §2 "Match controls")."""

    action: Literal["set_speed", "pause", "resume", "next_moment", "cancel_skip"]
    speed: float | None = Field(default=None, ge=MIN_SPEED, le=MAX_SPEED, description="set_speed: sim-time multiplier")
    min_significance: float = Field(
        default=DEFAULT_SKIP_SIGNIFICANCE, ge=0, le=1, description="next_moment: stop on a moment at least this big"
    )
    types: list[MomentType] | None = Field(
        default=None, description="next_moment: stop on these moment types instead of by significance"
    )


def _env_flag(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    return default if raw is None else raw.strip().lower() in ("1", "true", "yes", "on")


def problem(status: int, title: str, detail: Any = None, **extra: Any) -> JSONResponse:
    """RFC 7807 problem response."""
    body = {"type": "about:blank", "title": title, "status": status, "detail": detail, **extra}
    return JSONResponse(body, status_code=status, media_type="application/problem+json")


async def start_on_sim(app: FastAPI, sim_url: str, config: MatchConfig) -> None:
    """Ask the sim service (`fulcrum.sim.service`, `POST /matches`) to run `config`; 502 if it can't."""
    client: httpx.AsyncClient | None = getattr(app.state, "sim_client", None)
    try:
        if client is not None:
            r = await client.post(f"{sim_url.rstrip('/')}/matches", json=config.model_dump(mode="json"))
        else:
            async with httpx.AsyncClient(timeout=10) as c:
                r = await c.post(f"{sim_url.rstrip('/')}/matches", json=config.model_dump(mode="json"))
    except httpx.HTTPError as exc:
        raise HTTPException(502, f"sim service unreachable: {exc}") from exc
    if r.status_code >= 400:
        raise HTTPException(502, f"sim refused the match ({r.status_code}): {r.text[:200]}")


def create_app(
    bus: Bus | None = None,
    *,
    teams: dict[str, Team] | None = None,
    in_process: bool | None = None,
    topology: Topology | str | None = None,
    run_sim: bool | None = None,
    run_agents: bool | None = None,
    speech: SpeechSynth | None = None,
) -> FastAPI:
    """Build the app.

    `topology` defaults to `resolve_topology()` (FULCRUM_TOPOLOGY, else FULCRUM_INPROCESS, else FULCRUM_BUS);
    `in_process=True|False` is the older spelling of inprocess|split. `run_sim` / `run_agents` default to
    FULCRUM_RUN_SIM / FULCRUM_RUN_AGENTS (default on); hybrid never runs the sim here. Tests switch them off and
    publish onto the bus themselves. `speech` defaults to `SpeechSynth.from_env()` (None = Azure Speech not
    configured, `POST /api/speech` answers 503).
    """
    if topology is not None:
        topo: Topology | None = parse_topology(topology)
    elif in_process is not None:
        topo = Topology.INPROCESS if in_process else Topology.SPLIT
    else:
        topo = None  # resolved from the environment at start-up

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        configure_telemetry("api")  # no-op unless APPLICATIONINSIGHTS_CONNECTION_STRING is set
        the_bus = bus or get_bus()
        registry = Registry(teams if teams is not None else load_teams())
        translator = Translator()
        hub = Hub(the_bus, registry, translator)
        hub.start()
        the_topology = topo or resolve_topology()
        _warn_misconfigured(the_topology, the_bus)
        pipeline = None
        if the_topology.api_hosts_pipeline:
            sim = the_topology.api_runs_sim and (run_sim if run_sim is not None else _env_flag("FULCRUM_RUN_SIM", True))
            agents = run_agents if run_agents is not None else _env_flag("FULCRUM_RUN_AGENTS", True)
            engine = EngineService(the_bus, teams=registry.teams)
            pipeline = Pipeline(the_bus, registry, engine, translator, run_sim=sim, run_agents=agents)
            hub.moment_listeners.append(pipeline.on_moment)
        app.state.topology = the_topology
        app.state.bus, app.state.registry, app.state.hub = the_bus, registry, hub
        app.state.translator, app.state.pipeline = translator, pipeline
        app.state.speech = speech if speech is not None else SpeechSynth.from_env()
        app.state.ask_limiter = RateLimiter.from_env()
        app.state.ask_global = RateLimiter(60, 60)  # all clients together: a ceiling on LLM spend
        app.state.speech_limiter = RateLimiter.from_env("FULCRUM_SPEECH_RATE", DEFAULT_SPEECH_RATE)
        app.state.speech_global = RateLimiter(600, 60)
        if pipeline is not None:
            if not the_topology.api_runs_sim:
                # hybrid: events come from another process (and from sim autostarts nobody POSTed), so the engine
                # joins its consumer groups now rather than racing the first match's kick-off.
                pipeline.engine.start()
            pipeline.prestart()  # agents warm up now, not on the first match
        try:
            yield
        finally:
            if pipeline is not None:
                await pipeline.stop()
            await hub.stop()
            if app.state.speech is not None:
                await app.state.speech.close()

    app = FastAPI(title="Fulcrum API", version="0.1.0", lifespan=lifespan)
    origins = [o.strip() for o in os.getenv("FULCRUM_ALLOWED_ORIGINS", DEFAULT_ORIGINS).split(",") if o.strip()]
    app.add_middleware(CORSMiddleware, allow_origins=origins, allow_methods=["*"], allow_headers=["*"])

    @app.exception_handler(StarletteHTTPException)
    async def _http_problem(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        titles = {
            400: "Bad Request",
            404: "Not Found",
            409: "Conflict",
            422: "Unprocessable Entity",
            429: "Too Many Requests",
            501: "Not Implemented",
            502: "Bad Gateway",
            503: "Service Unavailable",
        }
        resp = problem(exc.status_code, titles.get(exc.status_code, "Error"), exc.detail)
        resp.headers.update(getattr(exc, "headers", None) or {})
        return resp

    @app.exception_handler(RequestValidationError)
    async def _validation_problem(_: Request, exc: RequestValidationError) -> JSONResponse:
        return problem(422, "Unprocessable Entity", "request validation failed", errors=_jsonable(exc.errors()))

    app.include_router(_router())

    @app.websocket("/ws")
    async def ws_endpoint(ws: WebSocket) -> None:
        await _serve_ws(ws, ws.app.state.hub)

    return app


def _warn_misconfigured(topology: Topology, bus: Bus) -> None:
    """Log (don't fail) topologies that cannot work as configured, so a misconfigured container still serves."""
    from fulcrum.bus.base import MemoryBus

    if topology.needs_shared_bus and isinstance(bus, MemoryBus) and os.getenv("FULCRUM_SIM_URL"):
        log.warning("topology %s with the memory bus: the sim service's events will not reach this process", topology)
    if not topology.api_runs_sim and not os.getenv("FULCRUM_SIM_URL"):
        log.warning("topology %s without FULCRUM_SIM_URL: POST /api/matches cannot start a simulated match", topology)
    log.info("api topology: %s", topology)


def _jsonable(errors: Any) -> Any:
    return [
        {k: (v if isinstance(v, (str, int, float, list, type(None))) else str(v)) for k, v in e.items()} for e in errors
    ]


# --------------------------------------------------------------------------- REST


def _router() -> APIRouter:
    r = APIRouter(prefix="/api")

    def registry(request: Request) -> Registry:
        return request.app.state.registry

    def match(request: Request, match_id: str) -> MatchReadModel:
        rm = registry(request).get(match_id)
        if rm is None:
            raise HTTPException(404, f"unknown match '{match_id}'")
        return rm

    @r.get("/health")
    async def health(request: Request) -> dict[str, Any]:
        pipeline: Pipeline | None = request.app.state.pipeline
        topology: Topology = request.app.state.topology
        if topology.api_runs_sim:
            sim = "simulator" if pipeline is not None and pipeline.sim_available else "replay"
        else:
            sim = "service" if os.getenv("FULCRUM_SIM_URL") else None
        return {
            "status": "ok",
            "bus": os.getenv("FULCRUM_BUS", "memory"),
            "llm": os.getenv("FULCRUM_LLM", "fake"),
            "topology": topology.value,
            "in_process": topology is Topology.INPROCESS,
            "sim": sim,
            # True only once the agents service is actually running (it starts with the first match);
            # agents_state says why not otherwise (e.g. "failed" with agents_error = a missing Foundry setting).
            "agents": None if pipeline is None else pipeline.agents_state == "running",
            "agents_state": None if pipeline is None else pipeline.agents_state,
            "agents_error": None if pipeline is None else pipeline.agents_error,
            "speech": request.app.state.speech is not None,
            # Demo director (speed / pause / next moment) needs the simulator running in this process.
            "match_controls": pipeline is not None and pipeline.run_sim,
        }

    @r.get("/eval/latest")
    async def eval_latest() -> dict[str, Any]:
        # Offline reports (scripts/eval_live.py, scripts/eval_foundry.py); small files, read per request.
        return await asyncio.to_thread(latest_reports)

    @r.get("/teams", response_model=list[Team])
    async def teams(request: Request) -> list[Team]:
        return list(registry(request).teams.values())

    @r.post("/matches", status_code=201)
    async def create_match(request: Request, config: MatchConfig) -> dict[str, str]:
        reg = registry(request)
        for team_id in (config.home_team_id, config.away_team_id):
            if team_id not in reg.teams:
                raise HTTPException(422, f"unknown team '{team_id}'")
        if config.home_team_id == config.away_team_id:
            raise HTTPException(422, "home and away must be different teams")
        if reg.get(config.match_id) is not None:
            raise HTTPException(409, f"match '{config.match_id}' already exists")
        reg.create(config)
        pipeline: Pipeline | None = request.app.state.pipeline
        if pipeline is not None:
            # inprocess: also starts the sim here. hybrid: engine + agents learn the teams before the first event.
            await pipeline.start_match(config)
        if not request.app.state.topology.api_runs_sim and (sim_url := os.getenv("FULCRUM_SIM_URL")):
            # split / hybrid (Azure): the sim Container App runs the match and publishes to Event Hubs.
            try:
                await start_on_sim(request.app, sim_url, config)
            except Exception:
                reg.matches.pop(config.match_id, None)  # let the client retry with the same match_id
                raise
        return {"match_id": config.match_id}

    @r.get("/matches")
    async def list_matches(request: Request) -> list[dict[str, Any]]:
        return [rm.summary() for rm in registry(request).matches.values()]

    @r.get("/matches/{match_id}/state", response_model=MatchState)
    async def get_state(request: Request, match_id: str) -> MatchState:
        rm = match(request, match_id)
        if rm.state is None:
            raise HTTPException(404, "no state yet — the match has not started")
        return rm.state

    @r.get("/matches/{match_id}/events", response_model=list[MatchEvent], response_model_by_alias=True)
    async def get_events(request: Request, match_id: str, since_seq: int = -1) -> list[MatchEvent]:
        return match(request, match_id).events_since(since_seq)

    @r.get("/matches/{match_id}/winprob", response_model=list[WinProbPoint])
    async def get_winprob(request: Request, match_id: str) -> list[WinProbPoint]:
        return list(match(request, match_id).winprob)

    @r.get("/matches/{match_id}/moments", response_model=list[Moment])
    async def get_moments(request: Request, match_id: str) -> list[Moment]:
        return list(match(request, match_id).moments)

    @r.get("/matches/{match_id}/insights", response_model=list[Insight])
    async def get_insights(
        request: Request,
        match_id: str,
        persona: Persona | None = None,
        language: str | None = None,
        favourite_team_id: str | None = None,
        followed_player_id: str | None = None,
    ) -> list[Insight]:
        rm = match(request, match_id)
        prefs = ps.personalise_query(persona, language, favourite_team_id, followed_player_id)
        if prefs is None:
            return list(rm.insights.values())
        tr = request.app.state.translator
        return [await ps.personalise_insight(i, prefs, tr) for i in rm.insights.values()]

    @r.get("/matches/{match_id}/overlay-cues", response_model=list[OverlayCue])
    async def get_cues(
        request: Request,
        match_id: str,
        persona: Persona | None = None,
        language: str | None = None,
        since_clock_ms: int = Query(-1),
        favourite_team_id: str | None = None,
        followed_player_id: str | None = None,
    ) -> list[OverlayCue]:
        rm = match(request, match_id)
        cues = [c for c in rm.cues if c.show_at_clock_ms > since_clock_ms]
        prefs = ps.personalise_query(persona, language, favourite_team_id, followed_player_id)
        if prefs is None:
            return cues
        tr = request.app.state.translator
        return [await tr.cue(c, prefs.language) for c in ps.select_cues(cues, prefs)]

    @r.get("/matches/{match_id}/recap", response_model=Recap)
    async def get_recap(
        request: Request, match_id: str, persona: Persona = Persona.CASUAL, language: str = "en"
    ) -> Recap:
        rm = match(request, match_id)
        if rm.status != "finished":
            raise HTTPException(404, "recap is available after full time")
        prefs = ps.personalise_query(persona, language, None, None)
        best = await request.app.state.hub.best_recap(rm, prefs) if prefs else None
        if best is None:
            raise HTTPException(404, "recap not ready yet")
        return best

    def control(request: Request, match_id: str) -> MatchControl:
        match(request, match_id)  # 404 first
        pipeline: Pipeline | None = request.app.state.pipeline
        ctl = pipeline.control(match_id) if pipeline is not None else None
        if ctl is None:
            raise HTTPException(
                501,
                "match controls need the simulator running inside the API process (FULCRUM_INPROCESS); "
                "this match is simulated elsewhere",
            )
        return ctl

    @r.get("/matches/{match_id}/control")
    async def get_control(request: Request, match_id: str) -> dict[str, Any]:
        """Demo director: the match's live pacing (speed, paused, skipping)."""
        return control(request, match_id).snapshot()

    @r.post("/matches/{match_id}/control")
    async def post_control(request: Request, match_id: str, body: ControlRequest) -> dict[str, Any]:
        """Demo director: set speed, pause / resume, or run fast to the next significant moment."""
        ctl = control(request, match_id)
        if ctl.finished:
            raise HTTPException(409, "the match has finished")
        if body.action == "set_speed":
            if body.speed is None:
                raise HTTPException(422, "set_speed requires speed")
            ctl.set_speed(body.speed)
        elif body.action == "pause":
            ctl.pause()
        elif body.action == "resume":
            ctl.resume()
        elif body.action == "next_moment":
            ctl.skip_to_next(body.min_significance, body.types)
        else:
            ctl.cancel_skip()
        return ctl.snapshot()

    @r.post("/matches/{match_id}/ask", response_model=AskAnswer)
    async def ask(request: Request, match_id: str, body: AskRequest) -> AskAnswer:
        """Ask the studio: the Studio Analyst answers from this match's data only (fact-checked, cached)."""
        rm = match(request, match_id)
        agents = request.app.state.translator.agents
        if agents is None or getattr(agents, "ask", None) is None:
            raise HTTPException(
                503, "the agents service is not running, so the studio can't answer questions right now"
            )
        client = client_key(request.headers, request.client.host if request.client else None)
        wait = request.app.state.ask_limiter.allow(client) or request.app.state.ask_global.allow("*")
        if wait is not None:
            raise HTTPException(
                429,
                "too many questions: wait a few seconds and ask again",
                headers={"Retry-After": str(max(1, round(wait)))},
            )
        return await agents.ask(
            match_id,
            body.question,
            persona=body.persona or Persona.CASUAL,
            language=body.language or "en",
            favourite_team_id=body.favourite_team_id,
            state=rm.state,
        )

    @r.post("/matches/{match_id}/translate", response_model=list[InsightVariant])
    async def translate(request: Request, match_id: str, body: TranslateRequest) -> list[InsightVariant]:
        rm = match(request, match_id)
        insight = rm.insights.get(body.insight_id)
        if insight is None:
            raise HTTPException(404, f"unknown insight '{body.insight_id}'")
        native = [v for v in insight.variants if v.language == body.language]
        if native:
            return native
        english = [v for v in insight.variants if v.language == "en"] or insight.variants
        return await request.app.state.translator.variants(insight, english, body.language)

    @r.get("/speech")
    async def speech_status(request: Request) -> dict[str, Any]:
        """Whether neural voice is available, so the overlay can pick its speech engine without a failed POST."""
        synth: SpeechSynth | None = request.app.state.speech
        voices = {lang: voice_for(lang)[1] for lang in ("en", "es", "fr", "pt", "de")}
        return {"enabled": synth is not None, "auth": synth.auth_mode if synth else None, "voices": voices}

    @r.post("/speech", response_class=Response)
    async def speech(request: Request, body: SpeechRequest) -> Response:
        """One commentary line as MP3 (Azure AI Speech neural voice); 503 when not configured."""
        synth: SpeechSynth | None = request.app.state.speech
        if synth is None:
            raise HTTPException(503, "Azure Speech is not configured; use the browser's speech synthesis")
        client = client_key(request.headers, request.client.host if request.client else None)
        wait = request.app.state.speech_limiter.allow(client) or request.app.state.speech_global.allow("*")
        if wait is not None:
            raise HTTPException(429, "too many speech requests", headers={"Retry-After": str(int(wait) + 1)})
        try:
            audio = await synth.synthesize(body.text, body.language)
        except SpeechError as exc:
            raise HTTPException(502, str(exc)) from exc
        return Response(audio, media_type="audio/mpeg", headers={"Cache-Control": "private, max-age=3600"})

    return r


# --------------------------------------------------------------------------- WebSocket


async def _serve_ws(ws: WebSocket, hub: Hub) -> None:
    """API_CONTRACT §3: subscribe / update_prefs / ping in; hello / event / state / … out."""
    await ws.accept()
    conn = Connection(ws, hub)
    sender = asyncio.create_task(_run_sender(conn), name="ws-sender")
    try:
        while True:
            raw = await ws.receive_text()
            try:
                msg = ClientMessage.model_validate_json(raw)
            except ValidationError as exc:
                conn.push("error", {"code": "bad_request", "message": str(exc.errors()[0]["msg"])})
                continue
            if msg.type == "ping":
                conn.push("pong", {})
            elif msg.type == "subscribe":
                if not msg.match_id or hub.registry.get(msg.match_id) is None:
                    conn.push("error", {"code": "unknown_match", "message": f"unknown match '{msg.match_id}'"})
                    continue
                if msg.prefs is not None:
                    conn.prefs = msg.prefs
                conn.reset_prefs_state()
                hub.subscribe(conn, msg.match_id)
                conn.push("hello", None)
            elif msg.type == "update_prefs":
                if msg.prefs is None:
                    conn.push("error", {"code": "bad_request", "message": "update_prefs requires prefs"})
                    continue
                conn.prefs = msg.prefs
                conn.reset_prefs_state()
                if conn.match_id:
                    conn.push("hello", None)  # re-send the catch-up snapshot in the new persona/language
    except WebSocketDisconnect:
        pass
    finally:
        hub.unsubscribe(conn)
        sender.cancel()
        await asyncio.gather(sender, return_exceptions=True)


async def _run_sender(conn: Connection) -> None:
    try:
        await conn.sender()
    except (WebSocketDisconnect, RuntimeError):
        pass  # socket closed underneath us
    except Exception:
        log.exception("websocket sender crashed")


# `uvicorn fulcrum.api.app:app` (make dev) reads .env like the other services do; variables already set in the
# environment win. Tests set FULCRUM_DOTENV=0 (tests/conftest.py) so a developer's .env never leaks into them.
if os.getenv("FULCRUM_DOTENV", "1") == "1":
    load_dotenv()
app = create_app()
