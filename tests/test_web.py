"""Tests for the web tool's untrusted marking and host filtering."""

from __future__ import annotations

from typing import Any

import pytest

from jpe.tools.builtin import web


async def test_fetch_marks_output_untrusted(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(web, "_is_public_host", lambda host: True)
    monkeypatch.setattr(
        web, "_fetch", lambda url, timeout, limit: ("<p>hello</p>", "text/html", 200)
    )
    result = await web.FetchUrlTool().run(url="https://example.com")
    assert result.untrusted_source == "https://example.com"
    assert "hello" in result.content
    assert "<untrusted" in result.for_model()


async def test_fetch_rejects_non_public_host(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(web, "_is_public_host", lambda host: False)
    result = await web.FetchUrlTool().run(url="http://127.0.0.1/admin")
    assert result.is_error is True
    assert "non-public host" in result.content


async def test_fetch_rejects_bad_scheme() -> None:
    result = await web.FetchUrlTool().run(url="file:///etc/passwd")
    assert result.is_error is True
    assert "Unsupported URL scheme" in result.content


async def test_fetch_truncates(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(web, "_is_public_host", lambda host: True)
    monkeypatch.setattr(web, "_fetch", lambda url, timeout, limit: ("x" * 500, "text/plain", 200))
    result = await web.FetchUrlTool().run(url="https://example.com", max_chars=100)
    assert "truncated at 100 characters" in result.content


def test_text_extractor_strips_scripts() -> None:
    extractor = web._TextExtractor()
    extractor.feed(
        "<html><head><title>t</title></head><body><script>bad()</script><p>good</p></body></html>"
    )
    text = extractor.text()
    assert "good" in text
    assert "bad()" not in text
    assert "t" not in text


@pytest.mark.parametrize(
    "host",
    ["127.0.0.1", "10.0.0.1", "192.168.1.1", "169.254.1.1", "::1"],
)
def test_private_hosts_are_rejected(host: str) -> None:
    assert web._is_public_host(host) is False


def test_public_host_is_accepted(monkeypatch: pytest.MonkeyPatch) -> None:
    """Resolve a public address without touching DNS, so the test is offline-safe."""
    monkeypatch.setattr(
        web.socket,
        "getaddrinfo",
        lambda host, port: [
            (web.socket.AF_INET, web.socket.SOCK_STREAM, 6, "", ("93.184.216.34", 0))
        ],
    )
    assert web._is_public_host("example.com") is True


def test_unresolvable_host_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail(host: str, port: object) -> list[object]:
        raise web.socket.gaierror("no such host")

    monkeypatch.setattr(web.socket, "getaddrinfo", fail)
    assert web._is_public_host("nonexistent.invalid") is False


def _unused(_: Any) -> None:  # pragma: no cover - keeps ANN lint quiet on imports
    return None
