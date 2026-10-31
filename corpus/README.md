# Benchmark corpus

One folder per task. Every member contributes tasks for their own area;
Prabanjan (Module 9) owns the format and the harness that runs them.

```
corpus/<task_id>/
    before/        the repository before migration: source and a passing test suite
    expected/      the hand-migrated repository (reference answer)
    task.json      metadata, see below
```

`task.json`:

```json
{
  "task_id": "sqla_001_simple_query",
  "spec": "specs/sqlalchemy_1_4_to_2_0.json",
  "author": "your GitHub username",
  "description": "one sentence on what makes this task interesting",
  "expected_call_sites": [
    {"file": "app/db.py", "line": 12, "pattern_id": "session_query"}
  ]
}
```

Rules for a valid task:

1. The tests in `before/` pass under the old library version.
2. The tests in `expected/` pass under the new library version.
3. `expected_call_sites` lists every deprecated usage; this is the ground
   truth for the completeness metric.
4. Tests are never edited during migration, so `before/tests/` and
   `expected/tests/` are identical.
