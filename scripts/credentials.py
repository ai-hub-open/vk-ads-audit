"""
credentials.py — персональная ссылка подключения к MCP VK Рекламы (ключ пути B).

Ссылка вида https://<сервер>/c/<токен> несёт креды в самом адресе: кто её знает,
тот читает кабинет. Поэтому хранится в ~/.vk-ads-audit/credentials.json (вне папки
скилла — обновление плагина её не сотрёт), а наружу выводится только mask().
На Windows доступ определяет ACL профиля пользователя — скрипт его не меняет.

    python -m scripts.credentials set --url <ссылка>
    python -m scripts.credentials show
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

try:
    from scripts._console import setup_console
except ImportError:  # запуск напрямую
    from _console import setup_console

ENV_VAR = "VK_ADS_CONNECTION_URL"
_URL_RE = re.compile(r"^https://[\w-]+(\.[\w-]+)+(/[\w./-]*)?$")


def cred_path() -> Path:
    return Path.home() / ".vk-ads-audit" / "credentials.json"


def get_connection_url() -> str | None:
    env = os.environ.get(ENV_VAR, "").strip()
    if env:
        return env
    path = cred_path()
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    raw = data.get("connection_url")
    if not isinstance(raw, str):
        return None
    return raw.strip() or None


def set_connection_url(url: str) -> Path:
    url = (url or "").strip()
    if not _URL_RE.match(url):
        raise ValueError("Ссылка подключения должна начинаться с https:// и вести на сервер MCP")
    path = cred_path()
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    # 0o600 задаётся при создании файла, чтобы он ни на мгновение не был читаемым другими.
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(json.dumps({"connection_url": url}, ensure_ascii=False, indent=2))
    # Файл мог уже существовать с более широкими правами — O_CREAT их не меняет.
    if os.name != "nt":
        os.chmod(path, 0o600)
    return path


def mask(url: str | None) -> str:
    if not url:
        return "—"
    m = re.match(r"^(https://[^/]+)/(.*)$", url)
    if not m:
        return "***"
    tail = m.group(2)
    return f"{m.group(1)}/…{tail[-4:]}" if len(tail) > 8 else f"{m.group(1)}/…"


def main(argv: list[str] | None = None) -> int:
    setup_console()
    parser = argparse.ArgumentParser(description="Ссылка подключения к MCP VK Рекламы")
    sub = parser.add_subparsers(dest="cmd", required=True)
    p_set = sub.add_parser("set", help="сохранить ссылку")
    p_set.add_argument("--url", required=True)
    sub.add_parser("show", help="показать сохранённую ссылку (маской)")
    args = parser.parse_args(argv)
    if args.cmd == "set":
        url = args.url.strip()
        try:
            path = set_connection_url(url)
        except ValueError as e:
            print(f"✗ {e}")
            return 2
        print(f"✓ ссылка сохранена ({mask(url)}) в {path}")
        return 0
    url = get_connection_url()
    print(f"ссылка: {mask(url)}" if url else "ссылка не сохранена — путь B недоступен")
    return 0


if __name__ == "__main__":
    sys.exit(main())
