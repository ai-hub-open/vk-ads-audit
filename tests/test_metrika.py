from __future__ import annotations

from scripts.diagnose import diagnose, load_thresholds
import json

from scripts.normalize import merge_metrika, validate_metrika
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


PERIOD = {"from": "2026-09-01", "to": "2026-09-30"}


def row(**kw):
    r = {"utm_campaign": "1", "utm_content": "", "visits": 50, "bounce_rate": 0.4, "goal_reaches": 1}
    r.update(kw)
    return r


def _errs(m, period=PERIOD):
    return validate_metrika(m, period)


def test_valid_metrika_has_no_errors():
    assert _errs(metrika([row()])) == []


def test_percent_bounce_rejected():
    errs = _errs(metrika([row(bounce_rate=43)]))
    assert any("проценты" in e for e in errs)


def test_string_visits_rejected():
    assert any("visits" in e for e in _errs({**metrika([row()]), "rows": [row(visits="50")]}))


def test_missing_bounce_rate_rejected():
    r = row()
    del r["bounce_rate"]
    assert any("bounce_rate" in e for e in _errs(metrika([r])))


def test_top_level_list_rejected():
    assert _errs([{"rows": []}])


def test_period_mismatch_rejected():
    errs = _errs({**metrika([row()]), "date_from": "2026-08-01", "date_to": "2026-08-31"})
    assert any("за другой период" in e for e in errs)


def test_period_match_accepted():
    assert _errs({**metrika([row()]), "date_from": "2026-09-01", "date_to": "2026-09-30"}) == []


def test_int_valued_string_campaign_matches_group():
    ds = make_ds([make_group(150895042)])
    merge_metrika(ds, metrika([row(utm_campaign="150895042.0", visits=120, bounce_rate=0.5)]))
    assert ds["groups"][0]["metrika"]["visits"] == 120


def _metrika_run(ws, body):
    (ws / "metrika.json").write_text(body, encoding="utf-8")
    return _ws_run(ws)


def test_cli_invalid_json_exits_3_names_file(tmp_path, capsys):
    ws = tmp_path / "ws"
    _good_cabinet(ws / "raw")
    (ws / "dataset.json").write_text("{}", encoding="utf-8")
    assert _metrika_run(ws, "{not json") == 3
    out = capsys.readouterr().out
    assert "metrika.json" in out and "raw/" not in out
    assert not (ws / "dataset.json").exists()


def test_cli_percent_bounce_exits_3(tmp_path, capsys):
    ws = tmp_path / "ws"
    _good_cabinet(ws / "raw")
    body = json.dumps(metrika([row(bounce_rate=43)]))
    assert _metrika_run(ws, body) == 3
    assert "проценты" in capsys.readouterr().out
    assert not (ws / "dataset.json").exists()


def test_cli_valid_metrika_exits_0_and_marks_coverage(tmp_path):
    ws = tmp_path / "ws"
    _good_cabinet(ws / "raw")
    assert _metrika_run(ws, json.dumps(metrika([row()]))) == 0
    ds = json.loads((ws / "dataset.json").read_text(encoding="utf-8"))
    assert ds["coverage"]["metrika"] is True


def test_metrika_rules_skip_stats_missing_groups():
    g = make_group(1, stats=stats(), stats_missing=True)
    ds = make_ds([g], banners(1))
    merge_metrika(ds, metrika([{"utm_campaign": "1", "utm_content": "", "visits": 250,
                                "bounce_rate": 0.9, "goal_reaches": 4}]))
    assert not [r for r in rules(ds) if r[0] in ("high_bounce", "click_loss", "goals_without_vk_conversions")]
