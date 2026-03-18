# /debug

Systematic debugging approach for project issues.

## Usage
```
/debug "<issue_description>" [component]
```

## Parameters
- `issue_description` (required): Description of the bug
- `component` (optional): api | db | scraper

## Debugging Process
1. **Reproduce Issue**
   - Identify steps to reproduce
   - Verify in development environment
   - Check logs for errors

2. **Identify Root Cause**
   - Trace execution flow
   - Check recent changes
   - Analyze error messages

3. **Implement Fix**
   - Write failing test for bug
   - Implement minimal fix
   - Verify test passes

4. **Regression Testing**
   - Run related test suites
   - Check for side effects
   - Verify fix in different scenarios

## Common Checks
- API: `docker compose logs -f api`
- Database: `docker compose logs -f db` and check query performance
- Scraper: Check scheduler logs and network errors
