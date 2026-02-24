import os
import pytest


@pytest.fixture(autouse=True)
def jira_env_vars(monkeypatch):
    """Set dummy Jira environment variables for all tests.

    Unit tests use mock sessions so the actual values don't matter, but
    jira_base_url() and jira_requests_session() read these at call time and
    will raise KeyError if they're absent.
    """
    monkeypatch.setenv("JIRA_URL", "https://jira.example.com")
    monkeypatch.setenv("JIRA_API_USERNAME", "test@example.com")
    monkeypatch.setenv("JIRA_API_PASSWORD", "test-token")
