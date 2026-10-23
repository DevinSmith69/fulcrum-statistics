.PHONY: install test lint schemas sim engine agents api web dev replay

install:
	uv sync --extra dev
test:
	uv run pytest -q
lint:
	uv run ruff check src tests && uv run ruff format --check src tests
schemas:
	uv run python scripts/export_schemas.py
# --- individual services (FULCRUM_BUS=eventhubs to use Azure) ---
sim:
	uv run python -m fulcrum.sim --home NBA --away RVR --speed 10
api:
	uv run uvicorn fulcrum.api.app:app --reload --port 8000
agents:
	uv run python -m fulcrum.agents
# --- single-process dev: sim + engine + agents + api on the memory bus ---
dev:
	FULCRUM_BUS=memory uv run uvicorn fulcrum.api.app:app --reload --port 8000
web:
	cd web && npm run dev
# record a full synthetic match to data/matches/<seed>.jsonl
replay:
	uv run python -m fulcrum.sim --home NBA --away RVR --seed 7 --speed 0 --out data/matches/seed7.jsonl
