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
        "estimates": [{"date": fy_end, "revenueAvg": base_rev * 1.12,
                       "estimatedRevenueAvg": base_rev * 1.12,
                       "epsAvg": 7.4, "estimatedEpsAvg": 7.4}],
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
    rows = [engine.compute_metrics(f"T{i:02d}", _blob(f"T{i:02d}", i), bench=None)
            for i in range(30)]
    return engine.rank_universe(pd.DataFrame(rows))


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
    for col in ("rev_cagr_3y", "rev_yoy_q0", "roic", "fcf_yield"):
        assert frame[col].notna().sum() >= len(frame) * 0.8, f"{col} mostly empty"


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
        assert "flux" not in f.read_text().lower(), f"stale name in {f.name}"


def test_metrics_accepts_a_prefetched_blob():
    m = vqmg.metrics("T00", blob=_blob("T00", 0))
    assert m["symbol"] == "T00"
    assert math.isfinite(m["rev_cagr_3y"])
