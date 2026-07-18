"""Allow running as: python -m agents.deep_search_v4.reg_compliance_search.cli"""
from .cli import main

import asyncio

asyncio.run(main())
