# CRASH -- query_id=5

## Exception

```
AttributeError: 'ExpanderOutput' object has no attribute 'sectors'
```

## Traceback

```
Traceback (most recent call last):
  File "C:\Programming\LUNA_AI\agents\deep_search_v4\monitor\run_monitor.py", line 899, in _run_one
    agg_output = await run_full_loop(
                 ^^^^^^^^^^^^^^^^^^^^
    ...<3 lines>...
    )
    ^
  File "C:\Programming\LUNA_AI\agents\deep_search_v4\orchestrator.py", line 703, in run_full_loop
    (reg_sqs, reg_log_id, sectors), comp_sqs, case_sqs = await asyncio.gather(
                                                         ^^^^^^^^^^^^^^^^^^^^^
    ...<3 lines>...
    )
    ^
  File "C:\Programming\LUNA_AI\agents\deep_search_v4\orchestrator.py", line 343, in _run_reg_phase
    if state.expander_output and state.expander_output.sectors:
                                 ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "C:\Users\mhfal\AppData\Roaming\Python\Python313\site-packages\pydantic\main.py", line 1026, in __getattr__
    raise AttributeError(f'{type(self).__name__!r} object has no attribute {item!r}')
AttributeError: 'ExpanderOutput' object has no attribute 'sectors'

```
