import copy

import pytest

from app.domain.daily import (HourPoint, band_label, build_bands, build_metrics, humidity_level,
                              most_common_condition, rain_level, summarize_day, summary_text)
from app.domain.scoring import (DEFAULT_PARAMS, NotEvaluable, evaluate, label_for, validate_params)


def point(hour, temp, cond, **kw):
    return HourPoint(hour=hour, temperature=temp, condition=cond, **kw)


def design_day():
    """Caso del diseño: bruma 22 °C · sol 26 °C · brisa 24 °C (sábado 19 de octubre)."""
    pts = [point(h, 22, "fog", humidity=82, rain_probability=15, precipitation=0, wind_speed=3) for h in range(8, 12)]
    pts += [point(h, 26, "clear", humidity=70, rain_probability=10, precipitation=0, wind_speed=6) for h in range(12, 16)]
    pts += [point(h, 24, "cloudy", humidity=80, rain_probability=15, precipitation=0.2, wind_speed=10) for h in (16, 17)]
    return pts


# ----------------------------------------------------------------- resumen
def test_design_golden_day():
    day = summarize_day(design_day())
    assert day["temperature"] == 26
    assert day["condition_code"] == "clear" and day["icon"] == "wb_sunny"
    assert day["summary"] == "Soleado con niebla matinal templada"
    bands = day["bands"]
    assert [(b["key"], b["temperature"], b["label"]) for b in bands] == [
        ("08-12", 22, "Bruma fresca"), ("12-16", 26, "Sol radiante"), ("16-18", 24, "Brisa suave")]
    assert bands[0]["from"] == "08:00" and bands[2]["to"] == "18:00"


def test_metrics_levels_follow_design_labels():
    m = summarize_day(design_day())["metrics"]
    assert m["rain_probability"] == {"value": 15, "level": "low", "label": "Baja"}
    assert m["humidity"]["label"] == "Óptima"
    assert m["precipitation"]["value"] == 0.4
    assert m["wind_speed"]["max"] == 10


@pytest.mark.parametrize("prob,expected", [(0, "Baja"), (29, "Baja"), (30, "Media"), (59, "Media"), (60, "Alta")])
def test_rain_level_boundaries(prob, expected):
    assert rain_level(prob)[1] == expected


@pytest.mark.parametrize("hum,expected", [(49, "Seca"), (50, "Óptima"), (85, "Óptima"), (86, "Muy húmeda")])
def test_humidity_level_boundaries(hum, expected):
    assert humidity_level(hum)[1] == expected


def test_levels_are_none_without_data():
    assert rain_level(None) is None and humidity_level(None) is None
    m = build_metrics([point(9, 20, "clear")])
    assert m["rain_probability"]["value"] is None and m["wind_speed"]["value"] is None


def test_rain_anywhere_overrides_midday_condition():
    pts = design_day()
    pts[-1] = point(17, 23, "thunderstorm", rain_probability=90)
    pts[0] = point(8, 22, "light_rain", rain_probability=60)
    assert summarize_day(pts)["condition_code"] == "thunderstorm"


def test_most_common_condition_breaks_ties_by_severity():
    assert most_common_condition(["clear", "rain", "clear"]) == "clear"
    assert most_common_condition(["clear", "rain"]) == "rain"


def test_hours_outside_operating_window_are_ignored():
    pts = [point(3, 15, "clear"), point(9, 25, "cloudy"), point(20, 18, "rain")]
    day = summarize_day(pts)
    assert day["temperature"] == 25 and day["condition_code"] == "cloudy"
    assert summarize_day([point(3, 15, "clear")]) is None
    assert summarize_day([]) is None


def test_missing_band_is_skipped():
    bands = build_bands([point(9, 20, "clear")])
    assert [b["key"] for b in bands] == ["08-12"]


@pytest.mark.parametrize("cond,temp,wind,label", [
    ("clear", 26, None, "Sol radiante"), ("clear", 20, None, "Despejado"),
    ("cloudy", 22, 12, "Brisa suave"), ("cloudy", 22, 2, "Nublado"),
    ("partly_cloudy", 22, None, "Sol con nubes"), ("rain", 20, None, "Lluvia"),
])
def test_band_labels(cond, temp, wind, label):
    assert band_label(cond, temp, wind) == label


def test_summary_text_variants():
    bands_fog = [{"condition_code": "fog"}]
    assert summary_text("clear", bands_fog, 30) == "Soleado con niebla matinal cálida"
    assert summary_text("clear", [{"condition_code": "clear"}], 26) == "Soleado y templado"
    assert summary_text("rain", [{"condition_code": "rain"}], 19) == "Lluvioso y fresco"
    assert summary_text("fog", bands_fog, 24) == "Con neblina y templado"


# -------------------------------------------------------------- puntuación
FORECAST = {"temperature": 25.0, "humidity": 75, "rain_probability": 70, "precipitation": 0.5,
            "wind_speed": 6.0, "condition_code": "rain"}
OBSERVED = {"temperature": 24.2, "humidity": 78, "precipitation": 0.6, "wind_speed": 5.0,
            "condition_code": "rain"}


def test_spec_example_variables_and_score():
    result = evaluate(FORECAST, OBSERVED, DEFAULT_PARAMS)
    v = result.variables
    assert v["temperature"] == {"predicted": 25.0, "observed": 24.2, "error": 0.8, "score": 0.84}
    assert v["humidity"]["error"] == 3 and v["humidity"]["predicted"] == 75
    assert v["rain"]["predicted"] is True and v["rain"]["observed"] is True and v["rain"]["correct"] is True
    assert result.accuracy_score == 91.4
    assert result.accuracy_label == "Muy buena precisión"


def test_perfect_forecast_scores_100_and_terrible_one_scores_low():
    perfect = {"temperature": 20, "humidity": 70, "rain_probability": 0, "precipitation": 0,
               "wind_speed": 5, "condition_code": "clear"}
    same = {"temperature": 20, "humidity": 70, "precipitation": 0, "wind_speed": 5, "condition_code": "clear"}
    assert evaluate(perfect, same, DEFAULT_PARAMS).accuracy_score == 100.0

    bad_forecast = {"temperature": 35, "humidity": 10, "rain_probability": 100, "precipitation": 20,
                    "wind_speed": 50, "condition_code": "thunderstorm"}
    bad = evaluate(bad_forecast, same, DEFAULT_PARAMS)
    assert bad.accuracy_score < 10 and bad.accuracy_label == "Precisión baja"


def test_rain_variable_uses_threshold_and_brier_score():
    dry_obs = dict(OBSERVED, precipitation=0.0)
    r = evaluate(dict(FORECAST, rain_probability=40), dry_obs, DEFAULT_PARAMS).variables["rain"]
    assert r == {"predicted": False, "observed": False, "correct": True, "probability": 40, "score": 0.84}
    wrong = evaluate(dict(FORECAST, rain_probability=90), dry_obs, DEFAULT_PARAMS).variables["rain"]
    assert wrong["correct"] is False and wrong["score"] == pytest.approx(0.19)


def test_partial_data_renormalizes_weights():
    result = evaluate({"temperature": 25}, {"temperature": 20}, DEFAULT_PARAMS)
    assert result.accuracy_score == 0.0 and list(result.variables) == ["temperature"]
    half = evaluate({"temperature": 25}, {"temperature": 22.5}, DEFAULT_PARAMS)
    assert half.accuracy_score == 50.0


def test_not_evaluable_without_common_variables():
    with pytest.raises(NotEvaluable):
        evaluate({"temperature": 25}, {"humidity": 70}, DEFAULT_PARAMS)


def test_zero_weight_variables_are_ignored():
    params = copy.deepcopy(DEFAULT_PARAMS)
    params["weights"] = {"temperature": 1.0, "rain": 0, "humidity": 0, "condition": 0, "precipitation": 0, "wind": 0}
    assert evaluate(FORECAST, OBSERVED, params).accuracy_score == 84.0


@pytest.mark.parametrize("score,label", [(100, "Muy buena precisión"), (90, "Muy buena precisión"),
                                         (89.9, "Buena precisión"), (75, "Buena precisión"),
                                         (60, "Precisión aceptable"), (59.9, "Precisión baja"), (0, "Precisión baja")])
def test_labels(score, label):
    assert label_for(score, DEFAULT_PARAMS["labels"]) == label


def test_validate_params_accepts_defaults_and_rejects_bad_input():
    assert validate_params(copy.deepcopy(DEFAULT_PARAMS))["labels"][0]["min"] == 90
    bad = [
        {**DEFAULT_PARAMS, "weights": {}},
        {**DEFAULT_PARAMS, "weights": {"viento": 1}},
        {**DEFAULT_PARAMS, "weights": {"temperature": -1}},
        {**DEFAULT_PARAMS, "weights": {"temperature": 0}},
        {**DEFAULT_PARAMS, "tolerances": {"temperature": 0, "humidity": 1, "wind": 1, "precipitation": 1}},
        {**DEFAULT_PARAMS, "rain_prob_threshold_pct": 150},
        {**DEFAULT_PARAMS, "labels": [{"min": 10, "label": "x"}]},
        {**DEFAULT_PARAMS, "labels": []},
    ]
    for payload in bad:
        with pytest.raises(ValueError):
            validate_params(payload)
