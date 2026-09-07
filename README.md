# VQMG

Growth / Momentum / Quality / Value quant model, packaged as a Python library.
Give it a list of tickers, get back every raw variable and every calculated
output of the four models.

The model logic is carried over unchanged: metric definitions, economic bounds,
robust z-scores, sub-factor blends and quintile rules are identical to the
source implementation. Only two things differ, both structural:

1. **No default super-factor weights.** You get the four super-factor scores and
   quintiles. `composite` and `core_rank` appear only if you pass your own
   `weights=`.
2. **Data layer.** FMP retired `/api/v3/` and `/api/v4/`, so `vqmg.fmp` fetches
   from `/stable/` and renames the handful of changed fields back to what the
   engine reads. The computation itself never touches the network.

---

## Install

Public repo, so no authentication and no access setup:

```powershell
pip install git+https://github.com/jprasham/VQMG-MODEL.git
```

Same command works in a `requirements.txt`:

```
vqmg @ git+https://github.com/jprasham/VQMG-MODEL.git
```

There are no releases or tags. That line always installs the current state of
`main`. To pick up changes:

```powershell
pip install --upgrade --force-reinstall git+https://github.com/jprasham/VQMG-MODEL.git
```

Because there is nothing to pin, a change pushed to `main` reaches every project
on its next install. Coordinate before editing `vqmg/engine.py`.

## API key

Each developer supplies their own FMP key. The library reads `FMP_API_KEY` from
the environment — nothing else, and it is never stored in this repo.

Locally:

```powershell
# current shell only
$env:FMP_API_KEY = "your_key"

# persist for this Windows user (reopen PowerShell afterwards)
setx FMP_API_KEY "your_key"
```

In your own project's GitHub Actions, store the key as a secret in **your**
repository and hand it to the step:

```yaml
- name: Run
  env:
    FMP_API_KEY: ${{ secrets.FMP_API_KEY }}
  run: python your_script.py
```

## Use

```python
import vqmg

df = vqmg.run(["NVDA", "MSFT", "ASML", "V"])
df.to_csv("vqmg.csv", index=False)
```

One company, raw variables only, no cross-section needed:

```python
m = vqmg.metrics("NVDA")
m["roic_tc"], m["expected_return"], m["max_downside"]
```

Command line:

```powershell
vqmg NVDA MSFT ASML V -o vqmg.csv
vqmg --tickers-file tickers.txt --neutral sector -o vqmg.csv
```

### `run()` options

| argument | meaning |
|---|---|
| `tickers` | list of symbols, or a comma/space separated string |
| `neutral` | `None` \| `'sector'` \| `'industry'` \| `'country'` — z-scores computed within groups, so a bank is judged against banks. Groups under 6 names fall back to universe-wide. |
| `weights` | opt-in composite, e.g. `{"GRW":.3,"MOM":.3,"QLT":.3,"VAL":.1}`. Adds `composite` and `core_rank`. |
| `refresh` | ignore the 24h cache and refetch |
| `workers` | parallel tickers (default 4); each ticker is ~12 FMP calls |
| `raise_on_error` | fail loudly instead of collecting failures in `df.attrs["errors"]` |

## Output

`run()` returns 70 columns: 9 identity, the 40 ranked factors, and 21 scores and
quintiles. Nothing that is not scored by the model.

| group | count | contents |
|---|---|---|
| `identity` | 9 | symbol, company, sector, industry, country, price, mktcap, fin_mode, cap_bar |
| `growth` -> GRW | 7 | 3Y revenue CAGR, TTM YoY, acceleration vs trend, gross-profit growth, forward revenue/EPS growth, growth persistence |
| `momentum_business` -> MOM | 5 | latest-quarter YoY, sequential acceleration, gross-margin change, EPS surprise, 3-month target change |
| `momentum_market` -> MOM | 5 | 12-1 momentum, trend smoothness, distance from 52-week high, relative strength vs SPY, down-market resilience |
| `quality_economics` -> QLT | 10 | GP/assets, gross margin, ROIC, incremental ROIC, ROIIC, Rule of 40, opex conversion, reinvestment intensity, sustainable growth, balance-sheet capacity |
| `quality_earnings` -> QLT | 5 | accruals, balance-sheet bloat, dilution, SBC/revenue, working-capital flattery |
| `value` -> VAL | 8 | EV/GP, expected return, re-rating gap, OCF yield, FCF yield, peak-margin risk, max downside, growth-adjusted EV/GP |
| scores | 21 | `score_G/B/M/R/Q/V`, `q_*` for each, `score_GRW/MOM/QLT/VAL`, `q_GRW/q_MOM/q_QLT/q_VAL`, `coverage` |

The factor list is derived from `engine.FACTOR_SPEC` at import time, so the
schema cannot drift from what the model actually ranks. `vqmg.RANKED` gives the
factors per sub-factor; `vqmg.SUB_TO_SUPER` maps sub-factors to super-factors.

### Diagnostics

The model computes more than it ranks: `roic_tc`, `roic_5y`, `cash_engine_yield`,
`midcycle_yield`, `terminal_yield`, `downside_rating`, `normalized_pe`,
`div_yield`, `distributed_yield`, `fcf_margin`, `vol_1y`, `price_vs_200d`, the
DSO/DPO day counts and others. These are absolute measures, useful when looking
at one name rather than sorting a list. They are not in the default output:

```python
df = vqmg.run(tickers, full=True)   # ranked factors plus every diagnostic
m  = vqmg.metrics("NVDA")           # one company, all variables, no ranking
```

### Financials mode

`fin_mode` is 1 when the FMP sector is `Financial Services`. Those names are
scored on an ROE frame with a 12% return bar instead of 15%, and the metrics that
have no meaning for a balance-sheet business — gross margin, GP/assets, FCF and
OCF yields, EV/GP, accruals, incremental ROIC, Rule of 40, opex conversion,
reinvestment intensity, balance-sheet capacity — are set to `NaN`. Shrinkage in
the sub-factor blend absorbs the gaps.

This is sector-wide. It catches payment networks, exchanges, asset managers and
credit-services names alongside banks and insurers.

### Absolute vs relative — the one thing to know

**Absolute** — every factor column, plus `expected_return` and `max_downside`.
Meaningful for a single stock on its own.

**Relative** — every `score_*` and `q_*` column. These describe a name's standing
*inside the list you passed*. Four tickers produce four quintiles that mean
almost nothing. Pass 25+ names, or score against a stored reference universe:

```python
ref = vqmg.run(my_500_names)          # save once
ref.to_csv("reference.csv", index=False)
```

`coverage` counts how many of the four super factors a name was scored on. Names
covered on fewer than 2 get no `core_rank` even when weights are supplied — a
single factor cannot set a rank.

## Missing data

There are no mock, placeholder or fallback values anywhere. A metric whose
inputs are missing is `NaN`. A ticker that returns no company data is not
emitted as a row — it lands in `df.attrs["errors"]`:

```python
df = vqmg.run(tickers)
for e in df.attrs["errors"]:
    print(e["symbol"], e["error"])
```

An invalid API key or an endpoint outside the plan raises `vqmg.FMPError`
immediately rather than silently producing empty columns.

## Cache

Per-ticker FMP payloads are cached as JSON for 24 hours in `.vqmg_cache/`
(override with `cache_dir=` or the `VQMG_CACHE_DIR` environment variable).
`refresh=True` forces a refetch.

## Layout

| file | purpose |
|---|---|
| `vqmg/engine.py` | the model — metrics, bounds, z-scores, sub/super factors, quintiles. Carried over unchanged. |
| `vqmg/fmp.py` | FMP `/stable/` fetching, field adapters, caching |
| `vqmg/__init__.py` | `run()`, `metrics()`, `columns()`, output schema |
| `vqmg/cli.py` | the `vqmg` command |
| `tests/test_contract.py` | wiring and schema tests, no network |

`engine.py` is not a file to tune. Any change to a factor definition changes what
every developer's numbers mean.

## Tests

No API key needed — the contract tests never touch the network.

```powershell
python -m pip install pytest
python -m pytest -q
```
