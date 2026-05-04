"""Dependencies for the agent_search publisher."""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional

from supabase import Client as SupabaseClient


@dataclass
class SearchPublishDeps:
    """Runtime deps for ``publish_search_result``.

    ``supabase`` is the sync Supabase client (project pattern: sync client
    used inside async coroutines). ``logger`` is optional -- defaults to a
    module-scoped logger so unit tests don't need to wire one in.
    """

    supabase: SupabaseClient
    logger: Optional[logging.Logger] = field(
        default_factory=lambda: logging.getLogger("agents.agent_search.publisher")
    )
