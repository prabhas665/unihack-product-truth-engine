"""Shared, size-capped HTTP download with typed error mapping.

Common plumbing for the HTML and PDF fetchers: GET with redirects, timeout,
and a hard size cap enforced while streaming. All failures are mapped to
typed RetrievalError kinds (timeout, network, size limit, HTTP status).
"""

from __future__ import annotations

import httpx

from app.sources.retrieval.limits import RetrievalLimits
from app.sources.retrieval.models import RetrievalError, RetrievalErrorKind


def download(
    url: str,
    limits: RetrievalLimits,
    max_bytes: int,
    transport: httpx.BaseTransport | None = None,
) -> tuple[str, str, bytes]:
    """Return (content_type, final_url, body).

    `transport` is injected by tests (httpx.MockTransport) to keep the suite
    fully offline; production uses the real network transport.
    """
    client_kwargs: dict = {
        "timeout": httpx.Timeout(limits.timeout_seconds),
        "follow_redirects": True,
        "headers": {"User-Agent": limits.user_agent},
    }
    if transport is not None:
        client_kwargs["transport"] = transport

    content_type = ""
    final_url = ""
    chunks: list[bytes] = []
    try:
        with httpx.Client(**client_kwargs) as client:
            with client.stream("GET", url) as response:
                if response.status_code >= 400:
                    raise RetrievalError(
                        RetrievalErrorKind.HTTP_STATUS,
                        f"HTTP {response.status_code} for {url}",
                    )
                content_type = (
                    response.headers.get("content-type", "")
                    .split(";")[0]
                    .strip()
                    .lower()
                )
                size = 0
                for chunk in response.iter_bytes():
                    size += len(chunk)
                    if size > max_bytes:
                        raise RetrievalError(
                            RetrievalErrorKind.SIZE_LIMIT,
                            f"response exceeds {max_bytes} bytes",
                        )
                    chunks.append(chunk)
                final_url = str(response.url)
    except RetrievalError:
        raise
    except httpx.TimeoutException as exc:
        raise RetrievalError(
            RetrievalErrorKind.TIMEOUT,
            f"timeout after {limits.timeout_seconds}s for {url}",
        ) from exc
    except httpx.TransportError as exc:
        raise RetrievalError(
            RetrievalErrorKind.NETWORK,
            f"transport error for {url}: {exc}",
        ) from exc
    return content_type, final_url, b"".join(chunks)
