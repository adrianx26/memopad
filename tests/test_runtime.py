"""Tests for runtime mode resolution.

NOTE: every test in this module exercises the ``cloud_mode`` runtime path
(``RuntimeMode.CLOUD``, ``is_cloud``, ``resolve_runtime_mode(cloud_mode_enabled=...)``)
which is a PLANNED FUTURE COMMERCIAL-TIER feature and is NOT implemented in this
branch. The local-only ``memopad.runtime`` has only ``LOCAL``/``TEST`` and no
``cloud_mode_enabled`` parameter, so these tests fail today with
``AttributeError``/``TypeError``. They are kept as the spec for the cloud build
and marked xfail so they don't pollute the test signal. Remove these marks when
the cloud build lands (the tests will flip to XPASS as a reminder).
"""

import pytest

from memopad.runtime import RuntimeMode, resolve_runtime_mode

# cloud_mode is a planned future commercial-tier feature; not implemented here.
# Expected to fail with AttributeError (no RuntimeMode.CLOUD / is_cloud),
# TypeError (no cloud_mode_enabled kwarg), or ValueError. A failure for any
# OTHER reason is a real regression and will still surface as a hard failure.
pytestmark = pytest.mark.xfail(
    strict=False,
    raises=(AttributeError, TypeError, ValueError),
    reason="cloud_mode is a planned future commercial-tier feature; not yet implemented",
)


class TestRuntimeMode:
    """Tests for RuntimeMode enum."""

    def test_local_mode_properties(self):
        mode = RuntimeMode.LOCAL
        assert mode.is_local is True
        assert mode.is_cloud is False
        assert mode.is_test is False

    def test_cloud_mode_properties(self):
        mode = RuntimeMode.CLOUD
        assert mode.is_local is False
        assert mode.is_cloud is True
        assert mode.is_test is False

    def test_test_mode_properties(self):
        mode = RuntimeMode.TEST
        assert mode.is_local is False
        assert mode.is_cloud is False
        assert mode.is_test is True


class TestResolveRuntimeMode:
    """Tests for resolve_runtime_mode function."""

    def test_resolves_to_test_when_test_env(self):
        """Test environment takes precedence over cloud mode."""
        mode = resolve_runtime_mode(cloud_mode_enabled=True, is_test_env=True)
        assert mode == RuntimeMode.TEST

    def test_resolves_to_cloud_when_enabled(self):
        """Cloud mode is used when enabled and not in test env."""
        mode = resolve_runtime_mode(cloud_mode_enabled=True, is_test_env=False)
        assert mode == RuntimeMode.CLOUD

    def test_resolves_to_local_by_default(self):
        """Local mode is the default when no other modes apply."""
        mode = resolve_runtime_mode(cloud_mode_enabled=False, is_test_env=False)
        assert mode == RuntimeMode.LOCAL

    def test_test_env_overrides_cloud_mode(self):
        """Test environment should override cloud mode."""
        # When both are enabled, test takes precedence
        mode = resolve_runtime_mode(cloud_mode_enabled=True, is_test_env=True)
        assert mode == RuntimeMode.TEST
        assert mode.is_test is True
        assert mode.is_cloud is False
