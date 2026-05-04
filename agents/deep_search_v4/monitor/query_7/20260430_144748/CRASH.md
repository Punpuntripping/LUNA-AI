# CRASH -- query_id=7

## Exception

```
ValidationError: 1 validation error for AggregatorOutput
validation
  Input should be a valid dictionary or instance of ValidationReport [type=model_type, input_value=ValidationReport(passed=T...nesty_ok=True, notes=[]), input_type=ValidationReport]
    For further information visit https://errors.pydantic.dev/2.12/v/model_type
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
  File "C:\Programming\LUNA_AI\agents\deep_search_v4\aggregator\runner.py", line 164, in handle_aggregator_turn
    output = AggregatorOutput(
        synthesis_md=clean_synthesis,
    ...<7 lines>...
        artifact=artifact,
    )
  File "C:\Users\mhfal\AppData\Roaming\Python\Python313\site-packages\pydantic\main.py", line 250, in __init__
    validated_self = self.__pydantic_validator__.validate_python(data, self_instance=self)
pydantic_core._pydantic_core.ValidationError: 1 validation error for AggregatorOutput
validation
  Input should be a valid dictionary or instance of ValidationReport [type=model_type, input_value=ValidationReport(passed=T...nesty_ok=True, notes=[]), input_type=ValidationReport]
    For further information visit https://errors.pydantic.dev/2.12/v/model_type

```
