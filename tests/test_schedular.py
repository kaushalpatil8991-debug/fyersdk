"""Unit tests for supervisor scheduler helpers.

schedular.py is loaded via importlib file-load because its package __init__
imports the orchestrator (-> fyers_apiv3, unavailable on Python 3.14).
"""
import importlib.util
import pathlib

MODULE_PATH = (
    pathlib.Path(__file__).resolve().parents[1]
    / "services" / "supervisor_service" / "schedular.py"
)


def _load():
    spec = importlib.util.spec_from_file_location("schedular_under_test", MODULE_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


sched = _load()


def test_should_reset_before_reset_time_is_false():
    assert sched.should_reset_tokens("16:59", None, "23-07-2026") is False


def test_should_reset_at_reset_time_is_true():
    assert sched.should_reset_tokens("17:00", None, "23-07-2026") is True


def test_should_reset_after_reset_time_is_true():
    assert sched.should_reset_tokens("20:30", "22-07-2026", "23-07-2026") is True


def test_should_not_reset_twice_same_day():
    assert sched.should_reset_tokens("17:05", "23-07-2026", "23-07-2026") is False


def test_should_not_reset_in_the_morning():
    assert sched.should_reset_tokens("09:13", None, "23-07-2026") is False
