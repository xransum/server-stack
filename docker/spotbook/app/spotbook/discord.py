"""
discord.py — Shared Discord webhook notification helper.

Usage:
    from .discord import notify
    notify(webhook_url, ":warning: something happened")

If webhook_url is empty or None, the call is a no-op.
Never log tokens, passphrases, raw webhook URLs, or personal data.
"""

from __future__ import annotations

import json
import logging
import urllib.request

log = logging.getLogger(__name__)


def notify(webhook_url: str | None, message: str) -> None:
    """Post a plain-text message to a Discord webhook. Silently ignores errors."""
    if not webhook_url:
        return
    try:
        data = json.dumps({"content": message}).encode()
        req = urllib.request.Request(
            webhook_url,
            data=data,
            headers={
                "Content-Type": "application/json",
                "User-Agent": "spotbook/1.0",
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=10):
            pass
    except Exception as e:
        log.warning("Discord notify failed: %s", e)
