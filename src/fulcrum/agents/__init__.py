"""Fulcrum agent team: explains why each engine moment matters, per persona, with verified numbers.

Entry points: `AgentService` (bus in/out + on-demand translate/recap), `ExplainMomentWorkflow` (one moment),
`check_insight_copy` (the deterministic fact-checker). See README.md in this folder.
"""

from .factcheck import FactSheet, check_insight_copy  # noqa: F401
from .orchestrator import ExplainMomentWorkflow, MomentProcessor, OrchestratorConfig  # noqa: F401
from .service import AgentService  # noqa: F401
