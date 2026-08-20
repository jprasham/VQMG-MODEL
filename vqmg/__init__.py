"""
VQMG — Growth / Momentum / Quality / Value quant model, as a library.

    import vqmg

    df = vqmg.run(["NVDA", "MSFT", "ASML", "V"])       # ranked frame, one row per ticker
    m  = vqmg.metrics("NVDA")                          # one company, raw variables only

`run()` returns every raw variable and every calculated output of the four
models, plus the sub-factor scores, super-factor scores and quintiles.

Absolute vs relative — the one thing to know:
  ABSOLUTE  every raw metric, expected_return, max_downside, downside_rating,
            bond_growth. Valid for a single stock on its own.
  RELATIVE  every score_* and q_* column. These are cross-sectional: they
            describe a name's standing INSIDE the list you passed. Pass 25+
            tickers before reading a quintile as meaningful.

VQMG ships no default super-factor weights. `composite` and `core_rank` are
produced only if you pass your own, e.g. run(tickers, weights={...}).
"""
from __future__ import annotations

import concurrent.futures as _cf
from pathlib import Path

import pandas as pd

from . import engine, fmp
from .engine import (
    BOUNDS,
    FACTOR_SPEC,
    SUBS,
    SUPER_LABELS,
    SUPERS,
    compute_metrics,
    rank_universe,
)
from .fmp import FMPError

__version__ = "1.0.0"

# ---------------------------------------------------------------------------
# Output schema — what a developer gets back, grouped by model
# ---------------------------------------------------------------------------

IDENTITY = ["symbol", "company", "sector", "industry", "country", "price", "mktcap",
            "fin_mode", "cap_bar"]

GROWTH = ["rev_cagr_3y", "rev_yoy_ttm", "rev_accel", "gp_growth_ttm",
          "fwd_rev_growth", "fwd_eps_growth", "growth_persistence"]

MOMENTUM_BUSINESS = ["rev_yoy_q0", "rev_accel_q", "gm_change_yoy", "eps_surprise",
                     "target_chg_3m", "target_upside"]

MOMENTUM_MARKET = ["mom_12_1", "trend_smoothness", "dist_from_high", "rel_strength",
                   "down_resilience", "price_vs_200d", "move_1m", "move_1m_pctile",
                   "worst_month_5y", "dist_to_52w_low", "vol_1y"]

QUALITY_ECONOMICS = ["gross_margin", "gp_to_assets", "roic", "roic_5y", "roic_tc",
                     "incremental_roic", "roiic", "intrinsic_compound", "rule_of_40",
                     "opex_conversion", "reinvest_intensity", "sustainable_growth",
                     "balance_capacity", "fcf_margin", "above_trend_capex",
                     "cash_to_mktcap"]

QUALITY_EARNINGS = ["accruals", "bs_bloat", "dilution", "sbc_to_rev", "wc_flatter",
                    "dpo_change_days", "dso_change_days", "dpo_now_days", "dpo_norm_days",
                    "dso_now_days", "dso_norm_days", "pay_day_value", "rec_day_value"]

VALUE = ["ev", "ev_to_gp", "ev_gp_growth_adj", "fwd_pe", "normalized_pe",
         "ocf_yield", "fcf_yield", "ocf_yield_reported", "fcf_yield_reported",
         "wc_strip_applied", "cash_engine_yield", "midcycle_yield", "terminal_yield",
         "rerating_gap", "expected_return", "peak_margin_risk", "normalized_fcf_yield",
         "div_yield", "buyback_yield", "distributed_yield",
         "max_downside", "worst_strong_downside", "downside_rating",
         "bond_growth", "earnings_suspect"]

COLUMN_GROUPS = {
    "identity": IDENTITY,
    "growth": GROWTH,
    "momentum_business": MOMENTUM_BUSINESS,
    "momentum_market": MOMENTUM_MARKET,
    "quality_economics": QUALITY_ECONOMICS,
    "quality_earnings": QUALITY_EARNINGS,
    "value": VALUE,
}

SCORE_COLUMNS = (
    [f"score_{s}" for s in SUBS]
    + [f"q_{s}" for s in SUBS]
    + [f"score_{s}" for s in SUPERS]
    + [f"q_{s}" for s in SUPERS]
    + ["coverage"]
)


def columns(include_scores: bool = True) -> list[str]:
    """The full ordered output schema of `run()`."""
    cols: list[str] = []
    for group in COLUMN_GROUPS.values():
        cols += group
    if include_scores:
        cols += SCORE_COLUMNS
    return cols


def _order(df: pd.DataFrame) -> pd.DataFrame:
    wanted = [c for c in columns() if c in df.columns]
    rest = [c for c in df.columns if c not in wanted]   # never silently drop anything
    return df[wanted + rest]


# ---------------------------------------------------------------------------
# Public entry points
# ---------------------------------------------------------------------------

def metrics(ticker: str, api_key: str | None = None, *, refresh: bool = False,
            cache_dir: str | Path = fmp.DEFAULT_CACHE_DIR,
            blob: dict | None = None, bench=None) -> dict:
    """Every raw variable and absolute calculated output for ONE company.

    No scores or quintiles — those need a cross-section (see `run`). Pass
    `blob=` to compute from FMP payloads you already have, with no network call.
    """
    if blob is None:
        key = fmp.resolve_api_key(api_key)
        blob = fmp.fetch_ticker(ticker, key, refresh=refresh, cache_dir=cache_dir)
        if bench is None:
            bench = fmp.fetch_benchmark(key, refresh=refresh, cache_dir=cache_dir)
    return engine.compute_metrics(ticker.strip().upper(), blob, bench=bench)


def run(tickers, api_key: str | None = None, *, neutral: str | None = None,
        weights: dict | None = None, refresh: bool = False, workers: int = 4,
        cache_dir: str | Path = fmp.DEFAULT_CACHE_DIR,
        raise_on_error: bool = False) -> pd.DataFrame:
    """Score a list of tickers. One row per ticker, columns as `vqmg.columns()`.

    tickers   list of symbols, or a comma/whitespace separated string.
    neutral   None | 'sector' | 'industry' | 'country'. Computes z-scores within
              groups so a bank is judged against banks. Groups smaller than
              engine.MIN_GROUP fall back to universe-wide scoring.
    weights   optional {'GRW':..,'MOM':..,'QLT':..,'VAL':..}. Supplying it adds
              `composite` and `core_rank`. Omit it and neither column appears.
    workers   parallel tickers. Each ticker is ~12 FMP calls.

    Tickers that could not be fetched are listed in `df.attrs["errors"]` rather
    than emitted as empty rows. Set raise_on_error=True to fail loudly instead.
    """
    if isinstance(tickers, str):
        tickers = tickers.replace(",", " ").split()
    tickers = [t.strip().upper() for t in tickers if t and t.strip()]
    if not tickers:
        raise ValueError("No tickers given.")
    seen, uniq = set(), []
    for t in tickers:
        if t not in seen:
            seen.add(t)
            uniq.append(t)
    tickers = uniq

    key = fmp.resolve_api_key(api_key)
    bench = fmp.fetch_benchmark(key, refresh=refresh, cache_dir=cache_dir)

    def one(t):
        try:
            blob = fmp.fetch_ticker(t, key, refresh=refresh, cache_dir=cache_dir)
            return engine.compute_metrics(t, blob, bench=bench)
        except Exception as e:
            return {"symbol": t, "_error": str(e)}

    with _cf.ThreadPoolExecutor(max_workers=max(1, int(workers))) as ex:
        rows = list(ex.map(one, tickers))

    errors = [{"symbol": r["symbol"], "error": r["_error"]} for r in rows if "_error" in r]
    if errors and raise_on_error:
        raise FMPError("; ".join(f"{e['symbol']}: {e['error']}" for e in errors))

    good = [r for r in rows if "_error" not in r]
    if not good:
        raise FMPError("No ticker returned usable data. " +
                       "; ".join(f"{e['symbol']}: {e['error']}" for e in errors))

    df = pd.DataFrame(good)
    df = engine.rank_universe(df, neutral=neutral, weights=weights)
    df = _order(df)
    df.attrs["errors"] = errors
    df.attrs["requested"] = tickers
    df.attrs["neutral"] = neutral
    df.attrs["weights"] = weights
    return df


__all__ = [
    "run", "metrics", "columns", "COLUMN_GROUPS", "SCORE_COLUMNS",
    "FACTOR_SPEC", "BOUNDS", "SUBS", "SUPERS", "SUPER_LABELS",
    "compute_metrics", "rank_universe", "engine", "fmp", "FMPError",
    "__version__",
]
