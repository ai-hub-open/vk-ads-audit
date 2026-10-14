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
