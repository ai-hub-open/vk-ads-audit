"""
collect.py — путь B: скрипт сам собирает кабинет из MCP VK Рекламы в raw/.

Предполётка (один вызов): расход пользователя за период по дням — в collect_log.json (preflight_spent). Нулевой
расход и нет живых групп — сбор останавливается; живые группы есть — сбор идёт, отчёт выйдет с плашкой «0 ₽».
Опись (кампании, группы, объявления по группам, сегменты, списки, пиксели) и
статистика (по дням по группам и по объявлениям; сводка по группам — за всё время, без дат, только ради
охвата и частоты).
Только инструменты из белого списка mcp_client. Частичный сбой не останавливает
сбор — он попадает в collect_log.json, а оттуда в ограничения отчёта.

Коды выхода:
  0 — сбор выполнен (возможно частично: сбои видны в collect_log.json, поле errors);
  2 — путь B недоступен (нет ссылки или --probe провалился), неверные аргументы или период;
  3 — критический сбой: не собрана опись кампаний (collect_log.json: critical);
  4 — raw/ уже содержит файлы: нужна новая рабочая папка или очистка raw/;
  5 — за период в кабинете нет расхода и нет живых групп: собран только список групп, предложи другой кабинет.
  (2 также — период старше доступной истории статистики, ~366 дней.)

    python -m scripts.collect --probe [--account-id ID]
    python -m scripts.collect --workspace <абс. путь> --date-from 2026-09-01 --date-to 2026-09-30 \
        [--account-id ID] [--mode deep|quick]
"""
from __future__ import annotations

import argparse
import json
import re
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
# Базовые списки и расширенные поля подтверждены живым прогоном 2026-10-16 (auto_utm, erid, ord_token, ord_erid
# сервер отвергает: 400 unknown_resource_fields; маркировка — ord_marker, статус ОРД — or_status, UTM группы — enable_utm
# и utm).
GROUP_FIELDS_BASE = ("id,name,status,objective,package_id,ad_plan_id,budget_limit,budget_limit_day,targetings,"
                     "issues,created,updated,date_start,date_end,delivery,autobidding_mode,max_price,priced_goal")
BANNER_FIELDS_BASE = ("id,name,campaign_id,status,moderation_status,moderation_reasons,issues,delivery,"
                      "textblocks,urls,content,created,updated,user_can_request_remoderation")
GROUP_FIELDS = GROUP_FIELDS_BASE + ",enable_utm,utm"
BANNER_FIELDS = BANNER_FIELDS_BASE + ",call_to_action,ord_marker,or_status"
STATS_CHUNK = 50
HISTORY_DAYS = 366          # глубина дневной статистики VK: MCP отказывает сам, VK — 400 WRONG_DATE (минимальная дата в тексте)
_MIN_DATE_RES = (re.compile(r"minimal allowed date is '(\d{4})-(\d{1,2})-(\d{1,2})'", re.I),   # VK: WRONG_DATE
                 re.compile(r"доступен период с (\d{4})-(\d{1,2})-(\d{1,2})"))                    # отказ самого MCP
# Подтверждено 16.10.2026: «base» и «base,uniques» принимаются (вместо тяжёлого «all»). Дням охват не нужен.
METRICS_DAY = "base"
METRICS_SUMMARY = "base,uniques"   # охват и частота — только в uniques
LISTS = ("vk_ads_remarketing_segments_list", "vk_ads_users_lists_list", "vk_ads_remarketing_pixels_list")
MODES = ("deep", "quick")


class RawNotEmptyError(Exception):
    """raw/ уже содержит файлы: повторный сбор в ту же папку удвоит данные."""


class DateLimitError(Exception):
    """Период начинается раньше, чем VK хранит дневную статистику."""

    def __init__(self, min_date: date, from_server: bool = False):
        self.min_date = min_date
        self.from_server = from_server
        super().__init__(f"История статистики VK доступна с {min_date.isoformat()} (около {HISTORY_DAYS} дней): "
                         f"начните период не раньше этой даты.")


def _wrong_date(error: McpError):
    """Минимальная дата из ошибки WRONG_DATE (`minimal allowed date is '2025-8-11'`) или None."""
    m = next((m for m in (r.search(str(error)) for r in _MIN_DATE_RES) if m), None)
    if not m:
        return None
    return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))


def _user_id(info):
    """id пользователя из vk_ads_account_info; None — форма ответа не распознана."""
    if not isinstance(info, dict):
        return None
    for src in (info, info.get("user") if isinstance(info.get("user"), dict) else {}):
        for key in ("id", "user_id"):
            v = src.get(key)
            if (isinstance(v, int) and not isinstance(v, bool)) or (isinstance(v, str) and v.strip().isdigit()):
                return v
    return None


def _num(v):
    """Число из строки/числа или None (нечитаемо): «не прочитали» не равно «0 ₽»."""
    if v is None or isinstance(v, bool):
        return None
    try:
        return float(str(v).replace(" ", "").replace(",", "."))
    except ValueError:
        return None


def total_spend(resp):
    """Расход за период из ответа statistics_day(entity=users): items[].total.base.spent (строка), иначе сумма
    rows[].base.spent, иначе верхний total. None — расход прочитать не удалось (форма не та, пусто, не число):
    это не «0 ₽», предполётка тогда пропускается."""
    if not isinstance(resp, dict):
        return None
    parsed = []
    for it in resp.get("items") if isinstance(resp.get("items"), list) else []:
        if not isinstance(it, dict):
            continue
        total = _num(((it.get("total") or {}).get("base") or {}).get("spent")) if isinstance(it.get("total"), dict) else None
        if total is not None:
            parsed.append(total)
            continue
        rows = [_num(((r or {}).get("base") or {}).get("spent")) for r in it.get("rows") or [] if isinstance(r, dict)]
        if rows and all(v is not None for v in rows):
            parsed.append(sum(rows))
    if not parsed:
        top = resp.get("total")
        v = _num(((top or {}).get("base") or {}).get("spent")) if isinstance(top, dict) else None
        return None if v is None else round(v, 2)
    return round(sum(parsed), 2)


def _archived(g: dict) -> bool:
    return any(isinstance(i, dict) and i.get("code") == "ARCHIVED" for i in g.get("issues") or [])


def _chunks(ids: list, size: int):
    for i in range(0, len(ids), size):
        yield ids[i:i + size]


def clamp_date_to(date_to: str, today: date) -> str:
    yesterday = today - timedelta(days=1)
    return min(date.fromisoformat(date_to), yesterday).isoformat()


class _Collector:
    def __init__(self, client, raw_dir: Path, secrets=()):
        self.client = client
        self.raw_dir = Path(raw_dir)
        self.log: list = []
        self._secrets = [s for s in secrets if s]

    def clean(self, text: str) -> str:
        # Ссылка подключения несёт креды: в журнал и на экран попадает только маска.
        for s in self._secrets:
            text = text.replace(s, mask(s))
        return text

    def _record(self, tool, args, error=None, no_access=False, preflight=False):
        entry = {"tool": tool, "arguments": args, "ok": error is None,
                 "error": None if error is None else self.clean(error)}
        if preflight:
            entry["preflight"] = True   # сбой предполётки в «Ограничения» клиента не идёт
        if no_access:
            entry["no_access"] = True   # 429 с limits 0: нет прав; отчёт скажет «нет доступа к базам клиентов»
        self.log.append(entry)

    def probe(self, tool: str, args: dict):
        """Вызов без сохранения в raw/ (предполётка): ответ нужен только сборщику. Сбой — None, в журнал."""
        try:
            return self.client.call(tool, args)
        except ForbiddenToolError:
            raise
        except McpError as e:
            self._fail(tool, args, e, preflight=True)
            return None

    def _fail(self, tool, args, e: McpError, preflight=False):
        min_date = _wrong_date(e)
        if min_date is not None:   # ответ сервера про глубину истории — не сырая ошибка, а понятное объяснение
            self._record(tool, args, str(DateLimitError(min_date, from_server=True)), preflight=preflight)
            raise DateLimitError(min_date, from_server=True)
        self._record(tool, args, str(e), getattr(e, "no_access", False), preflight=preflight)

    def call(self, tool: str, args: dict):
        try:
            resp = self.client.call(tool, args)
        except ForbiddenToolError:
            raise
        except McpError as e:
            self._fail(tool, args, e)
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
            self._fail(tool, args, e)
            return None
        write_call(self.raw_dir, tool, {**args, "paginated": True}, {"items": items})
        self._record(tool, args)
        return items

    def listing_with_fallback(self, tool: str, args: dict, base_fields: str):
        """Расширенный список fields; сервер мог его отвергнуть (McpError) — один повтор с базовым."""
        items = self.listing(tool, args)
        if items is None:
            failed = len(self.log) - 1
            items = self.listing(tool, {**args, "fields": base_fields})
            self.supersede(failed, items)
        return items

    def supersede(self, index: int, retry_result):
        """Упавший расширенный вызов, заменённый успешным базовым, — не отдельное ограничение отчёта."""
        if retry_result is not None and 0 <= index < len(self.log) and not self.log[index]["ok"]:
            self.log[index]["superseded"] = True


def history_start(today: date) -> date:
    return today - timedelta(days=HISTORY_DAYS - 1)   # окно в 366 дней включает сегодня


def collect(client, raw_dir: Path, date_from: str, date_to: str, mode: str = "deep", *,
            secrets=(), today: date | None = None) -> dict:
    if mode not in MODES:
        raise ValueError(f"режим должен быть deep или quick, а не {mode!r}")
    earliest = history_start(today or date.today())
    if date.fromisoformat(date_from) < earliest:
        raise DateLimitError(earliest)
    raw_dir = Path(raw_dir)
    if raw_dir.is_dir() and any(raw_dir.iterdir()):
        raise RawNotEmptyError(f"{raw_dir} уже содержит файлы — повторный сбор удвоит данные. "
                               "Укажи новую рабочую папку или удали содержимое raw/.")
    c = _Collector(client, raw_dir, secrets)
    log = {"mode": mode, "date_from": date_from, "date_to": date_to, "calls": c.log,
           "errors": 0, "critical": False, "no_spend": False, "preflight_spent": None}
    out = raw_dir.parent / "collect_log.json"
    try:
        period = {"date_from": date_from, "date_to": date_to, "metrics": METRICS_DAY}
        uid = _user_id(c.probe("vk_ads_account_info", {}))
        spent = None
        if uid is not None:   # предполётка: расход кабинета за период — в журнал, по нему плашка отчёта
            resp = c.probe("vk_ads_statistics_day", {"entity": "users", "ids": str(uid), **period})
            spent = total_spend(resp) if resp is not None else None
            log["preflight_spent"] = spent
            if spent is None:
                log["preflight"] = "пропущена: расход за период прочитать не удалось"
        else:
            log["preflight"] = "пропущена: id пользователя в ответе account_info не найден"
        c.listing("vk_ads_ad_plans_list", {"fields": PLAN_FIELDS})
        groups = c.listing_with_fallback("vk_ads_campaigns_list", {"fields": GROUP_FIELDS}, GROUP_FIELDS_BASE)
        if groups is None:
            log["critical"] = True
            groups = []
        measured = [g["id"] for g in groups if g.get("status") != "deleted"]          # расход архивных — в итогах
        live = [g["id"] for g in groups if g.get("status") != "deleted" and not _archived(g)]
        if spent == 0 and not live and not log["critical"]:   # 0 ₽ и живых групп нет — собирать нечего
            log["no_spend"] = True
            return log

        banner_ids: list = []
        banner_fields = BANNER_FIELDS
        if mode == "deep":
            for gid in live:
                banners = c.listing("vk_ads_banners_list", {"campaign_id": gid, "fields": banner_fields})
                if banners is None and banner_fields != BANNER_FIELDS_BASE:
                    failed = len(c.log) - 1
                    banner_fields = BANNER_FIELDS_BASE  # расширенные поля не приняты — дальше сразу базовые
                    banners = c.listing("vk_ads_banners_list", {"campaign_id": gid, "fields": banner_fields})
                    c.supersede(failed, banners)
                banners = banners or []
                banner_ids += [b["id"] for b in banners if b.get("status") != "deleted"]
            for tool in LISTS:
                c.listing(tool, {})

        for chunk in _chunks(measured, STATS_CHUNK):
            ids = ",".join(map(str, chunk))
            if mode == "deep":   # сводка — только за всё время, с датами MCP отказывает: нужна ради охвата и частоты
                c.call("vk_ads_statistics_summary", {"entity": "campaigns", "ids": ids, "metrics": METRICS_SUMMARY})
            c.call("vk_ads_statistics_day", {"entity": "campaigns", "ids": ids, **period})
        for chunk in _chunks(banner_ids, STATS_CHUNK):   # итоги объявлений — сумма дней, как у групп
            c.call("vk_ads_statistics_day", {"entity": "banners", "ids": ",".join(map(str, chunk)), **period})
    except BaseException as e:
        log["aborted"] = c.clean(f"{type(e).__name__}: {e}")
        raise
    finally:
        log["errors"] = sum(1 for x in c.log if not x["ok"] and not x.get("preflight"))
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
    try:
        date_from = date.fromisoformat(args.date_from)
        date.fromisoformat(args.date_to)
    except ValueError:
        print("✗ даты --date-from и --date-to задаются в формате ГГГГ-ММ-ДД")
        return 2
    ws = resolve_workspace(args.workspace)
    date_to = clamp_date_to(args.date_to, date.today())
    if date_to != args.date_to:
        print(f"Период сдвинут на {date_to}: данные за сегодня неполные.")
    if date.fromisoformat(date_to) < date_from:
        print(f"✗ период пуст: {args.date_from} — {date_to} (данные заканчиваются вчера)")
        return 2
    try:
        log = collect(client, ws / "raw", args.date_from, date_to, mode=args.mode, secrets=(url,))
    except RawNotEmptyError as e:
        print(f"✗ {e}")
        return 4
    except DateLimitError as e:
        print(f"✗ {e} Более ранний период через MCP не собрать.")
        return 2
    if log.get("no_spend") and args.mode == "quick":
        print(f"✗ За период {args.date_from} — {date_to} кабинет не откручивал (расход 0 ₽, живых групп нет): быстрый "
              "анализ строить не на чем. Выберите другой кабинет (--account-id).")
        return 5
    if log.get("no_spend"):
        print(f"✗ За период {args.date_from} — {date_to} расход в кабинете 0 ₽ и живых групп нет (все удалены или "
              "в архиве): собирать нечего. Выберите другой кабинет (--account-id) или другой период.")
        return 5
    print(f"вызовов: {len(log['calls'])}, с ошибками: {log['errors']} → {ws / 'collect_log.json'}")
    if log["critical"]:
        print("✗ опись кабинета не собрана: список кампаний не получен. См. collect_log.json.")
        return 3
    return 0


if __name__ == "__main__":
    sys.exit(main())
