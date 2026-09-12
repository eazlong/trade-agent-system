---
name: fix-and-verify
description: Use after applying a bug fix to verify it—diagnose root cause, apply minimal fix, run affected tests, stop if they fail.
---

# Fix and Verify

1. Before any code change, verify Docker/environment status: `docker ps`
2. Diagnose root cause thoroughly — read error logs, trace call stack
3. Apply minimal targeted fix
4. Run tests affected by the change (broaden to full suite only on regression or unresolved concern)
5. If tests fail, STOP and re-read the error before making another change
6. Declare fix complete when affected tests pass and no new failures
