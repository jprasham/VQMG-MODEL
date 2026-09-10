"""
Wiring / contract tests for the VQMG package.

The blobs built here are SYNTHETIC FIXTURES used only to prove that the engine
is correctly wired to the data layer's field names and that the output schema is
stable. They are never used at runtime: `vqmg.run()` and `vqmg.metrics()` return
real FMP data or an explicit error, never a fallback value.

    pip install -e ".[dev]" && python -m pytest -q
"""
from __future__ import annotations

import datetime as dt
import math

import numpy as np
import pandas as pd
import pytest

import vqmg
from vqmg import engine


# ---------------------------------------------------------------------------
# Synthetic FMP payloads, in the exact shape vqmg.fmp hands to the engine
# ---------------------------------------------------------------------------

def _blob(sym: str, seed: int) -> dict:
    rng = np.random.default_rng(seed)
    g = 0.08 + 0.04 * rng.random()          # annual growth
    margin = 0.35 + 0.25 * rng.random()

    def income(rev, period, date):
        gp = rev * margin
        return {
            "date": date, "period": period, "revenue": rev, "costOfRevenue": rev - gp,
            "grossProfit": gp, "researchAndDevelopmentExpenses": rev * 0.11,
            "sellingGeneralAndAdministrativeExpenses": rev * 0.13,
            "operatingIncome": gp - rev * 0.24, "netIncome": rev * 0.16,
            "eps": rev * 0.16 / 1e9, "epsDiluted": rev * 0.16 / 1e9,
            "epsdiluted": rev * 0.16 / 1e9,
            "weightedAverageShsOut": 1e9, "weightedAverageShsOutDil": 1e9 * (1 + 0.004 * rng.random()),
        }

    base_rev = 4.0e10
    inc_a, bal_a, cf_a = [], [], []
    for i in range(6):                       # newest-first, as FMP returns
        rev = base_rev / ((1 + g) ** i)
        date = f"{2025 - i}-12-31"
        inc_a.append(income(rev, "FY", date))
        assets = rev * 1.9
        bal_a.append({
            "date": date, "totalAssets": assets, "totalDebt": assets * 0.18,
            "totalStockholdersEquity": assets * 0.46,
            "cashAndShortTermInvestments": assets * 0.21,
            "accountPayables": rev * 0.09, "netReceivables": rev * 0.15,
        })
        ocf = rev * 0.30
        capex = -rev * 0.07
        cf_a.append({
            "date": date, "netIncome": rev * 0.16, "operatingCashFlow": ocf,
            "capitalExpenditure": capex, "freeCashFlow": ocf + capex,
            "stockBasedCompensation": rev * 0.05, "changeInWorkingCapital": rev * 0.012,
            "netDividendsPaid": -rev * 0.02, "dividendsPaid": -rev * 0.02,
            "commonStockRepurchased": -rev * 0.05,
        })

    inc_q, cf_q = [], []
    for i in range(12):
        rev = (base_rev / 4) / ((1 + g / 4) ** i)
        inc_q.append(income(rev, f"Q{4 - i % 4}", f"{2025 - i // 4}-{12 - 3 * (i % 4):02d}-28"))
    for i in range(8):
        rev = (base_rev / 4) / ((1 + g / 4) ** i)
        cf_q.append({
            "operatingCashFlow": rev * 0.30, "capitalExpenditure": -rev * 0.07,
            "stockBasedCompensation": rev * 0.05, "changeInWorkingCapital": rev * 0.012,
            "dividendsPaid": -rev * 0.02, "commonStockRepurchased": -rev * 0.05,
        })

    today = dt.date.today()
    prices = []
    px = 100.0
    for i in range(1300):                     # newest-first, 'close' key
        d = today - dt.timedelta(days=i)
        px_i = px * (1 + 0.0006) ** (1300 - i) * (1 + 0.01 * rng.standard_normal())
        prices.append({"date": d.isoformat(), "close": abs(px_i)})

    fy_end = (today + dt.timedelta(days=200)).isoformat()
    return {
        "profile": [{"companyName": f"{sym} Inc.", "sector": "Technology",
                     "industry": "Software - Infrastructure", "country": "US",
                     "lastDividend": 1.0, "lastDiv": 1.0}],
        "quote": [{"symbol": sym, "price": prices[0]["close"],
                   "marketCap": prices[0]["close"] * 1e9}],
        "income_a": inc_a, "income_q": inc_q, "balance_a": bal_a,
        "cashflow_a": cf_a, "cashflow_q": cf_q,
        "estimates": [
            {"date": fy_end, "revenueAvg": base_rev * 1.12,
             "estimatedRevenueAvg": base_rev * 1.12,
             "epsAvg": 7.4, "estimatedEpsAvg": 7.4},
            {"date": (dt.date.fromisoformat(fy_end) + dt.timedelta(days=365)).isoformat(),
             "revenueAvg": base_rev * 1.25, "estimatedRevenueAvg": base_rev * 1.25,
             "epsAvg": 8.6, "estimatedEpsAvg": 8.6},
        ],
        "prices": {"historical": prices},
        "surprises": [{"date": "2025-10-30", "actualEarningResult": 1.24,
                       "estimatedEarning": 1.15}],
        "targets": [{"publishedDate": (today - dt.timedelta(days=d)).isoformat(),
                     "priceTarget": 120 + d * 0.05, "adjPriceTarget": 120 + d * 0.05}
                    for d in (10, 30, 60, 120, 180, 240)],
        "target_cons": [{"targetConsensus": 130.0}],
        "_errors": {},
    }


@pytest.fixture(scope="module")
def frame():
    """Mirrors run() with no guidance file supplied."""
    rows = [engine.compute_metrics(f"T{i:02d}", _blob(f"T{i:02d}", i), bench=None)
            for i in range(30)]
    df = pd.DataFrame(rows)
    df["guid_net_dir"] = np.nan          # as run() does when guidance_file is None
    return engine.rank_universe(df)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_every_declared_column_is_produced(frame):
    """vqmg.columns() must not promise anything the engine does not emit."""
    missing = [c for c in vqmg.columns() if c not in frame.columns]
    assert not missing, f"declared but never produced: {missing}"


def test_all_four_models_produce_scores_and_quintiles(frame):
    for sup, label in vqmg.SUPER_LABELS.items():
        assert frame[f"score_{sup}"].notna().any(), f"{label} produced no scores"
        assert frame[f"q_{sup}"].dropna().between(1, 5).all(), f"{label} quintiles out of range"
    for sub in vqmg.SUBS:
        assert frame[f"score_{sub}"].notna().any(), f"sub-factor {sub} produced no scores"


def test_headline_metrics_are_populated(frame):
    """One representative live variable from each of the four models."""
    for col in ("rev_cagr_3y", "rev_yoy_q0", "roic", "fcf_yield", "ebit_to_ev",
                "book_to_price", "sales_to_ev", "normalized_ep", "fwd_earn_yield"):
        assert frame[col].notna().sum() >= len(frame) * 0.8, f"{col} mostly empty"


def test_schema_is_exactly_identity_ranked_scores():
    """run() returns identity + all 41 ranked factors + scores, and nothing else."""
    ranked = [c for g, cols in vqmg.COLUMN_GROUPS.items() if g != "identity" for c in cols]
    assert vqmg.columns() == vqmg.IDENTITY + ranked + vqmg.SCORE_COLUMNS
    assert len(vqmg.columns()) == 9 + 38 + 21 == 68


def test_business_momentum_is_five_factors_no_guidance():
    """Guidance Net Direction is not part of this model."""
    assert vqmg.COLUMN_GROUPS["momentum_business"] == [
        "rev_yoy_q0", "rev_accel_q", "gm_change_yoy", "eps_surprise", "target_chg_3m"]
    assert not any("guid" in c for c in vqmg.columns())


def test_every_ranked_factor_in_spec_is_in_the_schema():
    """The schema is derived from FACTOR_SPEC, so it cannot drift from it."""
    spec = set(engine.FACTOR_SPEC)
    assert spec == set(vqmg.columns()) - set(vqmg.IDENTITY) - set(vqmg.SCORE_COLUMNS)


def test_unranked_diagnostics_are_dropped_by_default_and_kept_with_full():
    rows = [engine.compute_metrics(f"T{i:02d}", _blob(f"T{i:02d}", i), bench=None)
            for i in range(30)]
    df = engine.rank_universe(pd.DataFrame(rows))
    lean = vqmg._order(df)
    wide = vqmg._order(df, full=True)
    for col in ("roic_tc", "cash_engine_yield", "downside_rating", "normalized_pe",
                "vol_1y", "expected_return", "rerating_gap", "ev_to_gp", "max_downside",
                "peak_margin_risk", "ocf_yield", "fwd_basis", "ntm_coverage"):
        assert col not in lean.columns
        assert col in wide.columns


def test_fin_mode_keys_on_sector():
    fin = _blob("BANK", 1)
    fin["profile"][0]["sector"] = "Financial Services"
    fin["profile"][0]["industry"] = "Financial - Credit Services"   # not a bank
    m = engine.compute_metrics("BANK", fin, bench=None)
    assert m["fin_mode"] == 1 and m["cap_bar"] == 0.12
    assert math.isnan(m["gross_margin"]), "financials mode must blank gross margin"

    tech = _blob("TECH", 2)
    tech["profile"][0]["sector"] = "Technology"
    tech["profile"][0]["industry"] = "Investment Banking Software"  # 'bank' in the text
    m2 = engine.compute_metrics("TECH", tech, bench=None)
    assert m2["fin_mode"] == 0 and m2["cap_bar"] == 0.15


def test_no_default_weights_anywhere():
    """The composite must be opt-in: no super-factor weights ship with VQMG."""
    assert not hasattr(engine, "WEIGHTS")
    src = (__import__("pathlib").Path(engine.__file__)).read_text()
    assert "WEIGHTS" not in src


def test_composite_absent_unless_weights_given(frame):
    assert "composite" not in frame.columns
    assert "core_rank" not in frame.columns


def test_composite_appears_when_weights_given():
    rows = [engine.compute_metrics(f"T{i:02d}", _blob(f"T{i:02d}", i), bench=None)
            for i in range(30)]
    w = {"GRW": 0.30, "MOM": 0.30, "QLT": 0.30, "VAL": 0.10}
    out = engine.rank_universe(pd.DataFrame(rows), weights=w)
    assert out["core_rank"].dropna().between(1, 5).all()
    assert out["composite"].notna().any()


def test_unknown_weight_key_is_rejected():
    rows = [engine.compute_metrics("T00", _blob("T00", 0), bench=None)]
    with pytest.raises(ValueError):
        engine.rank_universe(pd.DataFrame(rows), weights={"GROWTH": 1.0})


def test_ranked_counts_per_sub_factor(frame):
    """G 7 | B 5 | M 5 | R 10 | Q 5 | V 8 = 40."""
    assert {k: len(v) for k, v in vqmg.RANKED.items()} == {
        "G": 7, "B": 5, "M": 5, "R": 10, "Q": 5, "V": 6}


def test_bounds_are_respected(frame):
    for col, (lo, hi) in engine.BOUNDS.items():
        if col in frame.columns:
            s = pd.to_numeric(frame[col], errors="coerce").dropna()
            if len(s):
                assert s.min() >= lo - 1e-9 and s.max() <= hi + 1e-9, f"{col} out of bounds"


def test_coverage_counts_the_four_super_factors(frame):
    assert frame["coverage"].between(0, 4).all()


def test_no_name_string_leaks():
    import pathlib
    for f in pathlib.Path(vqmg.__file__).parent.glob("*.py"):
        text = f.read_text().lower()
        assert "flux" not in text, f"stale name in {f.name}"
        assert "guid_" not in text, f"stale guidance wiring in {f.name}"


def test_metrics_accepts_a_prefetched_blob():
    m = vqmg.metrics("T00", blob=_blob("T00", 0))
    assert m["symbol"] == "T00"
    assert math.isfinite(m["rev_cagr_3y"])


# ---------------------------------------------------------------------------
# Value model — the six ranked variables and the NTM interpolation
# ---------------------------------------------------------------------------

def _est_blob(sym, fy_end_offset_days, eps_pair=(10.0, 12.0), rev_pair=(1e10, 1.2e10)):
    """A blob whose only interesting feature is where its fiscal year ends."""
    b = _blob(sym, 7)
    today = dt.date.today()
    d0 = today + dt.timedelta(days=fy_end_offset_days)
    d1 = d0 + dt.timedelta(days=365)
    b["estimates"] = [
        {"date": d0.isoformat(), "estimatedEpsAvg": eps_pair[0], "estimatedRevenueAvg": rev_pair[0]},
        {"date": d1.isoformat(), "estimatedEpsAvg": eps_pair[1], "estimatedRevenueAvg": rev_pair[1]},
    ]
    return b


def test_value_is_six_equal_weighted_variables():
    assert vqmg.RANKED["V"] == ["ebit_to_ev", "fwd_earn_yield", "fcf_yield",
                                "book_to_price", "sales_to_ev", "normalized_ep"]
    for v in vqmg.RANKED["V"]:
        assert engine.FACTOR_SPEC[v] == ("V", +1), f"{v} must be higher-is-cheaper"


def test_owners_return_pipeline_is_no_longer_ranked():
    for col in ("expected_return", "rerating_gap", "terminal_yield", "midcycle_yield",
                "peak_margin_risk", "ev_to_gp", "ev_gp_growth_adj", "max_downside"):
        assert col not in engine.FACTOR_SPEC, f"{col} should be context-only now"


def test_ntm_blend_weights_by_overlap_with_the_next_twelve_months():
    """FY ending in 17 days -> ~5% of it, ~95% of the following year."""
    m = engine.compute_metrics("X", _est_blob("X", 17), bench=None)
    assert m["fwd_basis"] == "ntm"
    expected = (17 / 365) * 10.0 + (348 / 365) * 12.0
    assert abs(m["fwd_eps_ntm"] - expected) < 0.02
    assert 11.8 < m["fwd_eps_ntm"] < 12.0, "should sit close to the far year"


def test_ntm_blend_for_a_december_year_end_is_between_the_two():
    days = (dt.date(dt.date.today().year, 12, 31) - dt.date.today()).days
    m = engine.compute_metrics("X", _est_blob("X", days), bench=None)
    lo, hi = 10.0, 12.0
    assert lo < m["fwd_eps_ntm"] < hi
    assert abs(m["ntm_coverage"] - 1.0) < 0.01


def test_a_just_ended_fiscal_year_no_longer_counts_as_forward():
    """FY that ended 41 days ago contributes nothing; the blend is the next FY."""
    m = engine.compute_metrics("X", _est_blob("X", -41), bench=None)
    assert m["fwd_basis"] == "ntm"
    assert abs(m["fwd_eps_ntm"] - 12.0) < 1e-6, "must not carry the closed year"


def test_thin_coverage_falls_back_and_records_it():
    b = _blob("X", 7)
    today = dt.date.today()
    # a single row for a year ending 700 days out overlaps the next 12 months
    # by only ~30 days, so the blend cannot be trusted
    b["estimates"] = [{"date": (today + dt.timedelta(days=700)).isoformat(),
                       "estimatedEpsAvg": 9.0, "estimatedRevenueAvg": 5e10}]
    m = engine.compute_metrics("X", b, bench=None)
    assert m["ntm_coverage"] < engine.NTM_MIN_COVERAGE
    assert m["fwd_basis"] == "fy"


def test_forecast_loss_blanks_the_forward_yield_rather_than_modelling_it():
    b = _est_blob("LOSS", 200, eps_pair=(-2.0, -1.5))
    m = engine.compute_metrics("LOSS", b, bench=None)
    assert m["fwd_earn_yield_calc"] == 2
    assert math.isnan(m["fwd_earn_yield"]), "a forecast loss must not be back-filled"


def test_consensus_path_is_flagged_zero():
    m = engine.compute_metrics("X", _est_blob("X", 200), bench=None)
    assert m["fwd_earn_yield_calc"] == 0
    assert m["fwd_earn_yield"] > 0


def test_modelled_fallback_fires_only_when_there_is_no_consensus():
    b = _blob("X", 7)
    b["estimates"] = []
    m = engine.compute_metrics("X", b, bench=None)
    assert m["fwd_earn_yield_calc"] == 1


def test_financials_blank_the_two_variables_that_have_no_meaning():
    b = _blob("BANK", 3)
    b["profile"][0]["sector"] = "Financial Services"
    m = engine.compute_metrics("BANK", b, bench=None)
    assert math.isnan(m["fcf_yield"]) and math.isnan(m["sales_to_ev"])
    assert math.isfinite(m["ebit_to_ev"]), "financials use net income / mktcap"
    assert math.isfinite(m["book_to_price"])


def test_context_pipeline_still_reads_the_wc_stripped_yields():
    """Splitting plain from stripped must not have changed the downside stack."""
    m = engine.compute_metrics("T00", _blob("T00", 0), bench=None)
    assert math.isfinite(m["fcf_yield_core"]) and math.isfinite(m["ocf_yield_core"])
    assert math.isfinite(m["downside_rating"])


def test_a_missing_eps_leg_does_not_deny_revenue_its_blend():
    """Coverage differs by field on FMP; the fallback must be per field."""
    b = _blob("X", 7)
    today = dt.date.today()
    d0 = today + dt.timedelta(days=60)
    d1 = d0 + dt.timedelta(days=365)
    b["estimates"] = [
        {"date": d0.isoformat(), "estimatedEpsAvg": None, "estimatedRevenueAvg": 1e10},
        {"date": d1.isoformat(), "estimatedEpsAvg": 12.0, "estimatedRevenueAvg": 1.2e10},
    ]
    m = engine.compute_metrics("X", b, bench=None)
    assert m["fwd_rev_basis"] == "ntm", "revenue blended fine and must say so"
    assert m["fwd_basis"] == "fy", "EPS could not blend and must fall back"
    assert math.isfinite(m["fwd_rev_ntm"])
    assert math.isfinite(m["fwd_eps_ntm"]), "EPS must still get the fiscal-year rule"


def test_a_zero_estimate_is_missing_data_not_a_break_even_forecast():
    """FMP writes 0 for 'no estimate'. Reading it literally made an uncovered
    name look like a forecast of exactly zero, which then tripped the
    forecast-loss test and blanked the variable for the wrong reason."""
    b = _blob("NOCOV", 5)
    today = dt.date.today()
    b["estimates"] = [
        {"date": (today + dt.timedelta(days=60)).isoformat(),
         "estimatedEpsAvg": 0, "estimatedRevenueAvg": 0},
        {"date": (today + dt.timedelta(days=425)).isoformat(),
         "estimatedEpsAvg": 0, "estimatedRevenueAvg": 0},
    ]
    m = engine.compute_metrics("NOCOV", b, bench=None)
    assert m["fwd_earn_yield_calc"] == 1, "no coverage must use the modelled fallback"
    assert m["fwd_earn_yield_calc"] != 2, "zero is not a forecast loss"
    assert not math.isfinite(m["fwd_eps_ntm"])


def test_a_genuine_negative_forecast_still_blanks():
    m = engine.compute_metrics("LOSS2", _est_blob("LOSS2", 60, eps_pair=(-3.0, -1.0)), bench=None)
    assert m["fwd_earn_yield_calc"] == 2
    assert math.isnan(m["fwd_earn_yield"])
