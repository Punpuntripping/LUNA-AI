# 60 -- SSE Events

Total events captured: 3

## By type

- plan_ready: 1
- planner_done: 1
- planner_start: 1

## Tail (last 200)

```
[planner_start] {"event": "planner_start", "model": "qwen3.6-plus"}
[planner_done] {"event": "planner_done", "invoke": ["reg", "compliance"], "focus": {"reg": "high", "compliance": "default"}, "model": "qwen3.6-plus", "duration_s": 22.027}
[plan_ready] {"event": "plan_ready", "plan": {"invoke": ["reg", "compliance"], "focus": {"reg": "high", "compliance": "default"}, "sectors": ["العمل والتوظيف"], "rationale": "سؤال حول نظامية إرسال إشعار بعدم الرغبة في تجديد عقد محدد المدة لسائق أثناء إج...
```