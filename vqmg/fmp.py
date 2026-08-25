"""
vqmg.fmp — FMP data layer.

The engine's metric definitions read a specific set of field names. FMP retired
the /api/v3/ and /api/v4/ routes and renamed a handful of fields on the /stable/
surface, so this module fetches from /stable/ and then renames those fields back
to what the engine expects. That keeps `engine.compute_metrics` untouched.

Renames applied (stable -> engine):
    profile.lastDividend            -> lastDiv
    income.epsDiluted               -> epsdiluted
    cashflow.netDividendsPaid       -> dividendsPaid
    estimates.revenueAvg            -> estimatedRevenueAvg
    estimates.epsAvg                -> estimatedEpsAvg
    earnings.epsActual              -> actualEarningResult
    earnings.epsEstimated           -> estimatedEarning
    historical-price-eod/light      -> {"historical": [{"date", "close"}, ...]}

No synthetic, mocked or placeholder values are ever produced here. An endpoint
either returns real data or the corresponding blob key is None and the metrics
that depend on it come back as NaN.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import time
from pathlib import Path

try:
    import requests
except ImportError:  # pragma: no cover
    requests = None

STABLE_BASE = "https://financialmodelingprep.com/stable"

# Calendar-day lookbacks. 1900d ~ 1300 trading days (the 5y window the price
# metrics need); 470d ~ 320 trading days for the benchmark series.
PRICE_LOOKBACK_DAYS = 1900
BENCH_LOOKBACK_DAYS = 470
BENCHMARK = "SPY"

CACHE_TTL_HOURS = 24
DEFAULT_CACHE_DIR = Path(os.environ.get("VQMG_CACHE_DIR", ".vqmg_cache"))

TIMEOUT = 30
MAX_RETRIES = 3


class FMPError(RuntimeError):
    """Raised for auth failures and other non-recoverable API conditions."""


def resolve_api_key(api_key: str | None = None) -> str:
    key = api_key or os.environ.get("FMP_API_KEY") or os.environ.get("VQMG_FMP_API_KEY")
    if not key:
        raise FMPError(
            "No FMP API key. Pass api_key=... or set the FMP_API_KEY environment variable."
        )
    return key


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------

def _get(path: str, params: dict, api_key: str):
    if requests is None:  # pragma: no cover
        raise FMPError("The 'requests' package is required: pip install requests")
    url = f"{STABLE_BASE}/{path}"
    q = dict(params)
    q["apikey"] = api_key
    last = None
    for attempt in range(MAX_RETRIES):
        try:
            r = requests.get(url, params=q, timeout=TIMEOUT)
        except Exception as e:                      # network blip
            last = e
            time.sleep(1.5 * (attempt + 1))
            continue
        if r.status_code in (401, 403):
            raise FMPError(
                f"FMP rejected the request for /{path} (HTTP {r.status_code}). "
                "Check that the API key is valid and that the plan covers this endpoint."
            )
        if r.status_code == 429:                    # rate limited
            time.sleep(2.0 * (attempt + 1))
            last = RuntimeError("429 rate limited")
            continue
        if r.status_code >= 500:
            time.sleep(1.5 * (attempt + 1))
            last = RuntimeError(f"HTTP {r.status_code}")
            continue
        r.raise_for_status()
        data = r.json()
        if isinstance(data, dict) and "Error Message" in data:
            raise FMPError(f"/{path}: {data['Error Message']}")
        return data
    raise FMPError(f"/{path} failed after {MAX_RETRIES} attempts: {last}")


def _rows(data) -> list:
    return data if isinstance(data, list) else []


# ---------------------------------------------------------------------------
# Field adapters: stable payload -> the shape the engine reads
# ---------------------------------------------------------------------------

def _adapt_profile(data):
    rows = _rows(data)
    for r in rows:
        r.setdefault("lastDiv", r.get("lastDividend"))
    return rows


def _adapt_income(data):
    rows = _rows(data)
    for r in rows:
        r.setdefault("epsdiluted", r.get("epsDiluted"))
    return rows


def _adapt_cashflow(data):
    rows = _rows(data)
    for r in rows:
        if r.get("dividendsPaid") is None:
            r["dividendsPaid"] = r.get("netDividendsPaid", r.get("commonDividendsPaid"))
    return rows


def _adapt_estimates(data):
    rows = _rows(data)
    for r in rows:
        r.setdefault("estimatedRevenueAvg", r.get("revenueAvg"))
        r.setdefault("estimatedEpsAvg", r.get("epsAvg"))
    return rows


def _adapt_prices(data):
    """light EOD rows -> {"historical": [...]} newest-first, with a 'close' key."""
    rows = [r for r in _rows(data) if r.get("date") and r.get("price") is not None]
    rows.sort(key=lambda r: r["date"], reverse=True)
    return {"historical": [{"date": r["date"], "close": float(r["price"])} for r in rows]}


def _adapt_surprises(data):
    """stable /earnings includes not-yet-reported quarters; the engine's surprise
    metric reads the most recent REPORTED one, so unreported rows are dropped."""
    rows = [
        r for r in _rows(data)
        if r.get("epsActual") is not None and r.get("epsEstimated") is not None
    ]
    rows.sort(key=lambda r: r.get("date", ""), reverse=True)
    for r in rows:
        r.setdefault("actualEarningResult", r["epsActual"])
        r.setdefault("estimatedEarning", r["epsEstimated"])
    return rows


def _adapt_passthrough(data):
    return _rows(data)


# blob key -> (stable path, params, adapter)
def _requests_for(ticker: str):
    today = dt.date.today()
    price_from = (today - dt.timedelta(days=PRICE_LOOKBACK_DAYS)).isoformat()
    return {
        "profile":     ("profile", {"symbol": ticker}, _adapt_profile),
        "quote":       ("quote", {"symbol": ticker}, _adapt_passthrough),
        "income_a":    ("income-statement", {"symbol": ticker, "period": "annual", "limit": 6}, _adapt_income),
        "income_q":    ("income-statement", {"symbol": ticker, "period": "quarter", "limit": 12}, _adapt_income),
        "balance_a":   ("balance-sheet-statement", {"symbol": ticker, "period": "annual", "limit": 6}, _adapt_passthrough),
        "cashflow_a":  ("cash-flow-statement", {"symbol": ticker, "period": "annual", "limit": 6}, _adapt_cashflow),
        "cashflow_q":  ("cash-flow-statement", {"symbol": ticker, "period": "quarter", "limit": 8}, _adapt_cashflow),
        "estimates":   ("analyst-estimates", {"symbol": ticker, "period": "annual", "limit": 10}, _adapt_estimates),
        "prices":      ("historical-price-eod/light", {"symbol": ticker, "from": price_from}, _adapt_prices),
        "surprises":   ("earnings", {"symbol": ticker, "limit": 24}, _adapt_surprises),
        "targets":     ("price-target-news", {"symbol": ticker, "page": 0, "limit": 200}, _adapt_passthrough),
        "target_cons": ("price-target-consensus", {"symbol": ticker}, _adapt_passthrough),
    }


# Without these there is no company to score; a ticker missing all of them is
# reported as an error rather than emitted as a row of NaNs.
_ESSENTIAL = ("profile", "income_a", "income_q")


# ---------------------------------------------------------------------------
# Cache
# ---------------------------------------------------------------------------

def _cache_path(cache_dir: Path, name: str) -> Path:
    cache_dir.mkdir(parents=True, exist_ok=True)
    return cache_dir / f"{name}.json"


def _read_cache(path: Path, refresh: bool):
    if refresh or not path.exists():
        return None
    if (time.time() - path.stat().st_mtime) / 3600 > CACHE_TTL_HOURS:
        return None
    try:
        return json.loads(path.read_text())
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Public fetchers
# ---------------------------------------------------------------------------

def fetch_ticker(ticker: str, api_key: str, refresh: bool = False,
                 cache_dir: Path | str = DEFAULT_CACHE_DIR, pause: float = 0.0) -> dict:
    """Every FMP payload one company needs, keyed and shaped for the engine.

    Returns the blob. `blob["_errors"]` lists any endpoint that did not return
    data. Raises FMPError only for auth failures or when nothing usable came back.
    """
    ticker = ticker.strip().upper()
    cache_dir = Path(cache_dir)
    cpath = _cache_path(cache_dir, ticker)
    cached = _read_cache(cpath, refresh)
    if cached is not None:
        return cached

    blob: dict = {"_symbol": ticker, "_fetched": dt.datetime.now().isoformat(timespec="seconds")}
    errors: dict[str, str] = {}
    for key, (path, params, adapt) in _requests_for(ticker).items():
        try:
            blob[key] = adapt(_get(path, params, api_key))
        except FMPError:
            raise
        except Exception as e:
            blob[key] = None
            errors[key] = str(e)
        if pause:
            time.sleep(pause)
    blob["_errors"] = errors

    if all(not blob.get(k) for k in _ESSENTIAL):
        raise FMPError(
            f"{ticker}: no company data returned (unknown symbol, or not covered by this plan). "
            f"endpoint errors: {errors or 'none — empty responses'}"
        )

    try:
        cpath.write_text(json.dumps(blob))
    except Exception:
        pass
    return blob


def fetch_benchmark(api_key: str, refresh: bool = False,
                    cache_dir: Path | str = DEFAULT_CACHE_DIR):
    """SPY price history, fetched once per run. Used by the relative-strength and
    down-market-resilience metrics in the Momentum model. Returns None if
    unavailable — those two metrics then come back NaN."""
    cache_dir = Path(cache_dir)
    cpath = _cache_path(cache_dir, f"_BENCH_{BENCHMARK}")
    cached = _read_cache(cpath, refresh)
    if cached is not None:
        return cached
    frm = (dt.date.today() - dt.timedelta(days=BENCH_LOOKBACK_DAYS)).isoformat()
    try:
        data = _adapt_prices(
            _get("historical-price-eod/light", {"symbol": BENCHMARK, "from": frm}, api_key)
        )
    except FMPError:
        raise
    except Exception:
        return None
    try:
        cpath.write_text(json.dumps(data))
    except Exception:
        pass
    return data
