"""No network or actual credentials: in-memory Management API fake only."""

import pytest

from tools.ops_supabase_harden import Client, NoRedirect, SafeFailure, harden


class Fake:
    def __init__(self):
        self.auth = {"disable_signup": False, "other": "preserve"}
        self.rest = {"db_schema": "public, hmt_crypto, storage", "max_rows": 500}
        self.calls = []

    def request(self, method, path, payload=None):
        self.calls.append((method, path, payload))
        state = self.auth if path == "/config/auth" else self.rest
        if method == "PATCH":
            state.update(payload)
        return state.copy()


def test_default_is_get_only(capsys):
    client = Fake()
    harden(client, False)
    assert all(c[0] == "GET" for c in client.calls)
    assert "preserve" not in capsys.readouterr().out


def test_apply_minimal_fields_and_idempotent():
    client = Fake()
    harden(client, True, lambda _: "APPLY")
    assert [c for c in client.calls if c[0] == "PATCH"] == [
        ("PATCH", "/config/auth", {"disable_signup": True}),
        ("PATCH", "/postgrest", {"db_schema": "public,storage"}),
    ]
    assert client.auth["other"] == "preserve"
    assert client.rest["max_rows"] == 500
    client.calls.clear()
    harden(client, True, lambda _: "APPLY")
    assert all(c[0] == "GET" for c in client.calls)


def test_cancel_changes_nothing():
    client = Fake()
    harden(client, True, lambda _: "no")
    assert all(c[0] == "GET" for c in client.calls)


@pytest.mark.parametrize("raw", ["", "hmt_crypto", "public,secret\nname"])
def test_unsafe_schema_configuration_fails_closed(raw):
    client = Fake()
    client.rest["db_schema"] = raw
    with pytest.raises(SafeFailure):
        harden(client, True, lambda _: "APPLY")
    assert all(c[0] == "GET" for c in client.calls)


def test_concurrent_change_aborts():
    client = Fake()

    def confirm(_):
        client.rest["db_schema"] = "public,new_schema"
        return "APPLY"

    with pytest.raises(SafeFailure, match="changed during review"):
        harden(client, True, confirm)
    assert all(c[0] == "GET" for c in client.calls)


def test_invalid_auth_response_aborts():
    client = Fake()
    client.auth["disable_signup"] = None
    with pytest.raises(SafeFailure):
        harden(client, True, lambda _: "APPLY")


def test_redirect_and_invalid_project_rejected():
    with pytest.raises(SafeFailure):
        NoRedirect().redirect_request(None, None, 302, "", {}, "https://invalid.example")
    with pytest.raises(SafeFailure):
        Client("../invalid", "fake-token")
