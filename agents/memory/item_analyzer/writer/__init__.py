"""writer_planner caller package for item_analyzer.

Contains the per-family Arabic system prompts + user-message renderers used
when the writer-planner (Layer 2 Major) invokes the analyzer (Layer 4 Memory).

The runtime wiring lives in ``agents/memory/item_analyzer/prompt_registry.py``;
this sub-package only owns the prompt text and user-message rendering.
"""
