# Fix and Verify
1. Before any code change, verify Docker/environment status: `docker ps`
2. Diagnose root cause thoroughly — read error logs, trace call stack
3. Apply minimal targeted fix
4. Run full test suite: `python -m pytest`
5. If tests fail, STOP and re-read the error before making another change
6. Only declare fix complete after ALL tests pass
