# Fulcrum: one image for every backend service. `python -m fulcrum` (src/fulcrum/__main__.py) picks the process
# with FULCRUM_SERVICE:
#   api    -> uvicorn fulcrum.api.app:app          (REST + WebSocket, port $PORT, default 8000)
#   sim    -> uvicorn fulcrum.sim.service:app      (internal match trigger, port $PORT, default 8001)
#   engine -> python -m fulcrum.engine             (bus consumer, no port)
#   agents -> python -m fulcrum.agents             (bus consumer, no port)
# No BuildKit-only syntax (no RUN --mount, no heredocs) so `az acr build` / azd remoteBuild can build it too.
# Single-container fallback (cut list): FULCRUM_SERVICE=api FULCRUM_BUS=memory (or FULCRUM_INPROCESS=1)
# runs sim + engine + agents inside the API process.
#
#   docker build -t fulcrum .
#   docker run --rm -p 8000:8000 -e FULCRUM_SERVICE=api fulcrum

FROM python:3.12-slim AS base
COPY --from=ghcr.io/astral-sh/uv:0.13.5 /uv /usr/local/bin/uv

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    VIRTUAL_ENV=/app/.venv \
    PATH="/app/.venv/bin:$PATH"

WORKDIR /app

# Dependencies first (cached layer): runtime deps + the `azure` extra (aiokafka, azure-identity, Agent Framework,
# Azure Monitor). No dev tools.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --extra azure --no-install-project

# Then the code and reference data (teams.json; the replay fixture is the API's fallback when the sim is absent).
COPY src ./src
COPY data ./data
RUN uv sync --frozen --no-dev --extra azure

# Root-owned, world-readable code; the process runs unprivileged and never writes to /app.
RUN useradd --create-home --uid 10001 fulcrum
USER fulcrum

ENV FULCRUM_SERVICE=api \
    FULCRUM_TEAMS_PATH=/app/data/teams.json
EXPOSE 8000 8001

ENTRYPOINT ["python", "-m", "fulcrum"]
