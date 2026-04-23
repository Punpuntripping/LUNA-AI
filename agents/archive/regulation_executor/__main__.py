"""Allow running as: python -m agents.regulation_executor"""
from agents.regulation_executor.cli import main
import asyncio

asyncio.run(main())
