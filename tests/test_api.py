"""
Tests for new API endpoints and the depth filter fix.
Uses FastAPI TestClient — no live server or real DB needed
(endpoints that hit the DB are tested with a lightweight mock).
"""
import pytest
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient

from main import app

client = TestClient(app)


# ─────────────────────────────────────────────
# /  and  /health
# ─────────────────────────────────────────────

def test_root_returns_running():
    r = client.get("/")
    assert r.status_code == 200
    assert r.json()["message"] == "Backend is running!"


def _mock_db_health():
    mock_conn = MagicMock()
    mock_cur = MagicMock()
    mock_cur.__enter__ = lambda s: s
    mock_cur.__exit__ = MagicMock(return_value=False)
    mock_cur.fetchone.return_value = (262, 33861, 16875080, "2026-07-03 00:00:00")
    mock_conn.cursor.return_value = mock_cur
    return mock_conn


def test_health_ok():
    with patch("main._db", return_value=_mock_db_health()):
        r = client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["floats"] == 262
    assert body["measurements"] == 16875080


def test_health_db_down_returns_503():
    with patch("main._db", side_effect=Exception("connection refused")):
        r = client.get("/health")
    assert r.status_code == 503


# ─────────────────────────────────────────────
# /float/{id}/track
# ─────────────────────────────────────────────

def _mock_db_track(rows):
    mock_conn = MagicMock()
    mock_cur = MagicMock()
    mock_cur.__enter__ = lambda s: s
    mock_cur.__exit__ = MagicMock(return_value=False)
    mock_cur.fetchall.return_value = rows
    mock_conn.cursor.return_value = mock_cur
    return mock_conn


def test_track_returns_points():
    rows = [(1, "2026-01-01", 14.5, 72.3), (2, "2026-01-11", 14.8, 72.6)]
    with patch("main._db", return_value=_mock_db_track(rows)):
        r = client.get("/float/2902196/track")
    assert r.status_code == 200
    body = r.json()
    assert body["platform_number"] == "2902196"
    assert len(body["points"]) == 2
    assert body["points"][0]["cycle"] == 1
    assert body["points"][0]["latitude"] == 14.5


def test_track_not_found_returns_404():
    with patch("main._db", return_value=_mock_db_track([])):
        r = client.get("/float/9999999/track")
    assert r.status_code == 404


# ─────────────────────────────────────────────
# /profile/{id}/{cycle}
# ─────────────────────────────────────────────

def _mock_db_profile(rows):
    mock_conn = MagicMock()
    mock_cur = MagicMock()
    mock_cur.__enter__ = lambda s: s
    mock_cur.__exit__ = MagicMock(return_value=False)
    mock_cur.fetchall.return_value = rows
    mock_conn.cursor.return_value = mock_cur
    return mock_conn


def test_profile_returns_levels():
    rows = [
        (10.0, 28.5, 35.1, None, None, None, None, "D"),
        (50.0, 22.3, 35.5, None, None, None, None, "D"),
    ]
    with patch("main._db", return_value=_mock_db_profile(rows)):
        r = client.get("/profile/2902196/155")
    assert r.status_code == 200
    body = r.json()
    assert body["platform_number"] == "2902196"
    assert body["cycle_number"] == 155
    assert len(body["levels"]) == 2
    assert body["levels"][0]["pressure"] == 10.0
    assert body["levels"][0]["temperature"] == 28.5


def test_profile_not_found_returns_404():
    with patch("main._db", return_value=_mock_db_profile([])):
        r = client.get("/profile/9999999/999")
    assert r.status_code == 404


def test_profile_levels_ordered_by_pressure():
    rows = [(5.0, 29.0, 35.0, None, None, None, None, "D"),
            (100.0, 20.0, 35.8, None, None, None, None, "D"),
            (500.0, 12.0, 36.1, None, None, None, None, "D")]
    with patch("main._db", return_value=_mock_db_profile(rows)):
        r = client.get("/profile/2902196/1")
    pressures = [lv["pressure"] for lv in r.json()["levels"]]
    assert pressures == sorted(pressures)


# ─────────────────────────────────────────────
# depth filter in plan_filters
# ─────────────────────────────────────────────

from chat_main_optimised import plan_filters, normalize_intent


def test_depth_single_value_generates_between():
    intent = normalize_intent({"metric": "temperature", "geo": None, "depth": 200}, "")
    filters = plan_filters(intent)
    pres_f = next(f for f in filters if f[0] == "pressure")
    assert pres_f[1] == "BETWEEN"
    lo, hi = pres_f[2]
    assert lo == pytest.approx(180.0)
    assert hi == pytest.approx(220.0)


def test_depth_range_exact():
    intent = normalize_intent(
        {"metric": "temperature", "geo": None, "depth_min": 100, "depth_max": 300}, ""
    )
    filters = plan_filters(intent)
    pres_f = next(f for f in filters if f[0] == "pressure")
    assert pres_f[1] == "BETWEEN"
    assert pres_f[2] == (100.0, 300.0)


def test_depth_range_takes_priority_over_single_depth():
    intent = normalize_intent(
        {"metric": "temperature", "geo": None,
         "depth": 500, "depth_min": 100, "depth_max": 200}, ""
    )
    filters = plan_filters(intent)
    pres_f = next(f for f in filters if f[0] == "pressure")
    assert pres_f[2] == (100.0, 200.0)


def test_no_depth_no_pressure_filter():
    intent = normalize_intent({"metric": "temperature", "geo": None}, "")
    filters = plan_filters(intent)
    assert not any(f[0] == "pressure" for f in filters)


# ─────────────────────────────────────────────
# _to_float — guards against LLM returning dicts
# ─────────────────────────────────────────────

from chat_main_optimised import _to_float

def test_to_float_dict_returns_none():
    assert _to_float({"value": 5}) is None

def test_to_float_none_returns_none():
    assert _to_float(None) is None

def test_to_float_valid_number():
    assert _to_float(14.5) == 14.5

def test_to_float_valid_string():
    assert _to_float("72.3") == 72.3

def test_to_float_bad_string_returns_none():
    assert _to_float("bad") is None

def test_lat_min_as_dict_produces_no_filter():
    # LLM returned a dict for lat_min — must not crash, must produce no filter
    intent = normalize_intent(
        {"metric": "temperature", "geo": None,
         "lat_min": {"degrees": 5}, "lat_max": 22.0,
         "lon_min": None, "lon_max": None}, ""
    )
    filters = plan_filters(intent)
    assert not any(f[0] == "latitude" for f in filters)

def test_depth_min_as_dict_produces_no_filter():
    intent = normalize_intent(
        {"metric": "temperature", "geo": None,
         "depth_min": {"value": 100}, "depth_max": 200}, ""
    )
    filters = plan_filters(intent)
    assert not any(f[0] == "pressure" for f in filters)
