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

import numpy as np
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

# ---------------------------------------------------------------------------
# Output schema — what a developer gets back, grouped by model
# ---------------------------------------------------------------------------

IDENTITY = ["symbol", "company", "sector", "industry", "country", "price", "mktcap",
            "fin_mode", "cap_bar"]

# The ranked factors, taken straight from engine.FACTOR_SPEC so this list can
# never drift from what the model actually scores. Grouped by sub factor.
RANKED = {sub: [mtc for mtc, (f, _) in FACTOR_SPEC.items() if f == sub] for sub in SUBS}

# Sub factor -> the super factor model it rolls into.
SUB_TO_SUPER = {sub: sup for sup, blend in SUPERS.items() for sub in blend}

COLUMN_GROUPS = {
    "identity": IDENTITY,
    "growth": RANKED["G"],                  # -> GRW
    "momentum_business": RANKED["B"],       # -> MOM
    "momentum_market": RANKED["M"],         # -> MOM
    "quality_economics": RANKED["R"],       # -> QLT
    "quality_earnings": RANKED["Q"],        # -> QLT
    "value": RANKED["V"],                   # -> VAL
}

SCORE_COLUMNS = (
    [f"score_{s}" for s in SUBS]
    + [f"q_{s}" for s in SUBS]
    + [f"score_{s}" for s in SUPERS]
    + [f"q_{s}" for s in SUPERS]
    + ["coverage"]
)


def columns(include_scores: bool = True) -> list[str]:
    """The ordered output schema of `run()`: identity, the 41 ranked factors, scores."""
    cols: list[str] = []
    for group in COLUMN_GROUPS.values():
        cols += group
    if include_scores:
        cols += SCORE_COLUMNS
    return cols


def _order(df: pd.DataFrame, full: bool = False) -> pd.DataFrame:
    wanted = [c for c in columns() if c in df.columns]
    if full:
        rest = [c for c in df.columns if c not in wanted]
        return df[wanted + rest]
    return df[wanted]


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
        raise_on_error: bool = False, full: bool = False) -> pd.DataFrame:
    """Score a list of tickers. One row per ticker, columns as `vqmg.columns()`.

    tickers   list of symbols, or a comma/whitespace separated string.
    neutral   None | 'sector' | 'industry' | 'country'. Computes z-scores within
              groups so a bank is judged against banks. Groups smaller than
              engine.MIN_GROUP fall back to universe-wide scoring.
    weights   optional {'GRW':..,'MOM':..,'QLT':..,'VAL':..}. Supplying it adds
              `composite` and `core_rank`. Omit it and neither column appears.
    workers   parallel tickers. Each ticker is ~12 FMP calls.
    full      True also returns the diagnostic variables the model computes but
              does not rank (roic_tc, cash_engine_yield, downside_rating,
              normalized_pe, DSO/DPO days, vol_1y and the rest).

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
    df = _order(df, full=full)
    df.attrs["errors"] = errors
    df.attrs["requested"] = tickers
    df.attrs["neutral"] = neutral
    df.attrs["weights"] = weights
    return df


__all__ = [
    "run", "metrics", "columns", "COLUMN_GROUPS", "SCORE_COLUMNS",
    "RANKED", "SUB_TO_SUPER", "IDENTITY",
    "FACTOR_SPEC", "BOUNDS", "SUBS", "SUPERS", "SUPER_LABELS",
    "compute_metrics", "rank_universe", "engine", "fmp", "FMPError",
]
