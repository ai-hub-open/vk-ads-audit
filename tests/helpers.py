from __future__ import annotations

from scripts.normalize import _ratios, targeting_key


def stats(spent=0, shows=0, clicks=0, conversions=0, frequency=None):
    return {"spent": float(spent), "shows": shows, "clicks": clicks, "conversions": conversions,
            **_ratios(float(spent), shows, clicks, conversions),
            "vk_ctr_pct": None, "reach": 0, "frequency": frequency}


FEATURES = {"regions": [], "age_span": None, "sex": [], "has_interests": False, "has_communities": None,
            "has_keywords": None, "has_minus": None, "kw_period": None, "kw_expand": None,
            "has_exclusions": None, "auto_placements": None, "device_adjustments": None,
            "demo_adjustments": None}


def make_group(id=1, **kw):
    feats = kw.pop("features", {})
    g = {"id": id, "name": f"G{id}", "status": "active", "objective": "site_conversions", "package_id": 1,
         "ad_plan_id": 7, "delivery": "delivering", "autobidding_mode": None, "budget_day_rub": 1000.0,
         "budget_total_rub": None, "issues": [], "created": "2026-09-01 00:00:00", "date_end": None,
         "targeting_key": targeting_key({"geo": {"regions": [id]}}), "segment_ids": [],
         "stats": stats(), "daily": [], "banner_ids": [],
         "layer": "cold", "max_price_rub": None, "auto_utm": None, "windows": {"7": None, "14": None, "30": None}}
    g["features"] = {**FEATURES, "regions": [id], **feats}
    g.update(kw)
    return g


def make_banner(id=11, group_id=1, **kw):
    b = {"id": id, "name": f"B{id}", "group_id": group_id, "status": "active", "moderation_status": "allowed",
         "moderation_reasons": None, "issues": [], "can_remoderate": False, "stats": None,
         "cta": None, "text": "", "urls": [], "ratios": [], "has_video": False, "content_ids": [],
         "created": None, "age_days": None, "erid": None}
    b.update(kw)
    return b


def make_ds(groups, banners=(), **kw):
    ds = {"period": {"from": "2026-09-01", "to": "2026-09-30", "days": 30}, "target_cpa": None,
          "plans": [{"id": 7, "name": "План", "status": "active", "budget_day_rub": None,
                    "budget_total_rub": None, "has_budget": None}], "groups": list(groups),
          "banners": list(banners), "segments": [], "users_lists": [], "pixels": [{"id": 1, "name": "px"}],
          "as_of": "2026-10-01T10:00", "totals_windows": {"7": None, "14": None, "30": None},
          "totals": stats(), "coverage": {"path": "B", "banners": True, "summary": True, "stats": True,
                                 "pixels": True, "metrika": False}}
    ds.update(kw)
    cov = ds["coverage"]
    if cov.get("banners") and "banner_groups" not in cov:
        cov["banner_groups"] = [g["id"] for g in ds["groups"]]
    ds.setdefault("notes", [])
    for g in ds["groups"]:
        if not g["banner_ids"]:
            g["banner_ids"] = [b["id"] for b in ds["banners"] if b["group_id"] == g["id"]]
    return ds
