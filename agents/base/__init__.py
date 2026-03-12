"""Agent base layer — BaseAgent protocol, MockAgentBase, context builder, artifact helper."""
from agents.base.agent import BaseAgent, MockAgentBase
from agents.base.context import build_agent_context
from agents.base.artifact import create_agent_artifact

__all__ = ["BaseAgent", "MockAgentBase", "build_agent_context", "create_agent_artifact"]
