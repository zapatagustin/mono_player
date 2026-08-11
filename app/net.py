"""One AsyncClient app-wide: HTTP/2 multiplexing over pooled connections.
A client per request costs a TLS handshake each time (GUIDELINE.org)."""

import asyncio

import httpx

USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36"
    " (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)

# ~8 concurrent thumbnail fetches; more saturates, doesn't speed up.
THUMB_SEMAPHORE = asyncio.Semaphore(8)


def make_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        http2=True,
        headers={"user-agent": USER_AGENT},
        timeout=15.0,
        follow_redirects=True,
    )
