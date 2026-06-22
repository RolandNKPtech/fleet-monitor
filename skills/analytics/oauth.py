"""OAuth token management for the analytics data lake.

Loads refresh tokens from builds-reference/cloudflare-project/sites/GA4/alltoken.json,
exchanges them for access tokens, and provides a thin HTTP helper that handles
401-retry-after-refresh.
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
TOKEN_STORE = REPO_ROOT / "builds-reference" / "cloudflare-project" / "sites" / "GA4" / "alltoken.json"

# OAuth client credentials are read from env so secrets never live in source.
# Set GA4_GSC_CLIENT_ID + GA4_GSC_CLIENT_SECRET in .env (local) or as repo
# secrets (CI). Refresh fails with a clear last_error when either is missing.
CLIENT_ID = os.environ.get("GA4_GSC_CLIENT_ID", "")
CLIENT_SECRET = os.environ.get("GA4_GSC_CLIENT_SECRET", "")
TOKEN_URL = "https://oauth2.googleapis.com/token"


@dataclass
class TokenSession:
    """A live OAuth session for one labelled Google user."""
    label: str
    refresh_token: str
    access_token: str = ""
    expires_at: float = 0.0
    last_error: str = ""

    def ensure_fresh(self) -> bool:
        if self.access_token and time.time() < self.expires_at - 60:
            return True
        return self._refresh()

    def _refresh(self) -> bool:
        if not CLIENT_ID or not CLIENT_SECRET:
            self.last_error = (
                "GA4_GSC_CLIENT_ID / GA4_GSC_CLIENT_SECRET not set — refresh disabled"
            )
            return False
        data = urllib.parse.urlencode({
            "client_id": CLIENT_ID,
            "client_secret": CLIENT_SECRET,
            "refresh_token": self.refresh_token,
            "grant_type": "refresh_token",
        }).encode()
        req = urllib.request.Request(
            TOKEN_URL, data=data,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                body = json.loads(resp.read())
            self.access_token = body["access_token"]
            self.expires_at = time.time() + int(body.get("expires_in", 3600))
            self.last_error = ""
            return True
        except urllib.error.HTTPError as e:
            self.last_error = f"refresh HTTP {e.code}: {e.read().decode('utf-8', 'replace')[:200]}"
            return False
        except Exception as e:  # noqa: BLE001
            self.last_error = f"refresh error: {e}"
            return False


def load_sessions(token_store: Path = TOKEN_STORE) -> list[TokenSession]:
    """Load every refresh_token in the store and return TokenSession objects.

    Sessions are returned even if the token is dead — caller checks .ensure_fresh().
    """
    raw = json.loads(token_store.read_text())
    sessions = []
    for label, blob in raw.items():
        if isinstance(blob, dict) and blob.get("refresh_token"):
            sessions.append(TokenSession(label=label, refresh_token=blob["refresh_token"]))
    return sessions


def live_sessions(token_store: Path = TOKEN_STORE) -> list[TokenSession]:
    """Return only sessions whose refresh succeeds (skipping any revoked tokens)."""
    out = []
    for s in load_sessions(token_store):
        if s.ensure_fresh():
            out.append(s)
    return out


def api_get(session: TokenSession, url: str, *, timeout: int = 60) -> tuple[int, Any]:
    """GET with automatic 401 -> refresh -> retry. Returns (status, json-or-text)."""
    session.ensure_fresh()
    for attempt in range(2):
        req = urllib.request.Request(url, headers={"Authorization": f"Bearer {session.access_token}"})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                body = resp.read().decode("utf-8", "replace")
                try:
                    return resp.status, json.loads(body)
                except json.JSONDecodeError:
                    return resp.status, body
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", "replace")
            if e.code == 401 and attempt == 0:
                session._refresh()
                continue
            try:
                return e.code, json.loads(body)
            except json.JSONDecodeError:
                return e.code, body
        except urllib.error.URLError as e:
            return 0, f"URLError: {e.reason}"
    return 0, "exhausted retries"


def api_post(session: TokenSession, url: str, payload: dict, *, timeout: int = 120) -> tuple[int, Any]:
    """POST JSON with automatic 401 -> refresh -> retry."""
    session.ensure_fresh()
    data = json.dumps(payload).encode()
    for attempt in range(2):
        req = urllib.request.Request(
            url, data=data, method="POST",
            headers={
                "Authorization": f"Bearer {session.access_token}",
                "Content-Type": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                body = resp.read().decode("utf-8", "replace")
                try:
                    return resp.status, json.loads(body)
                except json.JSONDecodeError:
                    return resp.status, body
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", "replace")
            if e.code == 401 and attempt == 0:
                session._refresh()
                continue
            try:
                return e.code, json.loads(body)
            except json.JSONDecodeError:
                return e.code, body
        except urllib.error.URLError as e:
            return 0, f"URLError: {e.reason}"
    return 0, "exhausted retries"
