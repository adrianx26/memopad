"""Quality-evaluation harnesses for the MemoPad memory system (Tb G7).

Tests here are marked `@pytest.mark.eval` and excluded from default runs:
    pytest -m eval            # run only the evals
    pytest -m "not eval"       # the normal suite (unchanged behavior)

Rationale: evals are heavier, opinionated, and assert on quality thresholds rather
than exact behavior, so they should not gate the regular test loop.
"""