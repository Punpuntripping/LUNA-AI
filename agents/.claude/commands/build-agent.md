---
name: build-agent
description: Build a Pydantic AI agent from INITIAL.md plan. Invokes execution agents to write Python code, then wires into Luna's orchestrator.
user_invocable: true
---

# /build-agent — Implement Agent from Plan

You are building a real Pydantic AI agent from an INITIAL.md planning document by invoking specialized execution agents.

## Argument: $ARGUMENTS

The argument is the agent name (snake_case), e.g. `deep_search`, `end_services`, `extraction`.

If `$ARGUMENTS` is empty, list available plans by checking for `agents/*/planning/INITIAL.md` and ask which one to build.

## Step 1: Verify Plan Exists

Check for `agents/{agent_name}/planning/INITIAL.md`.

If missing: "Run the planner first: invoke `@pydantic-ai-planner` to create the INITIAL.md requirements doc."

**Do NOT proceed until INITIAL.md exists.**

## Step 2: Present Build Summary

Read INITIAL.md, then present:

> **Building agent: `{agent_name}`**
>
> | Component | Details |
> |-----------|---------|
> | Model | {from INITIAL.md} |
> | Output type | {from INITIAL.md} |
> | Tools | {count} ({names}) |
>
> **Pipeline:**
>
> | Wave | Agent(s) | Output |
> |------|----------|--------|
> | 1 (parallel) | prompt-engineer + dependency-manager | prompts.md, deps.py, agent.py, runner.py, logger.py, cli.py, __init__.py, logs/ |
> | 2 (needs agent.py) | tool-integrator | tools.py |
> | 3 (needs all code) | validator | tests/ |
> | 4 (optional) | luna-wiring | orchestrator.py, agent_models.py |
>
> Proceed with build?

**Wait for user approval.**

## Step 3: Wave 1 — Parallel (prompt-engineer + dependency-manager)

Invoke **2 agents in parallel** (no dependencies between them):

### Agent 1: prompt-engineer

```
subagent_type: pydantic-ai-prompt-engineer
```

Prompt:
```
Create the system prompt specification for the {agent_name} agent.

Read: agents/{agent_name}/planning/INITIAL.md

Write: agents/{agent_name}/planning/prompts.md

Design a clear, focused system prompt based on the requirements in INITIAL.md.
Keep it under 300 words. Include dynamic prompt patterns only if INITIAL.md requires runtime context.
```

### Agent 2: dependency-manager

```
subagent_type: pydantic-ai-dependency-manager
```

Prompt:
```
Implement deps, agent assembly, runner, and exports for the {agent_name} agent.

Read: agents/{agent_name}/planning/INITIAL.md

Write:
- agents/{agent_name}/deps.py — deps dataclass + build_*_deps() async function
- agents/{agent_name}/agent.py — assembly: imports deps + prompts, creates Agent, registers instructions
- agents/{agent_name}/logger.py — JSON run logger (full process: tool calls, DB results, model messages)
- agents/{agent_name}/runner.py — handle_{agent_name}_turn() entry point (calls logger after every run)
- agents/{agent_name}/cli.py — standalone CLI test runner (python -m agents.{agent_name}.cli "query")
- agents/{agent_name}/__init__.py — exports with tool registration trigger
- agents/{agent_name}/logs/ — directory with .gitkeep and .gitignore
```

## Step 4: Wave 2 — Tool Integrator (needs agent.py)

After Wave 1 completes:

### Agent 3: tool-integrator

```
subagent_type: pydantic-ai-tool-integrator
```

Prompt:
```
Implement tool functions for the {agent_name} agent.

Read: agents/{agent_name}/planning/INITIAL.md
Read: agents/{agent_name}/agent.py (for agent variable name)
Read: agents/{agent_name}/deps.py (for DepsType)

Write: agents/{agent_name}/tools.py

Import the agent instance from .agent and register tools using @agent.tool decorators.
```

## Step 5: Wave 3 — Validator (needs all code)

After Wave 2 completes:

### Agent 4: validator

```
subagent_type: pydantic-ai-validator
```

Prompt:
```
Create tests for the {agent_name} agent.

Read: agents/{agent_name}/planning/INITIAL.md
Read: agents/{agent_name}/ (all .py files)

Write:
- agents/{agent_name}/tests/__init__.py
- agents/{agent_name}/tests/conftest.py
- agents/{agent_name}/tests/test_agent.py

Run: pytest agents/{agent_name}/tests/ -v
Fix any failures.
```

## Step 6: Wave 4 — Luna Wiring (optional)

Ask the user: "Wire this agent into Luna's orchestrator? (adds to orchestrator.py, agent_models.py, router)"

If yes:

### Agent 5: luna-wiring

```
subagent_type: luna-wiring
```

Prompt:
```
Wire the {agent_name} agent into Luna's orchestrator infrastructure.

Read: agents/{agent_name}/ (agent.py, deps.py, runner.py, __init__.py)
Edit: agents/orchestrator.py, agents/utils/agent_models.py, agents/router/router.py
```

## Step 7: Report

> **Build complete for `{agent_name}`**
>
> | File | Status |
> |------|--------|
> | `agents/{agent_name}/planning/prompts.md` | Created |
> | `agents/{agent_name}/deps.py` | Created |
> | `agents/{agent_name}/agent.py` | Created |
> | `agents/{agent_name}/logger.py` | Created |
> | `agents/{agent_name}/runner.py` | Created |
> | `agents/{agent_name}/cli.py` | Created |
> | `agents/{agent_name}/__init__.py` | Created |
> | `agents/{agent_name}/logs/` | Created |
> | `agents/{agent_name}/tools.py` | Created |
> | `agents/{agent_name}/tests/` | Created |
> | Orchestrator wiring | {Wired / Skipped} |
>
> **Test it now:** `python -m agents.{agent_name}.cli "your query"`
> **View logs:** `python -m agents.{agent_name}.cli --list-logs`
>
> Unit tests: {pass/fail}

## Rules

- Use the SPECIALIZED agents (pydantic-ai-prompt-engineer, etc.), NOT general-purpose
- Wave 1 runs in PARALLEL (prompt-engineer + dependency-manager)
- Wave 2 AFTER Wave 1 — needs agent.py for tool decorators
- Wave 3 AFTER Wave 2 — needs all code files
- Wave 4 is OPTIONAL — ask user first
- Always wait for user approval before starting
- Do NOT deploy — this command only creates local files
