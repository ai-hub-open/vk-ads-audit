"""
normalize.py — raw/ → dataset.json. Единицы приводятся ТОЛЬКО здесь.

- бюджеты групп и кампаний приходят в рублях (подтверждено живым прогоном: 50000 = «50 000 ₽» в кабинете);
- деньги статистики — строками в рублях;
- CTR считаем сами из clicks/shows, значение VK (оно уже в процентах) храним для проверки единиц;
- итоги периода — сумма строк statistics_day: statistics_summary игнорирует период и отдаёт всё время
  (баг MCP), поэтому охват и частота берутся из сводки только для групп, чья жизнь целиком в периоде;
- конверсии: base.goals, для целей из CONVERSION_SOURCE — base.vk.result.
Проверка здравого смысла (check_sanity) останавливает аудит на кривых числах.

    python -m scripts.normalize --workspace <абс. путь> --date-from D --date-to D [--target-cpa N] [--path A|B]
"""
from __future__ import annotations

import argparse
import json
import math
import re
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

try:
    from scripts._console import setup_console
    from scripts._workspace import resolve_workspace
    from scripts.features import (AUTO_UTM_KEYS, ERID_KEYS, EXCLUDE_KEYS, PLAN_BUDGET_KEYS, SIZE_KEYS, UPDATED_KEYS,
                                  SEGMENT_RELATION_KEYS, banner_text, content_features, first_known, is_lal,
                                  layer_of, observed_keys, parse_dt, targeting_features, url_strings,
                                  users_list_ids)
    from scripts.raw_store import items_of, read_calls
except ImportError:  # запуск напрямую
    from _console import setup_console
    from _workspace import resolve_workspace
    from features import (AUTO_UTM_KEYS, ERID_KEYS, EXCLUDE_KEYS, PLAN_BUDGET_KEYS, SIZE_KEYS, UPDATED_KEYS,
                          SEGMENT_RELATION_KEYS, banner_text, content_features, first_known, is_lal,
                          layer_of, observed_keys, parse_dt, targeting_features, url_strings,
                          users_list_ids)
    from raw_store import items_of, read_calls

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


def _rub(v):
    """Бюджет в рублях как есть; None — поля нет (лимита нет или не получен)."""
    return None if v in (None, "") else round(money(v), 2)


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
            "vk_ctr_pct": None if b.get("ctr") in (None, "") else round(float(b["ctr"]), 4),   # у VK уже проценты
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


def _segment_ids(t, excluded: bool = False) -> list:
    """id сегментов таргетинга; excluded=True — только исключённые (под EXCLUDE_KEYS), иначе только целевые."""
    found: set = set()

    def walk(v, inside, under_exclude):
        if isinstance(v, dict):
            for k, x in v.items():
                is_exclude = k in EXCLUDE_KEYS
                if is_exclude and not excluded:
                    continue
                walk(x, inside or "segment" in k, under_exclude or is_exclude)
        elif isinstance(v, list):
            for x in v:
                walk(x, inside, under_exclude)
        elif inside and isinstance(v, int) and not isinstance(v, bool) and under_exclude == excluded:
            found.add(v)

    walk(t or {}, False, False)
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


def _dedupe(tool: str, items: list) -> list:
    """Один id — одна запись. Точные дубли (повторный файл списка) тихо отбрасываем, расхождение — ошибка."""
    seen: dict = {}
    out: list = []
    for it in items:
        key = it["id"]
        if key in seen:
            if seen[key] != it:
                raise ValueError(f"{tool}: id {key} встречается дважды с разным содержимым — "
                                 "в raw/ ответы разных прогонов или файл продублирован с правками")
            continue
        seen[key] = it
        out.append(it)
    return out


def _list_items(raw_dir: Path, tool: str) -> list:
    return _dedupe(tool, items_of(raw_dir, tool))


def _id_list(v) -> list:
    if isinstance(v, str):
        v = v.split(",")
    if not isinstance(v, (list, tuple)):
        return []
    out = []
    for x in v:
        x = str(x).strip()
        if x:
            out.append(int(x) if x.isdigit() else x)
    return out


def _gid(v):
    s = str(v).strip() if v is not None else ""
    return int(s) if s.isdigit() else v


def _entity_stats(raw_dir: Path, tool: str, entity: str, period: tuple) -> tuple:
    """(items по id, ids из аргументов вызовов, число вызовов). Период вызовов обязан совпасть с аудитом."""
    out: dict = {}
    covered: set = set()
    calls = 0
    for call in read_calls(raw_dir, tool):
        args = call["arguments"]
        if args.get("entity") != entity:
            continue
        calls += 1
        got = (args.get("date_from"), args.get("date_to"))
        if any(got) and got != period:
            raise ValueError(f"период raw/ ({got[0]} — {got[1]}) не совпадает с "
                             f"--date-from/--date-to ({period[0]} — {period[1]}): {tool}")
        covered.update(_id_list(args.get("ids")))
        for it in _response_items(call):
            if it["id"] in out and out[it["id"]] != it:
                raise ValueError(f"{tool}: id {it['id']} встречается дважды с разным содержимым")
            out[it["id"]] = it
            covered.add(it["id"])
    return out, covered, calls


def _price(v):
    if v in (None, ""):
        return None
    try:
        p = round(money(v), 2)
    except ValueError:
        return None
    return p or None


def _field_set(fields) -> set:
    if isinstance(fields, str):
        fields = fields.split(",")
    return {str(f).strip() for f in fields} if isinstance(fields, (list, tuple)) else set()


def _blind_ids(raw_dir: Path, tool: str, keys) -> set:
    """id сущностей, полученных только вызовами, где `fields` задан и не содержит ни одного из keys:
    значит, отсутствие поля у них — «не запрошено», а не «пусто». Вызов без `fields` отдаёт поля по умолчанию."""
    blind: set = set()
    sighted: set = set()
    for call in read_calls(raw_dir, tool):
        fields = call["arguments"].get("fields")
        asked = fields is None or any(k in _field_set(fields) for k in keys)
        ids = {it["id"] for it in _response_items(call) if isinstance(it, dict) and "id" in it}
        (sighted if asked else blind).update(ids)
    return blind - sighted


def _auto_utm(g, observed):
    if not any(k in observed for k in AUTO_UTM_KEYS):
        return None
    return bool(first_known(g, AUTO_UTM_KEYS, observed))


WINDOWS = (7, 14, 30)


def windows_of(daily: list, d_from: date, d_to: date) -> dict:
    """Окна последних N дней периода; окно длиннее периода — None (сравнивать не с чем)."""
    out: dict = {}
    for n in WINDOWS:
        start = d_to - timedelta(days=n - 1)
        if start < d_from:
            out[str(n)] = None
            continue
        rows = [r for r in daily if r.get("date") and start.isoformat() <= r["date"] <= d_to.isoformat()]
        out[str(n)] = aggregate(rows) if rows else None
    return out


def _is_live(g: dict) -> bool:
    """Группа не удалена и не в архиве (issues ARCHIVED): для гигиены и проверок она не существует."""
    return g["status"] != "deleted" and "ARCHIVED" not in (g.get("issues") or [])


def _lifetime_fits(g: dict, d_from: date, period_stats: dict, summary_stats: dict) -> bool:
    """Охват и частота из statistics_summary (она отдаёт всё время) годны за период, только если вся жизнь
    группы в периоде: создана не раньше начала и число показов в сводке совпадает с суммой дней."""
    created = parse_dt(g.get("created"))
    if not created or created.date() < d_from:
        return False
    shows, total = period_stats["shows"], summary_stats["shows"]
    return abs(total - shows) <= max(10, 0.01 * shows)


def _no_access_to_lists(collect_log) -> bool:
    return any(c.get("tool") == "vk_ads_users_lists_list" and not c.get("ok", True) and c.get("no_access")
               for c in (collect_log or {}).get("calls", []))


def build_dataset(raw_dir: Path, date_from: str, date_to: str, target_cpa=None, path: str = "B",
                  as_of=None, collect_log=None) -> dict:
    raw_dir = Path(raw_dir)
    d_from, d_to = date.fromisoformat(date_from), date.fromisoformat(date_to)
    if d_to < d_from:
        raise ValueError(f"дата окончания раньше начала: {date_from} > {date_to}")
    period = (date_from, date_to)
    summary, summary_ids, summary_calls = _entity_stats(raw_dir, "vk_ads_statistics_summary", "campaigns", period)
    bsummary, _, _ = _entity_stats(raw_dir, "vk_ads_statistics_summary", "banners", period)
    daily_raw, daily_ids, daily_calls = _entity_stats(raw_dir, "vk_ads_statistics_day", "campaigns", period)
    bdaily, bdaily_ids, _ = _entity_stats(raw_dir, "vk_ads_statistics_day", "banners", period)
    has_stats = bool(summary_calls or daily_calls)
    notes: list = []

    banner_calls = read_calls(raw_dir, "vk_ads_banners_list")
    banners: list = []
    banner_groups: set = set()
    seen_banners: dict = {}
    for call in banner_calls:
        gid = call["arguments"].get("campaign_id")
        if gid is not None:
            banner_groups.add(_gid(gid))
        for b in _response_items(call):
            if b["id"] in seen_banners:
                if seen_banners[b["id"]] != b:
                    raise ValueError(f"vk_ads_banners_list: id {b['id']} встречается дважды с разным содержимым")
                continue
            seen_banners[b["id"]] = b
            if b.get("campaign_id") is not None:
                banner_groups.add(_gid(b["campaign_id"]))
            banners.append({**b, "_group": b.get("campaign_id") or gid})

    raw_groups = _list_items(raw_dir, "vk_ads_campaigns_list")
    observed_t = observed_keys(g.get("targetings") for g in raw_groups)
    observed_g = observed_keys(raw_groups)
    seg_items = _list_items(raw_dir, "vk_ads_remarketing_segments_list")
    segments_by_id = {s.get("id"): s for s in seg_items}
    observed_b = observed_keys(banners)
    erid_blind = _blind_ids(raw_dir, "vk_ads_banners_list", ERID_KEYS)
    utm_blind = _blind_ids(raw_dir, "vk_ads_campaigns_list", AUTO_UTM_KEYS)

    objective_of: dict = {}
    unmeasured_freq: list = []
    groups = []
    for g in raw_groups:
        obj = g.get("objective")
        objective_of[g["id"]] = obj
        daily = [{"date": r.get("date"), **stats_from(r, obj)}
                 for r in (daily_raw.get(g["id"]) or {}).get("rows") or []]
        missing = False
        if g["id"] in daily_ids:  # нет в items при покрытии вызовом — показов не было, это нули
            stats = aggregate(daily)
            sm = stats_from((summary.get(g["id"]) or {}).get("total"), obj) if g["id"] in summary else None
            if sm and daily and _lifetime_fits(g, d_from, stats, sm):
                stats = {**stats, "reach": sm["reach"], "frequency": sm["frequency"]}
            else:
                unmeasured_freq.append(g["id"])
        elif g["id"] in summary_ids:  # старый raw/: сводка до обнаружения, что она отдаёт всё время
            stats = stats_from((summary.get(g["id"]) or {}).get("total"), obj)
        else:
            stats = stats_from(None, obj)
            missing = has_stats
        group = {
            "id": g["id"], "name": g.get("name") or "", "status": g.get("status"), "objective": obj,
            "package_id": g.get("package_id"), "ad_plan_id": g.get("ad_plan_id"),
            "delivery": g.get("delivery"), "autobidding_mode": g.get("autobidding_mode"),
            "budget_day_rub": _rub(g.get("budget_limit_day")),
            "budget_total_rub": _rub(g.get("budget_limit")),
            "issues": _codes(g.get("issues")), "created": g.get("created"), "date_end": g.get("date_end"),
            "targeting_key": targeting_key(g.get("targetings")),
            "segment_ids": _segment_ids(g.get("targetings")),
            "excluded_segment_ids": _segment_ids(g.get("targetings"), excluded=True),
            "stats": stats, "daily": daily,
            "features": targeting_features(g.get("targetings"), observed_t),
            "layer": layer_of(_segment_ids(g.get("targetings")), segments_by_id) if "targetings" in g else None,
            "max_price_rub": _price(g.get("max_price")),
            "auto_utm": None if g["id"] in utm_blind else _auto_utm(g, observed_g),
            "windows": windows_of(daily, d_from, d_to),
            "banner_ids": [b["id"] for b in banners if b["_group"] == g["id"]],
        }
        if missing:
            group["stats_missing"] = True
        groups.append(group)

    banner_out = []
    for b in banners:
        if b["id"] in bdaily:
            obj = objective_of.get(b["_group"])
            bstats = aggregate([stats_from(r, obj) for r in bdaily[b["id"]].get("rows") or []])
        elif b["id"] in bsummary:  # старый raw/
            bstats = stats_from(bsummary[b["id"]].get("total"), objective_of.get(b["_group"]))
        else:
            bstats = None
        banner_out.append({
            "id": b["id"], "name": b.get("name") or "", "group_id": b["_group"], "status": b.get("status"),
            "moderation_status": b.get("moderation_status"), "moderation_reasons": b.get("moderation_reasons"),
            "issues": _codes(b.get("issues")),
            "can_remoderate": bool(b.get("user_can_request_remoderation")),
            "cta": (str(b["call_to_action"]) if b.get("call_to_action") not in (None, "") else None),
            "text": banner_text(b.get("textblocks")),
            "urls": url_strings(b.get("urls")),
            **content_features(b.get("content")),
            "created": b.get("created"), "updated": b.get("updated"),
            "age_days": (d_to - parse_dt(b["created"]).date()).days if parse_dt(b.get("created")) else None,
            "erid": (None if b["id"] in erid_blind or not any(k in observed_b for k in ERID_KEYS)
                     else str(first_known(b, ERID_KEYS, observed_b) or "")),
            "stats": bstats,
        })

    live_groups = [g for g in groups if _is_live(g)]
    counted = [g for g in groups if g["status"] != "deleted"]   # расход архивных групп в итогах остаётся
    if banner_calls:
        uncovered = [g for g in live_groups if g["status"] == "active" and g["id"] not in banner_groups]
        if uncovered:
            notes.append(f"Объявления не собраны для {len(uncovered)} групп — по ним выводы на уровне группы.")
    absent = [g for g in live_groups if g.get("stats_missing")]
    if absent:
        notes.append(f"Статистика не получена для {len(absent)} групп — итоги и выводы по ним неполные.")
    by_id = {b["id"]: b for b in banner_out}
    for g in live_groups:
        bs = [by_id[i]["stats"] for i in g["banner_ids"] if by_id[i]["stats"]]
        if has_stats and bs and len(bs) == len(g["banner_ids"]) and not g.get("stats_missing"):
            total, spent = sum(s["spent"] for s in bs), g["stats"]["spent"]
            if spent - total > max(1.0, 0.01 * spent):
                notes.append(f"Сумма по объявлениям меньше расхода группы {g['id']} "
                             "(часть объявлений удалена?)")

    no_freq = [g for g in live_groups if g["id"] in unmeasured_freq and g["stats"]["shows"] > 0]
    if no_freq:
        notes.append(f"Частота и охват за период не определены для {len(no_freq)} групп: статистика VK отдаёт их "
                     "только за всё время жизни группы — правила по частоте (выгорание) по ним не применялись.")

    def named(tool):
        return [{"id": x.get("id"), "name": x.get("name") or ""} for x in _list_items(raw_dir, tool)]

    raw_plans = _list_items(raw_dir, "vk_ads_ad_plans_list")
    observed_p = observed_keys(raw_plans)
    plan_budget_known = any(k in observed_p for k in PLAN_BUDGET_KEYS)

    def plan_out(p):
        day, total = _rub(p.get("budget_limit_day")), _rub(p.get("budget_limit"))
        return {"id": p.get("id"), "name": p.get("name") or "", "status": p.get("status"),
                "budget_day_rub": day, "budget_total_rub": total,
                "has_budget": bool(day or total) if plan_budget_known else None}

    observed_s = observed_keys(seg_items)
    users_items = _list_items(raw_dir, "vk_ads_users_lists_list")
    observed_u = observed_keys(users_items)

    def size_of(x, observed):
        v = first_known(x, SIZE_KEYS, observed)
        return v if isinstance(v, int) and not isinstance(v, bool) else None

    relations_known = any(k in observed_s for k in SEGMENT_RELATION_KEYS)
    if seg_items and not relations_known and not any(k in observed_s for k in SIZE_KEYS):
        notes.append("MCP не возвращает размер сегментов и связи с базами клиентов — размер LAL-сегментов и "
                     "обновление баз клиентов не проверялись.")
    lists_collected = bool(read_calls(raw_dir, "vk_ads_users_lists_list"))
    lists_no_access = _no_access_to_lists(collect_log)
    if lists_no_access:
        notes.append("Нет доступа к базам клиентов (vk_ads_users_lists_list): проверки, зависящие от баз, "
                     "не выполнялись — не хватает прав у подключения.")
    return {
        "period": {"from": date_from, "to": date_to, "days": (d_to - d_from).days + 1},
        "target_cpa": target_cpa,
        "plans": [plan_out(p) for p in raw_plans],
        "groups": groups, "banners": banner_out,
        "segments": [{"id": s.get("id"), "name": s.get("name") or "", "lal": is_lal(s),
                      "size": size_of(s, observed_s),
                      "list_ids": users_list_ids(s) if relations_known else None} for s in seg_items],
        "segment_relations_known": relations_known,
        "users_lists": [{"id": u.get("id"), "name": u.get("name") or "", "size": size_of(u, observed_u),
                         "updated": first_known(u, UPDATED_KEYS, observed_u)} for u in users_items],
        "pixels": named("vk_ads_remarketing_pixels_list"),
        "totals": aggregate([g["stats"] for g in counted]),
        "as_of": as_of or datetime.now().isoformat(timespec="minutes"),
        "totals_windows": windows_of([r for g in counted for r in g["daily"]], d_from, d_to),
        "notes": notes,
        "coverage": {"path": path, "banners": bool(banner_calls),
                     "banner_groups": sorted(banner_groups, key=lambda x: (str(type(x)), x)),
                     "summary": bool(summary_calls), "stats": has_stats,
                     "users_lists": lists_collected, "users_lists_no_access": lists_no_access,
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
        if ds["coverage"].get("stats", True) and bs and len(bs) == len(g["banner_ids"]):
            total = sum(s["spent"] for s in bs)
            # Меньше группы — норма (часть объявлений удалена, см. notes); больше — единицы или данные кривые.
            if total - g["stats"]["spent"] > max(1.0, 0.01 * g["stats"]["spent"]):
                out.append({"code": "banners_vs_group", "entity": f"group:{g['id']}",
                            "detail": f"объявления {total} ₽, группа {g['stats']['spent']} ₽"})
    for b in ds["banners"]:
        _check_stats(f"banner:{b['id']}", b["stats"], out)
    return out


def campaign_key(v) -> str:
    """utm_campaign → ключ группы: int или строка, «150895042.0» → «150895042»."""
    s = str(v).strip()
    return s[:-2] if re.fullmatch(r"\d+\.0", s) else s


def validate_metrika(m, period: dict | None = None) -> list:
    """Строгая проверка metrika.json; пустой список — файл годен. Строки ошибок — по-русски."""
    if not isinstance(m, dict):
        return ["верхний уровень должен быть объектом {...}, а не " + type(m).__name__]
    errs: list = []
    if not isinstance(m.get("rows"), list):
        errs.append("поле rows должно быть списком")
    if not isinstance(m.get("utm_tagging"), bool):
        errs.append("поле utm_tagging должно быть true или false")
    for key, bound in (("date_from", "from"), ("date_to", "to")):
        if key in m and period and m[key] != period.get(bound):
            errs.append(f"metrika.json за другой период: {key}={m[key]}, аудит {period.get(bound)}")
    for i, r in enumerate(m.get("rows") if isinstance(m.get("rows"), list) else [], 1):
        if not isinstance(r, dict):
            errs.append(f"строка {i}: должна быть объектом")
            continue
        c = r.get("utm_campaign")
        if isinstance(c, bool) or not isinstance(c, (str, int)) or not campaign_key(c):
            errs.append(f"строка {i}: utm_campaign должен быть id группы (строка или число)")
        for k in ("visits", "goal_reaches"):
            v = r.get(k)
            if isinstance(v, bool) or not isinstance(v, int) or v < 0:
                errs.append(f"строка {i}: {k} должен быть целым неотрицательным числом")
        b = r.get("bounce_rate")
        if isinstance(b, bool) or not isinstance(b, (int, float)) or not (0 <= b <= 1):
            if isinstance(b, (int, float)) and not isinstance(b, bool) and b > 1:
                errs.append(f"строка {i}: bounce_rate {b} похоже на проценты — раздели на 100 (нужна доля 0..1)")
            else:
                errs.append(f"строка {i}: bounce_rate должен быть числом от 0 до 1")
    return errs


def merge_metrika(ds: dict, metrika: dict) -> dict:
    by_group: dict = {}
    for r in metrika.get("rows") or []:
        acc = by_group.setdefault(campaign_key(r.get("utm_campaign", "")),
                                  {"visits": 0, "bounced": 0.0, "goal_reaches": 0})
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


def _read_collect_log(ws: Path):
    try:
        data = json.loads((ws / "collect_log.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


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
        ds = build_dataset(ws / "raw", args.date_from, args.date_to, args.target_cpa, args.path,
                           collect_log=_read_collect_log(ws))
    except (ValueError, KeyError, TypeError, AttributeError) as e:
        print(f"✗ raw/ не разобран: {type(e).__name__}: {e}")
        return 3
    metrika_path = ws / "metrika.json"
    if metrika_path.is_file():
        try:
            metrika = json.loads(metrika_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            print(f"✗ metrika.json: не читается: {e}")
            return 3
        errs = validate_metrika(metrika, ds["period"])
        if errs:
            for e in errs:
                print(f"✗ metrika.json: {e}")
            return 3
        merge_metrika(ds, metrika)
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
