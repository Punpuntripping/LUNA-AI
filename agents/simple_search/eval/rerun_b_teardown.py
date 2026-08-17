"""Case-B RE-RUN — hard-delete the `[RERUN-CASE-B]` scratch conversation.

Reuses `ab_teardown` (FK-safe order: workspace_item_references → workspace_items
→ message_attachments → messages → paused_runs → the conversation), re-pointed
at this lane's title. Scoped by `user_id` AND the exact scratch title, so it can
never reach one of the account's 136 real conversations.

    .venv/Scripts/python.exe agents/simple_search/eval/rerun_b_teardown.py
"""
from __future__ import annotations

from rerun_b_common import use_rerun_scratch  # noqa: E402

use_rerun_scratch()          # MUST precede the import below — ab_teardown
                             # from-imports SCRATCH_TITLE at module load.

import ab_teardown  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(ab_teardown.main())
