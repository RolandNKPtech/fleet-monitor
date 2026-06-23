"""Test isolation for fleet_monitoring suite.

The plan_config.load_plans(fetch_live_limits=True) path (used by render
and analyze) calls the live WPE API when WPE_API_USER + WPE_API_PASSWORD
are set in the environment. Dev machines have those set (from .env or
the shell session), which makes every render/integration test hit the
live API and slows the suite from ~5s to ~30s.

We can't simply pop the env vars at module load because subprocess-based
tests (test_smoke, test_smoke_plan) re-inherit the parent shell env, and
because import-time dotenv loads in other modules can re-populate them.

The reliable fix: force `_wpe_credentials_present()` to return False for
the entire fleet_monitoring test session. Tests that specifically exercise
the live-fetch path (test_load_plans_fetches_live_bandwidth_when_yaml_field_is_null)
re-stub it via their own monkeypatch.
"""
import pytest


@pytest.fixture(autouse=True, scope="session")
def _disable_live_wpe_fetch():
    """Stub plan_config._wpe_credentials_present to return False for the
    whole session. autouse session-scoped so it applies to every test
    without each one having to opt in."""
    from projects.fleet_monitoring import plan_config
    original = plan_config._wpe_credentials_present
    plan_config._wpe_credentials_present = lambda: False
    yield
    plan_config._wpe_credentials_present = original
