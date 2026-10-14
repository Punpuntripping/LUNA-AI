"""The one output-token cap shared by every agent's ``UsageLimits``.

``output_tokens_limit`` counts reasoning tokens, and reasoning length moves with
every model swap / reasoning-level change. Per-agent caps tuned to "normal
output + headroom" therefore turned each swap into failed turns (writer
2026-10-10, router + artifact_editor 2026-10-13: the run finished, then the cap
discarded it). So the cap is a single runaway-model safety net, NOT a budget:

* how hard an agent thinks  -> its reasoning level in ``AGENT_MODELS``
* how long an agent loops   -> its own ``request_limit`` / ``tool_calls_limit``
* how long one call can be  -> the per-model ``max_tokens`` in ``model_registry``
"""

OUTPUT_TOKENS_CAP = 100_000
