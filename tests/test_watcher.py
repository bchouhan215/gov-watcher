import json
import tempfile
from pathlib import Path

from gov-watcher.watcher import load_json, save_json


def test_load_json_nonexistent(tmp_path):
    p = tmp_path / "nope.json"
    assert not p.exists()
    assert load_json(p) == {}


def test_load_json_invalid(tmp_path):
    p = tmp_path / "bad.json"
    p.write_text("{invalid json}", encoding="utf-8")
    assert load_json(p) == {}


def test_save_and_load_json(tmp_path):
    p = tmp_path / "state.json"
    data = {"a": 1}
    save_json(p, data)
    assert p.exists()
    loaded = load_json(p)
    assert loaded == data
