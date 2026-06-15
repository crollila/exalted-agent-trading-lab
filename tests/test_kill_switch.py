import pytest

from src.safety.kill_switch import (
    KillSwitchEngaged,
    assert_clear,
    disengage,
    engage,
    is_engaged,
    read_kill_switch,
)


def test_default_disengaged(tmp_path):
    path = tmp_path / "ks.json"
    assert is_engaged(path) is False
    assert read_kill_switch(path).engaged is False


def test_engage_and_disengage_roundtrip(tmp_path):
    path = tmp_path / "ks.json"
    engage(reason="testing", path=path)
    assert is_engaged(path) is True
    assert read_kill_switch(path).reason == "testing"
    disengage(path)
    assert is_engaged(path) is False


def test_assert_clear_raises_when_engaged(tmp_path):
    path = tmp_path / "ks.json"
    engage(reason="halt", path=path)
    with pytest.raises(KillSwitchEngaged):
        assert_clear(path)


def test_unreadable_file_fails_closed(tmp_path):
    path = tmp_path / "ks.json"
    path.write_text("not json", encoding="utf-8")
    assert is_engaged(path) is True
