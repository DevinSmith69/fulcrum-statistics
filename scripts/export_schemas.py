"""Regenerate schemas/*.json from the pydantic contracts (run after any contract change)."""

import json
from pathlib import Path

from fulcrum.contracts import ALL_MODELS

out = Path(__file__).resolve().parents[1] / "schemas"
out.mkdir(exist_ok=True)
for m in ALL_MODELS:
    (out / f"{m.__name__}.schema.json").write_text(json.dumps(m.model_json_schema(by_alias=True), indent=2))
    print("wrote", m.__name__)
