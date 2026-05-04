"""Allow running as: python -m agents.deep_search_v4.case_search.cli"""
from .cli import main

import asyncio

asyncio.run(main())
