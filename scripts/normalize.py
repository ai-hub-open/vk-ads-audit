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
import math
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
    if isinstance(v, bool):
        raise ValueError(f"не число: {v!r}")
    if isinstance(v, (int, float)):
        f = float(v)
    else:
        s = str(v).replace(" ", "").replace(" ", "").replace(",", ".")
        try:
            f = float(s)
        except ValueError:
            raise ValueError(f"не число: {v!r}") from None
    if not math.isfinite(f):
        raise ValueError(f"не число: {v!r}")
    return f


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


def _response_items(call: dict) -> list:
    """items ответа; не-объект или не-список — ValueError (как в raw_store.items_of)."""
    resp = call["response"]
    if resp is None:
        return []
    if not isinstance(resp, dict):
        raise ValueError(f"response должен быть объектом, а не {type(resp).__name__}")
    items = resp.get("items") or []
    if not isinstance(items, list):
        raise ValueError(f"items должен быть списком, а не {type(items).__name__}")
    return items


def _entity_stats(raw_dir: Path, tool: str, entity: str) -> dict:
    out: dict = {}
    for call in read_calls(raw_dir, tool):
        if call["arguments"].get("entity") != entity:
            continue
        for it in _response_items(call):
            out[it["id"]] = it
    return out


def build_dataset(raw_dir: Path, date_from: str, date_to: str, target_cpa=None, path: str = "B") -> dict:
    raw_dir = Path(raw_dir)
    d_from, d_to = date.fromisoformat(date_from), date.fromisoformat(date_to)
    if d_to < d_from:
        raise ValueError(f"дата окончания раньше начала: {date_from} > {date_to}")
    summary = _entity_stats(raw_dir, "vk_ads_statistics_summary", "campaigns")
    bsummary = _entity_stats(raw_dir, "vk_ads_statistics_summary", "banners")
    daily_raw = _entity_stats(raw_dir, "vk_ads_statistics_day", "campaigns")

    banners = []
    for call in read_calls(raw_dir, "vk_ads_banners_list"):
        gid = call["arguments"].get("campaign_id")
        for b in _response_items(call):
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
                     "summary": bool(summary), "stats": bool(summary or daily_raw),
                     "pixels": bool(read_calls(raw_dir, "vk_ads_remarketing_pixels_list")),
                     "metrika": False},
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
        for d in g["daily"]:
            _check_stats(f"group:{g['id']}@{d['date']}", d, out)
        bs = [banners[i]["stats"] for i in g["banner_ids"] if banners[i]["stats"]]
        if ds["coverage"]["summary"] and bs and len(bs) == len(g["banner_ids"]):
            total = sum(s["spent"] for s in bs)
            if abs(total - g["stats"]["spent"]) > max(1.0, 0.01 * g["stats"]["spent"]):
                out.append({"code": "banners_vs_group", "entity": f"group:{g['id']}",
                            "detail": f"объявления {total} ₽, группа {g['stats']['spent']} ₽"})
    for b in ds["banners"]:
        _check_stats(f"banner:{b['id']}", b["stats"], out)
    return out


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
    for name in ("dataset.json", "sanity.json"):  # старый результат не должен пережить этот прогон
        (ws / name).unlink(missing_ok=True)
    try:
        ds = build_dataset(ws / "raw", args.date_from, args.date_to, args.target_cpa, args.path)
        metrika_path = ws / "metrika.json"
        if metrika_path.is_file():
            merge_metrika(ds, json.loads(metrika_path.read_text(encoding="utf-8")))
    except (ValueError, KeyError, TypeError, AttributeError) as e:
        print(f"✗ raw/ не разобран: {type(e).__name__}: {e}")
        return 3
    issues = check_sanity(ds)
    if issues:
        (ws / "sanity.json").write_text(json.dumps(issues, ensure_ascii=False, indent=1), encoding="utf-8")
        for i in issues:
            print(f"✗ {i['code']} {i['entity']}: {i['detail']}")
        print("Проверка здравого смысла не пройдена — отчёт не строим, см. sanity.json")
        return 3
    (ws / "dataset.json").write_text(json.dumps(ds, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"групп: {len(ds['groups'])}, объявлений: {len(ds['banners'])}, расход: {ds['totals']['spent']} ₽")
    return 0


if __name__ == "__main__":
    sys.exit(main())
