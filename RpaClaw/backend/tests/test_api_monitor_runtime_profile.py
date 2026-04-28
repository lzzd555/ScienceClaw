import pytest

from backend.rpa.api_monitor_runtime_profile import ApiMonitorRuntimeProfile, ApiMonitorRuntimeProfileError


def test_profile_stores_secret_variables_and_masks_preview():
    profile = ApiMonitorRuntimeProfile(base_url="https://api.example.test")

    profile.set_variable("auth_token", "secret-auth-token", secret=True)
    profile.set_header("Authorization", "Bearer secret-auth-token", secret=True)

    assert profile.render_template("Bearer {{ auth_token }}") == "Bearer secret-auth-token"
    assert profile.preview() == {
        "headers": ["Authorization"],
        "variables": ["auth_token"],
        "cookies": False,
    }
    assert "secret-auth-token" not in str(profile.preview())


def test_profile_renders_nested_mappings_without_mutating_input():
    profile = ApiMonitorRuntimeProfile(base_url="https://api.example.test")
    profile.set_variable("csrf_token", "csrf-secret", secret=True)
    source = {
        "headers": {"X-CSRF-Token": "{{ csrf_token }}"},
        "body": {"nested": ["{{ csrf_token }}"]},
    }

    rendered = profile.render_value(source)

    assert rendered == {
        "headers": {"X-CSRF-Token": "csrf-secret"},
        "body": {"nested": ["csrf-secret"]},
    }
    assert source["headers"]["X-CSRF-Token"] == "{{ csrf_token }}"


def test_profile_apply_injection_supports_headers_query_and_body():
    profile = ApiMonitorRuntimeProfile(base_url="https://api.example.test")
    profile.set_variable("csrf_token", "csrf-secret", secret=True)
    headers = {"Authorization": "Bearer auth"}
    query = {"page": 1}
    body = {"name": "order"}

    applied = profile.apply_injection(
        {
            "headers": {"X-CSRF-Token": "{{ csrf_token }}"},
            "query": {"csrf": "{{ csrf_token }}"},
            "body": {"_csrf": "{{ csrf_token }}"},
        },
        headers=headers,
        query=query,
        body=body,
    )

    assert headers["X-CSRF-Token"] == "csrf-secret"
    assert query["csrf"] == "csrf-secret"
    assert body["_csrf"] == "csrf-secret"
    assert applied == ["headers.X-CSRF-Token", "query.csrf", "body._csrf"]


def test_profile_raises_when_template_variable_is_missing():
    profile = ApiMonitorRuntimeProfile(base_url="https://api.example.test")

    with pytest.raises(ApiMonitorRuntimeProfileError, match="missing variable: csrf_token"):
        profile.render_template("{{ csrf_token }}")


def test_profile_rejects_conflicting_secret_variable_overwrite():
    profile = ApiMonitorRuntimeProfile(base_url="https://api.example.test")
    profile.set_variable("csrf_token", "first-token", secret=True, source="flow_a")

    with pytest.raises(ApiMonitorRuntimeProfileError, match="variable conflict: csrf_token"):
        profile.set_variable("csrf_token", "second-token", secret=True, source="flow_b")
