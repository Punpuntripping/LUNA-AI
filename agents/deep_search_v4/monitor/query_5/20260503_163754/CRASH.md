# CRASH -- query_id=5

## Exception

```
TypeError: LoopState.__init__() got an unexpected keyword argument 'expander_max_queries'
```

## Traceback

```
Traceback (most recent call last):
  File "C:\Programming\LUNA_AI\agents\deep_search_v4\monitor\run_monitor.py", line 890, in _run_one
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
  File "C:\Programming\LUNA_AI\agents\deep_search_v4\orchestrator.py", line 427, in _run_compliance_phase
    state = ComplianceLoopState(
        focus_instruction=query,
    ...<7 lines>...
        ),
    )
TypeError: LoopState.__init__() got an unexpected keyword argument 'expander_max_queries'

```
