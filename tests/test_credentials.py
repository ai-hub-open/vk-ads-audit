from __future__ import annotations

import json
import os

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


@pytest.mark.parametrize("payload", ["[]", '"x"', '{"connection_url": 123}'])
def test_wrong_shape_file_returns_none(payload):
    path = credentials.cred_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(payload, encoding="utf-8")
    assert credentials.get_connection_url() is None


@pytest.mark.skipif(os.name == "nt", reason="на Windows права задаёт ACL профиля, chmod не работает")
def test_file_not_group_or_world_readable():
    path = credentials.set_connection_url(URL)
    assert path.stat().st_mode & 0o077 == 0


def test_cli_set_saves_and_does_not_print_url(capsys):
    assert credentials.main(["set", "--url", URL]) == 0
    out = capsys.readouterr().out
    assert URL not in out and "aihub.click.ru" in out
    assert json.loads(credentials.cred_path().read_text(encoding="utf-8")) == {"connection_url": URL}


def test_cli_set_masks_stripped_value(capsys):
    assert credentials.main(["set", "--url", f"  {URL}  "]) == 0
    out = capsys.readouterr().out
    assert credentials.mask(URL) in out
    assert URL not in out


def test_cli_set_invalid_returns_2_and_writes_nothing(capsys):
    assert credentials.main(["set", "--url", "http://x.ru/c/1"]) == 2
    assert not credentials.cred_path().exists()


@pytest.mark.parametrize("bad", ["http://x.ru/c/1", "not a url", "https://localhost/c/1 extra"])
def test_invalid_env_value_returns_none(monkeypatch, bad):
    credentials.set_connection_url(URL)  # валидный файл не должен подменять битый env
    monkeypatch.setenv("VK_ADS_CONNECTION_URL", bad)
    assert credentials.get_connection_url() is None


def test_invalid_file_value_returns_none():
    path = credentials.cred_path()
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"connection_url": "http://insecure.example/c/1"}), encoding="utf-8")
    assert credentials.get_connection_url() is None
