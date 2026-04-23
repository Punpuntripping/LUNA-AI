"""Executor agents for deep_search_v3.

Provides factory and runner for the 3 executor types:
- Regulations: Saudi laws, bylaws, procedures
- Cases: Court rulings, judicial precedents
- Compliance: Government e-services, official platforms
"""
from .base import create_executor, run_executor

__all__ = [
    "create_executor",
    "run_executor",
]
