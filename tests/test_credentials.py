from __future__ import annotations

import pytest

from scripts import credentials

URL = "https://vkads-mcp.aihub.click.ru/c/0123456789abcdef0123456789abcdef"


def test_roundtrip_under_home():
    path = credentials.set_connection_url(URL)
    assert path == credentials.cred_path()
    assert ".vk-ads-audit" in str(path)
    assert credentials.get_connection_url() == URL


def test_env_overrides_file(monkeypatch):
    credentials.set_connection_url(URL)
    monkeypatch.setenv("VK_ADS_CONNECTION_URL", "https://example.org/c/zzz")
    assert credentials.get_connection_url() == "https://example.org/c/zzz"


def test_missing_returns_none():
    assert credentials.get_connection_url() is None


@pytest.mark.parametrize("bad", ["http://x.ru/c/1", "vkads-mcp/c/1", "", "https://localhost/c/1"])
def test_invalid_rejected(bad):
    with pytest.raises(ValueError):
        credentials.set_connection_url(bad)


def test_mask_hides_token():
    masked = credentials.mask(URL)
    assert "0123456789abcdef0123" not in masked
    assert masked.startswith("https://vkads-mcp.aihub.click.ru/")
    assert credentials.mask(None) == "—"


def test_cli_show_masks(capsys):
    credentials.set_connection_url(URL)
    assert credentials.main(["show"]) == 0
    out = capsys.readouterr().out
    assert URL not in out and "aihub.click.ru" in out
