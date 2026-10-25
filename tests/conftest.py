"""Repo-wide test setup."""

import os

# Never load a developer's .env into the tests (fulcrum.api.app loads it at import): they run offline on
# FULCRUM_BUS=memory FULCRUM_LLM=fake whatever .env says (tests run offline).
os.environ["FULCRUM_DOTENV"] = "0"
