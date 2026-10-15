"""Modelos de predicción de visitas: funciones puras, sin base de datos."""

from datetime import date, timedelta

import pytest

from app import visit_model as vm


def seasonal_series(n, growth=10.0, base=1000.0):
    pattern = [0.8, 0.75, 0.85, 1.1, 0.95, 0.95, 1.3, 1.25, 1.0, 1.0, 0.9, 1.15]
    return [(base + growth * t) * pattern[t % 12] for t in range(n)]


def test_regional_index_is_real_bcrp_data_normalised_to_mean_one():
    index = vm.regional_seasonal_index()
    assert len(index) == 12 and sum(index) / 12 == pytest.approx(1, abs=0.005)
    assert index.index(max(index)) == 7                       # agosto: el mes más alto de Huánuco 2015-2019
    assert vm.regional()["series_code"] == "RD13362DM" and len(vm.regional()["monthly"]) == 60


def test_easter_and_peruvian_holidays():
    assert vm.easter(2026) == date(2026, 4, 5) and vm.easter(2025) == date(2025, 4, 20)
    h = vm.holidays(2026)
    assert date(2026, 4, 2) in h and date(2026, 4, 3) in h            # Jueves y Viernes Santo
    assert date(2026, 7, 28) in h and date(2026, 12, 25) in h and date(2026, 3, 3) not in h


def test_holt_winters_recovers_a_noiseless_seasonal_trend():
    y = seasonal_series(48)
    result = vm.holt_winters(y[:36], 12)
    assert result["method"] == "holt-winters" and len(result["points"]) == 12
    assert vm.mape(y[36:], [p["expected"] for p in result["points"]]) < 8
    assert all(p["low"] <= p["expected"] <= p["high"] for p in result["points"])
    widths = [p["high"] - p["low"] for p in result["points"]]
    assert widths[-1] > widths[0]                                    # la incertidumbre crece con el horizonte


def test_holt_winters_needs_two_years_and_the_fallback_needs_three_months():
    assert vm.holt_winters(seasonal_series(23), 6) is None
    assert vm.regional_fallback([100, 120], 1, 3) is None
    fb = vm.forecast_monthly(seasonal_series(8), 1, 6)
    assert fb["method"] == "regional" and len(fb["points"]) == 6 and all(p["expected"] > 0 for p in fb["points"])


def test_backtest_monthly_reports_a_small_error_on_clean_data():
    assert vm.backtest_monthly(seasonal_series(42), 1, 6) < 10
    assert vm.backtest_monthly(seasonal_series(5), 1, 6) is None


def history(days=120, end=date(2026, 10, 11)):
    dow = [0.7, 0.7, 0.7, 0.8, 1.1, 1.6, 1.5]
    out = {}
    for i in range(days):
        d = end - timedelta(days=days - 1 - i)
        out[d] = 100 * dow[d.weekday()] * (1.4 if vm.is_holiday(d) else 1.0)
    return out


def test_daily_forecast_follows_the_weekly_profile():
    first = date(2026, 10, 12)                                         # lunes
    result = vm.forecast_daily(history(), first, 14, month_index=[1.0] * 12)
    by_day = {p["date"]: p["expected"] for p in result["points"]}
    saturday, tuesday = date(2026, 10, 17), date(2026, 10, 13)
    assert by_day[saturday] > 1.8 * by_day[tuesday]
    assert result["points"][0]["date"] == first and len(result["points"]) == 14


def test_daily_forecast_marks_holidays_and_applies_the_rain_assumption():
    first = date(2026, 11, 1)                                          # domingo y feriado
    dry = vm.forecast_daily(history(end=date(2026, 10, 31)), first, 3, month_index=[1.0] * 12)
    wet = vm.forecast_daily(history(end=date(2026, 10, 31)), first, 3, rain={first: 100.0}, month_index=[1.0] * 12)
    assert dry["points"][0]["holiday"] is True and dry["points"][1]["holiday"] is False
    assert wet["points"][0]["expected"] == pytest.approx(dry["points"][0]["expected"] * (1 - vm.RAIN_EFFECT))
    assert wet["points"][1]["expected"] == pytest.approx(dry["points"][1]["expected"])


def test_daily_forecast_needs_four_weeks_and_backtest_is_small_on_clean_data():
    assert vm.forecast_daily(history(20), date(2026, 10, 12), 7) is None
    assert vm.backtest_daily(history(120), date(2026, 10, 12)) < 12


def test_helpers():
    assert vm.fill_gaps([1, None, 3, None]) == [1.0, 2.0, 3.0, 3.0]
    assert vm.fill_gaps([None, None]) == [0.0, 0.0]
    assert vm.month_add(2026, 11, 3) == (2027, 2) and vm.month_add(2026, 1, -1) == (2025, 12)
    assert vm.mape([100, 0], [90, 5]) == 10.0 and vm.mape([0], [1]) is None
