"""Agent router — classifies intent and dispatches to agent families."""
from agents.router.router import route_and_execute
from agents.router.classifier import classify

__all__ = ["route_and_execute", "classify"]
