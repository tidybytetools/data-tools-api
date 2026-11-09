"""Require Rapid's proxy secret on /v1 routes when configured."""

from __future__ import annotations

import hmac
import os

from fastapi import HTTPException, Request, status

SECRET_ENV = "RAPIDAPI_PROXY_SECRET"
HEADER = "X-RapidAPI-Proxy-Secret"


def proxy_secret() -> str:
    return os.environ.get(SECRET_ENV, "").strip()


def on_render() -> bool:
    return bool(os.environ.get("RENDER") or os.environ.get("RENDER_SERVICE_ID"))


def check_proxy_secret(request: Request) -> None:
    """Reject requests that did not come through the Rapid Runtime.

    - If RAPIDAPI_PROXY_SECRET is set, the header must match.
    - If we are on Render and the secret is missing, lock /v1 so the
      public URL cannot be used until the secret is set.
    - Local tests with no secret and no Render env are allowed.
    """
    expected = proxy_secret()
    if not expected:
        if on_render():
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="This API is not open yet. Set RAPIDAPI_PROXY_SECRET on the host.",
            )
        return
    got = request.headers.get(HEADER, "")
    if not got or not hmac.compare_digest(got, expected):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing or wrong X-RapidAPI-Proxy-Secret header.",
        )
