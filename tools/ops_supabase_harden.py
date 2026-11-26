"""Operator-only Management API hardening; never accepts credentials as arguments."""

from __future__ import annotations

import argparse
import getpass
import json
import re
import ssl
import sys
import urllib.error
import urllib.request
from collections.abc import Callable
from typing import Any


class SafeFailure(Exception):
    """Fixed diagnostics only; never attach response bodies or credentials."""


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise SafeFailure("STOP: redirect refused")


class Client:
    def __init__(self, project: str, token: str):
        if not re.fullmatch(r"[a-z]{20}", project) or not token.strip():
            raise SafeFailure("STOP: invalid input")
        if any(c in token for c in "\r\n"):
            raise SafeFailure("STOP: invalid input")
        self.base = f"https://api.supabase.com/v1/projects/{project}"
        self.token = token
        self.opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({}),
            urllib.request.HTTPSHandler(context=ssl.create_default_context()),
            NoRedirect(),
        )

    def request(self, method: str, path: str, payload: dict | None = None) -> dict:
        if path not in ("/config/auth", "/postgrest") or method not in ("GET", "PATCH"):
            raise SafeFailure("STOP: unsupported operation")
        request = urllib.request.Request(
            self.base + path,
            data=None if payload is None else json.dumps(payload).encode(),
            headers={"Authorization": f"Bearer {self.token}", "Content-Type": "application/json"},
            method=method,
        )
        try:
            with self.opener.open(request, timeout=20) as response:
                body = response.read(1048577)
            if len(body) > 1048576:
                raise SafeFailure("STOP: response too large")
            result = json.loads(body)
            if not isinstance(result, dict):
                raise SafeFailure("STOP: unexpected response")
            return result
        except urllib.error.HTTPError as error:
            # No URL, headers or response body is displayed.
            if error.code in (401, 403):
                raise SafeFailure("STOP: token permission or authentication failure") from None
            if error.code == 429:
                raise SafeFailure("STOP: rate limited; no automatic retry") from None
            raise SafeFailure("STOP: Management API request failed") from None
        except (OSError, ValueError):
            raise SafeFailure("STOP: connection, TLS or response failure") from None


def read_state(client: Any) -> tuple[bool, str, list[str]]:
    auth = client.request("GET", "/config/auth")
    rest = client.request("GET", "/postgrest")
    disabled, raw = auth.get("disable_signup"), rest.get("db_schema")
    if not isinstance(disabled, bool) or not isinstance(raw, str):
        raise SafeFailure("STOP: unrecognized configuration; use dashboard")
    schemas = [value.strip() for value in raw.split(",")]
    if not schemas or any(not re.fullmatch(r"[A-Za-z_][A-Za-z_0-9]*", s) for s in schemas):
        raise SafeFailure("STOP: unsupported schema list; use dashboard")
    return disabled, raw, schemas


def harden(client: Any, apply: bool, confirm: Callable[[str], str] = input) -> None:
    before = read_state(client)
    print("Public sign-ups disabled: " + str(before[0]).lower())
    print("Data API exposed schemas: " + ", ".join(before[2]))
    if not apply:
        print("DRY RUN: GET only; would disable sign-ups and remove hmt_crypto only.")
        return
    remaining = [s for s in before[2] if s != "hmt_crypto"]
    if not remaining:
        raise SafeFailure("STOP: removal leaves no schemas; review in dashboard")
    print("PLAN: disable public sign-ups; remove hmt_crypto only. Other fields are preserved.")
    if confirm("Type APPLY to confirm: ") != "APPLY":
        print("CANCELLED: no changes")
        return
    if read_state(client) != before:
        raise SafeFailure("STOP: configuration changed during review; rerun")
    if not before[0]:
        client.request("PATCH", "/config/auth", {"disable_signup": True})
    if remaining != before[2]:
        client.request("PATCH", "/postgrest", {"db_schema": ",".join(remaining)})
    after = read_state(client)
    if not after[0] or after[2] != remaining:
        raise SafeFailure(
            "STOP: verification failed; partial hardening may remain; inspect dashboard"
        )
    print("PASS: sign-ups disabled and private schema excluded; verified by GET")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    if not sys.stdin.isatty():
        print("STOP: run interactively in your own terminal")
        return 1
    try:
        project = getpass.getpass("Project ref (hidden): ").strip()
        token = getpass.getpass("Short-lived scoped personal access token (hidden): ")
        harden(Client(project, token), args.apply)
        return 0
    except SafeFailure as error:
        print(str(error))
    except (KeyboardInterrupt, EOFError):
        print("STOP: cancelled")
    except Exception:
        print("STOP: unexpected failure; no diagnostic values displayed")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
