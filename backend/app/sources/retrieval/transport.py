"""Shared, size-capped HTTP download with typed error mapping.

Common plumbing for the HTML and PDF fetchers: GET with redirects, timeout,
and a hard size cap enforced while streaming. All failures are mapped to
typed RetrievalError kinds (timeout, network, size limit, HTTP status).
"""

from __future__ import annotations

import ssl
from pathlib import Path

import certifi
import httpx

from app.sources.retrieval.limits import RetrievalLimits
from app.sources.retrieval.models import RetrievalError, RetrievalErrorKind


_EXTRA_CA_PEM = Path(__file__).parent / "certs" / "godaddy-g2-intermediate.pem"


def _verified_verify_arg() -> ssl.SSLContext | None:
    """Return a fully-verified SSL context that additionally trusts the
    vendored GoDaddy G2 intermediate (only when the PEM is present).

    The default certifi store is used as the baseline and verification stays
    fully enabled (CERT_REQUIRED). This only replenishes the intermediate that
    makitatools.com omits from its server chain; it adds no new trust root and
    never disables verification. Returns None when the PEM is unavailable so
    behavior matches the previous certifi-only path.
    """
    if not _EXTRA_CA_PEM.is_file():
        return None
    try:
        context = ssl.create_default_context()
    except (ssl.SSLError, OSError):
        # Fallback for environments where the OS trust store cannot be
        # enumerated (e.g. sandboxed Windows builds). The certifi bundle is
        # the same baseline httpx uses by default, so behavior is unchanged.
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        context.load_verify_locations(cafile=certifi.where())
    context.load_verify_locations(cafile=str(_EXTRA_CA_PEM))
    return context


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
    else:
        verify = _verified_verify_arg()
        if verify is not None:
            client_kwargs["verify"] = verify

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
