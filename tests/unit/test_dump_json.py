from __future__ import annotations

from rex_codex.scope_project.utils import dump_json


def test_dump_json_sorts_and_writes(tmp_path):
    path = tmp_path / "data.json"
    dump_json(path, {"z": 1, "a": 2})
    content = path.read_text(encoding="utf-8")
    assert "\"a\"" in content
    assert content.index("\"a\"") < content.index("\"z\"")


def test_dump_json_utf8(tmp_path):
    path = tmp_path / "utf8.json"
    accented = "hola caf" + chr(0x00E9)
    dump_json(path, {"greeting": accented}, ensure_ascii=False, sort_keys=False)
    content = path.read_text(encoding="utf-8")
    assert accented in content
