# CRASH -- query_id=7

## Exception

```
KeyError: "Unknown aggregator prompt key: 'prompt_reg_only'. Available: ['prompt_1', 'prompt_2', 'prompt_3_critique', 'prompt_3_draft', 'prompt_3_rewrite', 'prompt_4']"
```

## Traceback

```
Traceback (most recent call last):
  File "C:\Programming\LUNA_AI\agents\deep_search_v4\monitor\run_monitor.py", line 784, in _run_one
    agg_output = await run_full_loop(
                 ^^^^^^^^^^^^^^^^^^^^
    ...<3 lines>...
    )
    ^
  File "C:\Programming\LUNA_AI\agents\deep_search_v4\orchestrator.py", line 681, in run_full_loop
    agg_output = await handle_aggregator_turn(agg_input, agg_deps)
                 ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "C:\Programming\LUNA_AI\agents\deep_search_v3\aggregator\runner.py", line 95, in handle_aggregator_turn
    llm_output, model_used, raw_logs = await _run_primary_path(
                                       ^^^^^^^^^^^^^^^^^^^^^^^^
        agg_input, deps, user_message, references
        ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
    )
    ^
  File "C:\Programming\LUNA_AI\agents\deep_search_v3\aggregator\runner.py", line 238, in _run_primary_path
    return await _run_single_shot(
           ^^^^^^^^^^^^^^^^^^^^^^^
    ...<4 lines>...
    )
    ^
  File "C:\Programming\LUNA_AI\agents\deep_search_v3\aggregator\runner.py", line 257, in _run_single_shot
    agent = create_aggregator_agent(prompt_key=prompt_key, model_name=model_name)
  File "C:\Programming\LUNA_AI\agents\deep_search_v3\aggregator\agent.py", line 47, in create_aggregator_agent
    system_prompt = get_aggregator_prompt(prompt_key)
  File "C:\Programming\LUNA_AI\agents\deep_search_v3\aggregator\prompts.py", line 289, in get_aggregator_prompt
    raise KeyError(
    ...<2 lines>...
    )
KeyError: "Unknown aggregator prompt key: 'prompt_reg_only'. Available: ['prompt_1', 'prompt_2', 'prompt_3_critique', 'prompt_3_draft', 'prompt_3_rewrite', 'prompt_4']"

```
