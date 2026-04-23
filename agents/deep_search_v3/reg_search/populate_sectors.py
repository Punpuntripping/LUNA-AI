"""Batch-populate the `sectors` column on regulations that are missing it.

Uses DeepSeek via OpenRouter to classify each regulation's title into 1-4
legal sectors from the predefined list.

Usage:
    # Dry run — show classifications without updating DB
    python -m agents.deep_search_v3.reg_search.populate_sectors --dry-run

    # Run for real — update DB in batches of 20
    python -m agents.deep_search_v3.reg_search.populate_sectors

    # Limit to N regulations (useful for testing)
    python -m agents.deep_search_v3.reg_search.populate_sectors --limit 10
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import time

from openai import AsyncOpenAI

from shared.config import get_settings
from shared.db.client import get_supabase_client

from .sector_vocab import VALID_SECTORS, SECTORS_PROMPT_LIST

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

SECTORS_LIST = SECTORS_PROMPT_LIST

SYSTEM_PROMPT = f"""أنت مصنِّف قانوني. مهمتك تصنيف النظام أو اللائحة إلى 1-4 قطاعات من القائمة التالية فقط:

{SECTORS_LIST}

القواعد:
- اختر 1-4 قطاعات فقط
- استخدم الأسماء بالضبط كما هي في القائمة
- أجب بصيغة JSON array فقط، بدون أي نص إضافي
- مثال: ["العمل والتوظيف", "العدل والقضاء"]"""

MODEL = "deepseek/deepseek-chat-v3-0324"
BATCH_SIZE = 20


def _get_client() -> AsyncOpenAI:
    settings = get_settings()
    return AsyncOpenAI(
        api_key=settings.OPENROUTER_API_KEY,
        base_url=settings.OPENROUTER_BASE_URL,
    )


async def classify_title(client: AsyncOpenAI, title: str) -> list[str]:
    """Classify a regulation title into sectors."""
    try:
        response = await client.chat.completions.create(
            model=MODEL,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": title},
            ],
            temperature=0.0,
            max_tokens=200,
        )
        raw = response.choices[0].message.content.strip()
        # Parse JSON array from response
        sectors = json.loads(raw)
        if not isinstance(sectors, list):
            logger.warning("Non-list response for '%s': %s", title[:50], raw)
            return []
        # Validate against allowed sectors
        valid = [s for s in sectors if s in VALID_SECTORS]
        if len(valid) != len(sectors):
            invalid = [s for s in sectors if s not in VALID_SECTORS]
            logger.warning("Invalid sectors for '%s': %s", title[:50], invalid)
        return valid[:4]
    except (json.JSONDecodeError, Exception) as e:
        logger.error("Classification failed for '%s': %s", title[:50], e)
        return []


async def main() -> None:
    parser = argparse.ArgumentParser(description="Populate sectors for regulations")
    parser.add_argument("--dry-run", action="store_true", help="Show classifications without updating DB")
    parser.add_argument("--limit", type=int, default=None, help="Max regulations to process")
    parser.add_argument("--batch-size", type=int, default=BATCH_SIZE, help="Concurrent LLM calls per batch")
    args = parser.parse_args()

    supabase = get_supabase_client()
    client = _get_client()

    # Fetch regulations with NULL sectors
    query = supabase.table("regulations").select("id, title").is_("sectors", "null")
    if args.limit:
        query = query.limit(args.limit)
    else:
        query = query.limit(1000)
    result = query.execute()
    regulations = result.data or []

    logger.info("Found %d regulations with NULL sectors", len(regulations))
    if not regulations:
        print("Nothing to do.")
        return

    updated = 0
    skipped = 0
    t0 = time.perf_counter()

    for i in range(0, len(regulations), args.batch_size):
        batch = regulations[i : i + args.batch_size]
        logger.info("Processing batch %d-%d of %d...", i + 1, i + len(batch), len(regulations))

        # Classify all titles in this batch concurrently
        tasks = [classify_title(client, reg["title"]) for reg in batch]
        results = await asyncio.gather(*tasks)

        for reg, sectors in zip(batch, results):
            if not sectors:
                skipped += 1
                if args.dry_run:
                    print(f"  SKIP: {reg['title'][:80]}")
                continue

            if args.dry_run:
                print(f"  {reg['title'][:80]}")
                print(f"    -> {sectors}")
            else:
                try:
                    supabase.table("regulations").update(
                        {"sectors": sectors}
                    ).eq("id", reg["id"]).execute()
                    updated += 1
                except Exception as e:
                    logger.error("DB update failed for %s: %s", reg["id"], e)
                    skipped += 1

        # Brief pause between batches to avoid rate limits
        if i + args.batch_size < len(regulations):
            await asyncio.sleep(0.5)

    duration = time.perf_counter() - t0
    print(f"\nDone in {duration:.1f}s")
    print(f"  Updated: {updated}")
    print(f"  Skipped: {skipped}")
    print(f"  Total: {len(regulations)}")


if __name__ == "__main__":
    asyncio.run(main())
