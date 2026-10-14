# vk-ads-audit Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Read-only скилл аудита кабинета VK Рекламы: сбор данных из MCP `vk-ads` → нормализация → диагностика → HTML-отчёт, плюс быстрый анализ и необязательная Метрика.

**Architecture:** Python-скрипты без внешних зависимостей (stdlib) делают всю арифметику; модель ведёт маршрут по `SKILL.md`, гейты и выводы. Путь B — `collect.py` сам ходит в MCP по HTTP (JSON-RPC) с белым списком инструментов чтения; путь A — модель кладёт ответы MCP в тот же формат `raw/`. Единицы приводятся только в `normalize.py`; пороги — в `references/thresholds.json`.

**Tech Stack:** Python 3.9+ (stdlib: `json`, `urllib`, `argparse`, `statistics`, `html`, `zipfile`), pytest (только для разработки), MCP vk-ads 0.11.0 (streamable HTTP, JSON-RPC 2.0).

**Spec:** `docs/superpowers/specs/2026-10-14-vk-ads-audit-design.md`

## Global Constraints

- Скрипты — только stdlib, Python 3.9+; `from __future__ import annotations` в каждом модуле.
- Запуск скриптов — `python -m scripts.<имя>` из папки скилла; первой строкой `main()` — `setup_console()`.
- Только чтение: любой вызов MCP идёт через `McpClient.call`, вне `ALLOWED_TOOLS` — `ForbiddenToolError` до сети.
- Единицы денег/процентов приводятся только в `scripts/normalize.py`.
- Рабочая папка `<папка пользователя>/vk-audits/<slug>-<YYYY-MM-DD>/`, абсолютный путь, внутри скилла — отказ.
- Ссылка подключения (ключ пути B) никогда не печатается и не пишется в файлы целиком — только `mask()`.
- Тексты пользователю — на русском. `SKILL.md` ≤ 400 строк.
- Период аудита заканчивается не позже вчерашнего дня.
- Сеть в тестах закрыта (`tests/conftest.py`).

## Review Focus

1. Кабинет больше одной страницы списка (>250 групп/объявлений) — всё собирается постранично, ничего не теряется (Task 3, `test_paginate_collects_all_pages`; Task 5, `test_many_groups_chunked`).
2. Сущности без статистики за период отсутствуют в `items` ответа — считаются нулями, а не падают (Task 6, `test_missing_stats_are_zero`).
3. Нет целевого CPA и ни одной группы с конверсиями — правила не делят на ноль и не молчат (Task 8, `test_no_reference_cpa_uses_min_spend`).
4. Файл `raw/`, написанный моделью на пути A, битый или без ключей — понятная ошибка с именем файла (Task 4, `test_malformed_raw_names_file`).
5. Ссылка подключения не утекает в `raw/`, `collect_log.json` и тексты ошибок (Task 3, `test_error_does_not_leak_url`; Task 5, `test_cli_does_not_write_secret`).

---

## File Structure

```
vk-ads-audit/
  SKILL.md                         маршрут, режимы, гейты (Task 12)
  VERSION, CHANGELOG.md, README.md, LICENSE, .gitignore, pytest.ini, requirements-dev.txt
  .claude-plugin/plugin.json       (Task 12)
  .github/workflows/version-check.yml (Task 12)
  references/
    mcp-read-tools.md              (Task 12)
    diagnostic-rules.md            (Task 12)
    thresholds.json                (Task 7, дополняется в 8, 11)
    metrika-vk.md                  (Task 11)
    limitations.md                 (Task 12)
    report-template.md             (Task 12)
  scripts/
    __init__.py, _console.py, _workspace.py   (Task 1)
    credentials.py                 (Task 2)
    mcp_client.py                  (Task 3)
    raw_store.py                   (Task 4)
    collect.py                     (Task 5)
    normalize.py                   (Task 6, Метрика — Task 11)
    diagnose.py                    (Task 7, 8, 11)
    render_report.py               (Task 9)
    quick.py                       (Task 10)
    make_bundle.py                 (Task 12)
  evals/
    fixtures.py, run_evals.py      (Task 13)
  tests/
    conftest.py                    (Task 1)
    test_*.py                      по задачам
```

---

### Task 1: Каркас репозитория

**Files:**
- Create: `.gitignore`, `VERSION`, `pytest.ini`, `requirements-dev.txt`, `LICENSE` (копия), `scripts/__init__.py`, `scripts/_console.py` (копия), `scripts/_workspace.py`, `tests/__init__.py`, `tests/conftest.py`
- Test: `tests/test_workspace.py`

**Interfaces:**
- Produces: `scripts._console.setup_console() -> None`; `scripts._workspace.SKILL_ROOT: Path`, `is_inside_skill(path) -> bool`, `resolve_workspace(raw, what="Рабочая папка") -> Path` (выход `SystemExit` со строкой, если путь внутри скилла).

- [ ] **Step 1: Файлы каркаса**

```bash
cd D:/vk-ads-audit
cp D:/vk-ads-manager/LICENSE LICENSE
mkdir -p scripts tests references evals
cp D:/vk-ads-manager/scripts/_console.py scripts/_console.py
printf '0.1.0\n' > VERSION
: > scripts/__init__.py
: > tests/__init__.py
```

`.gitignore`:
```
__pycache__/
.pytest_cache/
*.pyc
.env
.mcp.json
vk-audits/
dist/
*.skill
*.zip
```

`pytest.ini`:
```ini
[pytest]
testpaths = tests
addopts = -q
```

`requirements-dev.txt`:
```
pytest>=7
```

`scripts/_workspace.py`:
```python
"""
_workspace.py — рабочая папка аудита всегда вне папки скилла.

`python -m scripts.<имя>` запускается только из папки скилла, поэтому относительный
`--workspace` лёг бы внутрь скилла. Скилл, установленный плагином Claude Code, при
обновлении заменяется целиком — данные клиента пропали бы вместе со старой версией.
"""
from __future__ import annotations

import sys
from pathlib import Path

SKILL_ROOT = Path(__file__).resolve().parent.parent


def is_inside_skill(path) -> bool:
    try:
        Path(path).resolve().relative_to(SKILL_ROOT)
    except ValueError:
        return False
    return True


def resolve_workspace(raw, what: str = "Рабочая папка") -> Path:
    """Абсолютный путь; внутри скилла — остановка с объяснением."""
    path = Path(raw).expanduser().resolve()
    if is_inside_skill(path):
        sys.exit(
            f"{what} {path} лежит внутри папки скилла ({SKILL_ROOT}).\n"
            "При обновлении плагина папка скилла заменяется целиком — данные клиента пропадут.\n"
            "Передай абсолютный путь вне скилла: <папка пользователя>/vk-audits/<slug>-<дата>"
        )
    return path
```

`tests/conftest.py`:
```python
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
```

- [ ] **Step 2: Написать тест**

`tests/test_workspace.py`:
```python
from __future__ import annotations

import pytest

from scripts._workspace import SKILL_ROOT, is_inside_skill, resolve_workspace


def test_outside_path_absolute(tmp_path):
    ws = tmp_path / "vk-audits" / "acme-2026-10-14"
    assert resolve_workspace(ws) == ws.resolve()


def test_relative_from_skill_dir_rejected(monkeypatch):
    monkeypatch.chdir(SKILL_ROOT)
    with pytest.raises(SystemExit) as exc:
        resolve_workspace("vk-audits/acme")
    assert "внутри папки скилла" in str(exc.value.code)


def test_sibling_with_common_prefix_is_outside():
    assert not is_inside_skill(SKILL_ROOT.parent / (SKILL_ROOT.name + "-data"))
```

- [ ] **Step 3: Прогнать**

Run: `python -m pytest tests/test_workspace.py -v`
Expected: 3 passed.

- [ ] **Step 4: Commit**

```bash
git add .
git commit -m "Каркас: рабочая папка вне скилла, UTF-8 консоли, изоляция тестов"
```

---

### Task 2: Реестр ссылки подключения

**Files:**
- Create: `scripts/credentials.py`
- Test: `tests/test_credentials.py`

**Interfaces:**
- Produces: `cred_path() -> Path`; `get_connection_url() -> str | None` (env `VK_ADS_CONNECTION_URL` важнее файла); `set_connection_url(url: str) -> Path` (`ValueError` на не-https); `mask(url: str | None) -> str`. CLI: `python -m scripts.credentials set --url <URL>` / `show`.

- [ ] **Step 1: Тест**

`tests/test_credentials.py`:
```python
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
```

- [ ] **Step 2: Убедиться, что падает**

Run: `python -m pytest tests/test_credentials.py -v`
Expected: FAIL — `ImportError: cannot import name 'credentials'`.

- [ ] **Step 3: Реализация**

`scripts/credentials.py`:
```python
"""
credentials.py — персональная ссылка подключения к MCP VK Рекламы (ключ пути B).

Ссылка вида https://<сервер>/c/<токен> несёт креды в самом адресе: кто её знает,
тот читает кабинет. Поэтому хранится в ~/.vk-ads-audit/credentials.json (вне папки
скилла — обновление плагина её не сотрёт), а наружу выводится только mask().

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
    url = (data.get("connection_url") or "").strip()
    return url or None


def set_connection_url(url: str) -> Path:
    url = (url or "").strip()
    if not _URL_RE.match(url):
        raise ValueError("Ссылка подключения должна начинаться с https:// и вести на сервер MCP")
    path = cred_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"connection_url": url}, ensure_ascii=False, indent=2), encoding="utf-8")
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
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
        try:
            path = set_connection_url(args.url)
        except ValueError as e:
            print(f"✗ {e}")
            return 2
        print(f"✓ ссылка сохранена ({mask(args.url)}) в {path}")
        return 0
    url = get_connection_url()
    print(f"ссылка: {mask(url)}" if url else "ссылка не сохранена — путь B недоступен")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Прогнать**

Run: `python -m pytest tests/test_credentials.py -v`
Expected: 9 passed.

- [ ] **Step 5: Commit**

```bash
git add scripts/credentials.py tests/test_credentials.py
git commit -m "credentials: ссылка подключения MCP в ~/.vk-ads-audit, вывод только маской"
```

---

### Task 3: MCP-клиент с белым списком чтения

**Files:**
- Create: `scripts/mcp_client.py`
- Test: `tests/test_mcp_client.py`

**Interfaces:**
- Produces: `ALLOWED_TOOLS: frozenset[str]`; `class McpError(Exception)` с атрибутом `status: int | None`; `class ForbiddenToolError(McpError)`; `parse_body(content_type: str, text: str) -> dict`; `http_transport(url: str, payload: dict, timeout: float = 60.0) -> dict`; `class McpClient(url, *, account_id=None, transport=None, sleep=time.sleep, retries=3, backoff=2.0)` с методами `call(tool: str, arguments: dict | None = None) -> dict | list` и `paginate(tool, arguments=None, *, page_size=250, max_pages=200) -> list[dict]`.

Факты сервера (сняты 2026-10-14 с vk-ads-mcp 0.11.0): ответ `{"result": {"content": [{"type": "text", "text": "<JSON>"}], "isError": true?}}`; ошибка инструмента — `isError: true`, текст либо JSON `{"error": true, "status": 400, "detail": ...}`, либо строка; Content-Type `application/json`, но клиент обязан понимать и `text/event-stream`.

- [ ] **Step 1: Тест**

`tests/test_mcp_client.py`:
```python
from __future__ import annotations

import json

import pytest

from scripts.mcp_client import (ALLOWED_TOOLS, ForbiddenToolError, McpClient, McpError,
                                parse_body)

SECRET_URL = "https://vkads-mcp.aihub.click.ru/c/SECRET0123456789"

# Все инструменты записи vk-ads-mcp 0.11.0 (tools/list от 2026-10-14).
WRITE_TOOLS = [
    "vk_ads_token_revoke", "vk_ads_ad_plans_create", "vk_ads_ad_plans_update", "vk_ads_ad_plans_delete",
    "vk_ads_campaigns_create", "vk_ads_campaigns_update", "vk_ads_campaigns_set_status",
    "vk_ads_campaigns_delete", "vk_ads_ad_groups_create", "vk_ads_ad_groups_update",
    "vk_ads_ad_groups_delete", "vk_ads_banners_update", "vk_ads_banners_remoderate",
    "vk_ads_banners_delete", "vk_ads_content_upload_image", "vk_ads_content_upload_video",
    "vk_ads_remarketing_segments_create", "vk_ads_remarketing_segments_update",
    "vk_ads_remarketing_segments_delete", "vk_ads_remarketing_pixels_create",
    "vk_ads_remarketing_pixels_delete", "vk_ads_users_lists_create", "vk_ads_users_lists_upload_items",
    "vk_ads_users_lists_delete", "vk_ads_agency_clients_create",
]


def ok(obj):
    return {"jsonrpc": "2.0", "id": 1, "result": {"content": [{"type": "text", "text": json.dumps(obj)}]}}


def tool_error(obj_or_text):
    text = obj_or_text if isinstance(obj_or_text, str) else json.dumps(obj_or_text)
    return {"jsonrpc": "2.0", "id": 1, "result": {"content": [{"type": "text", "text": text}], "isError": True}}


class FakeTransport:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.payloads = []

    def __call__(self, url, payload):
        self.payloads.append(payload)
        r = self.responses.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


def test_allowed_and_write_disjoint():
    assert not (ALLOWED_TOOLS & set(WRITE_TOOLS))


@pytest.mark.parametrize("tool", WRITE_TOOLS)
def test_write_tool_refused_before_network(tool):
    transport = FakeTransport()
    client = McpClient(SECRET_URL, transport=transport)
    with pytest.raises(ForbiddenToolError):
        client.call(tool, {})
    assert transport.payloads == []


def test_account_id_injected_where_supported():
    transport = FakeTransport(ok({"items": []}), ok({"accounts": []}))
    client = McpClient(SECRET_URL, account_id=652819, transport=transport)
    client.call("vk_ads_campaigns_list", {"fields": "id"})
    client.call("vk_ads_accounts_list")
    assert transport.payloads[0]["params"]["arguments"]["account_id"] == "652819"
    assert "account_id" not in transport.payloads[1]["params"]["arguments"]


def test_rate_limit_retried():
    sleeps = []
    transport = FakeTransport(tool_error({"error": True, "status": 429, "detail": "rate_limit_exceeded"}),
                              ok({"ok": True}))
    client = McpClient(SECRET_URL, transport=transport, sleep=sleeps.append)
    assert client.call("vk_ads_auth_check") == {"ok": True}
    assert sleeps == [2.0]


def test_validation_error_not_retried():
    transport = FakeTransport(tool_error({"error": True, "status": 400,
                                          "detail": {"error": {"message": "Validation failed"}}}))
    client = McpClient(SECRET_URL, transport=transport, sleep=lambda s: None)
    with pytest.raises(McpError) as exc:
        client.call("vk_ads_statistics_summary", {"entity": "campaigns", "ids": "1"})
    assert exc.value.status == 400
    assert "Validation failed" in str(exc.value)
    assert len(transport.payloads) == 1


def test_plain_text_error():
    transport = FakeTransport(tool_error("Инструмент 'x' не найден"))
    with pytest.raises(McpError, match="не найден"):
        McpClient(SECRET_URL, transport=transport).call("vk_ads_auth_check")


def test_paginate_collects_all_pages():
    pages = [ok({"count": 600, "items": [{"id": i} for i in range(s, min(s + 250, 600))]})
             for s in (0, 250, 500)]
    transport = FakeTransport(*pages)
    items = McpClient(SECRET_URL, transport=transport).paginate("vk_ads_banners_list", {"fields": "id"})
    assert [i["id"] for i in items] == list(range(600))
    assert [p["params"]["arguments"]["offset"] for p in transport.payloads] == [0, 250, 500]


def test_paginate_without_count_stops_on_short_page():
    transport = FakeTransport(ok({"items": [{"id": 1}, {"id": 2}]}))
    items = McpClient(SECRET_URL, transport=transport).paginate("vk_ads_remarketing_pixels_list")
    assert len(items) == 2 and len(transport.payloads) == 1


def test_error_does_not_leak_url():
    transport = FakeTransport(McpError("HTTP 401", status=401))
    with pytest.raises(McpError) as exc:
        McpClient(SECRET_URL, transport=transport).call("vk_ads_auth_check")
    assert "SECRET" not in str(exc.value)


def test_parse_body_sse():
    text = 'event: message\ndata: {"jsonrpc":"2.0","id":1,"result":{}}\n\n'
    assert parse_body("text/event-stream", text)["id"] == 1


def test_parse_body_json():
    assert parse_body("application/json; charset=utf-8", '{"id": 2}') == {"id": 2}
```

- [ ] **Step 2: Убедиться, что падает**

Run: `python -m pytest tests/test_mcp_client.py -v`
Expected: FAIL — `ModuleNotFoundError: scripts.mcp_client`.

- [ ] **Step 3: Реализация**

`scripts/mcp_client.py`:
```python
"""
mcp_client.py — клиент MCP VK Рекламы по HTTP (JSON-RPC tools/call) только на чтение.

Белый список ALLOWED_TOOLS — гарантия «аудит ничего не меняет в кабинете»:
любой другой инструмент отклоняется до сети. Адрес подключения несёт креды,
поэтому в сообщения об ошибках он не попадает.
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from typing import Callable, Optional

ALLOWED_TOOLS = frozenset({
    "vk_ads_auth_check", "vk_ads_account_info", "vk_ads_accounts_list", "vk_ads_agency_clients_list",
    "vk_ads_ad_plans_list", "vk_ads_ad_plans_get",
    "vk_ads_campaigns_list", "vk_ads_campaigns_get",
    "vk_ads_ad_groups_list", "vk_ads_ad_groups_get",
    "vk_ads_banners_list", "vk_ads_banners_get",
    "vk_ads_statistics_summary", "vk_ads_statistics_day",
    "vk_ads_remarketing_segments_list", "vk_ads_remarketing_pixels_list", "vk_ads_users_lists_list",
    "vk_ads_packages_list", "vk_ads_regions_search", "vk_ads_dictionary_get",
})
NO_ACCOUNT_ARG = frozenset({
    "vk_ads_accounts_list", "vk_ads_packages_list", "vk_ads_regions_search", "vk_ads_dictionary_get",
})
RETRY_STATUSES = frozenset({429, 500, 502, 503, 504})

Transport = Callable[[str, dict], dict]


class McpError(Exception):
    def __init__(self, message: str, status: Optional[int] = None):
        super().__init__(message)
        self.status = status


class ForbiddenToolError(McpError):
    pass


def parse_body(content_type: str, text: str) -> dict:
    if "text/event-stream" in (content_type or ""):
        data = [line[5:].strip() for line in text.splitlines() if line.startswith("data:")]
        if not data:
            raise McpError("пустой поток событий от сервера")
        text = data[-1]
    try:
        return json.loads(text)
    except ValueError:
        raise McpError(f"сервер ответил не JSON: {text[:200]}") from None


def http_transport(url: str, payload: dict, timeout: float = 60.0) -> dict:
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode("utf-8"), method="POST",
        headers={"Content-Type": "application/json", "Accept": "application/json, text/event-stream"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return parse_body(resp.headers.get("Content-Type", ""), resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        raise McpError(f"HTTP {e.code}", status=e.code) from None
    except urllib.error.URLError as e:
        raise McpError(f"сервер MCP недоступен: {e.reason}") from None


def _tool_error(tool: str, text: str) -> McpError:
    status, message = None, text
    try:
        obj = json.loads(text)
    except ValueError:
        obj = None
    if isinstance(obj, dict):
        status = obj.get("status") if isinstance(obj.get("status"), int) else None
        detail = obj.get("detail")
        if isinstance(detail, dict):
            detail = (detail.get("error") or {}).get("message") or json.dumps(detail, ensure_ascii=False)
        message = detail or text
    if status is None and "rate_limit" in text:
        status = 429
    return McpError(f"{tool}: {message}"[:500], status=status)


class McpClient:
    def __init__(self, url: str, *, account_id=None, transport: Optional[Transport] = None,
                 sleep=time.sleep, retries: int = 3, backoff: float = 2.0):
        self._url = url
        self.account_id = account_id
        self._transport = transport or http_transport
        self._sleep = sleep
        self._retries = retries
        self._backoff = backoff
        self._next_id = 0

    def call(self, tool: str, arguments: Optional[dict] = None):
        if tool not in ALLOWED_TOOLS:
            raise ForbiddenToolError(f"{tool}: вне белого списка чтения — аудит ничего не меняет в кабинете")
        args = dict(arguments or {})
        if self.account_id and tool not in NO_ACCOUNT_ARG:
            args.setdefault("account_id", str(self.account_id))
        attempt = 0
        while True:
            try:
                return self._call_once(tool, args)
            except ForbiddenToolError:
                raise
            except McpError as e:
                attempt += 1
                if e.status not in RETRY_STATUSES or attempt > self._retries:
                    raise
                self._sleep(self._backoff * attempt)

    def _call_once(self, tool: str, args: dict):
        self._next_id += 1
        payload = {"jsonrpc": "2.0", "id": self._next_id, "method": "tools/call",
                   "params": {"name": tool, "arguments": args}}
        resp = self._transport(self._url, payload)
        if "error" in resp:
            err = resp["error"] or {}
            raise McpError(f"{tool}: JSON-RPC {err.get('code')}: {err.get('message')}")
        result = resp.get("result") or {}
        text = "".join(c.get("text", "") for c in result.get("content", []) if c.get("type") == "text")
        if result.get("isError"):
            raise _tool_error(tool, text)
        try:
            return json.loads(text)
        except ValueError:
            raise McpError(f"{tool}: ответ не JSON: {text[:200]}") from None

    def paginate(self, tool: str, arguments: Optional[dict] = None, *,
                 page_size: int = 250, max_pages: int = 200) -> list:
        items: list = []
        offset = 0
        for _ in range(max_pages):
            args = dict(arguments or {})
            args.update(limit=page_size, offset=offset)
            page = self.call(tool, args)
            batch = page.get("items", []) if isinstance(page, dict) else []
            items.extend(batch)
            offset += len(batch)
            count = page.get("count") if isinstance(page, dict) else None
            if len(batch) < page_size or (isinstance(count, int) and offset >= count):
                return items
        raise McpError(f"{tool}: больше {max_pages} страниц — сбор остановлен")
```

- [ ] **Step 4: Прогнать**

Run: `python -m pytest tests/test_mcp_client.py -v`
Expected: все passed (25 параметризованных + 10).

- [ ] **Step 5: Commit**

```bash
git add scripts/mcp_client.py tests/test_mcp_client.py
git commit -m "mcp_client: JSON-RPC по HTTP, белый список чтения, повторы, пагинация"
```

---

### Task 4: Формат `raw/` — общий для путей A и B

**Files:**
- Create: `scripts/raw_store.py`
- Test: `tests/test_raw_store.py`

**Interfaces:**
- Consumes: —
- Produces: `write_call(raw_dir: Path, tool: str, arguments: dict, response, part: int | None = None) -> Path` (part=None → следующий свободный); `read_calls(raw_dir: Path, tool: str) -> list[dict]` (каждый `{"tool", "arguments", "response"}`, по возрастанию part; `ValueError` с именем файла на битом файле); `items_of(raw_dir, tool, where: Callable[[dict], bool] | None = None) -> list[dict]`. CLI: `python -m scripts.raw_store check --workspace <abs>`.

Файл: `raw/<tool>__<part:03d>.json` = `{"tool": ..., "arguments": {...}, "response": <распарсенный JSON ответа>}`. Путь A: модель пишет ровно такие файлы, `response` — ответ инструмента целиком, без правок.

- [ ] **Step 1: Тест**

`tests/test_raw_store.py`:
```python
from __future__ import annotations

import json

import pytest

from scripts.raw_store import items_of, main, read_calls, write_call


def test_roundtrip_and_parts(tmp_path):
    raw = tmp_path / "raw"
    write_call(raw, "vk_ads_campaigns_list", {"fields": "id"}, {"items": [{"id": 1}]})
    write_call(raw, "vk_ads_campaigns_list", {"fields": "id"}, {"items": [{"id": 2}]})
    calls = read_calls(raw, "vk_ads_campaigns_list")
    assert [c["response"]["items"][0]["id"] for c in calls] == [1, 2]
    assert (raw / "vk_ads_campaigns_list__001.json").is_file()


def test_items_of_with_filter(tmp_path):
    raw = tmp_path / "raw"
    write_call(raw, "vk_ads_statistics_summary", {"entity": "campaigns"}, {"items": [{"id": 1}]})
    write_call(raw, "vk_ads_statistics_summary", {"entity": "banners"}, {"items": [{"id": 9}]})
    got = items_of(raw, "vk_ads_statistics_summary", where=lambda a: a.get("entity") == "banners")
    assert got == [{"id": 9}]


def test_missing_tool_is_empty(tmp_path):
    assert read_calls(tmp_path / "raw", "vk_ads_banners_list") == []


def test_malformed_raw_names_file(tmp_path):
    raw = tmp_path / "raw"
    raw.mkdir()
    (raw / "vk_ads_banners_list__000.json").write_text("{not json", encoding="utf-8")
    with pytest.raises(ValueError, match="vk_ads_banners_list__000.json"):
        read_calls(raw, "vk_ads_banners_list")


def test_missing_keys_names_file(tmp_path):
    raw = tmp_path / "raw"
    raw.mkdir()
    (raw / "vk_ads_banners_list__000.json").write_text(json.dumps({"items": []}), encoding="utf-8")
    with pytest.raises(ValueError, match="response"):
        read_calls(raw, "vk_ads_banners_list")


def test_check_cli(tmp_path, capsys):
    write_call(tmp_path / "raw", "vk_ads_auth_check", {}, {"ok": True})
    assert main(["check", "--workspace", str(tmp_path)]) == 0
    (tmp_path / "raw" / "vk_ads_banners_list__000.json").write_text("[]", encoding="utf-8")
    assert main(["check", "--workspace", str(tmp_path)]) == 3
```

- [ ] **Step 2: Убедиться, что падает**

Run: `python -m pytest tests/test_raw_store.py -v`
Expected: FAIL — модуля нет.

- [ ] **Step 3: Реализация**

`scripts/raw_store.py`:
```python
"""
raw_store.py — сырые ответы MCP на диске: один файл на вызов.

raw/<tool>__<part:03d>.json = {"tool", "arguments", "response"}. Пишет collect.py
(путь B) или модель (путь A) — формат один, дальше код общий.

    python -m scripts.raw_store check --workspace <абсолютный путь>
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Callable, Optional

try:
    from scripts._console import setup_console
    from scripts._workspace import resolve_workspace
except ImportError:  # запуск напрямую
    from _console import setup_console
    from _workspace import resolve_workspace

REQUIRED_KEYS = ("tool", "arguments", "response")


def _files(raw_dir: Path, tool: str) -> list:
    return sorted(Path(raw_dir).glob(f"{tool}__*.json"))


def write_call(raw_dir: Path, tool: str, arguments: dict, response, part: Optional[int] = None) -> Path:
    raw_dir = Path(raw_dir)
    raw_dir.mkdir(parents=True, exist_ok=True)
    if part is None:
        part = len(_files(raw_dir, tool))
    path = raw_dir / f"{tool}__{part:03d}.json"
    path.write_text(json.dumps({"tool": tool, "arguments": dict(arguments or {}), "response": response},
                               ensure_ascii=False, indent=1), encoding="utf-8")
    return path


def _load(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise ValueError(f"{path.name}: не читается как JSON ({e})") from None
    if not isinstance(data, dict) or any(k not in data for k in REQUIRED_KEYS):
        raise ValueError(f"{path.name}: нужны ключи {', '.join(REQUIRED_KEYS)} — "
                         "ответ инструмента кладётся в response целиком")
    return data


def read_calls(raw_dir: Path, tool: str) -> list:
    return [_load(p) for p in _files(raw_dir, tool)]


def items_of(raw_dir: Path, tool: str, where: Optional[Callable[[dict], bool]] = None) -> list:
    out: list = []
    for call in read_calls(raw_dir, tool):
        if where is not None and not where(call["arguments"]):
            continue
        resp = call["response"]
        if isinstance(resp, dict):
            out.extend(resp.get("items") or [])
    return out


def main(argv=None) -> int:
    setup_console()
    parser = argparse.ArgumentParser(description="Проверка сырых ответов MCP в raw/")
    sub = parser.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("check")
    p.add_argument("--workspace", required=True)
    args = parser.parse_args(argv)
    raw = resolve_workspace(args.workspace) / "raw"
    bad = []
    files = sorted(raw.glob("*__*.json"))
    for path in files:
        try:
            _load(path)
        except ValueError as e:
            bad.append(str(e))
    for line in bad:
        print(f"✗ {line}")
    print(f"файлов: {len(files)}, с ошибками: {len(bad)}")
    return 3 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Прогнать**

Run: `python -m pytest tests/test_raw_store.py -v`
Expected: 6 passed.

- [ ] **Step 5: Commit**

```bash
git add scripts/raw_store.py tests/test_raw_store.py
git commit -m "raw_store: один формат сырых ответов для путей A и B, проверка файлов"
```

---

### Task 5: Сбор данных (путь B)

**Files:**
- Create: `scripts/collect.py`
- Test: `tests/test_collect.py`

**Interfaces:**
- Consumes: `McpClient.call/paginate`, `McpError`, `ForbiddenToolError` (Task 3); `write_call` (Task 4); `get_connection_url`, `mask` (Task 2); `resolve_workspace` (Task 1).
- Produces: константы `PLAN_FIELDS`, `GROUP_FIELDS`, `BANNER_FIELDS`, `STATS_CHUNK = 50`; `collect(client, raw_dir: Path, date_from: str, date_to: str, mode: str = "deep") -> dict` (лог `{"mode","date_from","date_to","calls":[{"tool","arguments","ok","error"}],"errors": int}`, он же пишется в `<workspace>/collect_log.json`); `clamp_date_to(date_to: str, today: date) -> str`. CLI: `python -m scripts.collect --workspace <abs> --date-from D --date-to D [--account-id ID] [--mode deep|quick]` и `python -m scripts.collect --probe [--account-id ID]`.

Объявления собираются по группам (`banners_list(campaign_id=...)`): так группа объявления известна из аргументов, даже если сервер не вернёт поле `campaign_id`. Неизвестные имена в `fields` сервер молча игнорирует — набор полей с запасом безопасен.

- [ ] **Step 1: Тест**

`tests/test_collect.py`:
```python
from __future__ import annotations

import json
from datetime import date

import pytest

from scripts import collect as collect_mod
from scripts.collect import STATS_CHUNK, clamp_date_to, collect
from scripts.mcp_client import ALLOWED_TOOLS, McpError


class FakeClient:
    def __init__(self, groups, banners_per_group=2, fail_tools=()):
        self.groups = groups
        self.banners_per_group = banners_per_group
        self.fail_tools = set(fail_tools)
        self.calls = []

    def _check(self, tool, args):
        assert tool in ALLOWED_TOOLS
        self.calls.append((tool, dict(args or {})))
        if tool in self.fail_tools:
            raise McpError(f"{tool}: HTTP 500", status=500)

    def paginate(self, tool, arguments=None, **_):
        self._check(tool, arguments)
        if tool == "vk_ads_campaigns_list":
            return self.groups
        if tool == "vk_ads_banners_list":
            gid = arguments["campaign_id"]
            return [{"id": gid * 100 + i, "status": "active"} for i in range(self.banners_per_group)]
        return []

    def call(self, tool, arguments=None):
        self._check(tool, arguments)
        return {"items": [], "total": {}}


def groups(n, deleted=()):
    return [{"id": i, "status": "deleted" if i in deleted else "active"} for i in range(1, n + 1)]


def tools(client, name):
    return [a for t, a in client.calls if t == name]


def test_deep_collects_everything(tmp_path):
    client = FakeClient(groups(3, deleted={3}))
    log = collect(client, tmp_path / "raw", "2026-09-01", "2026-09-30", mode="deep")
    assert log["errors"] == 0
    assert [a["campaign_id"] for a in tools(client, "vk_ads_banners_list")] == [1, 2]
    summary = tools(client, "vk_ads_statistics_summary")
    assert {a["entity"] for a in summary} == {"campaigns", "banners"}
    assert summary[0]["ids"] == "1,2"
    assert (tmp_path / "collect_log.json").is_file()
    assert (tmp_path / "raw" / "vk_ads_banners_list__001.json").is_file()


def test_banner_group_recorded_in_arguments(tmp_path):
    collect(FakeClient(groups(1)), tmp_path / "raw", "2026-09-01", "2026-09-30")
    saved = json.loads((tmp_path / "raw" / "vk_ads_banners_list__000.json").read_text(encoding="utf-8"))
    assert saved["arguments"]["campaign_id"] == 1


def test_many_groups_chunked(tmp_path):
    client = FakeClient(groups(120), banners_per_group=0)
    collect(client, tmp_path / "raw", "2026-09-01", "2026-09-30")
    day = tools(client, "vk_ads_statistics_day")
    assert len(day) == 3
    assert sum(len(a["ids"].split(",")) for a in day) == 120
    assert all(len(a["ids"].split(",")) <= STATS_CHUNK for a in day)


def test_quick_mode_skips_banners_and_summary(tmp_path):
    client = FakeClient(groups(2))
    collect(client, tmp_path / "raw", "2026-10-06", "2026-10-13", mode="quick")
    names = {t for t, _ in client.calls}
    assert "vk_ads_banners_list" not in names
    assert "vk_ads_statistics_summary" not in names
    assert "vk_ads_statistics_day" in names


def test_partial_failure_logged_and_continues(tmp_path):
    client = FakeClient(groups(2), fail_tools={"vk_ads_remarketing_pixels_list"})
    log = collect(client, tmp_path / "raw", "2026-09-01", "2026-09-30")
    assert log["errors"] == 1
    failed = [c for c in log["calls"] if not c["ok"]]
    assert failed[0]["tool"] == "vk_ads_remarketing_pixels_list"
    assert tools(client, "vk_ads_statistics_day")


def test_clamp_date_to():
    today = date(2026, 10, 14)
    assert clamp_date_to("2026-10-14", today) == "2026-10-13"
    assert clamp_date_to("2026-10-20", today) == "2026-10-13"
    assert clamp_date_to("2026-10-01", today) == "2026-10-01"


def test_cli_does_not_write_secret(tmp_path, monkeypatch):
    secret = "https://vkads-mcp.aihub.click.ru/c/TOPSECRET999999"
    monkeypatch.setenv("VK_ADS_CONNECTION_URL", secret)
    monkeypatch.setattr(collect_mod, "McpClient", lambda url, **kw: FakeClient(groups(1)))
    ws = tmp_path / "audit"
    rc = collect_mod.main(["--workspace", str(ws), "--date-from", "2026-09-01", "--date-to", "2026-09-30"])
    assert rc == 0
    for path in ws.rglob("*.json"):
        assert "TOPSECRET" not in path.read_text(encoding="utf-8")


def test_cli_without_url_exits_2(tmp_path, capsys):
    rc = collect_mod.main(["--workspace", str(tmp_path / "a"), "--date-from", "2026-09-01",
                           "--date-to", "2026-09-30"])
    assert rc == 2
    assert "путь B недоступен" in capsys.readouterr().out
```

- [ ] **Step 2: Убедиться, что падает**

Run: `python -m pytest tests/test_collect.py -v`
Expected: FAIL — модуля нет.

- [ ] **Step 3: Реализация**

`scripts/collect.py`:
```python
"""
collect.py — путь B: скрипт сам собирает кабинет из MCP VK Рекламы в raw/.

Опись (кампании, группы, объявления по группам, сегменты, списки, пиксели) и
статистика (сводка за период по группам и объявлениям, по дням по группам).
Только инструменты из белого списка mcp_client. Частичный сбой не останавливает
сбор — он попадает в collect_log.json, а оттуда в ограничения отчёта.

    python -m scripts.collect --probe [--account-id ID]
    python -m scripts.collect --workspace <абс. путь> --date-from 2026-09-01 --date-to 2026-09-30 \
        [--account-id ID] [--mode deep|quick]
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date, timedelta
from pathlib import Path

try:
    from scripts._console import setup_console
    from scripts._workspace import resolve_workspace
    from scripts.credentials import get_connection_url, mask
    from scripts.mcp_client import ForbiddenToolError, McpClient, McpError
    from scripts.raw_store import write_call
except ImportError:  # запуск напрямую
    from _console import setup_console
    from _workspace import resolve_workspace
    from credentials import get_connection_url, mask
    from mcp_client import ForbiddenToolError, McpClient, McpError
    from raw_store import write_call

PLAN_FIELDS = "id,name,status,objective,budget_limit,budget_limit_day,date_start,date_end,created,updated"
GROUP_FIELDS = ("id,name,status,objective,package_id,ad_plan_id,budget_limit,budget_limit_day,targetings,"
                "issues,created,updated,date_start,date_end,delivery,autobidding_mode,max_price,priced_goal")
BANNER_FIELDS = ("id,name,campaign_id,status,moderation_status,moderation_reasons,issues,delivery,"
                 "textblocks,urls,content,created,updated,user_can_request_remoderation")
STATS_CHUNK = 50
METRICS = "all"
LISTS = ("vk_ads_remarketing_segments_list", "vk_ads_users_lists_list", "vk_ads_remarketing_pixels_list")


def _chunks(ids: list, size: int):
    for i in range(0, len(ids), size):
        yield ids[i:i + size]


def clamp_date_to(date_to: str, today: date) -> str:
    yesterday = today - timedelta(days=1)
    return min(date.fromisoformat(date_to), yesterday).isoformat()


class _Collector:
    def __init__(self, client, raw_dir: Path):
        self.client = client
        self.raw_dir = Path(raw_dir)
        self.log: list = []

    def _record(self, tool, args, error=None):
        self.log.append({"tool": tool, "arguments": args, "ok": error is None, "error": error})

    def call(self, tool: str, args: dict):
        try:
            resp = self.client.call(tool, args)
        except ForbiddenToolError:
            raise
        except McpError as e:
            self._record(tool, args, str(e))
            return None
        write_call(self.raw_dir, tool, args, resp)
        self._record(tool, args)
        return resp

    def listing(self, tool: str, args: dict):
        try:
            items = self.client.paginate(tool, args)
        except ForbiddenToolError:
            raise
        except McpError as e:
            self._record(tool, args, str(e))
            return None
        write_call(self.raw_dir, tool, {**args, "paginated": True}, {"items": items})
        self._record(tool, args)
        return items


def collect(client, raw_dir: Path, date_from: str, date_to: str, mode: str = "deep") -> dict:
    c = _Collector(client, raw_dir)
    period = {"date_from": date_from, "date_to": date_to, "metrics": METRICS}
    c.listing("vk_ads_ad_plans_list", {"fields": PLAN_FIELDS})
    groups = c.listing("vk_ads_campaigns_list", {"fields": GROUP_FIELDS}) or []
    live = [g["id"] for g in groups if g.get("status") != "deleted"]

    banner_ids: list = []
    if mode == "deep":
        for gid in live:
            banners = c.listing("vk_ads_banners_list", {"campaign_id": gid, "fields": BANNER_FIELDS}) or []
            banner_ids += [b["id"] for b in banners if b.get("status") != "deleted"]
        for tool in LISTS:
            c.listing(tool, {})

    for chunk in _chunks(live, STATS_CHUNK):
        ids = ",".join(map(str, chunk))
        if mode == "deep":
            c.call("vk_ads_statistics_summary", {"entity": "campaigns", "ids": ids, **period})
        c.call("vk_ads_statistics_day", {"entity": "campaigns", "ids": ids, **period})
    for chunk in _chunks(banner_ids, STATS_CHUNK):
        c.call("vk_ads_statistics_summary", {"entity": "banners", "ids": ",".join(map(str, chunk)), **period})

    log = {"mode": mode, "date_from": date_from, "date_to": date_to, "calls": c.log,
           "errors": sum(1 for x in c.log if not x["ok"])}
    out = Path(raw_dir).parent / "collect_log.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(log, ensure_ascii=False, indent=1), encoding="utf-8")
    return log


def main(argv=None) -> int:
    setup_console()
    parser = argparse.ArgumentParser(description="Сбор кабинета VK Рекламы из MCP (путь B)")
    parser.add_argument("--workspace")
    parser.add_argument("--date-from")
    parser.add_argument("--date-to")
    parser.add_argument("--account-id")
    parser.add_argument("--mode", choices=["deep", "quick"], default="deep")
    parser.add_argument("--probe", action="store_true", help="только проверить, работает ли путь B")
    args = parser.parse_args(argv)

    url = get_connection_url()
    if not url:
        print("Ссылка подключения не сохранена — путь B недоступен, работаем путём A "
              "(python -m scripts.credentials set --url <ссылка>, чтобы включить B).")
        return 2
    client = McpClient(url, account_id=args.account_id)

    if args.probe:
        try:
            client.call("vk_ads_auth_check")
        except McpError as e:
            print(f"✗ путь B не работает ({mask(url)}): {e}. Работаем путём A.")
            return 2
        print(f"✓ путь B работает ({mask(url)})")
        return 0

    if not (args.workspace and args.date_from and args.date_to):
        parser.error("нужны --workspace, --date-from и --date-to")
    ws = resolve_workspace(args.workspace)
    date_to = clamp_date_to(args.date_to, date.today())
    if date_to != args.date_to:
        print(f"Период сдвинут на {date_to}: данные за сегодня неполные.")
    log = collect(client, ws / "raw", args.date_from, date_to, mode=args.mode)
    print(f"вызовов: {len(log['calls'])}, с ошибками: {log['errors']} → {ws / 'collect_log.json'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Прогнать**

Run: `python -m pytest tests/test_collect.py -v`
Expected: 8 passed.

- [ ] **Step 5: Commit**

```bash
git add scripts/collect.py tests/test_collect.py
git commit -m "collect: путь B — опись и статистика кабинета в raw/, журнал вызовов"
```

---

### Task 6: Нормализация в `dataset.json`

**Files:**
- Create: `scripts/normalize.py`
- Test: `tests/test_normalize.py`

**Interfaces:**
- Consumes: `read_calls`, `items_of` (Task 4); `resolve_workspace` (Task 1).
- Produces: `money(v) -> float`; `kopecks_to_rub(v) -> float | None`; `stats_from(block: dict | None, objective: str | None) -> dict` (ключи `spent, shows, clicks, conversions, ctr_pct, vk_ctr_pct, cpc, cpm, cpa, reach, frequency`); `aggregate(rows: list[dict]) -> dict` (те же ключи, `vk_ctr_pct=None`, `reach=0`, `frequency=None`); `targeting_key(t: dict | None) -> str`; `build_dataset(raw_dir: Path, date_from: str, date_to: str, target_cpa: float | None = None, path: str = "B") -> dict`; `check_sanity(ds: dict) -> list[dict]` (`{"code","entity","detail"}`). CLI: `python -m scripts.normalize --workspace <abs> --date-from D --date-to D [--target-cpa N] [--path A|B]` → `dataset.json`; при сбоях здравого смысла — `sanity.json` и код 3.

Структура `dataset.json`:
```
{"period": {"from", "to", "days"}, "target_cpa": float|null,
 "plans": [{"id","name","status"}],
 "groups": [{"id","name","status","objective","package_id","ad_plan_id","delivery","autobidding_mode",
             "budget_day_rub","budget_total_rub","issues":[code],"created","date_end",
             "targeting_key","segment_ids":[int],"stats":{...},"daily":[{"date", ...stats}],"banner_ids":[int]}],
 "banners": [{"id","name","group_id","status","moderation_status","moderation_reasons","issues":[code],
              "can_remoderate": bool,"stats": {...}|null}],
 "segments": [{"id","name"}], "users_lists": [{"id","name"}], "pixels": [{"id","name"}],
 "totals": {...stats активных и приостановленных групп},
 "coverage": {"path": "A"|"B", "banners": bool, "summary": bool, "metrika": false}}
```

Конверсии: для `objective` из `CONVERSION_SOURCE` (`leadads`, `socialengagement`) — `base.vk.result`, иначе `base.goals`. CTR считаем сами (`clicks / shows * 100`), а значение VK (`base.ctr`, доля) кладём в `vk_ctr_pct` только для проверки единиц: расхождение = мы неверно поняли единицы. Обе догадки помечены в `references/mcp-read-tools.md` как «проверить живым прогоном».

- [ ] **Step 1: Тест**

`tests/test_normalize.py`:
```python
from __future__ import annotations

import pytest

from scripts.normalize import (aggregate, build_dataset, check_sanity, kopecks_to_rub, money,
                               stats_from, targeting_key)
from scripts.raw_store import write_call

P = {"date_from": "2026-09-01", "date_to": "2026-09-30", "metrics": "all"}


def base(spent="0", shows=0, clicks=0, goals=0, ctr=None, vk_result=0):
    ctr = (clicks / shows) if (ctr is None and shows) else (ctr or 0)
    return {"base": {"spent": spent, "shows": shows, "clicks": clicks, "goals": goals, "ctr": ctr,
                     "vk": {"result": vk_result}},
            "uniques": {"reach": shows // 2, "frequency": 2.0 if shows else 0}}


def cabinet(raw, groups, banners_by_group=None, summary=None, banner_summary=None, daily=None):
    write_call(raw, "vk_ads_ad_plans_list", {"paginated": True}, {"items": [{"id": 7, "name": "План"}]})
    write_call(raw, "vk_ads_campaigns_list", {"paginated": True}, {"items": groups})
    for gid, banners in (banners_by_group or {}).items():
        write_call(raw, "vk_ads_banners_list", {"campaign_id": gid, "paginated": True}, {"items": banners})
    if summary is not None:
        write_call(raw, "vk_ads_statistics_summary", {"entity": "campaigns", **P},
                   {"items": [{"id": k, "total": v} for k, v in summary.items()]})
    if banner_summary is not None:
        write_call(raw, "vk_ads_statistics_summary", {"entity": "banners", **P},
                   {"items": [{"id": k, "total": v} for k, v in banner_summary.items()]})
    if daily is not None:
        write_call(raw, "vk_ads_statistics_day", {"entity": "campaigns", **P},
                   {"items": [{"id": k, "rows": rows} for k, rows in daily.items()]})


def test_money_and_kopecks():
    assert money("1234.50") == 1234.5
    assert money("1 234,5") == 1234.5
    assert money(None) == 0.0 and money("") == 0.0
    assert kopecks_to_rub(6000000) == 60000.0
    assert kopecks_to_rub(None) is None
    with pytest.raises(ValueError):
        money("abc")


def test_stats_ratios():
    s = stats_from(base(spent="1000", shows=10000, clicks=100, goals=4), "site_conversions")
    assert s["ctr_pct"] == 1.0 and s["cpc"] == 10.0 and s["cpm"] == 100.0 and s["cpa"] == 250.0
    assert s["conversions"] == 4 and s["vk_ctr_pct"] == pytest.approx(1.0)


def test_conversion_source_by_objective():
    block = base(spent="100", shows=100, clicks=10, goals=0, vk_result=5)
    assert stats_from(block, "leadads")["conversions"] == 5
    assert stats_from(block, "site_conversions")["conversions"] == 0


def test_aggregate_daily():
    rows = [stats_from(base(spent="100", shows=1000, clicks=10, goals=1), None),
            stats_from(base(spent="300", shows=1000, clicks=30, goals=1), None)]
    a = aggregate(rows)
    assert a["spent"] == 400.0 and a["clicks"] == 40 and a["cpa"] == 200.0 and a["ctr_pct"] == 2.0


def test_targeting_key_order_insensitive():
    a = {"geo": {"regions": [2, 1]}, "sex": ["male", "female"], "fulltime": {"mon": [1]}}
    b = {"sex": ["female", "male"], "geo": {"regions": [1, 2]}}
    assert targeting_key(a) == targeting_key(b)


def test_build_dataset(tmp_path):
    raw = tmp_path / "raw"
    groups = [{"id": 1, "name": "G1", "status": "active", "objective": "site_conversions",
               "budget_limit_day": 50000, "issues": [{"code": "NO_MONEY"}],
               "targetings": {"geo": {"regions": [1]}, "segments": [55]}},
              {"id": 2, "name": "G2", "status": "deleted", "objective": "site_conversions"}]
    cabinet(raw, groups, banners_by_group={1: [{"id": 11, "status": "active"}]},
            summary={1: base(spent="900", shows=9000, clicks=90, goals=3)},
            banner_summary={11: base(spent="900", shows=9000, clicks=90, goals=3)})
    ds = build_dataset(raw, "2026-09-01", "2026-09-30", target_cpa=250)
    g = ds["groups"][0]
    assert ds["period"]["days"] == 30
    assert g["budget_day_rub"] == 500.0 and g["issues"] == ["NO_MONEY"] and g["segment_ids"] == [55]
    assert g["banner_ids"] == [11] and ds["banners"][0]["group_id"] == 1
    assert ds["totals"]["spent"] == 900.0
    assert ds["coverage"] == {"path": "B", "banners": True, "summary": True, "metrika": False}
    assert check_sanity(ds) == []


def test_missing_stats_are_zero(tmp_path):
    """Сущности без показов VK не кладёт в items — это нули, а не ошибка."""
    raw = tmp_path / "raw"
    cabinet(raw, [{"id": 1, "name": "G1", "status": "active"}, {"id": 2, "name": "G2", "status": "active"}],
            summary={2: base(spent="100", shows=1000, clicks=10)})
    g1, g2 = build_dataset(raw, "2026-09-01", "2026-09-30")["groups"]
    assert g1["stats"]["spent"] == 0.0 and g1["stats"]["cpa"] is None
    assert g2["stats"]["spent"] == 100.0


def test_daily_used_when_no_summary(tmp_path):
    raw = tmp_path / "raw"
    rows = [{"date": "2026-09-01", **base(spent="100", shows=1000, clicks=10, goals=1)},
            {"date": "2026-09-02", **base(spent="200", shows=1000, clicks=20, goals=1)}]
    cabinet(raw, [{"id": 1, "name": "G1", "status": "active"}], daily={1: rows})
    ds = build_dataset(raw, "2026-09-01", "2026-09-02", path="A")
    g = ds["groups"][0]
    assert [d["date"] for d in g["daily"]] == ["2026-09-01", "2026-09-02"]
    assert g["stats"]["spent"] == 300.0
    assert ds["coverage"]["summary"] is False and ds["coverage"]["path"] == "A"


def test_sanity_catches_ctr_in_percent(tmp_path):
    raw = tmp_path / "raw"
    cabinet(raw, [{"id": 1, "name": "G1", "status": "active"}],
            summary={1: base(spent="100", shows=10000, clicks=100, ctr=1.0)})  # 1.0 = «процент», а не доля
    issues = check_sanity(build_dataset(raw, "2026-09-01", "2026-09-30"))
    assert [i["code"] for i in issues] == ["ctr_units"]


def test_sanity_banners_vs_group(tmp_path):
    raw = tmp_path / "raw"
    cabinet(raw, [{"id": 1, "name": "G1", "status": "active"}],
            banners_by_group={1: [{"id": 11, "status": "active"}]},
            summary={1: base(spent="1000", shows=1000, clicks=10)},
            banner_summary={11: base(spent="500", shows=1000, clicks=10)})
    issues = check_sanity(build_dataset(raw, "2026-09-01", "2026-09-30"))
    assert "banners_vs_group" in [i["code"] for i in issues]


def test_empty_cabinet(tmp_path):
    raw = tmp_path / "raw"
    cabinet(raw, [])
    ds = build_dataset(raw, "2026-09-01", "2026-09-30")
    assert ds["groups"] == [] and ds["totals"]["spent"] == 0.0 and check_sanity(ds) == []
```

- [ ] **Step 2: Убедиться, что падает**

Run: `python -m pytest tests/test_normalize.py -v`
Expected: FAIL — модуля нет.

- [ ] **Step 3: Реализация**

`scripts/normalize.py`:
```python
"""
normalize.py — raw/ → dataset.json. Единицы приводятся ТОЛЬКО здесь.

- бюджеты групп приходят в копейках (6000000 = 60 000 ₽);
- деньги статистики — строками в рублях;
- CTR считаем сами из clicks/shows, значение VK (доля) храним для проверки единиц;
- конверсии: base.goals, для целей из CONVERSION_SOURCE — base.vk.result.
Проверка здравого смысла (check_sanity) останавливает аудит на кривых числах.

    python -m scripts.normalize --workspace <абс. путь> --date-from D --date-to D [--target-cpa N] [--path A|B]
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

try:
    from scripts._console import setup_console
    from scripts._workspace import resolve_workspace
    from scripts.raw_store import items_of, read_calls
except ImportError:  # запуск напрямую
    from _console import setup_console
    from _workspace import resolve_workspace
    from raw_store import items_of, read_calls

KOPECKS = 100
CONVERSION_SOURCE = {"leadads": "vk.result", "socialengagement": "vk.result"}
NOT_TARGETING = ("fulltime",)


def money(v) -> float:
    if v is None or v == "":
        return 0.0
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).replace("\u00a0", "").replace(" ", "").replace(",", ".")
    try:
        return float(s)
    except ValueError:
        raise ValueError(f"не число: {v!r}") from None


def kopecks_to_rub(v):
    return None if v in (None, "") else round(money(v) / KOPECKS, 2)


def _ratios(spent: float, shows: int, clicks: int, conv: int) -> dict:
    return {"ctr_pct": round(clicks / shows * 100, 4) if shows else 0.0,
            "cpc": round(spent / clicks, 2) if clicks else None,
            "cpm": round(spent / shows * 1000, 2) if shows else None,
            "cpa": round(spent / conv, 2) if conv else None}


def stats_from(block, objective) -> dict:
    block = block or {}
    b = block.get("base") or {}
    u = block.get("uniques") or {}
    spent = round(money(b.get("spent")), 2)
    shows = int(b.get("shows") or 0)
    clicks = int(b.get("clicks") or 0)
    if CONVERSION_SOURCE.get(objective or "") == "vk.result":
        conv = int((b.get("vk") or {}).get("result") or 0)
    else:
        conv = int(b.get("goals") or 0)
    return {"spent": spent, "shows": shows, "clicks": clicks, "conversions": conv,
            **_ratios(spent, shows, clicks, conv),
            "vk_ctr_pct": round(float(b.get("ctr") or 0) * 100, 4),
            "reach": int(u.get("reach") or 0), "frequency": float(u.get("frequency") or 0) or None}


def aggregate(rows: list) -> dict:
    spent = round(sum(r["spent"] for r in rows), 2)
    shows = sum(r["shows"] for r in rows)
    clicks = sum(r["clicks"] for r in rows)
    conv = sum(r["conversions"] for r in rows)
    return {"spent": spent, "shows": shows, "clicks": clicks, "conversions": conv,
            **_ratios(spent, shows, clicks, conv), "vk_ctr_pct": None, "reach": 0, "frequency": None}


def _canon(v):
    if isinstance(v, dict):
        return {k: _canon(v[k]) for k in sorted(v)}
    if isinstance(v, list):
        items = [_canon(x) for x in v]
        return sorted(items, key=lambda x: json.dumps(x, sort_keys=True, ensure_ascii=False))
    return v


def targeting_key(t) -> str:
    t = {k: v for k, v in (t or {}).items() if k not in NOT_TARGETING}
    return json.dumps(_canon(t), sort_keys=True, ensure_ascii=False)


def _segment_ids(t) -> list:
    found: set = set()

    def walk(v, inside):
        if isinstance(v, dict):
            for k, x in v.items():
                walk(x, inside or "segment" in k)
        elif isinstance(v, list):
            for x in v:
                walk(x, inside)
        elif inside and isinstance(v, int):
            found.add(v)

    walk(t or {}, False)
    return sorted(found)


def _codes(issues) -> list:
    return [i.get("code") for i in (issues or []) if isinstance(i, dict) and i.get("code")]


def _entity_stats(raw_dir: Path, tool: str, entity: str) -> dict:
    out: dict = {}
    for call in read_calls(raw_dir, tool):
        if call["arguments"].get("entity") != entity:
            continue
        for it in (call["response"] or {}).get("items") or []:
            out[it["id"]] = it
    return out


def build_dataset(raw_dir: Path, date_from: str, date_to: str, target_cpa=None, path: str = "B") -> dict:
    raw_dir = Path(raw_dir)
    summary = _entity_stats(raw_dir, "vk_ads_statistics_summary", "campaigns")
    bsummary = _entity_stats(raw_dir, "vk_ads_statistics_summary", "banners")
    daily_raw = _entity_stats(raw_dir, "vk_ads_statistics_day", "campaigns")

    banners = []
    for call in read_calls(raw_dir, "vk_ads_banners_list"):
        gid = call["arguments"].get("campaign_id")
        for b in (call["response"] or {}).get("items") or []:
            banners.append({**b, "_group": b.get("campaign_id") or gid})

    objective_of: dict = {}
    groups = []
    for g in items_of(raw_dir, "vk_ads_campaigns_list"):
        obj = g.get("objective")
        objective_of[g["id"]] = obj
        daily = [{"date": r.get("date"), **stats_from(r, obj)}
                 for r in (daily_raw.get(g["id"]) or {}).get("rows") or []]
        if summary:
            stats = stats_from((summary.get(g["id"]) or {}).get("total"), obj)
        else:
            stats = aggregate(daily)
        groups.append({
            "id": g["id"], "name": g.get("name", ""), "status": g.get("status"), "objective": obj,
            "package_id": g.get("package_id"), "ad_plan_id": g.get("ad_plan_id"),
            "delivery": g.get("delivery"), "autobidding_mode": g.get("autobidding_mode"),
            "budget_day_rub": kopecks_to_rub(g.get("budget_limit_day")),
            "budget_total_rub": kopecks_to_rub(g.get("budget_limit")),
            "issues": _codes(g.get("issues")), "created": g.get("created"), "date_end": g.get("date_end"),
            "targeting_key": targeting_key(g.get("targetings")),
            "segment_ids": _segment_ids(g.get("targetings")),
            "stats": stats, "daily": daily,
            "banner_ids": [b["id"] for b in banners if b["_group"] == g["id"]],
        })

    banner_out = []
    for b in banners:
        st = bsummary.get(b["id"])
        banner_out.append({
            "id": b["id"], "name": b.get("name", ""), "group_id": b["_group"], "status": b.get("status"),
            "moderation_status": b.get("moderation_status"), "moderation_reasons": b.get("moderation_reasons"),
            "issues": _codes(b.get("issues")),
            "can_remoderate": bool(b.get("user_can_request_remoderation")),
            "stats": stats_from(st.get("total"), objective_of.get(b["_group"])) if st else None,
        })

    def named(tool):
        return [{"id": x.get("id"), "name": x.get("name", "")} for x in items_of(raw_dir, tool)]

    live = [g["stats"] for g in groups if g["status"] != "deleted"]
    d_from, d_to = date.fromisoformat(date_from), date.fromisoformat(date_to)
    return {
        "period": {"from": date_from, "to": date_to, "days": (d_to - d_from).days + 1},
        "target_cpa": target_cpa,
        "plans": [{"id": p.get("id"), "name": p.get("name", ""), "status": p.get("status")}
                  for p in items_of(raw_dir, "vk_ads_ad_plans_list")],
        "groups": groups, "banners": banner_out,
        "segments": named("vk_ads_remarketing_segments_list"),
        "users_lists": named("vk_ads_users_lists_list"),
        "pixels": named("vk_ads_remarketing_pixels_list"),
        "totals": aggregate(live),
        "coverage": {"path": path, "banners": bool(read_calls(raw_dir, "vk_ads_banners_list")),
                     "summary": bool(summary), "metrika": False},
    }


def _check_stats(entity: str, s, out: list):
    if not s:
        return
    if s["spent"] < 0:
        out.append({"code": "spent_negative", "entity": entity, "detail": f"расход {s['spent']}"})
    if s["ctr_pct"] > 100:
        out.append({"code": "ctr_over_100", "entity": entity, "detail": f"CTR {s['ctr_pct']} %"})
    vk = s.get("vk_ctr_pct")
    if vk is not None and s["shows"] >= 1000 and abs(vk - s["ctr_pct"]) > max(0.05, 0.05 * s["ctr_pct"]):
        out.append({"code": "ctr_units", "entity": entity,
                    "detail": f"CTR VK {vk} % против расчётного {s['ctr_pct']} % — единицы поняты неверно"})


def check_sanity(ds: dict) -> list:
    out: list = []
    banners = {b["id"]: b for b in ds["banners"]}
    for g in ds["groups"]:
        _check_stats(f"group:{g['id']}", g["stats"], out)
        bs = [banners[i]["stats"] for i in g["banner_ids"] if banners[i]["stats"]]
        if ds["coverage"]["summary"] and bs and len(bs) == len(g["banner_ids"]):
            total = sum(s["spent"] for s in bs)
            if abs(total - g["stats"]["spent"]) > max(1.0, 0.01 * g["stats"]["spent"]):
                out.append({"code": "banners_vs_group", "entity": f"group:{g['id']}",
                            "detail": f"объявления {total} ₽, группа {g['stats']['spent']} ₽"})
    for b in ds["banners"]:
        _check_stats(f"banner:{b['id']}", b["stats"], out)
    return out


def main(argv=None) -> int:
    setup_console()
    parser = argparse.ArgumentParser(description="raw/ → dataset.json")
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--date-from", required=True)
    parser.add_argument("--date-to", required=True)
    parser.add_argument("--target-cpa", type=float)
    parser.add_argument("--path", choices=["A", "B"], default="B")
    args = parser.parse_args(argv)
    ws = resolve_workspace(args.workspace)
    try:
        ds = build_dataset(ws / "raw", args.date_from, args.date_to, args.target_cpa, args.path)
    except ValueError as e:
        print(f"✗ {e}")
        return 3
    (ws / "dataset.json").write_text(json.dumps(ds, ensure_ascii=False, indent=1), encoding="utf-8")
    issues = check_sanity(ds)
    if issues:
        (ws / "sanity.json").write_text(json.dumps(issues, ensure_ascii=False, indent=1), encoding="utf-8")
        for i in issues:
            print(f"✗ {i['code']} {i['entity']}: {i['detail']}")
        print("Проверка здравого смысла не пройдена — отчёт не строим, см. sanity.json")
        return 3
    print(f"групп: {len(ds['groups'])}, объявлений: {len(ds['banners'])}, расход: {ds['totals']['spent']} ₽")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Прогнать**

Run: `python -m pytest tests/test_normalize.py -v`
Expected: 11 passed.

- [ ] **Step 5: Commit**

```bash
git add scripts/normalize.py tests/test_normalize.py
git commit -m "normalize: raw/ → dataset.json, единицы в одном месте, проверка здравого смысла"
```

---

### Task 7: Диагностика — ядро и правила гигиены

**Files:**
- Create: `scripts/diagnose.py`, `references/thresholds.json`
- Test: `tests/test_diagnose_hygiene.py`, `tests/helpers.py`

**Interfaces:**
- Consumes: формат `dataset.json` (Task 6).
- Produces: `load_thresholds(path: Path | None = None) -> dict`; `reference_cpa(ds, th) -> tuple[float | None, str]` (`"target"` / `"median"` / `"none"`); `confidence(clicks: int, th) -> str`; `finding(rule, level, etype, ent, title, evidence, action, effect_rub=0.0, conf="high") -> dict` (ключи `rule, level, entity{type,id,name}, title, evidence, effect_rub, confidence, action`); `RULES: list`; декоратор `rule`; `diagnose(ds, th) -> dict` (`{"ref_cpa","ref_source","findings":[...]}`, находки отсортированы: уровень `red<orange<green<white`, затем `effect_rub` по убыванию). CLI: `python -m scripts.diagnose --workspace <abs> [--thresholds <path>]` → `findings.json`.
- `tests/helpers.py`: `make_group(**kw) -> dict`, `make_banner(**kw) -> dict`, `make_ds(groups, banners=(), **kw) -> dict`, `stats(spent=0, shows=0, clicks=0, conversions=0, frequency=None) -> dict` — общие для Task 7, 8, 9, 10, 11.

Правила гигиены (rule id → уровень):
`no_active_banners` 🟠, `rejected_banners` 🟠, `blocking_issues` 🟠, `autodelete_risk` ⚪, `banners_per_group` ⚪, `low_budget` 🟠, `duplicate_targeting` 🟠, `unused_segments` ⚪, `no_pixel` 🟠.

- [ ] **Step 1: Пороги**

`references/thresholds.json`:
```json
{
  "_source": "Черновик vk-ads-audit 0.1. Заменяется методичкой; смысл каждого числа — references/diagnostic-rules.md",
  "active_statuses": ["active"],
  "min_daily_budget_rub": 300,
  "learning_budget_cpa_multiplier": 2,
  "blocking_issue_codes": ["NO_MONEY", "AD_PLAN_STOPPED", "NO_BANNERS_WITH_ACTIVE_STATUS"],
  "rejected_moderation_statuses": ["banned", "rejected"],
  "autodelete_warn_days": 150,
  "max_active_banners_per_group": 8,
  "pixel_objectives": ["site_conversions"],
  "min_conversions_for_cpa": 5,
  "confidence_high_clicks": 300,
  "confidence_medium_clicks": 100
}
```

- [ ] **Step 2: Хелперы тестов**

`tests/helpers.py`:
```python
from __future__ import annotations

from scripts.normalize import _ratios, targeting_key


def stats(spent=0, shows=0, clicks=0, conversions=0, frequency=None):
    return {"spent": float(spent), "shows": shows, "clicks": clicks, "conversions": conversions,
            **_ratios(float(spent), shows, clicks, conversions),
            "vk_ctr_pct": None, "reach": 0, "frequency": frequency}


def make_group(id=1, **kw):
    g = {"id": id, "name": f"G{id}", "status": "active", "objective": "site_conversions", "package_id": 1,
         "ad_plan_id": 7, "delivery": "delivering", "autobidding_mode": None, "budget_day_rub": 1000.0,
         "budget_total_rub": None, "issues": [], "created": "2026-09-01 00:00:00", "date_end": None,
         "targeting_key": targeting_key({"geo": {"regions": [id]}}), "segment_ids": [],
         "stats": stats(), "daily": [], "banner_ids": []}
    g.update(kw)
    return g


def make_banner(id=11, group_id=1, **kw):
    b = {"id": id, "name": f"B{id}", "group_id": group_id, "status": "active", "moderation_status": "allowed",
         "moderation_reasons": None, "issues": [], "can_remoderate": False, "stats": None}
    b.update(kw)
    return b


def make_ds(groups, banners=(), **kw):
    ds = {"period": {"from": "2026-09-01", "to": "2026-09-30", "days": 30}, "target_cpa": None,
          "plans": [{"id": 7, "name": "План", "status": "active"}], "groups": list(groups),
          "banners": list(banners), "segments": [], "users_lists": [], "pixels": [{"id": 1, "name": "px"}],
          "totals": stats(), "coverage": {"path": "B", "banners": True, "summary": True, "metrika": False}}
    ds.update(kw)
    for g in ds["groups"]:
        if not g["banner_ids"]:
            g["banner_ids"] = [b["id"] for b in ds["banners"] if b["group_id"] == g["id"]]
    return ds
```

- [ ] **Step 3: Тест**

`tests/test_diagnose_hygiene.py`:
```python
from __future__ import annotations

from scripts.diagnose import diagnose, load_thresholds, reference_cpa
from tests.helpers import make_banner, make_ds, make_group, stats

TH = load_thresholds()


def rules(ds):
    return [(f["rule"], f["entity"]["id"]) for f in diagnose(ds, TH)["findings"]]


def healthy_banners(gid, n=3):
    return [make_banner(id=gid * 10 + i, group_id=gid) for i in range(n)]


def test_healthy_cabinet_has_no_hygiene_findings():
    assert rules(make_ds([make_group(1)], healthy_banners(1))) == []


def test_no_active_banners():
    ds = make_ds([make_group(1)], [make_banner(11, status="blocked")])
    assert ("no_active_banners", 1) in rules(ds)


def test_rejected_banner():
    ds = make_ds([make_group(1)], healthy_banners(1) + [make_banner(19, moderation_status="banned",
                                                                    can_remoderate=True)])
    f = [x for x in diagnose(ds, TH)["findings"] if x["rule"] == "rejected_banners"][0]
    assert f["entity"]["id"] == 19 and f["evidence"]["can_remoderate"] is True


def test_blocking_issue():
    assert ("blocking_issues", 1) in rules(make_ds([make_group(1, issues=["NO_MONEY"])], healthy_banners(1)))


def test_autodelete_risk():
    g = make_group(1, created="2026-03-01 00:00:00", stats=stats(shows=0))
    assert ("autodelete_risk", 1) in rules(make_ds([g], healthy_banners(1)))
    young = make_group(2, created="2026-09-01 00:00:00")
    assert ("autodelete_risk", 2) not in rules(make_ds([young], healthy_banners(2)))


def test_banners_per_group():
    assert ("banners_per_group", 1) in rules(make_ds([make_group(1)], healthy_banners(1, n=1)))
    assert ("banners_per_group", 2) in rules(make_ds([make_group(2)], healthy_banners(2, n=9)))


def test_low_budget():
    assert ("low_budget", 1) in rules(make_ds([make_group(1, budget_day_rub=200.0)], healthy_banners(1)))


def test_low_budget_for_learning_with_target():
    g = make_group(1, autobidding_mode="max_goals", budget_day_rub=600.0)
    assert ("low_budget", 1) in rules(make_ds([g], healthy_banners(1), target_cpa=500))


def test_duplicate_targeting():
    g1 = make_group(1, targeting_key='{"geo":1}')
    g2 = make_group(2, targeting_key='{"geo":1}')
    found = rules(make_ds([g1, g2], healthy_banners(1) + healthy_banners(2)))
    assert ("duplicate_targeting", 1) in found or ("duplicate_targeting", 2) in found


def test_unused_segments():
    ds = make_ds([make_group(1, segment_ids=[5])], healthy_banners(1),
                 segments=[{"id": 5, "name": "used"}, {"id": 6, "name": "unused"}])
    assert ("unused_segments", 6) in rules(ds) and ("unused_segments", 5) not in rules(ds)


def test_no_pixel():
    ds = make_ds([make_group(1)], healthy_banners(1), pixels=[])
    assert ("no_pixel", 1) in rules(ds)


def test_deleted_groups_ignored():
    assert rules(make_ds([make_group(1, status="deleted", budget_day_rub=10.0)])) == []


def test_reference_cpa_sources():
    assert reference_cpa(make_ds([], target_cpa=500), TH) == (500.0, "target")
    gs = [make_group(i, stats=stats(spent=1000 * i, clicks=100, conversions=10)) for i in (1, 2, 3)]
    assert reference_cpa(make_ds(gs), TH) == (200.0, "median")
    assert reference_cpa(make_ds([make_group(1)]), TH) == (None, "none")


def test_sorted_by_level_then_effect():
    ds = make_ds([make_group(1, budget_day_rub=100.0)], healthy_banners(1, n=1), pixels=[])
    levels = [f["level"] for f in diagnose(ds, TH)["findings"]]
    order = {"red": 0, "orange": 1, "green": 2, "white": 3}
    assert levels == sorted(levels, key=order.get)
```

- [ ] **Step 4: Убедиться, что падает**

Run: `python -m pytest tests/test_diagnose_hygiene.py -v`
Expected: FAIL — модуля `scripts.diagnose` нет.

- [ ] **Step 5: Реализация**

`scripts/diagnose.py`:
```python
"""
diagnose.py — dataset.json + references/thresholds.json → findings.json.

Каждое правило — функция (ds, th, ctx) -> list[finding], зарегистрированная @rule.
Модель не считает: все числа находок — из dataset.json. Методичка меняет пороги
(thresholds.json) и правила здесь, контракт находки остаётся прежним.

    python -m scripts.diagnose --workspace <абс. путь> [--thresholds <path>]
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
from collections import defaultdict
from datetime import date, datetime
from pathlib import Path

try:
    from scripts._console import setup_console
    from scripts._workspace import SKILL_ROOT, resolve_workspace
except ImportError:  # запуск напрямую
    from _console import setup_console
    from _workspace import SKILL_ROOT, resolve_workspace

LEVEL_ORDER = {"red": 0, "orange": 1, "green": 2, "white": 3}
DEFAULT_THRESHOLDS = SKILL_ROOT / "references" / "thresholds.json"
RULES: list = []


def rule(fn):
    RULES.append(fn)
    return fn


def load_thresholds(path=None) -> dict:
    return json.loads(Path(path or DEFAULT_THRESHOLDS).read_text(encoding="utf-8"))


def finding(rule_id, level, etype, ent, title, evidence, action, effect_rub=0.0, conf="high") -> dict:
    return {"rule": rule_id, "level": level,
            "entity": {"type": etype, "id": ent.get("id"), "name": ent.get("name", "")},
            "title": title, "evidence": evidence, "effect_rub": round(float(effect_rub), 2),
            "confidence": conf, "action": action}


def confidence(clicks: int, th) -> str:
    if clicks >= th["confidence_high_clicks"]:
        return "high"
    return "medium" if clicks >= th["confidence_medium_clicks"] else "low"


def _active(g, th) -> bool:
    return g["status"] in th["active_statuses"]


def _live(ds):
    return [g for g in ds["groups"] if g["status"] != "deleted"]


def reference_cpa(ds, th):
    if ds.get("target_cpa"):
        return float(ds["target_cpa"]), "target"
    cpas = [g["stats"]["cpa"] for g in _live(ds)
            if g["stats"]["cpa"] is not None and g["stats"]["conversions"] >= th["min_conversions_for_cpa"]]
    if cpas:
        return round(float(statistics.median(cpas)), 2), "median"
    return None, "none"


def _banners_of(ds, g):
    ids = set(g["banner_ids"])
    return [b for b in ds["banners"] if b["id"] in ids]


# ---------- гигиена ----------

@rule
def no_active_banners(ds, th, ctx):
    if not ds["coverage"]["banners"]:
        return []
    out = []
    for g in _live(ds):
        if not _active(g, th):
            continue
        active = [b for b in _banners_of(ds, g) if b["status"] in th["active_statuses"]]
        if not active or "NO_BANNERS_WITH_ACTIVE_STATUS" in g["issues"]:
            out.append(finding("no_active_banners", "orange", "group", g,
                               "Группа включена, но крутить нечего — нет активных объявлений",
                               {"banners_total": len(g["banner_ids"]), "banners_active": len(active)},
                               "Добавить или включить объявления — либо выключить группу"))
    return out


@rule
def rejected_banners(ds, th, ctx):
    return [finding("rejected_banners", "orange", "banner", b, "Объявление отклонено модерацией",
                    {"moderation_status": b["moderation_status"], "reasons": b["moderation_reasons"],
                     "can_remoderate": b["can_remoderate"]},
                    "Исправить по причине отказа и отправить на перемодерацию"
                    if b["can_remoderate"] else "Исправить и создать заново: перемодерация недоступна")
            for b in ds["banners"]
            if b["status"] != "deleted" and b["moderation_status"] in th["rejected_moderation_statuses"]]


@rule
def blocking_issues(ds, th, ctx):
    out = []
    for g in _live(ds):
        codes = [c for c in g["issues"] if c in th["blocking_issue_codes"] and c != "NO_BANNERS_WITH_ACTIVE_STATUS"]
        if _active(g, th) and codes:
            out.append(finding("blocking_issues", "orange", "group", g, "VK сообщает, почему группа не крутится",
                               {"issues": codes}, "Устранить причину: " + ", ".join(codes)))
    return out


@rule
def autodelete_risk(ds, th, ctx):
    out = []
    end = date.fromisoformat(ds["period"]["to"])
    for g in _live(ds):
        if not g.get("created") or g["stats"]["shows"]:
            continue
        age = (end - datetime.strptime(g["created"][:10], "%Y-%m-%d").date()).days
        if age >= th["autodelete_warn_days"]:
            out.append(finding("autodelete_risk", "white", "group", g,
                               "Нет показов за период, группа старая — VK удаляет группы без показов за 180 дней",
                               {"age_days": age, "shows": 0},
                               "Решить: перезапустить или осознанно выключить"))
    return out


@rule
def banners_per_group(ds, th, ctx):
    if not ds["coverage"]["banners"]:
        return []
    out = []
    for g in _live(ds):
        if not _active(g, th):
            continue
        n = len([b for b in _banners_of(ds, g) if b["status"] in th["active_statuses"]])
        if n == 1:
            out.append(finding("banners_per_group", "white", "group", g,
                               "В группе одно объявление — алгоритму не из чего выбирать",
                               {"active_banners": n}, "Добавить 2–4 варианта креатива"))
        elif n > th["max_active_banners_per_group"]:
            out.append(finding("banners_per_group", "white", "group", g,
                               "Слишком много объявлений — бюджет размазан, выводы по каждому слабые",
                               {"active_banners": n}, "Оставить лучшие по результату"))
    return out


@rule
def low_budget(ds, th, ctx):
    out = []
    for g in _live(ds):
        b = g["budget_day_rub"]
        if not _active(g, th) or b is None:
            continue
        if b < th["min_daily_budget_rub"]:
            out.append(finding("low_budget", "orange", "group", g, "Дневной бюджет ниже минимума VK",
                               {"budget_day_rub": b, "minimum_rub": th["min_daily_budget_rub"]},
                               f"Поднять дневной бюджет до {th['min_daily_budget_rub']} ₽ и выше"))
        elif g["autobidding_mode"] and ds.get("target_cpa") and \
                b < ds["target_cpa"] * th["learning_budget_cpa_multiplier"]:
            need = ds["target_cpa"] * th["learning_budget_cpa_multiplier"]
            out.append(finding("low_budget", "orange", "group", g,
                               "Бюджета не хватает на обучение автостратегии",
                               {"budget_day_rub": b, "needed_rub": need},
                               f"Дневной бюджет не ниже {need:.0f} ₽ (≥ {th['learning_budget_cpa_multiplier']} × CPA)"))
    return out


@rule
def duplicate_targeting(ds, th, ctx):
    by_key = defaultdict(list)
    for g in _live(ds):
        if _active(g, th):
            by_key[(g["objective"], g["targeting_key"])].append(g)
    out = []
    for gs in by_key.values():
        if len(gs) > 1:
            out.append(finding("duplicate_targeting", "orange", "group", gs[0],
                               "Одинаковые таргетинги у активных групп — они конкурируют между собой",
                               {"groups": [g["id"] for g in gs]},
                               "Объединить группы или развести аудитории"))
    return out


@rule
def unused_segments(ds, th, ctx):
    used = {i for g in _live(ds) for i in g["segment_ids"]}
    return [finding("unused_segments", "white", "segment", s, "Сегмент не используется ни в одной группе",
                    {}, "Удалить или запустить на него группу")
            for s in ds["segments"] if s["id"] not in used]


@rule
def no_pixel(ds, th, ctx):
    if ds["pixels"] or not ds["coverage"]["banners"]:
        return []
    return [finding("no_pixel", "orange", "group", g, "Цель «конверсии на сайте», а пикселя в кабинете нет",
                    {"objective": g["objective"]}, "Установить пиксель VK и настроить события")
            for g in _live(ds) if _active(g, th) and g["objective"] in th["pixel_objectives"]]


# ---------- запуск ----------

def diagnose(ds: dict, th: dict) -> dict:
    ref, source = reference_cpa(ds, th)
    ctx = {"ref_cpa": ref, "ref_source": source}
    out = []
    for fn in RULES:
        out.extend(fn(ds, th, ctx))
    out.sort(key=lambda f: (LEVEL_ORDER[f["level"]], -f["effect_rub"]))
    return {"ref_cpa": ref, "ref_source": source, "findings": out}


def main(argv=None) -> int:
    setup_console()
    parser = argparse.ArgumentParser(description="dataset.json → findings.json")
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--thresholds")
    args = parser.parse_args(argv)
    ws = resolve_workspace(args.workspace)
    ds = json.loads((ws / "dataset.json").read_text(encoding="utf-8"))
    result = diagnose(ds, load_thresholds(args.thresholds))
    (ws / "findings.json").write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    counts = defaultdict(int)
    for f in result["findings"]:
        counts[f["level"]] += 1
    print(f"находок: {len(result['findings'])} ({dict(counts)}), опорный CPA: {result['ref_cpa']} "
          f"({result['ref_source']})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 6: Прогнать**

Run: `python -m pytest tests/test_diagnose_hygiene.py -v`
Expected: 14 passed.

- [ ] **Step 7: Commit**

```bash
git add scripts/diagnose.py references/thresholds.json tests/helpers.py tests/test_diagnose_hygiene.py
git commit -m "diagnose: контракт находки, опорный CPA, правила гигиены (черновик)"
```

---

### Task 8: Диагностика — правила эффективности

**Files:**
- Modify: `scripts/diagnose.py` (добавить блок `# ---------- эффективность ----------` перед `# ---------- запуск ----------`), `references/thresholds.json` (новые ключи)
- Test: `tests/test_diagnose_performance.py`

**Interfaces:**
- Consumes: `rule`, `finding`, `confidence`, `_live`, `_active`, `_banners_of`, `ctx["ref_cpa"]` (Task 7); `stats`, `make_group`, `make_banner`, `make_ds` (tests/helpers.py).
- Produces: правила `spend_no_conversions` 🔴, `cpa_above_target` 🔴, `low_ctr` 🟠, `burnout` 🟠, `learning_starved` 🟠, `spend_concentration` ⚪, `scale_leader` 🟢.

- [ ] **Step 1: Пороги** — добавить в `references/thresholds.json` (перед закрывающей скобкой, через запятую):

```json
  "no_conv_spend_cpa_multiplier": 2,
  "no_conv_min_spend_rub": 3000,
  "cpa_over_ratio": 1.5,
  "low_ctr_pct": 0.3,
  "low_ctr_min_shows": 5000,
  "burnout_ctr_drop": 0.3,
  "burnout_min_frequency": 3,
  "burnout_min_shows": 10000,
  "learning_min_conversions_per_week": 10,
  "concentration_share": 0.7,
  "concentration_min_groups": 3,
  "leader_cpa_ratio": 0.7,
  "leader_budget_use": 0.9,
  "leader_growth_share": 0.5
```

- [ ] **Step 2: Тест**

`tests/test_diagnose_performance.py`:
```python
from __future__ import annotations

from scripts.diagnose import diagnose, load_thresholds
from tests.helpers import make_banner, make_ds, make_group, stats

TH = load_thresholds()


def found(ds):
    return {(f["rule"], f["entity"]["id"]): f for f in diagnose(ds, TH)["findings"]}


def banners(gid, n=3):
    return [make_banner(id=gid * 10 + i, group_id=gid) for i in range(n)]


def day(d, spent, shows, clicks, conv=0):
    return {"date": d, **stats(spent, shows, clicks, conv)}


def test_spend_no_conversions_vs_target():
    g = make_group(1, stats=stats(spent=1200, shows=20000, clicks=200, conversions=0))
    f = found(make_ds([g], banners(1), target_cpa=500))[("spend_no_conversions", 1)]
    assert f["level"] == "red" and f["effect_rub"] == 1200.0


def test_no_reference_cpa_uses_min_spend():
    low = make_group(1, stats=stats(spent=2000, shows=20000, clicks=200))
    high = make_group(2, stats=stats(spent=5000, shows=20000, clicks=200))
    got = found(make_ds([low, high], banners(1) + banners(2)))
    assert ("spend_no_conversions", 1) not in got and ("spend_no_conversions", 2) in got


def test_cpa_above_target():
    g = make_group(1, stats=stats(spent=10000, shows=50000, clicks=500, conversions=10))
    f = found(make_ds([g], banners(1), target_cpa=500))[("cpa_above_target", 1)]
    assert f["effect_rub"] == 5000.0 and f["evidence"]["cpa"] == 1000.0


def test_cpa_above_needs_volume():
    g = make_group(1, stats=stats(spent=4000, shows=5000, clicks=50, conversions=2))
    assert ("cpa_above_target", 1) not in found(make_ds([g], banners(1), target_cpa=500))


def test_low_ctr_on_banner():
    b = make_banner(19, 1, stats=stats(spent=900, shows=10000, clicks=20))
    got = found(make_ds([make_group(1)], [b] + banners(1)))
    assert ("low_ctr", 19) in got


def test_low_ctr_needs_shows():
    b = make_banner(19, 1, stats=stats(spent=50, shows=1000, clicks=1))
    assert ("low_ctr", 19) not in found(make_ds([make_group(1)], [b] + banners(1)))


def test_burnout():
    daily = [day(f"2026-09-{d:02d}", 100, 1000, 20) for d in range(1, 16)] + \
            [day(f"2026-09-{d:02d}", 100, 1000, 10) for d in range(16, 31)]
    g = make_group(1, daily=daily, stats=stats(3000, 30000, 450, 0, frequency=4.2))
    assert ("burnout", 1) in found(make_ds([g], banners(1)))


def test_no_burnout_with_low_frequency():
    daily = [day(f"2026-09-{d:02d}", 100, 1000, 20) for d in range(1, 16)] + \
            [day(f"2026-09-{d:02d}", 100, 1000, 10) for d in range(16, 31)]
    g = make_group(1, daily=daily, stats=stats(3000, 30000, 450, 0, frequency=1.5))
    assert ("burnout", 1) not in found(make_ds([g], banners(1)))


def test_learning_starved():
    g = make_group(1, autobidding_mode="max_goals", stats=stats(3000, 30000, 300, 8))
    assert ("learning_starved", 1) in found(make_ds([g], banners(1)))


def test_spend_concentration():
    gs = [make_group(1, stats=stats(8000, 1, 1)), make_group(2, stats=stats(1000, 1, 1)),
          make_group(3, stats=stats(1000, 1, 1))]
    assert ("spend_concentration", 1) in found(make_ds(gs, banners(1) + banners(2) + banners(3)))


def test_scale_leader():
    daily = [day(f"2026-09-{d:02d}", 950, 5000, 50, 3) for d in range(1, 31)]
    g = make_group(1, budget_day_rub=1000.0, daily=daily, stats=stats(28500, 150000, 1500, 90))
    f = found(make_ds([g], banners(1), target_cpa=500))[("scale_leader", 1)]
    assert f["level"] == "green" and f["effect_rub"] == round(950 * 0.5 * 30, 2)
```

- [ ] **Step 3: Убедиться, что падает**

Run: `python -m pytest tests/test_diagnose_performance.py -v`
Expected: FAIL — находок `spend_no_conversions` и др. нет (KeyError / assert).

- [ ] **Step 4: Реализация** — вставить в `scripts/diagnose.py` перед `# ---------- запуск ----------`:

```python
# ---------- эффективность ----------

@rule
def spend_no_conversions(ds, th, ctx):
    ref = ctx["ref_cpa"]
    limit = ref * th["no_conv_spend_cpa_multiplier"] if ref else th["no_conv_min_spend_rub"]
    out = []
    for g in _live(ds):
        s = g["stats"]
        if s["conversions"] == 0 and s["spent"] >= limit:
            out.append(finding("spend_no_conversions", "red", "group", g, "Расход без конверсий",
                               {"spent": s["spent"], "clicks": s["clicks"], "limit_rub": round(limit, 2),
                                "ref_source": ctx["ref_source"]},
                               "Остановить или пересобрать: аудитория, креатив, посадочная, событие пикселя",
                               effect_rub=s["spent"], conf=confidence(s["clicks"], th)))
    return out


@rule
def cpa_above_target(ds, th, ctx):
    ref = ctx["ref_cpa"]
    if not ref:
        return []
    out = []
    for g in _live(ds):
        s = g["stats"]
        if s["conversions"] >= th["min_conversions_for_cpa"] and s["cpa"] > ref * th["cpa_over_ratio"]:
            over = s["spent"] - s["conversions"] * ref
            out.append(finding("cpa_above_target", "red", "group", g, "CPA заметно выше опорного",
                               {"cpa": s["cpa"], "ref_cpa": ref, "ref_source": ctx["ref_source"],
                                "conversions": s["conversions"]},
                               "Сузить или сменить аудиторию, заменить слабые креативы, снизить ставку",
                               effect_rub=over, conf=confidence(s["clicks"], th)))
    return out


@rule
def low_ctr(ds, th, ctx):
    entities = [("banner", b, b["stats"]) for b in ds["banners"] if b["stats"] and b["status"] != "deleted"]
    if not entities:
        entities = [("group", g, g["stats"]) for g in _live(ds)]
    return [finding("low_ctr", "orange", etype, ent, "Низкий CTR при достаточных показах",
                    {"ctr_pct": s["ctr_pct"], "shows": s["shows"], "threshold_pct": th["low_ctr_pct"]},
                    "Заменить креатив или проверить, той ли аудитории он показывается",
                    effect_rub=s["spent"], conf=confidence(s["shows"] // 100, th))
            for etype, ent, s in entities
            if s["shows"] >= th["low_ctr_min_shows"] and s["ctr_pct"] < th["low_ctr_pct"]]


def _ctr(rows):
    shows = sum(r["shows"] for r in rows)
    return sum(r["clicks"] for r in rows) / shows * 100 if shows else 0.0


@rule
def burnout(ds, th, ctx):
    out = []
    for g in _live(ds):
        rows = g["daily"]
        freq = g["stats"]["frequency"]
        if len(rows) < 4 or not freq or freq < th["burnout_min_frequency"] \
                or g["stats"]["shows"] < th["burnout_min_shows"]:
            continue
        half = len(rows) // 2
        first, second = _ctr(rows[:half]), _ctr(rows[half:])
        if first and (first - second) / first >= th["burnout_ctr_drop"]:
            out.append(finding("burnout", "orange", "group", g, "Выгорание: CTR падает, частота высокая",
                               {"ctr_first_half_pct": round(first, 3), "ctr_second_half_pct": round(second, 3),
                                "frequency": freq},
                               "Обновить креативы или расширить аудиторию",
                               effect_rub=sum(r["spent"] for r in rows[half:])))
    return out


@rule
def learning_starved(ds, th, ctx):
    weeks = max(ds["period"]["days"] / 7, 1)
    out = []
    for g in _live(ds):
        s = g["stats"]
        per_week = s["conversions"] / weeks
        if g["autobidding_mode"] and s["spent"] > 0 and per_week < th["learning_min_conversions_per_week"]:
            out.append(finding("learning_starved", "orange", "group", g,
                               "Автостратегии не хватает конверсий для обучения",
                               {"conversions_per_week": round(per_week, 1),
                                "needed": th["learning_min_conversions_per_week"]},
                               "Объединить группы, расширить аудиторию или оптимизировать на более частое событие"))
    return out


@rule
def spend_concentration(ds, th, ctx):
    live = [g for g in _live(ds) if g["stats"]["spent"] > 0]
    total = sum(g["stats"]["spent"] for g in live)
    if len(live) < th["concentration_min_groups"] or not total:
        return []
    top = max(live, key=lambda g: g["stats"]["spent"])
    share = top["stats"]["spent"] / total
    if share < th["concentration_share"]:
        return []
    return [finding("spend_concentration", "white", "group", top, "Почти весь расход в одной группе",
                    {"share": round(share, 3), "groups_with_spend": len(live)},
                    "Проверить, не голодают ли остальные группы, и осознанно перераспределить бюджет")]


@rule
def scale_leader(ds, th, ctx):
    ref = ctx["ref_cpa"]
    if not ref:
        return []
    out = []
    for g in _live(ds):
        s, b = g["stats"], g["budget_day_rub"]
        if not b or not g["daily"] or s["conversions"] < th["min_conversions_for_cpa"]:
            continue
        avg = sum(r["spent"] for r in g["daily"]) / len(g["daily"])
        if s["cpa"] <= ref * th["leader_cpa_ratio"] and avg >= b * th["leader_budget_use"]:
            out.append(finding("scale_leader", "green", "group", g, "Лидер упирается в бюджет",
                               {"cpa": s["cpa"], "ref_cpa": ref, "avg_daily_spent": round(avg, 2),
                                "budget_day_rub": b},
                               f"Поднять дневной бюджет на {int(th['leader_growth_share'] * 100)} % и следить за CPA",
                               effect_rub=avg * th["leader_growth_share"] * ds["period"]["days"],
                               conf=confidence(s["clicks"], th)))
    return out
```

- [ ] **Step 5: Прогнать все тесты диагностики**

Run: `python -m pytest tests/test_diagnose_hygiene.py tests/test_diagnose_performance.py -v`
Expected: все passed (гигиенические тесты не должны сломаться: у их групп нулевая статистика).

- [ ] **Step 6: Commit**

```bash
git add scripts/diagnose.py references/thresholds.json tests/test_diagnose_performance.py
git commit -m "diagnose: правила эффективности (черновик) — слив, CPA, CTR, выгорание, обучение, лидеры"
```

---

### Task 9: HTML-отчёт

**Files:**
- Create: `scripts/render_report.py`
- Test: `tests/test_render_report.py`

**Interfaces:**
- Consumes: `dataset.json` (Task 6), `findings.json` (Task 7–8), `collect_log.json` (Task 5, необязателен).
- Produces: `fmt_rub(v) -> str` (`"12 345 ₽"`, `None → "—"`), `fmt_pct(v) -> str` (`"1.23 %"`), `ALWAYS_LIMITATIONS: list[str]`; `limitations(ds, collect_log: dict | None) -> list[str]`; `render(ds, findings: dict, collect_log=None, title="") -> str`. CLI: `python -m scripts.render_report --workspace <abs> --slug <slug>` → `<workspace>/АУДИТ_<slug>.html`.

- [ ] **Step 1: Тест**

`tests/test_render_report.py`:
```python
from __future__ import annotations

from scripts.diagnose import diagnose, load_thresholds
from scripts.render_report import ALWAYS_LIMITATIONS, fmt_pct, fmt_rub, limitations, render
from tests.helpers import make_banner, make_ds, make_group, stats


def sample():
    g = make_group(1, name="Группа <script>alert(1)</script>",
                   stats=stats(spent=12345.6, shows=20000, clicks=200, conversions=0))
    ds = make_ds([g], [make_banner(11, 1)], target_cpa=500)
    ds["totals"] = g["stats"]
    return ds, diagnose(ds, load_thresholds())


def test_formatters():
    assert fmt_rub(12345.6) == "12 346 ₽"
    assert fmt_rub(None) == "—"
    assert fmt_pct(1.23456) == "1.23 %"


def test_numbers_and_findings_present():
    ds, fs = sample()
    html = render(ds, fs, title="acme")
    assert "12 346 ₽" in html
    for f in fs["findings"]:
        assert f["title"] in html
    for line in ALWAYS_LIMITATIONS:
        assert line in html


def test_names_escaped():
    ds, fs = sample()
    html = render(ds, fs)
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;" in html


def test_dynamic_limitations():
    ds, _ = sample()
    ds["coverage"] = {"path": "A", "banners": False, "summary": True, "metrika": False}
    log = {"calls": [{"tool": "vk_ads_users_lists_list", "ok": False, "error": "HTTP 500"}], "errors": 1}
    lines = limitations(ds, log)
    assert any("путь A" in x for x in lines)
    assert any("Метрика" in x for x in lines)
    assert any("vk_ads_users_lists_list" in x for x in lines)


def test_empty_findings_message():
    ds = make_ds([make_group(1)], [make_banner(11, 1), make_banner(12, 1)])
    html = render(ds, {"ref_cpa": None, "ref_source": "none", "findings": []})
    assert "Существенных проблем не найдено" in html
```

- [ ] **Step 2: Убедиться, что падает**

Run: `python -m pytest tests/test_render_report.py -v`
Expected: FAIL — модуля нет.

- [ ] **Step 3: Реализация**

`scripts/render_report.py`:
```python
"""
render_report.py — findings.json + dataset.json → самодостаточный HTML-отчёт (печать A4).

Каждое число отчёта — из dataset.json/findings.json, модель чисел не вписывает.
Раздел «Ограничения» обязателен: постоянные ограничения API + недобранное в прогоне.

    python -m scripts.render_report --workspace <абс. путь> --slug <slug>
"""
from __future__ import annotations

import argparse
import json
import sys
from html import escape

try:
    from scripts._console import setup_console
    from scripts._workspace import resolve_workspace
except ImportError:  # запуск напрямую
    from _console import setup_console
    from _workspace import resolve_workspace

LEVELS = [("red", "🔴 Срезать слив"), ("orange", "🟠 Починить"), ("green", "🟢 Масштабировать"),
          ("white", "⚪ Гигиена")]
CONF = {"high": "надёжно", "medium": "средне", "low": "мало данных"}
ALWAYS_LIMITATIONS = [
    "В API нет разрезов по полу, возрасту, гео, площадкам и устройствам — «кто конвертит» по ним не определить.",
    "API не показывает, срабатывает ли пиксель: ноль конверсий может означать сломанный пиксель.",
    "Атрибуция (post-click или post-view) меняет число конверсий — сверяйте с настройкой кабинета.",
    "Данные за сегодня неполные — период заканчивается вчерашним днём.",
    "Статусы ОРД видны частично.",
]
CSS = """
body{font:14px/1.45 -apple-system,Segoe UI,Roboto,sans-serif;color:#1d1d1f;margin:24px;max-width:1100px}
h1{font-size:22px;margin:0 0 4px}h2{font-size:17px;margin:28px 0 8px;border-bottom:1px solid #ddd}
.muted{color:#666}.tiles{display:flex;gap:12px;flex-wrap:wrap;margin:16px 0}
.tile{border:1px solid #ddd;border-radius:8px;padding:10px 14px;min-width:120px}
.tile b{display:block;font-size:18px}.f{border:1px solid #e3e3e3;border-radius:8px;padding:10px 14px;margin:8px 0}
.f h3{font-size:15px;margin:0 0 4px}table{border-collapse:collapse;width:100%;font-size:13px}
td,th{border-bottom:1px solid #eee;padding:4px 6px;text-align:left}th{background:#fafafa}
@media print{body{margin:0}.f{break-inside:avoid}}
"""


def fmt_rub(v) -> str:
    return "—" if v is None else f"{v:,.0f} ₽".replace(",", " ")


def fmt_pct(v) -> str:
    return "—" if v is None else f"{v:.2f} %"


def _num(v) -> str:
    if isinstance(v, bool):
        return "да" if v else "нет"
    if v is None:
        return "—"
    if isinstance(v, float):
        return f"{v:,.2f}".replace(",", " ")
    if isinstance(v, int):
        return f"{v:,}".replace(",", " ")
    return escape(json.dumps(v, ensure_ascii=False) if isinstance(v, (list, dict)) else str(v))


def limitations(ds: dict, collect_log=None) -> list:
    lines = list(ALWAYS_LIMITATIONS)
    cov = ds["coverage"]
    if cov["path"] == "A":
        lines.append("Данные собраны через модель (путь A): объявления разобраны только у групп "
                     "с наибольшим расходом.")
    if not cov["banners"]:
        lines.append("Объявления не собирались — выводы на уровне групп.")
    if not cov["metrika"]:
        lines.append("Метрика не подключена: поведение на сайте после клика не проверялось.")
    for c in (collect_log or {}).get("calls", []):
        if not c.get("ok"):
            lines.append(f"Не удалось получить {c['tool']}: {c.get('error')}")
    return lines


def _finding_html(f) -> str:
    ev = "".join(f"<tr><th>{escape(k)}</th><td>{_num(v)}</td></tr>" for k, v in f["evidence"].items())
    effect = f"<p>Эффект: <b>{fmt_rub(f['effect_rub'])}</b></p>" if f["effect_rub"] else ""
    return (f'<div class="f"><h3>{escape(f["title"])}</h3>'
            f'<p class="muted">{escape(f["entity"]["type"])} {escape(str(f["entity"]["id"]))} · '
            f'{escape(f["entity"]["name"])} · надёжность: {CONF.get(f["confidence"], f["confidence"])}</p>'
            f'<table>{ev}</table>{effect}<p>Действие: {escape(f["action"])}</p></div>')


def render(ds: dict, findings: dict, collect_log=None, title: str = "") -> str:
    t = ds["totals"]
    tiles = [("Расход", fmt_rub(t["spent"])), ("Показы", _num(t["shows"])), ("Клики", _num(t["clicks"])),
             ("Конверсии", _num(t["conversions"])), ("CPA", fmt_rub(t["cpa"])), ("CTR", fmt_pct(t["ctr_pct"]))]
    ref = findings.get("ref_cpa")
    ref_note = {"target": "целевой CPA клиента", "median": "медиана CPA групп кабинета (цель не задана)",
                "none": "опорного CPA нет — пороги по минимальному расходу"}[findings.get("ref_source", "none")]
    parts = [f"<!doctype html><html lang='ru'><head><meta charset='utf-8'>"
             f"<title>Аудит VK Рекламы {escape(title)}</title><style>{CSS}</style></head><body>",
             f"<h1>Аудит VK Рекламы {escape(title)}</h1>",
             f"<p class='muted'>Период {ds['period']['from']} — {ds['period']['to']} · путь {ds['coverage']['path']}"
             f" · опорный CPA: {fmt_rub(ref)} ({ref_note})</p>",
             "<div class='tiles'>" + "".join(f"<div class='tile'>{k}<b>{v}</b></div>" for k, v in tiles) + "</div>",
             "<h2>Находки</h2>"]
    if not findings["findings"]:
        parts.append("<p>Существенных проблем не найдено.</p>")
    for level, label in LEVELS:
        items = [f for f in findings["findings"] if f["level"] == level]
        if items:
            parts.append(f"<h2>{label} ({len(items)})</h2>" + "".join(_finding_html(f) for f in items))
    rows = "".join(
        f"<tr><td>{escape(g['name'])}</td><td>{escape(str(g['status']))}</td><td>{fmt_rub(g['stats']['spent'])}</td>"
        f"<td>{g['stats']['conversions']}</td><td>{fmt_rub(g['stats']['cpa'])}</td>"
        f"<td>{fmt_pct(g['stats']['ctr_pct'])}</td><td>{len(g['banner_ids'])}</td></tr>"
        for g in sorted(ds["groups"], key=lambda g: -g["stats"]["spent"]) if g["status"] != "deleted")
    parts.append("<h2>Структура</h2><table><tr><th>Группа</th><th>Статус</th><th>Расход</th><th>Конв.</th>"
                 f"<th>CPA</th><th>CTR</th><th>Объявл.</th></tr>{rows}</table>")
    parts.append("<h2>Ограничения</h2><ul>" +
                 "".join(f"<li>{escape(x)}</li>" for x in limitations(ds, collect_log)) + "</ul></body></html>")
    return "\n".join(parts)


def main(argv=None) -> int:
    setup_console()
    parser = argparse.ArgumentParser(description="findings.json → HTML-отчёт")
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--slug", required=True)
    args = parser.parse_args(argv)
    ws = resolve_workspace(args.workspace)
    ds = json.loads((ws / "dataset.json").read_text(encoding="utf-8"))
    fs = json.loads((ws / "findings.json").read_text(encoding="utf-8"))
    log_path = ws / "collect_log.json"
    log = json.loads(log_path.read_text(encoding="utf-8")) if log_path.is_file() else None
    out = ws / f"АУДИТ_{args.slug}.html"
    out.write_text(render(ds, fs, log, title=args.slug), encoding="utf-8")
    print(f"✓ {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Прогнать**

Run: `python -m pytest tests/test_render_report.py -v`
Expected: 5 passed.

- [ ] **Step 5: Commit**

```bash
git add scripts/render_report.py tests/test_render_report.py
git commit -m "render_report: HTML-отчёт с находками, структурой и обязательными ограничениями"
```

---

### Task 10: Быстрый анализ

**Files:**
- Create: `scripts/quick.py`
- Test: `tests/test_quick.py`

**Interfaces:**
- Consumes: `dataset.json` с `daily` по группам (собран `collect --mode quick` + `normalize`), `aggregate` (Task 6).
- Produces: `quick_summary(ds: dict, day: str, target_cpa: float | None = None) -> dict` (`{"day", "today": stats, "prev_avg": stats, "groups": [{"id","name","spent","conversions","cpa","flags":[...]}], "lines": [str]}`, `lines` ≤ 10); флаги `stopped`, `spend_spike`, `cpa_jump`, `over_target`. CLI: `python -m scripts.quick --workspace <abs> --day YYYY-MM-DD [--target-cpa N]` → печать `lines` и `quick.json`.

- [ ] **Step 1: Тест**

`tests/test_quick.py`:
```python
from __future__ import annotations

from scripts.quick import quick_summary
from tests.helpers import make_ds, make_group, stats


def rows(values):
    # values: список (spent, conversions) на даты 2026-10-06..
    return [{"date": f"2026-10-{6 + i:02d}", **stats(s, 1000, 10, c)} for i, (s, c) in enumerate(values)]


def test_flags_and_lines():
    normal = make_group(1, name="Норма", daily=rows([(100, 2)] * 8))
    spike = make_group(2, name="Всплеск", daily=rows([(100, 2)] * 7 + [(300, 2)]))
    stopped = make_group(3, name="Встала", daily=rows([(100, 2)] * 7 + [(0, 0)]))
    res = quick_summary(make_ds([normal, spike, stopped]), "2026-10-13")
    flags = {g["id"]: g["flags"] for g in res["groups"]}
    assert flags[1] == [] and "spend_spike" in flags[2] and "stopped" in flags[3]
    assert res["today"]["spent"] == 400.0
    assert 3 <= len(res["lines"]) <= 10
    assert res["lines"][0].startswith("📅")


def test_over_target():
    g = make_group(1, daily=rows([(100, 1)] * 7 + [(600, 1)]))
    res = quick_summary(make_ds([g]), "2026-10-13", target_cpa=300)
    assert "over_target" in res["groups"][0]["flags"]


def test_no_data_for_day():
    res = quick_summary(make_ds([make_group(1, daily=[])]), "2026-10-13")
    assert res["today"]["spent"] == 0.0
    assert any("нет" in line.lower() for line in res["lines"])


def test_lines_capped_at_ten():
    gs = [make_group(i, daily=rows([(100, 2)] * 7 + [(0, 0)])) for i in range(1, 30)]
    assert len(quick_summary(make_ds(gs), "2026-10-13")["lines"]) <= 10
```

- [ ] **Step 2: Убедиться, что падает**

Run: `python -m pytest tests/test_quick.py -v`
Expected: FAIL — модуля нет.

- [ ] **Step 3: Реализация**

`scripts/quick.py`:
```python
"""
quick.py — быстрый анализ: день против среднего за 7 дней до него, по группам.

В чат уходит ≤ 10 строк: итог дня и только аномалии. Серьёзное → предложить глубокий аудит.

    python -m scripts.quick --workspace <абс. путь> --day 2026-10-13 [--target-cpa N]
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date, timedelta

try:
    from scripts._console import setup_console
    from scripts._workspace import resolve_workspace
    from scripts.normalize import aggregate
except ImportError:  # запуск напрямую
    from _console import setup_console
    from _workspace import resolve_workspace
    from normalize import aggregate

SPIKE = 1.5
CPA_JUMP = 1.5
MAX_LINES = 10
ICON = {"stopped": "⛔", "spend_spike": "📈", "cpa_jump": "🔺", "over_target": "🔴"}
TEXT = {"stopped": "не открутилась", "spend_spike": "расход выше обычного", "cpa_jump": "CPA вырос",
        "over_target": "CPA выше цели"}


def _rub(v):
    return "—" if v is None else f"{v:,.0f} ₽".replace(",", " ")


def quick_summary(ds: dict, day: str, target_cpa=None) -> dict:
    d = date.fromisoformat(day)
    prev_days = {(d - timedelta(days=i)).isoformat() for i in range(1, 8)}
    today_rows, prev_rows, groups = [], [], []
    for g in ds["groups"]:
        if g["status"] == "deleted":
            continue
        t = [r for r in g["daily"] if r["date"] == day]
        p = [r for r in g["daily"] if r["date"] in prev_days]
        today_rows += t
        prev_rows += p
        ts, ps = aggregate(t), aggregate(p)
        avg = ps["spent"] / 7
        flags = []
        if ts["spent"] == 0 and avg > 0:
            flags.append("stopped")
        elif avg and ts["spent"] > avg * SPIKE:
            flags.append("spend_spike")
        if ts["cpa"] and ps["cpa"] and ts["cpa"] > ps["cpa"] * CPA_JUMP:
            flags.append("cpa_jump")
        if ts["cpa"] and target_cpa and ts["cpa"] > target_cpa:
            flags.append("over_target")
        groups.append({"id": g["id"], "name": g["name"], "spent": ts["spent"],
                       "conversions": ts["conversions"], "cpa": ts["cpa"], "flags": flags})
    today, prev = aggregate(today_rows), aggregate(prev_rows)
    lines = [f"📅 VK Реклама — {d.strftime('%d.%m.%Y')}"]
    if not today_rows:
        lines.append("Данных за этот день нет — возможно, статистика ещё не пришла.")
    else:
        lines.append(f"Расход: {_rub(today['spent'])} (в среднем {_rub(prev['spent'] / 7)} в день за неделю)")
        lines.append(f"Конверсии: {today['conversions']} · CPA: {_rub(today['cpa'])}")
    flagged = sorted((g for g in groups if g["flags"]), key=lambda g: -g["spent"])
    for g in flagged[:MAX_LINES - len(lines) - 1]:
        f = g["flags"][0]
        lines.append(f"{ICON[f]} «{g['name']}»: {TEXT[f]} ({_rub(g['spent'])}, CPA {_rub(g['cpa'])})")
    if flagged:
        lines.append("→ Разобрать подробно? Могу провести глубокий аудит.")
    elif today_rows:
        lines.append("Аномалий нет.")
    return {"day": day, "today": today, "prev_avg": {k: v for k, v in prev.items()},
            "groups": groups, "lines": lines[:MAX_LINES]}


def main(argv=None) -> int:
    setup_console()
    parser = argparse.ArgumentParser(description="Быстрый анализ дня")
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--day", required=True)
    parser.add_argument("--target-cpa", type=float)
    args = parser.parse_args(argv)
    ws = resolve_workspace(args.workspace)
    ds = json.loads((ws / "dataset.json").read_text(encoding="utf-8"))
    res = quick_summary(ds, args.day, args.target_cpa)
    (ws / "quick.json").write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    print("\n".join(res["lines"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Прогнать**

Run: `python -m pytest tests/test_quick.py -v`
Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add scripts/quick.py tests/test_quick.py
git commit -m "quick: быстрый анализ дня — итог и аномалии в 10 строк"
```

---

### Task 11: Метрика — склейка и правила

**Files:**
- Modify: `scripts/normalize.py` (функция `merge_metrika`, вызов в `main`), `scripts/diagnose.py` (блок `# ---------- метрика ----------` перед `# ---------- запуск ----------`), `references/thresholds.json`
- Create: `references/metrika-vk.md`
- Test: `tests/test_metrika.py`

**Interfaces:**
- Consumes: `build_dataset` (Task 6), `rule/finding/_live/confidence` (Task 7).
- Produces: контракт `metrika.json` (пишет модель по `references/metrika-vk.md`):
  ```
  {"counter_id": int, "utm_tagging": bool,
   "rows": [{"utm_campaign": "<id группы>", "utm_content": "<id объявления>|''", "visits": int,
             "bounce_rate": float (доля 0..1), "goal_reaches": int}],
   "total": {"visits": int, "bounce_rate": float, "goal_reaches": int}}
  ```
  `merge_metrika(ds: dict, metrika: dict) -> dict` — кладёт `g["metrika"] = {"visits","bounce_rate","goal_reaches"} | None` в каждую группу (сумма строк с `utm_campaign == str(id)`, `bounce_rate` — средневзвешенно по визитам), `ds["metrika_total"]`, `ds["coverage"]["metrika"] = True`, `ds["coverage"]["metrika_utm"] = utm_tagging`. Правила: `high_bounce` 🟠, `click_loss` 🟠, `goals_without_vk_conversions` 🟠.

- [ ] **Step 1: Пороги** — добавить в `references/thresholds.json`:

```json
  "metrika_min_visits": 100,
  "high_bounce_rate": 0.6,
  "click_loss_min_clicks": 200,
  "click_loss_visit_ratio": 0.6
```

- [ ] **Step 2: Тест**

`tests/test_metrika.py`:
```python
from __future__ import annotations

from scripts.diagnose import diagnose, load_thresholds
from scripts.normalize import merge_metrika
from tests.helpers import make_banner, make_ds, make_group, stats

TH = load_thresholds()


def metrika(rows, utm=True):
    return {"counter_id": 1, "utm_tagging": utm, "rows": rows,
            "total": {"visits": sum(r["visits"] for r in rows), "bounce_rate": 0.5, "goal_reaches": 0}}


def banners(gid):
    return [make_banner(gid * 10 + i, gid) for i in range(3)]


def test_merge_weighted_bounce():
    ds = make_ds([make_group(1), make_group(2)])
    merge_metrika(ds, metrika([
        {"utm_campaign": "1", "utm_content": "11", "visits": 100, "bounce_rate": 0.2, "goal_reaches": 1},
        {"utm_campaign": "1", "utm_content": "12", "visits": 300, "bounce_rate": 0.6, "goal_reaches": 2},
    ]))
    m = ds["groups"][0]["metrika"]
    assert m == {"visits": 400, "bounce_rate": 0.5, "goal_reaches": 3}
    assert ds["groups"][1]["metrika"] is None
    assert ds["coverage"]["metrika"] is True and ds["coverage"]["metrika_utm"] is True


def rules(ds):
    return {(f["rule"], f["entity"]["id"]) for f in diagnose(ds, TH)["findings"]}


def test_high_bounce():
    ds = make_ds([make_group(1, stats=stats(1000, 10000, 300, 5))], banners(1))
    merge_metrika(ds, metrika([{"utm_campaign": "1", "utm_content": "", "visits": 250,
                                "bounce_rate": 0.75, "goal_reaches": 5}]))
    assert ("high_bounce", 1) in rules(ds)


def test_click_loss():
    ds = make_ds([make_group(1, stats=stats(1000, 10000, 500, 5))], banners(1))
    merge_metrika(ds, metrika([{"utm_campaign": "1", "utm_content": "", "visits": 150,
                                "bounce_rate": 0.3, "goal_reaches": 5}]))
    assert ("click_loss", 1) in rules(ds)


def test_goals_without_vk_conversions():
    ds = make_ds([make_group(1, stats=stats(1000, 10000, 300, 0))], banners(1))
    merge_metrika(ds, metrika([{"utm_campaign": "1", "utm_content": "", "visits": 250,
                                "bounce_rate": 0.3, "goal_reaches": 12}]))
    assert ("goals_without_vk_conversions", 1) in rules(ds)


def test_no_metrika_no_findings():
    ds = make_ds([make_group(1, stats=stats(1000, 10000, 500, 0))], banners(1))
    assert not {r for r, _ in rules(ds)} & {"high_bounce", "click_loss", "goals_without_vk_conversions"}
```

- [ ] **Step 3: Убедиться, что падает**

Run: `python -m pytest tests/test_metrika.py -v`
Expected: FAIL — `ImportError: cannot import name 'merge_metrika'`.

- [ ] **Step 4: Реализация в `scripts/normalize.py`** — добавить после `check_sanity`:

```python
def merge_metrika(ds: dict, metrika: dict) -> dict:
    by_group: dict = {}
    for r in metrika.get("rows") or []:
        acc = by_group.setdefault(str(r.get("utm_campaign", "")), {"visits": 0, "bounced": 0.0, "goal_reaches": 0})
        v = int(r.get("visits") or 0)
        acc["visits"] += v
        acc["bounced"] += v * float(r.get("bounce_rate") or 0)
        acc["goal_reaches"] += int(r.get("goal_reaches") or 0)
    for g in ds["groups"]:
        acc = by_group.get(str(g["id"]))
        g["metrika"] = None if not acc else {
            "visits": acc["visits"],
            "bounce_rate": round(acc["bounced"] / acc["visits"], 4) if acc["visits"] else None,
            "goal_reaches": acc["goal_reaches"]}
    ds["metrika_total"] = metrika.get("total")
    ds["coverage"]["metrika"] = True
    ds["coverage"]["metrika_utm"] = bool(metrika.get("utm_tagging"))
    return ds
```

и в `main()` сразу после `ds = build_dataset(...)` (внутри `try`):

```python
        metrika_path = ws / "metrika.json"
        if metrika_path.is_file():
            merge_metrika(ds, json.loads(metrika_path.read_text(encoding="utf-8")))
```

- [ ] **Step 5: Реализация в `scripts/diagnose.py`** — перед `# ---------- запуск ----------`:

```python
# ---------- метрика ----------

def _with_metrika(ds):
    return [g for g in _live(ds) if g.get("metrika")]


@rule
def high_bounce(ds, th, ctx):
    return [finding("high_bounce", "orange", "group", g, "Высокий отказ трафика из группы (Метрика)",
                    {"bounce_rate": g["metrika"]["bounce_rate"], "visits": g["metrika"]["visits"]},
                    "Проверить соответствие посадочной обещанию креатива и скорость загрузки",
                    effect_rub=g["stats"]["spent"] * g["metrika"]["bounce_rate"],
                    conf=confidence(g["metrika"]["visits"], th))
            for g in _with_metrika(ds)
            if g["metrika"]["visits"] >= th["metrika_min_visits"]
            and (g["metrika"]["bounce_rate"] or 0) >= th["high_bounce_rate"]]


@rule
def click_loss(ds, th, ctx):
    out = []
    for g in _with_metrika(ds):
        clicks, visits = g["stats"]["clicks"], g["metrika"]["visits"]
        if clicks >= th["click_loss_min_clicks"] and visits / clicks < th["click_loss_visit_ratio"]:
            out.append(finding("click_loss", "orange", "group", g, "Кликов в VK заметно больше, чем визитов в Метрике",
                               {"clicks": clicks, "visits": visits, "ratio": round(visits / clicks, 3)},
                               "Проверить скорость посадочной, редиректы и разметку UTM",
                               effect_rub=g["stats"]["spent"] * (1 - visits / clicks)))
    return out


@rule
def goals_without_vk_conversions(ds, th, ctx):
    return [finding("goals_without_vk_conversions", "orange", "group", g,
                    "Цели в Метрике есть, а конверсий в VK нет — пиксель или атрибуция",
                    {"metrika_goal_reaches": g["metrika"]["goal_reaches"], "vk_conversions": 0},
                    "Проверить пиксель VK и событие оптимизации группы")
            for g in _with_metrika(ds)
            if g["metrika"]["goal_reaches"] > 0 and g["stats"]["conversions"] == 0]
```

- [ ] **Step 6: `references/metrika-vk.md`**

```markdown
# Метрика для аудита VK

Необязательный усилитель: VK показывает клики и конверсии пикселя, Метрика — что люди делали на сайте.
Нет коннектора Метрики — шаг пропускается, в отчёте строка в «Ограничениях».

## Как собрать (модель, коннектор Метрики, только чтение)

1. Счётчик: спроси у пользователя или возьми из `yandex_metrika_counters_get` тот, что на домене посадочных.
2. Отчёт за тот же период, что аудит:
   - метрики `ym:s:visits,ym:s:bounceRate,ym:s:goal<ID>reaches` (цель — та, что важна клиенту; нет — `ym:s:sumGoalReachesAny`);
   - измерения `ym:s:UTMCampaign,ym:s:UTMContent`;
   - фильтр `ym:s:UTMSource=='vk_ads'` (шаблон UTM пакетов VK по умолчанию);
   - `limit` 1000.
3. Пусто при наличии кликов в VK → авторазметка выключена: повтори без измерений с фильтром
   `ym:s:lastTrafficSource=='ad' AND ym:s:referer=@'vk.'`, `utm_tagging: false`.
4. Запиши `<workspace>/metrika.json` строго по контракту:

```json
{"counter_id": 123, "utm_tagging": true,
 "rows": [{"utm_campaign": "150895042", "utm_content": "", "visits": 250, "bounce_rate": 0.43, "goal_reaches": 7}],
 "total": {"visits": 250, "bounce_rate": 0.43, "goal_reaches": 7}}
```

`bounce_rate` — доля 0..1 (Метрика отдаёт проценты — раздели на 100). `utm_campaign` = id группы VK.

## Что проверяет диагностика

`high_bounce`, `click_loss`, `goals_without_vk_conversions` — смысл и пороги в `diagnostic-rules.md`.
```

- [ ] **Step 7: Прогнать все тесты**

Run: `python -m pytest -v`
Expected: все passed.

- [ ] **Step 8: Commit**

```bash
git add scripts/normalize.py scripts/diagnose.py references/thresholds.json references/metrika-vk.md tests/test_metrika.py
git commit -m "Метрика: контракт metrika.json, склейка по UTM, правила отказов и потери кликов"
```

---

### Task 12: SKILL.md, справочники, плагин, сборка

**Files:**
- Create: `SKILL.md`, `README.md`, `CHANGELOG.md`, `.claude-plugin/plugin.json`, `.github/workflows/version-check.yml` (копия), `references/mcp-read-tools.md`, `references/diagnostic-rules.md`, `references/limitations.md`, `references/report-template.md`, `scripts/make_bundle.py`
- Test: `tests/test_skill_docs.py`

**Interfaces:**
- Consumes: CLI всех скриптов (Tasks 2–11), `ALWAYS_LIMITATIONS` (Task 9), `RULES` (Task 7).
- Produces: `make_bundle.build(output: Path) -> Path` (zip `vk-ads-audit/...` без `tests/`, `evals/`, `docs/`, `.github/`, `.claude-plugin/`, кешей, `.env`, `.mcp.json`, `vk-audits/`); CLI `python -m scripts.make_bundle [--output path]`.

- [ ] **Step 1: Тест**

`tests/test_skill_docs.py`:
```python
from __future__ import annotations

import json
import re
import zipfile

from scripts._workspace import SKILL_ROOT
from scripts.diagnose import RULES
from scripts.make_bundle import build
from scripts.render_report import ALWAYS_LIMITATIONS

SKILL = (SKILL_ROOT / "SKILL.md").read_text(encoding="utf-8")


def test_skill_md_size():
    assert len(SKILL.splitlines()) <= 400


def test_frontmatter():
    assert SKILL.startswith("---\nname: \"vk-ads-audit\"\ndescription: \"")


def test_referenced_files_exist():
    for rel in set(re.findall(r"`((?:references|scripts)/[\w./-]+)`", SKILL)):
        assert (SKILL_ROOT / rel).exists(), rel


def test_versions_match():
    plugin = json.loads((SKILL_ROOT / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))
    version = (SKILL_ROOT / "VERSION").read_text(encoding="utf-8").strip()
    top = re.search(r"^## (\d+\.\d+\.\d+)", (SKILL_ROOT / "CHANGELOG.md").read_text(encoding="utf-8"), re.M)
    assert plugin["version"] == version == top.group(1)


def test_every_rule_documented():
    doc = (SKILL_ROOT / "references" / "diagnostic-rules.md").read_text(encoding="utf-8")
    for fn in RULES:
        assert f"`{fn.__name__}`" in doc, fn.__name__


def test_limitations_doc_matches_report():
    doc = (SKILL_ROOT / "references" / "limitations.md").read_text(encoding="utf-8")
    for line in ALWAYS_LIMITATIONS:
        assert line in doc


def test_bundle_excludes_dev_and_secrets(tmp_path):
    env = SKILL_ROOT / ".env"
    created = not env.exists()
    if created:
        env.write_text("SECRET=1", encoding="utf-8")
    try:
        names = zipfile.ZipFile(build(tmp_path / "vk-ads-audit.zip")).namelist()
    finally:
        if created:
            env.unlink()
    assert "vk-ads-audit/SKILL.md" in names and "vk-ads-audit/VERSION" in names
    assert "vk-ads-audit/references/thresholds.json" in names
    for bad in ("tests/", "evals/", "docs/", ".github/", ".claude-plugin/", ".env", "__pycache__"):
        assert not any(bad in n for n in names), bad
```

- [ ] **Step 2: Убедиться, что падает**

Run: `python -m pytest tests/test_skill_docs.py -v`
Expected: FAIL — нет `SKILL.md` / `make_bundle`.

- [ ] **Step 3: `SKILL.md`**

```markdown
---
name: "vk-ads-audit"
description: "Аудит и анализ кабинета VK Рекламы (ads.vk.ru) только на чтение через MCP vk-ads, опционально с Метрикой. ГЛУБОКИЙ АУДИТ — структура кампаний, групп и объявлений, расход и результат, слив без конверсий, CPA выше цели, низкий CTR, выгорание креативов, голодание автостратегий, гигиена (пустые группы, отклонённые объявления, бюджеты, дубли таргетингов, пиксель), лидеры для масштабирования → HTML-отчёт с приоритетами и ограничениями. БЫСТРЫЙ АНАЛИЗ — сводка за вчера или неделю, аномалии в 10 строк. Триггеры: «аудит VK», «разбери кабинет VK», «почему дорогой лид в VK», «что отключить в VK», «как у нас в VK», «сводка VK за вчера», «как прошла неделя в VK». НЕ для создания и правки кампаний — это vk-ads-manager."
---

# VK Реклама — аудит и анализ (только чтение)

Два режима над одним кабинетом:

- **Глубокий аудит** — разовая диагностика за период → `АУДИТ_<slug>.html` с находками по приоритету.
- **Быстрый анализ** — «как вчера/неделя» → 5–10 строк в чат, только аномалии.

**Скилл ничего не меняет в кабинете.** Скрипты ходят в MCP через белый список инструментов чтения
(`scripts/mcp_client.py`). Ты на пути A вызываешь **только** инструменты чтения: `*_list`, `*_get`,
`vk_ads_statistics_*`, `vk_ads_auth_check`, `vk_ads_account_info`, `vk_ads_accounts_list`. Просьбу «поправь»,
«выключи», «создай» передай в vk-ads-manager: находки сформулированы как действия.

**Ты не считаешь.** Все числа — из `dataset.json` и `findings.json`, их считают скрипты. Твоя работа —
маршрут, вопросы, выводы словами и гейт.

## Проверка версии (один раз за разговор, до выбора режима)

Прочитай `VERSION` рядом с этим файлом и одним запросом загрузи
`https://raw.githubusercontent.com/ai-hub-open/vk-ads-audit/main/VERSION`.

- Совпадает или загрузить не вышло — молча работай дальше.
- На GitHub новее (semver) — одним сообщением: вышла X (у тебя Y),
  [CHANGELOG](https://github.com/ai-hub-open/vk-ads-audit/blob/main/CHANGELOG.md); обновление:
  Claude Code — `/plugin marketplace update ai-hub-open` и перезапуск; архив — скачать `vk-ads-audit.skill` из
  [релизов](https://github.com/ai-hub-open/vk-ads-audit/releases/latest). Спроси: обновиться или продолжить.

Сам файлы скилла не скачивай и не заменяй.

## Режим

| Запрос | Режим |
|---|---|
| «аудит», «разбери кабинет», «почему дорого», «что отключить», «найди точки роста» | Глубокий аудит |
| «как у нас», «сводка за вчера», «как прошла неделя», один конкретный вопрос | Быстрый анализ |

Не уверен — спроси одним вопросом.

## Где что лежит

Скрипты — в папке скилла, запуск `python -m scripts.<имя>` только оттуда. Рабочая папка —
`<папка пользователя>/vk-audits/<slug>-<YYYY-MM-DD>/`, **не внутри скилла**; `--workspace` всегда абсолютный.
Скрипты сами откажутся работать с папкой внутри скилла. Ниже `<ws>` — этот абсолютный путь.

Запуска кода в среде нет — скилл работать не может (вся арифметика в скриптах): скажи об этом прямо.

---

# Глубокий аудит

## Шаг 0. Охват

1. Кабинет: `vk_ads_accounts_list`; несколько — покажи список, зафиксируй `account_id`.
2. Период: по умолчанию 30 последних полных дней (до вчера), минимум 14.
3. Целевой CPA/CPL: спроси один раз. Нет — пороги к медиане кабинета, в отчёте пометка.
4. Создай `<ws>`, запиши `_state.json`: `{"mode","account_id","date_from","date_to","target_cpa"}`.

## Шаг 1. Предполётка (только чтение)

1. `vk_ads_auth_check`, `vk_ads_account_info` (баланс, валюта).
2. Путь B: `python -m scripts.collect --probe --account-id <ID>`.
   - Код 0 → путь B.
   - Код 2 «ссылка не сохранена» → спроси у пользователя персональную ссылку подключения MCP
     (вид `https://…/c/<токен>`), сохрани: `python -m scripts.credentials set --url <ссылка>`, повтори probe.
     В чате ссылку не повторяй. Нет ссылки или probe снова 2 → путь A.
3. Метрика: есть ли коннектор Метрики; если да — какой счётчик (спроси или найди по домену посадочных).
4. Запиши выбор в `_state.json`, в чат одной строкой: «Путь B, Метрика: счётчик N» или что недоступно.

## Шаг 2. Сбор

**Путь B:** `python -m scripts.collect --workspace <ws> --account-id <ID> --date-from D --date-to D`.
Ошибки вызовов — в `collect_log.json`, они сами попадут в ограничения; сбор не повторяй вслепую.

**Путь A** — ты кладёшь ответы инструментов в `<ws>/raw/` по формату `scripts/raw_store.py`:
файл `<инструмент>__<NNN>.json` = `{"tool": ..., "arguments": {...}, "response": <ответ целиком, без правок>}`.
Порядок и поля — `references/mcp-read-tools.md`. На пути A: кампании, группы, статистика по дням и сводка
по всем группам; объявления и их сводка — только у 5 групп с наибольшим расходом.
После — `python -m scripts.raw_store check --workspace <ws>`; ошибки исправь до шага 3.

## Шаг 3. Нормализация

`python -m scripts.normalize --workspace <ws> --date-from D --date-to D [--target-cpa N] --path A|B`

Код 3 — **остановись**: покажи пользователю строки из `sanity.json` и скажи, что отчёт на таких числах
строить нельзя. `ctr_units` значит, что формат ответа VK изменился, — это повод для задачи разработчикам.
Расход за период 0 — скажи сразу и предложи другой период; пустой отчёт не делай.

## Шаг 4. Метрика (если подключена)

По `references/metrika-vk.md` собери `<ws>/metrika.json`, затем повтори Шаг 3 — нормализация подхватит файл.

## Шаг 5. Диагностика

`python -m scripts.diagnose --workspace <ws>` → `findings.json`. Правила и пороги —
`references/diagnostic-rules.md`, `references/thresholds.json`.

## Шаг 6. Гейт

В чат 5–10 строк: топ находок (уровень, объект, ₽, действие) из `findings.json` и один вопрос:
«Есть контекст, который меняет вывод? Например, группа тестовая, сезон, акция». Ответ учитывай: находку,
которую пользователь объяснил, перенеси в отчёт с пометкой «объяснено клиентом» — правкой `findings.json`
(поле `client_note`), числа не трогай.

## Шаг 7. Отчёт

`python -m scripts.render_report --workspace <ws> --slug <slug>` → `<ws>/АУДИТ_<slug>.html`.
Покажи файл способом, который есть в среде, и дай сводку 5–10 строк. Структура отчёта —
`references/report-template.md`. Предложи передать действия в vk-ads-manager.

---

# Быстрый анализ

1. Охват: кабинет, день (по умолчанию вчера), целевой CPA если известен.
2. Предполётка как в Шаге 1, без Метрики.
3. Путь B: `python -m scripts.collect --workspace <ws> --account-id <ID> --mode quick --date-from <день−7> --date-to <день>`;
   путь A: `vk_ads_campaigns_list` (поля из `references/mcp-read-tools.md`) и `vk_ads_statistics_day` по живым группам
   за те же 8 дней → `raw/`.
4. `python -m scripts.normalize --workspace <ws> --date-from <день−7> --date-to <день> --path A|B`
5. `python -m scripts.quick --workspace <ws> --day <день> [--target-cpa N]` — выведи его строки как есть.
6. Есть аномалии — предложи глубокий аудит; сам не запускай.

---

# Ограничения

Всегда входят в отчёт — `references/limitations.md`. Плюс недобранное в прогоне: путь A, нет Метрики,
нет UTM-разметки, ошибки сбора. Молча упрощённый результат хуже честно недобранного.

# Стиль

- Один вопрос за раз. Артефакт — в файл, в чат — сводка 5–10 строк.
- Не выдумывай причин: «не знаю, данных нет» лучше догадки. Гипотеза — помечай как гипотезу.
- Деньги — в рублях, CTR — в процентах; сырые значения API не показывай.

# Возобновление

«Продолжаем аудит VK» → найди последнюю `vk-audits/*/_state.json`, по наличию файлов
(`raw/`, `dataset.json`, `findings.json`, `АУДИТ_*.html`) продолжи с первого незавершённого шага.
```

- [ ] **Step 4: Справочники**

`references/mcp-read-tools.md`:
```markdown
# Инструменты MCP vk-ads для аудита (только чтение)

Сервер vk-ads-mcp 0.11.0. Иерархия: `ad_plan` (UI «Кампания») → `campaign` (UI «Группа») → `banner` (UI «Объявление»).
`campaigns_*` и `ad_groups_*` — один ресурс; используем `campaigns_*`.

## Порядок сбора (путь A повторяет его руками)

| # | Инструмент | Аргументы | Файл raw/ |
|---|---|---|---|
| 1 | `vk_ads_ad_plans_list` | `fields=id,name,status,objective,budget_limit,budget_limit_day,date_start,date_end,created,updated`, `limit=250`, `offset` | `vk_ads_ad_plans_list__NNN.json` |
| 2 | `vk_ads_campaigns_list` | `fields=id,name,status,objective,package_id,ad_plan_id,budget_limit,budget_limit_day,targetings,issues,created,updated,date_start,date_end,delivery,autobidding_mode,max_price,priced_goal` | `vk_ads_campaigns_list__NNN.json` |
| 3 | `vk_ads_banners_list` | `campaign_id=<группа>`, `fields=id,name,campaign_id,status,moderation_status,moderation_reasons,issues,delivery,textblocks,urls,content,created,updated,user_can_request_remoderation` | по файлу на группу |
| 4 | `vk_ads_remarketing_segments_list`, `vk_ads_users_lists_list`, `vk_ads_remarketing_pixels_list` | — | по файлу |
| 5 | `vk_ads_statistics_summary` | `entity=campaigns`, `ids` ≤ 50 через запятую, `date_from`, `date_to`, `metrics=all` | по файлу на пачку |
| 6 | `vk_ads_statistics_day` | то же | по файлу на пачку |
| 7 | `vk_ads_statistics_summary` | `entity=banners`, `ids` объявлений | по файлу на пачку |

Без `fields` списки отдают только `id`, `name`, `package_id`. Неизвестные имена в `fields` сервер игнорирует.

## Ответы

- Списки: `{"count"?, "items": [...], "offset"?}` — `count` бывает не у всех.
- Статистика: `{"items": [{"id", "total": {"base": {...}, "uniques": {...}, ...}, "rows"?: [{"date", "base", ...}]}], "total": {...}}`;
  `rows` — только у `statistics_day`. Сущностей без показов в `items` может не быть — это нули.
- `base`: `shows`, `clicks`, `goals`, `spent` (строка, ₽), `cpm`/`cpc`/`cpa` (строки), `ctr`/`cr` (доли), `vk.{goals,result,cpa,cr,cpr}`.
- `uniques`: `reach`, `frequency`.
- Группа: `budget_limit`/`budget_limit_day` — **копейки**; `max_price` — строка в рублях; `issues: [{"code","message"}]`;
  `delivery`; `autobidding_mode` (например `max_goals`); `targetings` (`geo.regions`, `age.age_list`, `sex`, `pads`, `fulltime`…).
- Ошибка: `isError` + текст, часто JSON `{"error": true, "status": N, "detail": ...}`.

## Проверить живым прогоном (до релиза 0.1.0)

- Конверсии: `base.goals` (сайт) и `base.vk.result` (лид-формы, сообщество) — верно ли по целям (`CONVERSION_SOURCE` в `scripts/normalize.py`).
- `ctr` — доля (проверка `ctr_units` в `check_sanity`).
- Сколько `ids` принимает один вызов статистики (сейчас пачки по 50).
- Имена полей объявления: `moderation_status`, `moderation_reasons`, `user_can_request_remoderation`.
```

`references/diagnostic-rules.md`:
```markdown
# Правила диагностики (черновик 0.1 — заменяется методичкой)

Каждое правило — функция в `scripts/diagnose.py`, числа — `references/thresholds.json`.
Опорный CPA: целевой клиента → иначе медиана CPA групп с ≥ `min_conversions_for_cpa` конверсиями → иначе нет.
Надёжность: клики ≥ `confidence_high_clicks` — надёжно, ≥ `confidence_medium_clicks` — средне, меньше — мало данных.

## Гигиена

| Правило | Уровень | Когда | Пороги |
|---|---|---|---|
| `no_active_banners` | 🟠 | активная группа без активных объявлений или issue `NO_BANNERS_WITH_ACTIVE_STATUS` | — |
| `rejected_banners` | 🟠 | `moderation_status` из `rejected_moderation_statuses` | — |
| `blocking_issues` | 🟠 | активная группа с issue из `blocking_issue_codes` | — |
| `autodelete_risk` | ⚪ | нет показов за период, группе ≥ `autodelete_warn_days` дней (VK удаляет через 180 без показов) | 150 |
| `banners_per_group` | ⚪ | 1 активное объявление или больше `max_active_banners_per_group` | 8 |
| `low_budget` | 🟠 | дневной бюджет < `min_daily_budget_rub`; у автостратегии < цель × `learning_budget_cpa_multiplier` | 300 ₽; ×2 |
| `duplicate_targeting` | 🟠 | ≥ 2 активные группы с одной целью и одинаковым таргетингом (без расписания) | — |
| `unused_segments` | ⚪ | сегмент не встречается в таргетингах живых групп | — |
| `no_pixel` | 🟠 | цель из `pixel_objectives`, пикселей в кабинете нет | — |

## Эффективность

| Правило | Уровень | Когда | Эффект ₽ |
|---|---|---|---|
| `spend_no_conversions` | 🔴 | 0 конверсий, расход ≥ опорный CPA × `no_conv_spend_cpa_multiplier` (нет опорного — ≥ `no_conv_min_spend_rub`) | весь расход |
| `cpa_above_target` | 🔴 | конверсий ≥ `min_conversions_for_cpa`, CPA > опорный × `cpa_over_ratio` | расход − конверсии × опорный CPA |
| `low_ctr` | 🟠 | показов ≥ `low_ctr_min_shows`, CTR < `low_ctr_pct` (по объявлениям, без них — по группам) | расход |
| `burnout` | 🟠 | частота ≥ `burnout_min_frequency`, показов ≥ `burnout_min_shows`, CTR второй половины периода ниже первой на ≥ `burnout_ctr_drop` | расход второй половины |
| `learning_starved` | 🟠 | автостратегия, конверсий в неделю < `learning_min_conversions_per_week` | — |
| `spend_concentration` | ⚪ | ≥ `concentration_min_groups` групп с расходом, у одной доля ≥ `concentration_share` | — |
| `scale_leader` | 🟢 | CPA ≤ опорный × `leader_cpa_ratio`, средний дневной расход ≥ бюджет × `leader_budget_use` | средний расход × `leader_growth_share` × дни |

## Метрика

| Правило | Уровень | Когда |
|---|---|---|
| `high_bounce` | 🟠 | визитов ≥ `metrika_min_visits`, отказы ≥ `high_bounce_rate` |
| `click_loss` | 🟠 | кликов ≥ `click_loss_min_clicks`, визиты / клики < `click_loss_visit_ratio` |
| `goals_without_vk_conversions` | 🟠 | в Метрике достижения целей есть, в VK конверсий 0 |
```

`references/limitations.md`:
```markdown
# Ограничения аудита

## Всегда (в каждом отчёте, дословно — `ALWAYS_LIMITATIONS` в `scripts/render_report.py`)

- В API нет разрезов по полу, возрасту, гео, площадкам и устройствам — «кто конвертит» по ним не определить.
- API не показывает, срабатывает ли пиксель: ноль конверсий может означать сломанный пиксель.
- Атрибуция (post-click или post-view) меняет число конверсий — сверяйте с настройкой кабинета.
- Данные за сегодня неполные — период заканчивается вчерашним днём.
- Статусы ОРД видны частично.

## По прогону (добавляет `render_report.limitations`)

- путь A — объявления только у групп с наибольшим расходом;
- объявления не собирались;
- Метрика не подключена;
- каждая неудачная запись `collect_log.json`.
```

`references/report-template.md`:
```markdown
# Отчёт аудита

`scripts/render_report.py` собирает один самодостаточный HTML (печать A4):

1. Шапка: период, путь (A/B), опорный CPA и его источник.
2. Плитки: расход, показы, клики, конверсии, CPA, CTR.
3. Находки по уровням: 🔴 срезать слив → 🟠 починить → 🟢 масштабировать → ⚪ гигиена; внутри — по эффекту ₽.
   Карточка: что, объект, доказательство (числа), эффект, надёжность, действие.
4. Структура: живые группы по расходу.
5. Ограничения — обязательный раздел.

Сводка в чат после отчёта — 5–10 строк: итог, топ-3 находки, ссылка на файл.
```

- [ ] **Step 5: Плагин, журнал, README, CI**

`.claude-plugin/plugin.json`:
```json
{
  "name": "vk-ads-audit",
  "version": "0.1.0",
  "description": "Аудит и быстрый анализ кабинета VK Рекламы (ads.vk.ru) только на чтение: слив, CPA, CTR, выгорание, гигиена, лидеры, Метрика → HTML-отчёт с приоритетами",
  "author": { "name": "ai-hub-open" },
  "homepage": "https://github.com/ai-hub-open/vk-ads-audit",
  "repository": "https://github.com/ai-hub-open/vk-ads-audit",
  "license": "Apache-2.0",
  "keywords": ["vk-ads", "vk-reklama", "audit", "analytics", "ads"]
}
```

`CHANGELOG.md`:
```markdown
# Журнал изменений

Версии — [semver](https://semver.org/lang/ru/): до 1.0 состав правил может меняться.

## 0.1.0 — не выпущена

Первая версия: глубокий аудит и быстрый анализ кабинета VK Рекламы только на чтение.
Правила диагностики — черновик до методички.
```

`README.md`:
```markdown
# vk-ads-audit

Скилл Claude: аудит и быстрый анализ кабинета VK Рекламы только на чтение через MCP `vk-ads`
(опционально с Яндекс Метрикой). Глубокий аудит → HTML-отчёт с находками по приоритету; быстрый анализ —
сводка дня в 10 строк. Создание и правка кампаний — [vk-ads-manager](https://github.com/ai-hub-open/vk-ads-manager).

Установка: маркетплейс плагинов `ai-hub-open` (`/plugin`) или архив `vk-ads-audit.skill` из релизов.

Разработка: `pip install -r requirements-dev.txt && python -m pytest`, эталоны — `python -m evals.run_evals`.
```

```bash
mkdir -p .github/workflows
cp D:/vk-ads-manager/.github/workflows/version-check.yml .github/workflows/version-check.yml
```

- [ ] **Step 6: `scripts/make_bundle.py`**

```python
"""
make_bundle.py — архив vk-ads-audit.skill/.zip для Claude Desktop и релизов.

Белый шум исключений: в архив не попадают ни секреты (.env, .mcp.json), ни данные
клиентов (vk-audits/), ни файлы разработки. Собирать из чистого клона: в git worktree
`.git` — файл и иначе уехал бы в архив (исключается и он).

    python -m scripts.make_bundle [--output dist/vk-ads-audit.skill]
"""
from __future__ import annotations

import argparse
import fnmatch
import sys
import zipfile
from pathlib import Path

try:
    from scripts._console import setup_console
    from scripts._workspace import SKILL_ROOT
except ImportError:  # запуск напрямую
    from _console import setup_console
    from _workspace import SKILL_ROOT

NAME = "vk-ads-audit"
EXCLUDE_DIRS = {".git", "__pycache__", ".pytest_cache", "tests", "evals", "docs", ".github",
                ".claude-plugin", ".claude", "vk-audits", "dist", ".venv", "venv"}
EXCLUDE_FILES = {".git", ".env", ".mcp.json", ".gitignore", "pytest.ini", "requirements-dev.txt",
                 "CHANGELOG.md", ".DS_Store", "Thumbs.db"}
EXCLUDE_GLOBS = ("*.pyc", "*.pyo", "*.zip", "*.skill", "*.tmp", "*.bak")


def _skip(rel: Path) -> bool:
    if any(part in EXCLUDE_DIRS for part in rel.parts[:-1]) or rel.parts[0] in EXCLUDE_DIRS:
        return True
    return rel.name in EXCLUDE_FILES or any(fnmatch.fnmatch(rel.name, p) for p in EXCLUDE_GLOBS)


def build(output: Path) -> Path:
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(SKILL_ROOT.rglob("*")):
            rel = path.relative_to(SKILL_ROOT)
            if path.is_file() and not _skip(rel):
                zf.write(path, f"{NAME}/{rel.as_posix()}")
    return output


def main(argv=None) -> int:
    setup_console()
    parser = argparse.ArgumentParser(description="Собрать архив скилла")
    parser.add_argument("--output", default=str(SKILL_ROOT / "dist" / f"{NAME}.skill"))
    args = parser.parse_args(argv)
    out = build(Path(args.output))
    print(f"✓ {out} ({out.stat().st_size // 1024} КБ)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 7: Прогнать всё**

Run: `python -m pytest -v`
Expected: все passed. Если `test_referenced_files_exist` падает — исправить путь в `SKILL.md`, а не тест.

- [ ] **Step 8: Commit**

```bash
git add SKILL.md README.md CHANGELOG.md .claude-plugin .github references scripts/make_bundle.py tests/test_skill_docs.py
git commit -m "SKILL.md, справочники, манифест плагина, CI версии, сборка архива"
```

---

### Task 13: Эталонные прогоны (evals)

**Files:**
- Create: `evals/__init__.py`, `evals/fixtures.py`, `evals/run_evals.py`
- Test: `tests/test_evals.py`

**Interfaces:**
- Consumes: `write_call` (Task 4), `build_dataset`, `check_sanity` (Task 6), `diagnose`, `load_thresholds` (Task 7–8).
- Produces: `CABINETS: dict[str, Callable[[Path], dict]]` — каждая функция пишет `raw/` синтетического кабинета и возвращает meta `{"date_from","date_to","target_cpa","expected": set[tuple[rule, id]]}`; `run(name, workdir: Path) -> tuple[set, set]` (найдено, ожидалось); CLI `python -m evals.run_evals` — код 0, если все совпали.

Синтетика по формату из `references/mcp-read-tools.md`. Ожидания — точное множество `(rule, entity_id)`: лишняя находка так же плоха, как пропущенная.

- [ ] **Step 1: Тест**

`tests/test_evals.py`:
```python
from __future__ import annotations

import pytest

from evals.fixtures import CABINETS
from evals.run_evals import run


@pytest.mark.parametrize("name", sorted(CABINETS))
def test_cabinet(name, tmp_path):
    found, expected = run(name, tmp_path)
    assert found == expected


def test_four_cabinets():
    assert set(CABINETS) == {"healthy", "leaking", "hygiene", "empty"}
```

- [ ] **Step 2: Убедиться, что падает**

Run: `python -m pytest tests/test_evals.py -v`
Expected: FAIL — `ModuleNotFoundError: evals.fixtures`.

- [ ] **Step 3: Фикстуры**

`evals/__init__.py` — пустой. `evals/fixtures.py`:
```python
"""Синтетические кабинеты VK в формате raw/ (как отдаёт vk-ads-mcp 0.11.0)."""
from __future__ import annotations

from pathlib import Path

from scripts.raw_store import write_call

P = {"date_from": "2026-09-01", "date_to": "2026-09-30", "metrics": "all"}
DAYS = [f"2026-09-{d:02d}" for d in range(1, 31)]


def block(spent, shows, clicks, goals, frequency=2.0):
    return {"base": {"spent": f"{spent:.2f}", "shows": shows, "clicks": clicks, "goals": goals,
                     "ctr": (clicks / shows) if shows else 0, "vk": {"result": 0}},
            "uniques": {"reach": int(shows / frequency) if shows else 0, "frequency": frequency if shows else 0}}


def group(gid, regions, budget_kop=200000, status="active", created="2026-08-01 10:00:00", **kw):
    g = {"id": gid, "name": f"Группа {gid}", "status": status, "objective": "site_conversions",
         "package_id": 3217, "ad_plan_id": 1, "budget_limit_day": budget_kop, "issues": [],
         "created": created, "delivery": "delivering", "autobidding_mode": None,
         "targetings": {"geo": {"regions": regions}, "sex": ["male", "female"]}}
    g.update(kw)
    return g


def banners(gid, n=3, **kw):
    return [{"id": gid * 100 + i, "name": f"Объявление {gid}-{i}", "status": "active",
             "moderation_status": "allowed", **kw} for i in range(n)]


def write_cabinet(raw: Path, groups, banners_by_group, per_day, per_banner_share=None, pixels=1, segments=()):
    """per_day[gid] = (spent, shows, clicks, goals) за каждый день; сводка — сумма."""
    write_call(raw, "vk_ads_ad_plans_list", {"paginated": True}, {"items": [{"id": 1, "name": "План"}]})
    write_call(raw, "vk_ads_campaigns_list", {"paginated": True}, {"items": groups})
    for gid, bs in banners_by_group.items():
        write_call(raw, "vk_ads_banners_list", {"campaign_id": gid, "paginated": True}, {"items": bs})
    write_call(raw, "vk_ads_remarketing_segments_list", {"paginated": True},
               {"items": [{"id": s, "name": f"Сегмент {s}"} for s in segments]})
    write_call(raw, "vk_ads_users_lists_list", {"paginated": True}, {"items": []})
    write_call(raw, "vk_ads_remarketing_pixels_list", {"paginated": True},
               {"items": [{"id": i, "name": "pixel"} for i in range(pixels)]})
    live = [g["id"] for g in groups if g["status"] != "deleted"]
    summary, daily, bsum = [], [], []
    for gid in live:
        s, sh, c, go = per_day.get(gid, (0, 0, 0, 0))
        n = len(DAYS)
        summary.append({"id": gid, "total": block(s * n, sh * n, c * n, go * n)})
        daily.append({"id": gid, "rows": [{"date": d, **block(s, sh, c, go)} for d in DAYS]})
        bs = banners_by_group.get(gid, [])
        for b in bs:
            share = 1 / len(bs)
            bsum.append({"id": b["id"], "total": block(s * n * share, int(sh * n * share),
                                                       int(c * n * share), int(go * n * share))})
    write_call(raw, "vk_ads_statistics_summary", {"entity": "campaigns", "ids": ",".join(map(str, live)), **P},
               {"items": summary})
    write_call(raw, "vk_ads_statistics_day", {"entity": "campaigns", "ids": ",".join(map(str, live)), **P},
               {"items": daily})
    write_call(raw, "vk_ads_statistics_summary", {"entity": "banners", **P}, {"items": bsum})


def healthy(raw: Path) -> dict:
    groups = [group(1, [1]), group(2, [2])]
    write_cabinet(raw, groups, {1: banners(1), 2: banners(2)},
                  {1: (500, 10000, 150, 2), 2: (600, 12000, 180, 2)})
    return {**P, "target_cpa": 300.0, "expected": set()}


def leaking(raw: Path) -> dict:
    groups = [group(1, [1]), group(2, [2]), group(3, [3])]
    write_cabinet(raw, groups, {1: banners(1), 2: banners(2), 3: banners(3)},
                  {1: (500, 10000, 150, 2),      # норма: CPA 250
                   2: (400, 10000, 100, 0),      # 12 000 ₽ без конверсий
                   3: (900, 10000, 120, 1)})     # CPA 900 > 300 × 1.5
    return {**P, "target_cpa": 300.0, "expected": {("spend_no_conversions", 2), ("cpa_above_target", 3)}}


def hygiene(raw: Path) -> dict:
    groups = [group(1, [1], budget_kop=20000),                       # 200 ₽ < 300
              group(2, [5]), group(3, [5]),                          # одинаковый таргетинг
              group(4, [4], created="2026-01-10 00:00:00"),          # старая, без показов
              group(5, [6])]                                         # одно объявление
    bs = {1: banners(1), 2: banners(2), 3: banners(3), 4: banners(4),
          5: banners(5, n=1)}
    bs[1][0]["moderation_status"] = "banned"
    write_cabinet(raw, groups, bs, {1: (100, 3000, 30, 1), 2: (100, 3000, 30, 1), 3: (100, 3000, 30, 1),
                                    5: (100, 3000, 30, 1)},
                  segments=(77,))
    return {**P, "target_cpa": None, "expected": {
        ("low_budget", 1), ("rejected_banners", 100), ("duplicate_targeting", 2), ("autodelete_risk", 4),
        ("banners_per_group", 5), ("unused_segments", 77)}}


def empty(raw: Path) -> dict:
    write_cabinet(raw, [], {}, {})
    return {**P, "target_cpa": None, "expected": set()}


CABINETS = {"healthy": healthy, "leaking": leaking, "hygiene": hygiene, "empty": empty}
```

`evals/run_evals.py`:
```python
"""Детерминированные эталоны: raw/ → dataset → findings, сравнение с ожидаемым множеством."""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

from evals.fixtures import CABINETS
from scripts._console import setup_console
from scripts.diagnose import diagnose, load_thresholds
from scripts.normalize import build_dataset, check_sanity


def run(name: str, workdir: Path):
    raw = Path(workdir) / name / "raw"
    meta = CABINETS[name](raw)
    ds = build_dataset(raw, meta["date_from"], meta["date_to"], meta["target_cpa"])
    assert check_sanity(ds) == [], f"{name}: синтетика не проходит проверку здравого смысла"
    found = {(f["rule"], f["entity"]["id"]) for f in diagnose(ds, load_thresholds())["findings"]}
    return found, meta["expected"]


def main() -> int:
    setup_console()
    bad = 0
    with tempfile.TemporaryDirectory() as tmp:
        for name in sorted(CABINETS):
            found, expected = run(name, Path(tmp))
            ok = found == expected
            bad += not ok
            print(f"{'✓' if ok else '✗'} {name}: найдено {sorted(found)}"
                  + ("" if ok else f", ожидалось {sorted(expected)}"))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Прогнать**

Run: `python -m pytest tests/test_evals.py -v` и `python -m evals.run_evals`
Expected: 5 passed; все строки `✓`.
Если какой-то кабинет даёт лишнюю находку — сначала проверить, верна ли находка по смыслу; если верна — поправить синтетику (а не правило), чтобы кабинет проверял ровно то, ради чего задуман; если неверна — чинить правило в `diagnose.py` с тестом в `tests/test_diagnose_*.py`.

- [ ] **Step 5: Commit**

```bash
git add evals tests/test_evals.py
git commit -m "evals: 4 синтетических кабинета с точным набором ожидаемых находок"
```

---

### Task 14: Гейт релиза 0.1.0 (ручной, после всех задач)

Не код — проверка на живых данных; без неё релиза нет (спека, раздел 7).

**Files:**
- Create: `docs/live-run-YYYY-MM-DD.md` (протокол)
- Modify: по итогам — `scripts/normalize.py`, `references/mcp-read-tools.md`, `evals/fixtures.py`, тесты

- [ ] **Step 1:** Получить от Лизы `account_id` кабинета с расходом ≥ 30 дней (путь B — ссылка подключения).
- [ ] **Step 2:** Claude Desktop, путь B: глубокий аудит целиком по `SKILL.md`. Записать в протокол: код probe, число вызовов и ошибок из `collect_log.json`, `sanity.json` (если был), время.
- [ ] **Step 3:** Тот же кабинет путём A (удалить ссылку: `python -m scripts.credentials set` не трогать, а запустить без неё — переменная окружения пустая и файла нет; или временно переименовать `~/.vk-ads-audit/credentials.json`).
- [ ] **Step 4:** Сверить 3–5 чисел отчёта с интерфейсом ads.vk.ru (расход группы, клики, конверсии, CTR, дневной бюджет) — в протокол с расхождениями.
- [ ] **Step 5:** `collect_log.json` — только инструменты из `ALLOWED_TOOLS` (`python -c "import json;print({c['tool'] for c in json.load(open('<ws>/collect_log.json',encoding='utf-8'))['calls']})"`).
- [ ] **Step 6:** Закрыть пункты «Проверить живым прогоном» в `references/mcp-read-tools.md`: источник конверсий, единицы CTR, размер пачки `ids`, имена полей объявления. Расхождения — правка `normalize.py`/`collect.py` с тестом.
- [ ] **Step 7:** Обезличить 2–3 реальных ответа (имена → «Группа N», id → последовательные) и добавить кабинет `real_anonymized` в `evals/fixtures.py`; разошедшиеся синтетические тесты переписать.
- [ ] **Step 8:** `CHANGELOG.md`: «0.1.0 — <дата>»; `python -m pytest`, `python -m evals.run_evals`, `python -m scripts.make_bundle` (из чистого клона); дальше релиз по схеме остальных скиллов — с согласия Лизы (создание репозитория на GitHub, PR, тег, запись в маркетплейсе — отдельные шаги).
