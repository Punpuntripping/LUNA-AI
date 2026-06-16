"""Extract every agent prompt into a clean .md reference catalog.

Reference-only: this does NOT change how code loads prompts. The .py files
remain the source of truth; this dumps a human-readable copy of each prompt
(pure text, no Python wrapper / variable name) into agents/prompts/.

Two extraction modes:
- IMPORT targets (prompts.py modules): import the module and read the live
  string/dict values. This renders module-level f-strings (e.g. the
  sector_picker catalog) to their real text.
- AST targets (agent.py / router.py / reranker.py / populate_sectors.py):
  parse the file and pull string-literal / f-string assignments WITHOUT
  executing the module (no agent construction, no env/DB needed). f-strings are
  reconstructed as templates with {placeholder} markers.

Run from repo root:  python scripts/extract_prompts_md.py
"""
from __future__ import annotations

import ast
import hashlib
import importlib
import os
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)

OUT_DIR = os.path.join(REPO_ROOT, "agents", "prompts")
MIN_LEN = 120  # ignore short strings (labels, keys, format fragments)

# Domain subfolder for each agent label. Deep-search components (planner,
# sector_picker, aggregator) live under search/; router gets its own folder.
SUBDIRS = {
    "reg_search": "search/reg",
    "populate_sectors": "search/reg",
    "case_search": "search/case",
    "compliance_search": "search/compliance",
    "sector_picker": "search/sector_picker",
    "planner": "search/planner",
    "aggregator": "search/aggregator",
    "writer": "writers",
    "writer_planner": "writers",
    "artifact_summarizer": "memory",
    "artifact_editor": "memory",
    "template_ingester": "template",
    "router": "router",
}

# (dotted module, agent label) — imported, live values read
IMPORT_TARGETS = [
    ("agents.deep_search_v4.reg_search.prompts", "reg_search"),
    ("agents.deep_search_v4.case_search.prompts", "case_search"),
    ("agents.deep_search_v4.compliance_search.prompts", "compliance_search"),
    ("agents.deep_search_v4.sector_picker.prompts", "sector_picker"),
    ("agents.deep_search_v4.aggregator.prompts", "aggregator"),
    ("agents.deep_search_v4.planner.prompts", "planner"),
    ("agents.writer.prompts", "writer"),
    ("agents.writer_planner.prompts", "writer_planner"),
    ("agents.memory.artifact_summarizer.prompts", "artifact_summarizer"),
    ("agents.memory.template_ingester.prompts", "template_ingester"),
]

# (relative file path, agent label) — parsed via AST, never executed
AST_TARGETS = [
    ("agents/artifact_editor/agent.py", "artifact_editor"),
    ("agents/router/router.py", "router"),
    ("agents/deep_search_v4/reg_search/reranker.py", "reg_search"),
    ("agents/deep_search_v4/case_search/reranker.py", "case_search"),
    ("agents/deep_search_v4/compliance_search/reranker.py", "compliance_search"),
    ("agents/deep_search_v4/reg_search/populate_sectors.py", "populate_sectors"),
]

_DROP_TOKENS = {"system", "prompt", "prompts", "ar", "en", "text", "the"}


def clean_role(symbol: str, agent: str) -> str:
    """Derive a clean filename role from a Python symbol name."""
    toks = [t for t in symbol.strip("_").split("_") if t]
    toks = [t.lower() for t in toks]
    kept = [t for t in toks if t not in _DROP_TOKENS]
    # drop tokens already in the agent label to avoid agent__agent
    agent_toks = set(agent.lower().split("_"))
    kept = [t for t in kept if t not in agent_toks]
    role = "_".join(kept)
    return role or "system"


def fstring_to_template(node: ast.JoinedStr) -> str:
    """Reconstruct an f-string's text, rendering interpolations as {expr}."""
    parts: list[str] = []
    for v in node.values:
        if isinstance(v, ast.Constant):
            parts.append(str(v.value))
        elif isinstance(v, ast.FormattedValue):
            try:
                parts.append("{" + ast.unparse(v.value) + "}")
            except Exception:
                parts.append("{...}")
    return "".join(parts)


def static_str(node: ast.AST) -> str | None:
    """Best-effort static extraction of a string value from an AST node.

    Handles plain literals, f-strings, and string literals wrapped in a
    transforming call like a triple-quoted string followed by .replace(...) or
    dedent(...) -- the router prompt does SYSTEM_PROMPT = <literal>.replace(...).
    Dynamic substitutions are left as {placeholder} markers.
    """
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        return fstring_to_template(node)
    if isinstance(node, ast.Call):
        func = node.func
        if isinstance(func, ast.Attribute):
            if func.attr in {"replace", "format", "strip", "lstrip", "rstrip"}:
                return static_str(func.value)
            if func.attr == "dedent" and node.args:
                return static_str(node.args[0])
        elif isinstance(func, ast.Name) and func.id == "dedent" and node.args:
            return static_str(node.args[0])
    return None


_seen_hashes: dict[str, str] = {}  # content-hash -> filename (dedupe re-exports)
_manifest: list[tuple[str, int]] = []


def emit(agent: str, role: str, text: str, key: str | None = None) -> None:
    text = text.strip("\n")
    if len(text) < MIN_LEN:
        return
    h = hashlib.sha1(text.encode("utf-8")).hexdigest()
    name = f"{agent}__{role}" + (f"__{key}" if key else "")
    fname = name + ".md"
    if h in _seen_hashes:
        return  # identical content already written (re-exported constant)
    _seen_hashes[h] = fname
    subdir = SUBDIRS.get(agent, agent)
    out_dir = os.path.join(OUT_DIR, *subdir.split("/"))
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, fname)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text.rstrip() + "\n")
    _manifest.append((f"{subdir}/{fname}", len(text)))


def from_import(module: str, agent: str) -> None:
    try:
        mod = importlib.import_module(module)
    except Exception as e:  # noqa: BLE001
        print(f"  [import-failed] {module}: {e.__class__.__name__}: {e}")
        return
    for symbol, value in vars(mod).items():
        if symbol.startswith("__"):
            continue
        if isinstance(value, str):
            emit(agent, clean_role(symbol, agent), value)
        elif isinstance(value, dict) and value:
            if all(isinstance(v, str) for v in value.values()):
                role = clean_role(symbol, agent)
                for k, v in value.items():
                    emit(agent, role, v, key=str(k))


def from_ast(rel_path: str, agent: str) -> None:
    path = os.path.join(REPO_ROOT, rel_path)
    if not os.path.exists(path):
        print(f"  [missing] {rel_path}")
        return
    with open(path, encoding="utf-8") as f:
        tree = ast.parse(f.read(), filename=rel_path)
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        if not (len(node.targets) == 1 and isinstance(node.targets[0], ast.Name)):
            continue
        symbol = node.targets[0].id
        text = static_str(node.value)
        if text is not None:
            emit(agent, clean_role(symbol, agent), text)


def main() -> None:
    os.makedirs(OUT_DIR, exist_ok=True)
    ast_only = "--ast-only" in sys.argv
    if not ast_only:
        print("== import targets ==")
        for module, agent in IMPORT_TARGETS:
            from_import(module, agent)
    print("== ast targets ==")
    for rel, agent in AST_TARGETS:
        from_ast(rel, agent)
    print(f"\nWrote {len(_manifest)} prompt files to agents/prompts/:")
    for fname, n in sorted(_manifest):
        print(f"  {fname:<48} {n:>6} chars")


if __name__ == "__main__":
    main()
