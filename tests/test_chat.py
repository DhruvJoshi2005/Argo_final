"""
Tests for chat intent normalization and filter planning.
No LLM calls — tests the deterministic post-LLM pipeline only.
"""
import pytest
from chat_main_optimised import normalize_intent, plan_filters, validate_intent


# ─────────────────────────────────────────────
# normalize_intent — named geo regions
# ─────────────────────────────────────────────

class TestNormalizeIntent:
    def _norm(self, geo):
        return normalize_intent({"geo": geo, "metric": "temperature"}, geo)["geo"]

    # Original regions
    def test_pacific_ocean(self):         assert self._norm("pacific ocean")    == "geo_pacific"
    def test_atlantic_ocean(self):        assert self._norm("atlantic ocean")   == "geo_atlantic"
    def test_indian_ocean(self):          assert self._norm("indian ocean")     == "geo_indian"
    def test_southern_ocean(self):        assert self._norm("southern ocean")   == "geo_southern"
    def test_arctic_ocean(self):          assert self._norm("arctic ocean")     == "geo_arctic"

    # New Indian Ocean sub-regions
    def test_arabian_sea(self):           assert self._norm("arabian sea")      == "geo_arabian_sea"
    def test_bay_of_bengal(self):         assert self._norm("bay of bengal")    == "geo_bay_of_bengal"
    def test_andaman_sea(self):           assert self._norm("andaman sea")      == "geo_andaman_sea"

    def test_unknown_region_unchanged(self):
        assert self._norm("caspian sea") == "caspian sea"

    def test_case_insensitive(self):
        assert self._norm("Arabian Sea") == "geo_arabian_sea"
        assert self._norm("BAY OF BENGAL") == "geo_bay_of_bengal"

    def test_metric_synonym_oxygen(self):
        intent = normalize_intent({"metric": "dissolved oxygen", "geo": None}, "")
        assert intent["metric"] == "oxygen"

    def test_metric_synonym_chlorophyll(self):
        intent = normalize_intent({"metric": "chlorophyll-a", "geo": None}, "")
        assert intent["metric"] == "chlorophyll"

    def test_raw_question_stored(self):
        intent = normalize_intent({"metric": "temperature", "geo": None}, "Test Question")
        assert intent["_raw_question"] == "test question"


# ─────────────────────────────────────────────
# plan_filters — named geo bounding boxes
# ─────────────────────────────────────────────

class TestPlanFiltersNamed:
    def _filters(self, geo):
        intent = normalize_intent({"geo": geo, "metric": "temperature"}, geo)
        return plan_filters(intent)

    def _lat(self, geo):
        return next(f for f in self._filters(geo) if f[0] == "latitude")

    def _lon(self, geo):
        return next(f for f in self._filters(geo) if f[0] == "longitude")

    def test_arabian_sea_lat_bbox(self):
        assert self._lat("arabian sea")[2] == (8, 25)

    def test_arabian_sea_lon_bbox(self):
        assert self._lon("arabian sea")[2] == (50, 77)

    def test_bay_of_bengal_lat_bbox(self):
        assert self._lat("bay of bengal")[2] == (5, 22)

    def test_bay_of_bengal_lon_bbox(self):
        assert self._lon("bay of bengal")[2] == (77, 100)

    def test_andaman_sea_lat_bbox(self):
        assert self._lat("andaman sea")[2] == (5, 18)

    def test_andaman_sea_lon_bbox(self):
        assert self._lon("andaman sea")[2] == (92, 100)

    def test_unknown_region_no_filters(self):
        filters = self._filters("caspian sea")
        assert not any(f[0] == "latitude" for f in filters)

    def test_no_geo_no_spatial_filters(self):
        intent = normalize_intent({"geo": None, "metric": "temperature"}, "")
        filters = plan_filters(intent)
        assert not any(f[0] in ("latitude", "longitude") for f in filters)


# ─────────────────────────────────────────────
# plan_filters — explicit lat/lon coordinates
# ─────────────────────────────────────────────

class TestPlanFiltersExplicitCoords:
    def _make_intent(self, lat_min, lat_max, lon_min, lon_max):
        return {
            "metric": "temperature",
            "geo": None,
            "lat_min": lat_min,
            "lat_max": lat_max,
            "lon_min": lon_min,
            "lon_max": lon_max,
        }

    def test_explicit_coords_generate_filters(self):
        intent = self._make_intent(5.0, 21.9, 77.7, 94.0)
        filters = plan_filters(intent)
        lat_f = next(f for f in filters if f[0] == "latitude")
        lon_f = next(f for f in filters if f[0] == "longitude")
        assert lat_f[2] == (5.0, 21.9)
        assert lon_f[2] == (77.7, 94.0)

    def test_explicit_coords_override_named_geo(self):
        # Even if geo is set, explicit coords take priority
        intent = {
            "metric": "temperature",
            "geo": "arabian sea",
            "lat_min": 10.0, "lat_max": 15.0,
            "lon_min": 60.0, "lon_max": 70.0,
        }
        intent = normalize_intent(intent, "")
        filters = plan_filters(intent)
        lat_f = next(f for f in filters if f[0] == "latitude")
        assert lat_f[2] == (10.0, 15.0)

    def test_partial_coords_only_lat(self):
        # lat given, lon missing → only lat filter
        intent = self._make_intent(5.0, 21.9, None, None)
        filters = plan_filters(intent)
        assert any(f[0] == "latitude" for f in filters)
        assert not any(f[0] == "longitude" for f in filters)

    def test_no_coords_falls_back_to_named_geo(self):
        intent = {
            "metric": "temperature",
            "geo": "arabian sea",
            "lat_min": None, "lat_max": None,
            "lon_min": None, "lon_max": None,
        }
        intent = normalize_intent(intent, "arabian sea")
        filters = plan_filters(intent)
        lat_f = next(f for f in filters if f[0] == "latitude")
        assert lat_f[2] == (8, 25)


# ─────────────────────────────────────────────
# validate_intent
# ─────────────────────────────────────────────

class TestValidateIntent:
    def test_valid_keys_pass(self):
        validate_intent({
            "metric": "temperature", "geo": None, "time": None,
            "depth": None, "aggregation": "avg",
            "lat_min": None, "lat_max": None,
            "lon_min": None, "lon_max": None,
        })

    def test_unknown_key_raises(self):
        with pytest.raises(ValueError, match="Invalid intent key"):
            validate_intent({"metric": "temperature", "bad_key": "x"})

    def test_missing_metric_raises(self):
        with pytest.raises(ValueError, match="Metric is required"):
            validate_intent({"metric": None})
