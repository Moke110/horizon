"""Release artifact sources: local paths, file:// URLs and https:// downloads.

Spec #92 says ``install package --from`` accepts a local file or a URL.
``hostrun.release.resolve_release_source`` is the one release helper that turns
such a source into a local file; it is pure apart from an injectable ``urlopen``,
so every test here is offline.
"""

from __future__ import annotations

import shutil
import urllib.error

import pytest

from hostrun import release

ARTIFACT_URL = (
    "https://github.com/Moke110/horizon/releases/download/"
    "v0.1.0/horizon-0.1.0-stable.zip"
)


class _FakeUrlResponse:
    """Minimal stand-in for ``urllib.request.urlopen``'s response."""

    def __init__(self, data: bytes, *, closed: bool = False):
        self._data = data
        self.closed = closed

    def read(self, size: int = -1) -> bytes:
        if size is None or size < 0:
            data, self._data = self._data, b""
            return data
        data, self._data = self._data[:size], self._data[size:]
        return data

    def close(self) -> None:
        self.closed = True


def test_resolve_release_source_returns_local_paths_and_file_urls(tmp_path, monkeypatch):
    artifact = tmp_path / "horizon-0.1.0-stable.zip"
    artifact.write_bytes(b"PK artifact")

    assert release.resolve_release_source(str(artifact)) == artifact.resolve()
    assert release.resolve_release_source(artifact.as_uri()) == artifact.resolve()
    monkeypatch.chdir(tmp_path)
    assert release.resolve_release_source(artifact.name) == artifact.resolve()


def test_resolve_release_source_downloads_an_https_url_with_a_timeout(tmp_path):
    seen: dict[str, object] = {}

    def fake_urlopen(url, timeout=None):
        seen["url"] = url
        seen["timeout"] = timeout
        return _FakeUrlResponse(b"PK downloaded artifact")

    downloaded = release.resolve_release_source(
        ARTIFACT_URL,
        download_dir=tmp_path / "downloads",
        timeout=7.5,
        urlopen=fake_urlopen,
    )

    assert downloaded == (tmp_path / "downloads" / "horizon-0.1.0-stable.zip").resolve()
    assert downloaded.read_bytes() == b"PK downloaded artifact"
    assert downloaded.parent.is_dir()
    assert seen == {"url": ARTIFACT_URL, "timeout": 7.5}


def test_resolve_release_source_downloads_into_a_fresh_temp_dir_by_default(tmp_path):
    def fake_urlopen(url, timeout=None):
        return _FakeUrlResponse(b"PK artifact")

    downloaded = release.resolve_release_source(ARTIFACT_URL, urlopen=fake_urlopen)
    try:
        assert downloaded.is_file()
        assert downloaded.name == "horizon-0.1.0-stable.zip"
    finally:
        shutil.rmtree(downloaded.parent, ignore_errors=True)


@pytest.mark.parametrize(
    ("source", "reason"),
    [
        pytest.param("http://example.com/horizon.zip", "https", id="plain-http"),
        pytest.param("ftp://example.com/horizon.zip", "https", id="ftp"),
        pytest.param("https://example.com/releases/", "filename", id="no-filename"),
        pytest.param("https:///horizon.zip", "host", id="no-host"),
        pytest.param("https://example.com/a b.zip", "whitespace", id="whitespace"),
        pytest.param("", "empty", id="empty"),
    ],
)
def test_resolve_release_source_refuses_unsupported_or_malformed_sources(source, reason):
    with pytest.raises(release.ReleaseError) as excinfo:
        release.resolve_release_source(source)
    assert reason in str(excinfo.value).lower()


def test_resolve_release_source_reports_download_failures_and_empty_bodies(tmp_path):
    def failing_urlopen(url, timeout=None):
        raise urllib.error.URLError("connection refused")

    with pytest.raises(release.ReleaseError) as excinfo:
        release.resolve_release_source(
            ARTIFACT_URL, download_dir=tmp_path / "a", urlopen=failing_urlopen
        )
    message = str(excinfo.value)
    assert "could not download" in message.lower()
    assert "connection refused" in message

    def empty_urlopen(url, timeout=None):
        return _FakeUrlResponse(b"")

    with pytest.raises(release.ReleaseError) as excinfo:
        release.resolve_release_source(
            ARTIFACT_URL, download_dir=tmp_path / "b", urlopen=empty_urlopen
        )
    assert "empty" in str(excinfo.value).lower()
