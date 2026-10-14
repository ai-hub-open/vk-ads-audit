from __future__ import annotations

from scripts.diagnose import diagnose, load_thresholds
import json

from scripts.normalize import merge_metrika
from tests.helpers import make_banner, make_ds, make_group, stats
from tests.test_normalize import _good_cabinet, _ws_run

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


def test_cli_merges_metrika_json_when_present(tmp_path):
    ws = tmp_path / "ws"
    _good_cabinet(ws / "raw")
    (ws / "metrika.json").write_text(json.dumps(metrika([
        {"utm_campaign": "1", "utm_content": "", "visits": 50, "bounce_rate": 0.4, "goal_reaches": 2}])),
        encoding="utf-8")
    assert _ws_run(ws) == 0
    ds = json.loads((ws / "dataset.json").read_text(encoding="utf-8"))
    assert ds["coverage"]["metrika"] is True
    assert ds["groups"][0]["metrika"] == {"visits": 50, "bounce_rate": 0.4, "goal_reaches": 2}


def test_paused_group_gets_no_metrika_findings():
    ds = make_ds([make_group(1, status="paused", stats=stats(1000, 10000, 500, 0))], banners(1))
    merge_metrika(ds, metrika([{"utm_campaign": "1", "utm_content": "", "visits": 150,
                                "bounce_rate": 0.9, "goal_reaches": 5}]))
    assert not {r for r, _ in rules(ds)} & {"high_bounce", "click_loss", "goals_without_vk_conversions"}
