"""Изоляция HOME/env и запрет сети: тесты не ходят в MCP и не трогают ключи разработчика."""
from __future__ import annotations

import socket

import pytest


@pytest.fixture(autouse=True)
def _isolate_home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.delenv("VK_ADS_CONNECTION_URL", raising=False)


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    def deny(*_a, **_kw):
        raise AssertionError("тест полез в сеть — нужен мок")

    monkeypatch.setattr(socket.socket, "connect", deny)
