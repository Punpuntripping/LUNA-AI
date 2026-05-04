"""Central model assignment for all Luna agents."""

from agents.model_registry import create_model

AGENT_MODELS: dict[str, str] = {
    "deep_search_planner": "qwen3.6-plus",   # tool-calling planner
    "search_regulations":  "qwen3.6-plus",   # tool-calling executor
    "search_cases_courts": "gemini-2.5-flash",
    "search_compliance":   "gemini-2.5-flash",
    "router":              "qwen3.6-plus",   # tool-calling router
    "memory":              "gemini-2.5-flash",
    # Deep Search V2 (revised) — hierarchical supervisor pattern
    "deep_search_v2_plan_agent": "qwen3.6-plus",        # PlanAgent (supervisor)
    "deep_search_v2_expander":   "or-minimax-m2.7",     # QueryExpander (inner loop)
    "deep_search_v2_aggregator": "or-minimax-m2.7",     # Aggregator (inner loop)
    # Deep Search V3 — multi-executor supervisor pattern
    "deep_search_v3_plan_agent":          "qwen3.6-plus",            # PlanAgent supervisor
    "deep_search_v3_regulations_executor": "or-minimax-m2.7",    # Regulations executor
    "deep_search_v3_cases_executor":       "or-minimax-m2.7",    # Cases/Courts executor
    "deep_search_v3_compliance_executor":  "or-minimax-m2.7",    # Government Services executor
    # Reg Search — domain search loop (inside deep_search_v3)
    "reg_search_expander":   "qwen3.5-plus",
    "reg_search_reranker":   "qwen3.5-flash",
    "reg_search_aggregator": "or-gemma-4-31b",
    # Case Search — domain search loop (inside deep_search_v3)
    "case_search_expander":   "qwen3.5-plus",
    "case_search_reranker":   "qwen3.5-flash",
    "case_search_aggregator": "or-qwen3.5-397b",
    # Compliance Search — domain search loop (inside deep_search_v3)
    "compliance_search_expander":   "qwen3.5-plus",
    "compliance_search_reranker":   "qwen3.5-flash",
}


def get_agent_model(agent_name: str):
    model_key = AGENT_MODELS[agent_name]
    return create_model(model_key)
