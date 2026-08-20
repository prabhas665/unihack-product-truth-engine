"""SSRF guard tests: fully offline with injected resolvers and MockTransport.

The guard must never touch the network; every resolution is faked.
"""

from __future__ import annotations

import ipaddress

import httpx
import pytest

from app.sources.retrieval.models import RetrievalError, RetrievalErrorKind
from app.sources.retrieval.ssrf import (
    assert_public_http_url,
    is_private_ip,
)
from app.sources.retrieval.transport import download


class FakeResolver:
    """host -> IP literals; IP literals resolve to themselves."""

    def __init__(self, mapping: dict[str, list[str]]) -> None:
        self._mapping = mapping

    def __call__(self, host: str) -> list[str]:
        if host in self._mapping:
            return self._mapping[host]
        try:
            ipaddress.ip_address(host)
        except ValueError:
            return []
        return [host]


def _public_resolver() -> FakeResolver:
    return FakeResolver(
        {
            "acme-controls.example": ["93.184.216.34"],
            "rebind.example": ["10.0.0.5"],
            "mixed.example": ["93.184.216.34", "192.168.1.1"],
        }
    )


class TestIsPrivateIp:
    @pytest.mark.parametrize(
        "address",
        [
            "127.0.0.1",
            "10.0.0.1",
            "172.16.0.1",
            "192.168.1.1",
            "169.254.169.254",
            "100.64.0.1",
            "0.0.0.0",
            "::1",
            "::",
            "fc00::1",
            "fe80::1",
            "::ffff:127.0.0.1",
        ],
    )
    def test_private(self, address):
        assert is_private_ip(address) is True

    @pytest.mark.parametrize(
        "address",
        ["8.8.8.8", "93.184.216.34", "1.2.3.4", "2001:4860:4860::8888", "::ffff:8.8.8.8"],
    )
    def test_public(self, address):
        assert is_private_ip(address) is False


class TestAssertPublicHttpUrl:
    def test_public_hostname_ok(self):
        assert_public_http_url(
            "https://acme-controls.example/products/m1", resolver=_public_resolver()
        )

    def test_public_ip_literal_ok(self):
        assert_public_http_url("https://8.8.8.8/x", resolver=_public_resolver())

    def test_private_ip_literal_blocked(self):
        with pytest.raises(RetrievalError) as exc:
            assert_public_http_url("http://127.0.0.1/x", resolver=_public_resolver())
        assert exc.value.kind is RetrievalErrorKind.UNSAFE_URL

    def test_metadata_ip_blocked(self):
        with pytest.raises(RetrievalError) as exc:
            assert_public_http_url(
                "http://169.254.169.254/latest/meta-data",
                resolver=_public_resolver(),
            )
        assert exc.value.kind is RetrievalErrorKind.UNSAFE_URL

    def test_localhost_blocked(self):
        with pytest.raises(RetrievalError):
            assert_public_http_url("http://localhost/x", resolver=_public_resolver())

    def test_single_label_hostname_blocked(self):
        with pytest.raises(RetrievalError) as exc:
            assert_public_http_url("http://internal/x", resolver=_public_resolver())
        assert exc.value.kind is RetrievalErrorKind.UNSAFE_URL

    def test_non_http_scheme_blocked(self):
        with pytest.raises(RetrievalError):
            assert_public_http_url("file:///etc/passwd", resolver=_public_resolver())
        with pytest.raises(RetrievalError):
            assert_public_http_url("ftp://example.com/x", resolver=_public_resolver())

    def test_dns_rebinding_to_private_blocked(self):
        with pytest.raises(RetrievalError) as exc:
            assert_public_http_url(
                "https://rebind.example/x", resolver=_public_resolver()
            )
        assert "10.0.0.5" in str(exc.value)
        assert exc.value.kind is RetrievalErrorKind.UNSAFE_URL

    def test_mixed_public_and_private_answers_blocked(self):
        with pytest.raises(RetrievalError):
            assert_public_http_url(
                "https://mixed.example/x", resolver=_public_resolver()
            )

    def test_unresolvable_host_fails_closed(self):
        with pytest.raises(RetrievalError) as exc:
            assert_public_http_url("https://nx.example/x", resolver=FakeResolver({}))
        assert exc.value.kind is RetrievalErrorKind.UNSAFE_URL


class TestDownloadGuard:
    def _limits(self):
        return type("L", (), {"timeout_seconds": 5, "user_agent": "ua"})()

    def test_download_rejects_private_url_before_request(self):
        def handler(request: httpx.Request) -> httpx.Response:
            raise AssertionError("request must never be sent")

        with pytest.raises(RetrievalError) as exc:
            download(
                "http://127.0.0.1/admin",
                self._limits(),
                1024,
                transport=httpx.MockTransport(handler),
                resolver=_public_resolver(),
            )
        assert exc.value.kind is RetrievalErrorKind.UNSAFE_URL

    def test_download_fetches_public_url(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, text="ok", headers={"content-type": "text/html"})

        content_type, _, body = download(
            "https://acme-controls.example/products/m1",
            self._limits(),
            1024,
            transport=httpx.MockTransport(handler),
            resolver=_public_resolver(),
        )
        assert content_type == "text/html"
        assert body == b"ok"

    def test_redirect_to_private_url_blocked(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                302,
                headers={"location": "http://169.254.169.254/latest/meta-data"},
            )

        with pytest.raises(RetrievalError) as exc:
            download(
                "https://acme-controls.example/products/m1",
                self._limits(),
                1024,
                transport=httpx.MockTransport(handler),
                resolver=_public_resolver(),
            )
        assert exc.value.kind is RetrievalErrorKind.UNSAFE_URL

    def test_redirect_to_public_url_ok(self):
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/final":
                return httpx.Response(
                    200, text="ok", headers={"content-type": "text/html"}
                )
            return httpx.Response(
                302, headers={"location": "https://acme-controls.example/final"}
            )

        _, final_url, body = download(
            "https://acme-controls.example/start",
            self._limits(),
            1024,
            transport=httpx.MockTransport(handler),
            resolver=_public_resolver(),
        )
        assert final_url.endswith("/final")
        assert body == b"ok"

    def test_guard_off_when_mock_transport_without_resolver(self):
        """Existing offline test path: injected transport alone disables the
        guard (no DNS resolution is possible in hermetic tests)."""

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, text="body", headers={"content-type": "text/html"})

        result = download(
            "https://example.invalid/product",
            self._limits(),
            1024,
            transport=httpx.MockTransport(handler),
        )
        assert result[2] == b"body"