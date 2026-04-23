"""Central model assignment for all Luna agents."""

from agents.model_registry import create_model

AGENT_MODELS: dict[str, str] = {
    "deep_search_planner": "or-gemini-3.1-pro-tools",   # tool-calling planner
    "search_regulations":  "or-gemini-3.1-pro-tools",   # tool-calling executor
    "search_cases_courts": "gemini-2.5-flash",
    "search_compliance":   "gemini-2.5-flash",
    "router":              "or-gemini-3.1-pro-tools",   # tool-calling router
    "end_services":        "gemini-2.5-flash",
    "extraction":          "gemini-2.5-flash",
    "memory":              "gemini-2.5-flash",
    # Deep Search V2 (revised) — hierarchical supervisor pattern
    "deep_search_v2_plan_agent": "or-gemini-3.1-pro",   # PlanAgent (supervisor)
    "deep_search_v2_expander":   "or-minimax-m2.7",     # QueryExpander (inner loop)
    "deep_search_v2_aggregator": "or-minimax-m2.7",     # Aggregator (inner loop)
    # Deep Search V3 — multi-executor supervisor pattern
    "deep_search_v3_plan_agent":          "or-gemini-3.1-pro-tools",   # PlanAgent supervisor (customtools variant)
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
