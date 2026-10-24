"""Fulcrum API gateway (API_CONTRACT §2–3): REST under `/api`, WebSocket at `/ws`.

    uvicorn fulcrum.api.app:app --port 8000          (module-level `app`)
    create_app(bus=MemoryBus(), run_sim=False)       (tests)

With FULCRUM_BUS=memory (default) the lifespan runs everything in one process: the hub always consumes the
bus; the engine, agents and simulator are started by the first `POST /api/matches` (see `pipeline.py`).
"""

from __future__ import annotations

import asyncio
import logging
import os
from contextlib import asynccontextmanager
from typing import Any

import httpx
from fastapi import APIRouter, FastAPI, HTTPException, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException

from fulcrum.bus import Bus, get_bus
from fulcrum.contracts import (
    ClientMessage,
    Insight,
    InsightVariant,
    MatchConfig,
    MatchEvent,
    MatchState,
    Moment,
    OverlayCue,
    Persona,
    Recap,
    Team,
    WinProbPoint,
)
from fulcrum.engine.service import EngineService
from fulcrum.engine.teams import load_teams

from . import personalise as ps
from .hub import Connection, Hub
from .pipeline import Pipeline
from .readmodel import MatchReadModel, Registry
from .translation import Translator

log = logging.getLogger("fulcrum.api")
DEFAULT_ORIGINS = "http://localhost:5173,http://127.0.0.1:5173"


class TranslateRequest(BaseModel):
    insight_id: str
    language: str


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
    run_sim: bool | None = None,
    run_agents: bool | None = None,
) -> FastAPI:
    """Build the app.

    `in_process` defaults to FULCRUM_BUS=memory; `run_sim` / `run_agents` to FULCRUM_RUN_SIM / FULCRUM_RUN_AGENTS
    (default on). Tests switch them off and publish onto the bus themselves.
    """

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        the_bus = bus or get_bus()
        registry = Registry(teams if teams is not None else load_teams())
        translator = Translator()
        hub = Hub(the_bus, registry, translator)
        hub.start()
        inproc = (
            in_process
            if in_process is not None
            else _env_flag("FULCRUM_INPROCESS", os.getenv("FULCRUM_BUS", "memory") == "memory")
        )
        pipeline = None
        if inproc:
            sim = run_sim if run_sim is not None else _env_flag("FULCRUM_RUN_SIM", True)
            agents = run_agents if run_agents is not None else _env_flag("FULCRUM_RUN_AGENTS", True)
            engine = EngineService(the_bus, teams=registry.teams)
            pipeline = Pipeline(the_bus, registry, engine, translator, run_sim=sim, run_agents=agents)
        app.state.bus, app.state.registry, app.state.hub = the_bus, registry, hub
        app.state.translator, app.state.pipeline = translator, pipeline
        try:
            yield
        finally:
            if pipeline is not None:
                await pipeline.stop()
            await hub.stop()

    app = FastAPI(title="Fulcrum API", version="0.1.0", lifespan=lifespan)
    origins = [o.strip() for o in os.getenv("FULCRUM_ALLOWED_ORIGINS", DEFAULT_ORIGINS).split(",") if o.strip()]
    app.add_middleware(CORSMiddleware, allow_origins=origins, allow_methods=["*"], allow_headers=["*"])

    @app.exception_handler(StarletteHTTPException)
    async def _http_problem(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        titles = {404: "Not Found", 409: "Conflict", 422: "Unprocessable Entity", 400: "Bad Request"}
        return problem(exc.status_code, titles.get(exc.status_code, "Error"), exc.detail)

    @app.exception_handler(RequestValidationError)
    async def _validation_problem(_: Request, exc: RequestValidationError) -> JSONResponse:
        return problem(422, "Unprocessable Entity", "request validation failed", errors=_jsonable(exc.errors()))

    app.include_router(_router())

    @app.websocket("/ws")
    async def ws_endpoint(ws: WebSocket) -> None:
        await _serve_ws(ws, ws.app.state.hub)

    return app


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
        return {
            "status": "ok",
            "bus": os.getenv("FULCRUM_BUS", "memory"),
            "llm": os.getenv("FULCRUM_LLM", "fake"),
            "in_process": pipeline is not None,
            "sim": None if pipeline is None else ("simulator" if pipeline.sim_available else "replay"),
            "agents": None if pipeline is None else pipeline.agents_available,
        }

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
            await pipeline.start_match(config)
        elif sim_url := os.getenv("FULCRUM_SIM_URL"):
            # Out-of-process (Azure): the sim Container App runs the match and publishes to Event Hubs.
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


app = create_app()
