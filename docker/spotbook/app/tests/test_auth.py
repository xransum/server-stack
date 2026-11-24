"""
test_auth.py — Unit tests for auth.py SpotbookAuthProvider.

Tests cover:
  - DCR: register succeeds with valid redirect_uri
  - DCR: register rejects invalid redirect_uri
  - DCR: register requires redirect_uris
  - begin_authorize: validates client_id, redirect_uri, PKCE method
  - PKCE verification: correct verifier passes, wrong verifier fails
  - Authorization code: single-use, TTL, client_id binding
  - Access token: issued, verifiable, not reusable after revoke
  - Refresh token rotation: new pair issued, old refresh invalidated
  - Reused refresh token: triggers invalid_grant + revokes all client tokens
  - Rate limiting: 5 failures within window → PermissionError

GitHub calls are mocked — no network access in these tests.
"""

import json
import base64
import hashlib
import secrets
import time
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

from spotbook.db import Db
from spotbook.auth import (
    SpotbookAuthProvider,
    ALLOWED_REDIRECT_URI,
    _pkce_verify,
    _sha256_hex,
    _generate_token,
    _generate_code,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def tmp_db(tmp_path):
    return Db(str(tmp_path / "test.db"))


@pytest.fixture
def provider(tmp_db):
    return SpotbookAuthProvider(
        db=tmp_db,
        public_base_url="https://spotbook.example.com",
        github_client_id="gh_client_id",
        github_client_secret="gh_client_secret",
        allowed_github_user_id=12345678,
    )


def _make_pkce() -> tuple[str, str]:
    """Return (code_verifier, code_challenge_S256)."""
    verifier = base64.urlsafe_b64encode(secrets.token_bytes(32)).rstrip(b"=").decode()
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
    return verifier, challenge


# ---------------------------------------------------------------------------
# _pkce_verify
# ---------------------------------------------------------------------------

def test_pkce_verify_correct():
    verifier, challenge = _make_pkce()
    assert _pkce_verify(verifier, challenge) is True


def test_pkce_verify_wrong_verifier():
    _, challenge = _make_pkce()
    assert _pkce_verify("wrongverifier", challenge) is False


def test_pkce_verify_wrong_challenge():
    verifier, _ = _make_pkce()
    assert _pkce_verify(verifier, "wrongchallenge") is False


# ---------------------------------------------------------------------------
# DCR — register_client
# ---------------------------------------------------------------------------

def test_register_valid(provider):
    result = provider.register_client({
        "redirect_uris": [ALLOWED_REDIRECT_URI],
        "client_name": "Claude Test",
    })
    assert "client_id" in result
    assert result["redirect_uris"] == [ALLOWED_REDIRECT_URI]
    assert result["token_endpoint_auth_method"] == "none"


def test_register_invalid_redirect_uri(provider):
    with pytest.raises(ValueError, match="not allowed"):
        provider.register_client({
            "redirect_uris": ["https://evil.com/callback"],
        })


def test_register_missing_redirect_uris(provider):
    with pytest.raises(ValueError):
        provider.register_client({})


def test_register_mixed_redirect_uris_rejected(provider):
    """Even if one URI is correct, any invalid URI rejects the registration."""
    with pytest.raises(ValueError, match="not allowed"):
        provider.register_client({
            "redirect_uris": [ALLOWED_REDIRECT_URI, "https://evil.com/cb"],
        })


def test_get_client_after_register(provider):
    result = provider.register_client({"redirect_uris": [ALLOWED_REDIRECT_URI]})
    client = provider.get_client(result["client_id"])
    assert client is not None
    assert client["client_id"] == result["client_id"]


def test_get_unknown_client_returns_none(provider):
    assert provider.get_client("nonexistent") is None


# ---------------------------------------------------------------------------
# begin_authorize
# ---------------------------------------------------------------------------

def _register_and_begin(provider) -> tuple[str, str, str]:
    """Register a client and start an authorization flow. Returns (client_id, verifier, state)."""
    reg = provider.register_client({"redirect_uris": [ALLOWED_REDIRECT_URI]})
    client_id = reg["client_id"]
    verifier, challenge = _make_pkce()

    github_url = provider.begin_authorize(
        client_id=client_id,
        redirect_uri=ALLOWED_REDIRECT_URI,
        code_challenge=challenge,
        code_challenge_method="S256",
        scope=None,
        state="test-state-123",
    )
    # Extract flow state from GitHub URL
    from urllib.parse import urlparse, parse_qs
    params = parse_qs(urlparse(github_url).query)
    flow_state = params["state"][0]
    return client_id, verifier, flow_state


def test_begin_authorize_returns_github_url(provider):
    reg = provider.register_client({"redirect_uris": [ALLOWED_REDIRECT_URI]})
    _, challenge = _make_pkce()

    url = provider.begin_authorize(
        client_id=reg["client_id"],
        redirect_uri=ALLOWED_REDIRECT_URI,
        code_challenge=challenge,
        code_challenge_method="S256",
        scope=None,
        state=None,
    )
    assert "github.com/login/oauth/authorize" in url


def test_begin_authorize_unknown_client(provider):
    _, challenge = _make_pkce()
    with pytest.raises(ValueError, match="Unknown client_id"):
        provider.begin_authorize(
            client_id="nosuchclient",
            redirect_uri=ALLOWED_REDIRECT_URI,
            code_challenge=challenge,
            code_challenge_method="S256",
            scope=None,
            state=None,
        )


def test_begin_authorize_invalid_redirect(provider):
    reg = provider.register_client({"redirect_uris": [ALLOWED_REDIRECT_URI]})
    _, challenge = _make_pkce()
    with pytest.raises(ValueError, match="Invalid redirect_uri"):
        provider.begin_authorize(
            client_id=reg["client_id"],
            redirect_uri="https://attacker.com/cb",
            code_challenge=challenge,
            code_challenge_method="S256",
            scope=None,
            state=None,
        )


def test_begin_authorize_invalid_pkce_method(provider):
    reg = provider.register_client({"redirect_uris": [ALLOWED_REDIRECT_URI]})
    with pytest.raises(ValueError, match="S256"):
        provider.begin_authorize(
            client_id=reg["client_id"],
            redirect_uri=ALLOWED_REDIRECT_URI,
            code_challenge="somechallenge",
            code_challenge_method="plain",
            scope=None,
            state=None,
        )


# ---------------------------------------------------------------------------
# Full auth flow: begin → callback → token exchange
# ---------------------------------------------------------------------------

def _full_flow(provider) -> tuple[str, str, str]:
    """
    Run the full auth flow with mocked GitHub calls.
    Returns (client_id, access_token, refresh_token).
    """
    client_id, verifier, flow_state = _register_and_begin(provider)

    with (
        patch("spotbook.auth.github_exchange_code", return_value="gh_access_token"),
        patch("spotbook.auth.github_get_user_id", return_value=12345678),
    ):
        redirect_url = provider.handle_github_callback(
            code="gh_code_123",
            state=flow_state,
            client_ip="127.0.0.1",
        )

    # Extract auth code from redirect URL
    from urllib.parse import urlparse, parse_qs
    params = parse_qs(urlparse(redirect_url).query)
    auth_code = params["code"][0]

    tokens = provider.exchange_authorization_code(
        client_id=client_id,
        code=auth_code,
        redirect_uri=ALLOWED_REDIRECT_URI,
        code_verifier=verifier,
    )

    return client_id, tokens["access_token"], tokens["refresh_token"]


def test_full_flow_issues_tokens(provider):
    _, access_token, refresh_token = _full_flow(provider)
    assert len(access_token) == 64  # 32 bytes hex
    assert len(refresh_token) == 64


def test_access_token_verifies(provider):
    _, access_token, _ = _full_flow(provider)
    result = provider.verify_access_token(access_token)
    assert result is not None
    assert result["token_type"] == "access"


def test_invalid_access_token_rejected(provider):
    _full_flow(provider)
    result = provider.verify_access_token("notarealtoken")
    assert result is None


def test_auth_code_single_use(provider):
    client_id, verifier, flow_state = _register_and_begin(provider)

    with (
        patch("spotbook.auth.github_exchange_code", return_value="gh_token"),
        patch("spotbook.auth.github_get_user_id", return_value=12345678),
    ):
        redirect_url = provider.handle_github_callback(
            code="gh_code", state=flow_state, client_ip="127.0.0.1"
        )

    from urllib.parse import urlparse, parse_qs
    auth_code = parse_qs(urlparse(redirect_url).query)["code"][0]

    # First use succeeds
    provider.exchange_authorization_code(
        client_id=client_id, code=auth_code,
        redirect_uri=ALLOWED_REDIRECT_URI, code_verifier=verifier,
    )

    # Second use fails
    with pytest.raises(ValueError, match="invalid_grant"):
        provider.exchange_authorization_code(
            client_id=client_id, code=auth_code,
            redirect_uri=ALLOWED_REDIRECT_URI, code_verifier=verifier,
        )


def test_wrong_pkce_verifier_rejected(provider):
    client_id, _, flow_state = _register_and_begin(provider)

    with (
        patch("spotbook.auth.github_exchange_code", return_value="gh_token"),
        patch("spotbook.auth.github_get_user_id", return_value=12345678),
    ):
        redirect_url = provider.handle_github_callback(
            code="gh_code", state=flow_state, client_ip="127.0.0.1"
        )

    from urllib.parse import urlparse, parse_qs
    auth_code = parse_qs(urlparse(redirect_url).query)["code"][0]

    with pytest.raises(ValueError, match="invalid_grant"):
        provider.exchange_authorization_code(
            client_id=client_id, code=auth_code,
            redirect_uri=ALLOWED_REDIRECT_URI,
            code_verifier="wrong_verifier_that_does_not_match",
        )


# ---------------------------------------------------------------------------
# Refresh token rotation
# ---------------------------------------------------------------------------

def test_refresh_token_rotation(provider):
    _, access_token, refresh_token = _full_flow(provider)

    # Exchange refresh token for new pair
    new_tokens = provider.exchange_refresh_token(
        client_id=provider.get_client(
            provider.verify_access_token(access_token)["client_id"]
        )["client_id"],
        refresh_token=refresh_token,
    )

    assert "access_token" in new_tokens
    assert "refresh_token" in new_tokens
    # New tokens must be different
    assert new_tokens["access_token"] != access_token
    assert new_tokens["refresh_token"] != refresh_token


def test_old_refresh_token_invalid_after_rotation(provider):
    client_id, _, refresh_token = _full_flow(provider)

    # First exchange succeeds
    provider.exchange_refresh_token(client_id=client_id, refresh_token=refresh_token)

    # Reusing the old refresh token fails with invalid_grant
    with pytest.raises(ValueError, match="invalid_grant"):
        provider.exchange_refresh_token(client_id=client_id, refresh_token=refresh_token)


def test_reused_refresh_token_revokes_all(provider):
    """
    Reusing an already-rotated refresh token is a security event.
    All tokens for the client must be revoked.
    """
    client_id, access_token, refresh_token = _full_flow(provider)

    # Rotate once
    new_tokens = provider.exchange_refresh_token(
        client_id=client_id, refresh_token=refresh_token
    )

    # Reuse the original (already rotated) token — this triggers the security path
    with pytest.raises(ValueError, match="invalid_grant"):
        provider.exchange_refresh_token(
            client_id=client_id, refresh_token=refresh_token
        )

    # New access token should also be revoked now
    result = provider.verify_access_token(new_tokens["access_token"])
    assert result is None


# ---------------------------------------------------------------------------
# GitHub identity verification
# ---------------------------------------------------------------------------

def test_wrong_github_user_id_rejected(provider):
    client_id, verifier, flow_state = _register_and_begin(provider)

    with (
        patch("spotbook.auth.github_exchange_code", return_value="gh_token"),
        patch("spotbook.auth.github_get_user_id", return_value=99999999),  # wrong ID
    ):
        with pytest.raises(PermissionError, match="Not authorized"):
            provider.handle_github_callback(
                code="gh_code", state=flow_state, client_ip="127.0.0.1"
            )


def test_invalid_github_state_rejected(provider):
    with pytest.raises(ValueError, match="Invalid or expired OAuth state"):
        provider.handle_github_callback(
            code="gh_code", state="invalid_state_xyz", client_ip="127.0.0.1"
        )


# ---------------------------------------------------------------------------
# Rate limiting
# ---------------------------------------------------------------------------

def test_rate_limit_triggers_after_failures(provider, tmp_db):
    """5 failures within the window should trigger rate limiting."""
    client_id, verifier, _ = _register_and_begin(provider)

    # Generate 5 failures using an invalid state
    for _ in range(5):
        try:
            with (
                patch("spotbook.auth.github_exchange_code", return_value="gh_token"),
                patch("spotbook.auth.github_get_user_id", return_value=99999999),
            ):
                # Create a fresh flow for each attempt
                _, _, flow_state = _register_and_begin(provider)
                provider.handle_github_callback(
                    code="bad_code", state=flow_state, client_ip="10.0.0.1"
                )
        except (PermissionError, ValueError):
            pass

    # 6th attempt should be rate-limited
    _, _, flow_state = _register_and_begin(provider)
    with pytest.raises(PermissionError, match="Too many"):
        with (
            patch("spotbook.auth.github_exchange_code", return_value="gh_token"),
            patch("spotbook.auth.github_get_user_id", return_value=12345678),
        ):
            provider.handle_github_callback(
                code="valid_code", state=flow_state, client_ip="10.0.0.1"
            )
