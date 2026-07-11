"""
Tests for ingestion helper functions.
No database or NetCDF files required — pure logic only.
"""
import math
import pytest
import numpy as np
from datetime import datetime

from ingestion_logic.rtraj_ingestion import (
    safe_float,
    safe_position,
    safe_cycle_int,
    safe_array_value,
)
from ingestion_logic.prof_ingestion import (
    safe_float as prof_safe_float,
    safe_position as prof_safe_position,
    juld_to_timestamp as prof_juld,
)
from ingestion_logic.sprof_ingestion import (
    safe_float as sprof_safe_float,
    safe_position as sprof_safe_position,
    juld_to_timestamp as sprof_juld,
)


# ─────────────────────────────────────────────
# safe_float (rtraj)
# ─────────────────────────────────────────────

class TestSafeFloat:
    def test_nan_returns_none(self):
        assert safe_float(float("nan")) is None

    def test_numpy_nan_returns_none(self):
        assert safe_float(np.nan) is None

    def test_inf_returns_none(self):
        assert safe_float(float("inf")) is None

    def test_neg_inf_returns_none(self):
        assert safe_float(float("-inf")) is None

    def test_valid_float(self):
        assert safe_float(14.5) == 14.5

    def test_valid_string_float(self):
        assert safe_float("12.5") == 12.5

    def test_bad_string_returns_none(self):
        assert safe_float("bad") is None

    def test_custom_default_on_nan(self):
        assert safe_float(float("nan"), default=0.0) == 0.0

    def test_none_input_returns_none(self):
        assert safe_float(None) is None

    def test_large_valid_value(self):
        assert safe_float(99999.0) == 99999.0

    def test_negative_valid_value(self):
        assert safe_float(-2.5) == -2.5


# ─────────────────────────────────────────────
# safe_position (rtraj, prof, sprof)
# ─────────────────────────────────────────────

@pytest.mark.parametrize("fn", [safe_position, prof_safe_position, sprof_safe_position])
class TestSafePosition:
    def test_qc_1_passes(self, fn):
        assert fn(14.5, 72.3, b"1") == (14.5, 72.3)

    def test_qc_2_passes(self, fn):
        assert fn(14.5, 72.3, b"2") == (14.5, 72.3)

    def test_qc_4_rejected(self, fn):
        assert fn(14.5, 72.3, b"4") == (None, None)

    def test_qc_9_rejected(self, fn):
        assert fn(14.5, 72.3, b"9") == (None, None)

    def test_qc_3_rejected(self, fn):
        assert fn(14.5, 72.3, b"3") == (None, None)

    def test_nan_lat_rejected(self, fn):
        assert fn(float("nan"), 72.3, b"1") == (None, None)

    def test_nan_lon_rejected(self, fn):
        assert fn(14.5, float("nan"), b"1") == (None, None)

    def test_none_qc_rejected(self, fn):
        assert fn(14.5, 72.3, None) == (None, None)

    def test_null_island_nan_rejected(self, fn):
        # NaN fill values must not produce (0.0, 0.0)
        result = fn(float("nan"), float("nan"), b"1")
        assert result == (None, None)


# ─────────────────────────────────────────────
# safe_cycle_int (rtraj)
# ─────────────────────────────────────────────

class TestSafeCycleInt:
    def test_valid_int(self):
        assert safe_cycle_int(45) == 45

    def test_valid_float(self):
        assert safe_cycle_int(45.0) == 45

    def test_nan_returns_none(self):
        assert safe_cycle_int(float("nan")) is None

    def test_inf_returns_none(self):
        assert safe_cycle_int(float("inf")) is None

    def test_string_number(self):
        assert safe_cycle_int("12") == 12

    def test_bad_string_returns_none(self):
        assert safe_cycle_int("bad") is None

    def test_none_returns_none(self):
        assert safe_cycle_int(None) is None


# ─────────────────────────────────────────────
# per-measurement position walk (rtraj logic)
# ─────────────────────────────────────────────

class TestPerMeasurementWalk:
    def _walk(self, cycle_arr, lat_arr, lon_arr, qc_arr):
        per_cycle = {}
        measurement_aligned = (
            len(lat_arr) == len(cycle_arr) and len(lon_arr) == len(cycle_arr)
        )
        for i, c in enumerate(cycle_arr):
            if c is None:
                continue
            c_int = safe_cycle_int(c)
            if c_int is None or c_int < 0:
                continue
            entry = per_cycle.setdefault(c_int, {"lat": None, "lon": None})
            if measurement_aligned:
                lat, lon = safe_position(
                    safe_array_value(lat_arr, i),
                    safe_array_value(lon_arr, i),
                    safe_array_value(qc_arr, i),
                )
                if lat is not None and lon is not None:
                    entry["lat"] = lat
                    entry["lon"] = lon
        return per_cycle

    def test_last_good_position_kept(self):
        # Cycle 1 has two good rows — last one wins
        result = self._walk(
            cycle_arr=np.array([1, 1]),
            lat_arr=np.array([14.5, 14.6]),
            lon_arr=np.array([72.3, 72.4]),
            qc_arr=[b"1", b"2"],
        )
        assert result[1]["lat"] == 14.6
        assert result[1]["lon"] == 72.4

    def test_first_row_bad_qc_second_good(self):
        result = self._walk(
            cycle_arr=np.array([1, 1]),
            lat_arr=np.array([14.5, 14.6]),
            lon_arr=np.array([72.3, 72.4]),
            qc_arr=[b"9", b"1"],
        )
        assert result[1]["lat"] == 14.6

    def test_all_bad_qc_stays_none(self):
        # Must not produce (0.0, 0.0) — the null-island bug
        result = self._walk(
            cycle_arr=np.array([3]),
            lat_arr=np.array([float("nan")]),
            lon_arr=np.array([float("nan")]),
            qc_arr=[b"9"],
        )
        assert result[3]["lat"] is None
        assert result[3]["lon"] is None

    def test_multiple_cycles_independent(self):
        result = self._walk(
            cycle_arr=np.array([1, 2]),
            lat_arr=np.array([10.0, 20.0]),
            lon_arr=np.array([60.0, 80.0]),
            qc_arr=[b"1", b"1"],
        )
        assert result[1]["lat"] == 10.0
        assert result[2]["lat"] == 20.0

    def test_nan_cycle_number_skipped(self):
        result = self._walk(
            cycle_arr=np.array([float("nan"), 5]),
            lat_arr=np.array([10.0, 20.0]),
            lon_arr=np.array([60.0, 80.0]),
            qc_arr=[b"1", b"1"],
        )
        assert float("nan") not in result
        assert result[5]["lat"] == 20.0


# ─────────────────────────────────────────────
# juld_to_timestamp (prof + sprof)
# ─────────────────────────────────────────────

@pytest.mark.parametrize("fn", [prof_juld, sprof_juld])
class TestJuldToTimestamp:
    def test_epoch_zero(self, fn):
        assert fn(0) == datetime(1950, 1, 1)

    def test_known_date(self, fn):
        # 25567 days after 1950-01-01 = 2020-01-01
        result = fn(25567)
        assert isinstance(result, datetime)
        assert result.year == 2020

    def test_nan_returns_none(self, fn):
        assert fn(float("nan")) is None

    def test_bad_string_returns_none(self, fn):
        assert fn("bad") is None

    def test_large_value_returns_datetime(self, fn):
        result = fn(28000)
        assert isinstance(result, datetime)
