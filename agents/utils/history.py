"""Convert DB message rows to Pydantic AI ModelMessage list."""
from __future__ import annotations

from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    UserPromptPart,
    TextPart,
)


def messages_to_history(rows: list[dict]) -> list[ModelMessage]:
    """
    Convert rows from the `messages` table into Pydantic AI message history.

    Each row must have at least `role` and `content` keys.
    - role="user"      → ModelRequest with UserPromptPart
    - role="assistant"  → ModelResponse with TextPart
    - role="system"     → ModelRequest with UserPromptPart (treated as context)

    Rows are expected to be ordered by created_at ASC.
    """
    history: list[ModelMessage] = []
    for row in rows:
        role = row.get("role", "")
        content = row.get("content", "")
        if not content:
            continue

        if role == "user":
            history.append(ModelRequest(parts=[UserPromptPart(content=content)]))
        elif role == "assistant":
            history.append(ModelResponse(parts=[TextPart(content=content)]))
        elif role == "system":
            # System-injected messages (e.g. task summaries) appear as assistant context
            history.append(ModelResponse(parts=[TextPart(content=content)]))

    return history
