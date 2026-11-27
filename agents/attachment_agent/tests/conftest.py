from __future__ import annotations

import pytest

from backend.app.services import masking_service
from shared.files import flags as files_flags


@pytest.fixture(autouse=True)
def _isolated(monkeypatch):
    """Attachments read from workspace_items (the user_files switch off), no turn
    codec unless a test sets one, and the model never live. The attachment agent's
    flag is ON for everyone: the agent only runs then, and the readers gate the
    user's promotions on it (``shared.attachments.flags.edits_enabled``)."""
    from shared.config import get_settings

    settings = get_settings()
    monkeypatch.setattr(settings, "ATTACHMENT_AGENT_ENABLED", True)
    monkeypatch.setattr(settings, "ATTACHMENT_AGENT_USERS", "")
    token = masking_service.set_active_codec(None)
    with files_flags.override(read=False, write=False):
        yield
    masking_service.reset_active_codec(token)
