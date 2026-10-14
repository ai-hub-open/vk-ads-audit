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


def test_paused_group_counted_but_not_flagged():
    paused = make_group(1, status="blocked", daily=rows([(100, 2)] * 7 + [(300, 2)]))
    active = make_group(2, daily=rows([(100, 2)] * 8))
    res = quick_summary(make_ds([paused, active]), "2026-10-13")
    flags = {g["id"]: g["flags"] for g in res["groups"]}
    assert flags[1] == []
    assert res["today"]["spent"] == 400.0


def test_no_data_day_skips_flags():
    g = make_group(1, daily=rows([(100, 2)] * 7))  # история 10-06..10-12, строки за 10-13 нет
    h = make_group(2, daily=rows([(100, 2)] * 7))
    res = quick_summary(make_ds([g, h]), "2026-10-13")
    assert res["lines"] == ["📅 VK Реклама — 13.10.2026",
                            "Данных за этот день нет — возможно, статистика ещё не пришла."]
    assert all(x["flags"] == [] for x in res["groups"])


def test_cpa_jump_flagged():
    g = make_group(1, daily=rows([(100, 2)] * 7 + [(100, 1)]))  # CPA 50 → 100, расход ровный
    res = quick_summary(make_ds([g]), "2026-10-13")
    assert res["groups"][0]["flags"] == ["cpa_jump"]


def test_deleted_group_ignored():
    deleted = make_group(1, status="deleted", daily=rows([(100, 2)] * 7 + [(0, 0)]))
    active = make_group(2, daily=rows([(100, 2)] * 8))
    res = quick_summary(make_ds([deleted, active]), "2026-10-13")
    assert [g["id"] for g in res["groups"]] == [2]
    assert res["today"]["spent"] == 100.0


def test_new_group_no_spend_spike():
    new = make_group(1, daily=[{"date": "2026-10-13", **stats(300, 1000, 10, 2)}])
    res = quick_summary(make_ds([new]), "2026-10-13")
    assert res["groups"][0]["flags"] == []


def test_average_uses_days_with_rows():
    # две дня с данными по 100 ₽ → среднее 100, порог 150; сегодня 120 — не всплеск
    g = make_group(1, daily=[{"date": "2026-10-11", **stats(100, 1000, 10, 2)},
                             {"date": "2026-10-12", **stats(100, 1000, 10, 2)},
                             {"date": "2026-10-13", **stats(120, 1000, 10, 2)}])
    res = quick_summary(make_ds([g]), "2026-10-13")
    assert res["groups"][0]["flags"] == []
    assert res["prev_days"] == 2


def test_multi_flag_line_joins_texts_and_icon_of_most_severe():
    g = make_group(1, name="X", daily=rows([(100, 2)] * 7 + [(300, 1)]))  # cpa_jump + spend_spike
    res = quick_summary(make_ds([g]), "2026-10-13")
    assert res["groups"][0]["flags"] == ["cpa_jump", "spend_spike"]
    assert "🔺 «X»: CPA вырос, расход выше обычного (300 ₽, CPA 300 ₽)" in res["lines"]


def test_cap_keeps_stopped_and_shows_hidden_count_and_arrow():
    stopped = [make_group(i, daily=rows([(100, 2)] * 7 + [(0, 0)])) for i in range(1, 4)]
    spikes = [make_group(i, daily=rows([(100, 2)] * 7 + [(300, 2)])) for i in range(4, 13)]
    res = quick_summary(make_ds(stopped + spikes), "2026-10-13")
    lines = res["lines"]
    assert len(lines) == 10
    assert sum("⛔" in line for line in lines) == 3
    assert lines[8] == "…и ещё 7 групп с отклонениями"
    assert lines[9] == "→ Разобрать подробно? Могу провести глубокий аудит."


# --- CLI ---------------------------------------------------------------------
import json

from scripts import quick as quick_mod


def _ws(tmp_path, ds=None):
    ws = tmp_path / "ws"
    ws.mkdir()
    if ds is not None:
        (ws / "dataset.json").write_text(json.dumps(ds, ensure_ascii=False), encoding="utf-8")
    return ws


def test_cli_success_writes_quick_json(tmp_path, capsys):
    ws = _ws(tmp_path, make_ds([make_group(1, name="Норма", daily=rows([(100, 2)] * 8))]))
    rc = quick_mod.main(["--workspace", str(ws), "--day", "2026-10-13"])
    assert rc == 0
    assert "Аномалий нет." in capsys.readouterr().out
    saved = json.loads((ws / "quick.json").read_text(encoding="utf-8"))
    assert saved["day"] == "2026-10-13" and saved["lines"][0].startswith("📅")


def test_cli_malformed_day(tmp_path, capsys):
    rc = quick_mod.main(["--workspace", str(_ws(tmp_path, make_ds([]))), "--day", "13.10.2026"])
    assert rc == 2
    assert "Неверная дата" in capsys.readouterr().err


def test_cli_future_day_incomplete(tmp_path, capsys):
    rc = quick_mod.main(["--workspace", str(_ws(tmp_path, make_ds([]))), "--day", "2099-01-01"])
    assert rc == 2
    assert "ещё не завершён" in capsys.readouterr().err


def test_cli_missing_dataset(tmp_path, capsys):
    rc = quick_mod.main(["--workspace", str(_ws(tmp_path)), "--day", "2026-10-13"])
    assert rc == 2
    assert "dataset.json" in capsys.readouterr().err


def test_cli_corrupt_dataset(tmp_path, capsys):
    ws = _ws(tmp_path)
    (ws / "dataset.json").write_text("{не json", encoding="utf-8")
    rc = quick_mod.main(["--workspace", str(ws), "--day", "2026-10-13"])
    assert rc == 2
    assert "Не удалось прочитать dataset.json" in capsys.readouterr().err
