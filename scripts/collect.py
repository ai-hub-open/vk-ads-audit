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
