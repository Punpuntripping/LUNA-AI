"""THE RUNTIME article path — `searcher.resolve_article`, not `_fetch_article_content`.

WHY THIS FILE EXISTS
--------------------
`run_resolution.py` scores the article leg through
``fetch_article._fetch_article_content``. That is **not** the function the
searcher calls. The searcher's ``resolve_article`` tool calls
``searcher.fetch_article_identity`` — the tree's THIRD copy of the article key —
and until 2026-08-16 that copy had its own bare ``.eq("article_number", …)``.
The consequence, recorded in plan §13e: «٨١» read as *fixed* in the eval while
the runtime path still missed it.

So a re-run that only re-runs `run_resolution` cannot tell whether the fix
landed on the path that matters. This module closes that gap by invoking the
**real tool object** pulled off a real ``create_searcher_agent()`` — not a
re-implementation of its body — with a real ``RunContext`` and real
``SearcherDeps``. Zero LLM calls: the tool function is called directly, which is
exactly what the model's tool-call would do.

    python agents/simple_search/eval/rerun_runtime_article.py
"""
from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from pathlib import Path

from rerun_common import USER_ID, hr, service_client

from agents.simple_search.eval.fixtures_resolution import FIXTURES  # noqa: E402
from agents.simple_search.searcher import SearcherDeps, create_searcher_agent  # noqa: E402


@dataclass
class RuntimeResult:
    fid: str
    query: str
    article_number: str
    expect: str
    expect_reg_id: str
    tool_reply: str
    handle_reg_id: str
    handle_article_id: str
    handle_article_number: str
    verdict: str
    note: str = ""


def _run_context(deps: SearcherDeps):
    """A minimal RunContext the tool closure accepts.

    `@agent.tool` functions take ``ctx`` and read only ``ctx.deps`` here, so the
    context needs deps and the model — everything else pydantic-ai fills at run
    time and none of it is touched on this path.
    """
    from pydantic_ai.tools import RunContext

    from agents.utils.agent_models import get_agent_model
    from agents.simple_search.searcher import SEARCHER_SLOT

    return RunContext(deps=deps, model=get_agent_model(SEARCHER_SLOT), usage=None)


async def main() -> int:
    sb = service_client()
    hr("RUNTIME article path — searcher.resolve_article (the tool the LLM calls)")

    agent = create_searcher_agent()
    tool = agent._function_toolset.tools["resolve_article"]  # noqa: SLF001
    print(f"tool under test: {tool.function.__qualname__}")

    # Every article fixture, plus the wrong-parent negative (fp-04).
    fixtures = [f for f in FIXTURES if f.data_type == "article"]
    out: list[RuntimeResult] = []

    for f in fixtures:
        deps = SearcherDeps(supabase=sb, user_id=USER_ID, conversation_id="")
        ctx = _run_context(deps)
        reply = await tool.function(ctx, f.query, f.article_number)
        obj = deps.candidates.get("C1")
        r = RuntimeResult(
            fid=f.fid, query=f.query, article_number=f.article_number,
            expect=f.expect, expect_reg_id=f.expect_reg_id,
            tool_reply=reply,
            handle_reg_id=getattr(obj, "regulation_id", "") if obj else "",
            handle_article_id=getattr(obj, "article_id", "") if obj else "",
            handle_article_number=getattr(obj, "article_number", "") if obj else "",
            verdict="",
        )

        if f.expect == "resolve":
            if not r.handle_reg_id:
                r.verdict, r.note = "FAIL", "no regulation resolved"
            elif f.expect_reg_id and r.handle_reg_id != f.expect_reg_id:
                r.verdict, r.note = "WRONG_DOC", "resolved the wrong نظام"
            elif not r.handle_article_id:
                # The tool still mints a handle and defers to the chunk
                # fallback, so this is a real miss on the ARTICLE key even
                # though a handle came back.
                r.verdict, r.note = "FAIL", "parent resolved, article key MISSED (no articles_v2 row)"
            else:
                r.verdict = "PASS"
        else:  # refuse
            if r.handle_article_id:
                r.verdict, r.note = "WRONG_DOC", "handed back a real article for a query that must refuse"
            else:
                r.verdict = "PASS"
        out.append(r)
        print(f"  [{r.fid:7s}] num={r.article_number:22s} {r.verdict:9s} "
              f"art_id={'yes' if r.handle_article_id else 'NO ':3s} "
              f"art_no={r.handle_article_number:6s} «{reply[:78]}» {r.note}")

    n = len(out)
    p = sum(1 for r in out if r.verdict == "PASS")
    print(f"\nRUNTIME tp_article: {p}/{n} PASS "
          f"({sum(1 for r in out if r.verdict == 'WRONG_DOC')} wrong-doc)")

    Path(__file__).with_name("rerun_runtime_article_results.json").write_text(
        json.dumps([r.__dict__ for r in out], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print("wrote rerun_runtime_article_results.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
