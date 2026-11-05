# xfail Tests — Expected Failures

This document tracks all 13 `pytest.xfail()` tests across the test suite.
These tests are expected to fail due to external data dependencies or
known data quality issues that cannot be fixed in unit tests.

## Summary

| Source | Count | Reason |
|---|---|---|
| `tests/test_alpha_zoo.py` | 11 | Alpha factors requiring `fund:*` data |
| `tests/test_alpha_zoo_adapter.py` | 2 | Data issues or `inf` values |

## Why xfail Instead of skip?

We use `xfail` rather than `skip` because:

1. **Visible in test reports**: CI logs show these as "expected failures"
   rather than silently skipped, so coverage gaps are documented.
2. **Self-documenting**: Each `xfail()` includes the reason inline.
3. **Fix tracking**: When data sources become available, these tests will
   start passing and the suite will report it.

## Common Reasons

| Reason Pattern | Description |
|---|---|
| `requires external fund:* data` | Test requires fundamental data (financial statements) not available in unit test environment |
| `cannot compute (likely needs extra data)` | Alpha calculation requires more input data than provided |
| `known data quality issue` | Test data has known quality issues (NaN, inf, shape mismatch) |
| `inf values` | Computation produces infinite values that fail assertions |
| `too few valid points` | Not enough non-NaN data points to compute statistics |
| `shape mismatch (known)` | Output shape doesn't match expected (known limitation) |

## Fixing These Tests

To convert any xfail to a passing test:

1. Identify the missing data source or known issue from the xfail message
2. Add a fixture or mock data source to `tests/conftest.py`
3. Update the test to use the new fixture
4. Remove the `pytest.xfail()` call

Tests can also be marked as `@pytest.mark.skip(reason="...")` if the
external dependency is unlikely to be resolved in the near term.