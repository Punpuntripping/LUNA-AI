"""template_ingester — Layer-4 Memory transformer (raw doc → reusable template).

Single public entrypoint: ``handle_template_ingestion(item_id, deps) ->
IngestResult``. Takes ONE raw legal document (a ``workspace_items`` row), cleans
it into a placeholder'd, uniquely-titled template, and inserts it into the
user's ``user_templates`` library (``created_by='agent'``).

tier_2 (deepseek), agentic, no user talk. Co-located with ``item_analyzer``
(same family: system-side content transform reading ``content_md``). See
``.claude/plans/writer_planner_user_templates.md`` §Wave D.
"""
from .agent import INGESTER_LIMITS, create_template_ingester  # noqa: F401
from .deps import IngesterDeps, build_ingester_deps  # noqa: F401
from .models import (  # noqa: F401
    INGEST_FAILED_AR,
    CleanedTemplate,
    IngestInput,
    IngestResult,
)
from .runner import handle_template_ingestion  # noqa: F401

__all__ = [
    "handle_template_ingestion",
    "build_ingester_deps",
    "IngesterDeps",
    "IngestInput",
    "IngestResult",
    "CleanedTemplate",
    "INGEST_FAILED_AR",
    "create_template_ingester",
    "INGESTER_LIMITS",
]
