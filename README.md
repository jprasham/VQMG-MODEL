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

`run()` returns 68 columns: 9 identity, the 38 ranked factors, and 21 scores and
quintiles. Nothing that is not scored by the model.

| group | count | contents |
|---|---|---|
| `identity` | 9 | symbol, company, sector, industry, country, price, mktcap, fin_mode, cap_bar |
| `growth` -> GRW | 7 | 3Y revenue CAGR, TTM YoY, acceleration vs trend, gross-profit growth, forward revenue/EPS growth, growth persistence |
| `momentum_business` -> MOM | 5 | latest-quarter YoY, sequential acceleration, gross-margin change, EPS surprise, 3-month target change |
| `momentum_market` -> MOM | 5 | 12-1 momentum, trend smoothness, distance from 52-week high, relative strength vs SPY, down-market resilience |
| `quality_economics` -> QLT | 10 | GP/assets, gross margin, ROIC, incremental ROIC, ROIIC, Rule of 40, opex conversion, reinvestment intensity, sustainable growth, balance-sheet capacity |
| `quality_earnings` -> QLT | 5 | accruals, balance-sheet bloat, dilution, SBC/revenue, working-capital flattery |
| `value` -> VAL | 6 | EBIT/EV, forward earnings yield, FCF yield, book/price, sales/EV, normalized E/P |
| scores | 21 | `score_G/B/M/R/Q/V`, `q_*` for each, `score_GRW/MOM/QLT/VAL`, `q_GRW/q_MOM/q_QLT/q_VAL`, `coverage` |

The factor list is derived from `engine.FACTOR_SPEC` at import time, so the
schema cannot drift from what the model actually ranks. `vqmg.RANKED` gives the
factors per sub-factor; `vqmg.SUB_TO_SUPER` maps sub-factors to super-factors.

### The Value model

Six equal-weighted variables, each worth about 1.67% of a composite when Value is
10% of it. Every one is a yield or a ratio to market value, so **higher always
means cheaper** and all six carry the same sign.

| # | Variable | Formula | Financials |
|---|---|---|---|
| 1 | `ebit_to_ev` | TTM operating income / EV | TTM net income / mktcap |
| 2 | `fwd_earn_yield` | consensus NTM EPS / price | 5y avg ROE x book x (1+g) / mktcap |
| 3 | `fcf_yield` | TTM (OCF - capex) / mktcap | blank |
| 4 | `book_to_price` | book equity / mktcap | the same, and the primary metric |
| 5 | `sales_to_ev` | TTM revenue / EV | blank |
| 6 | `normalized_ep` | 5y avg net margin x TTM revenue / mktcap | 5y avg ROE x book / mktcap |

A financial has no meaningful enterprise value and no comparable sales line, so
two of the six are blank; coverage shrinkage scores the name on four and pulls it
toward the universe average in proportion.

**Forward estimates are interpolated to a common window.** FMP publishes
estimates per fiscal year, so taking "the next FY that has not closed" makes the
horizon depend on each company's accounting calendar — a June year-end gets a
ten-month-ahead forecast while a nearly-finished year gets a trailing number
wearing a forward label. Each row is instead treated as covering the twelve
months ending on its date, weighted by its overlap with the next twelve months,
and the overlapping years are blended. If the rows span less than
`engine.NTM_MIN_COVERAGE` (80%) of the year, or a leg is missing, the old
fiscal-year rule is used for that field and the basis is recorded: `fwd_basis`
for EPS, `fwd_rev_basis` for revenue. The fallback is per field, since revenue
and EPS coverage differ row by row on FMP. `ntm_blend` exports the weights,
`ntm_coverage` the span.
The same blend feeds `fwd_eps_growth`, `fwd_rev_growth` and `fwd_pe`, so Growth
ranks shift slightly too.

**A forecast loss is a real answer, not a gap.** When consensus forecasts a loss,
`fwd_earn_yield` is left blank rather than back-filled with the profitability the
company used to have. `fwd_earn_yield_calc` records the path taken: `0` consensus,
`1` modelled fallback (no consensus available), `2` blank because consensus
forecasts a loss.

The `g` in the modelled fallback is delivered TTM revenue growth floored at 0%,
capped at `engine.VALUE_G_CAP` (20%) for erratic growers. A steady grower — 5+ of
the last 8 quarters, or a sector in `engine.STEADY_GROWTH_SECTORS` — projects at
its full delivered rate.

### Diagnostics

The owner's-return pipeline (`expected_return`, `midcycle_yield`,
`terminal_yield`, `rerating_gap`, `peak_margin_risk`, `ev_to_gp`,
`ev_gp_growth_adj`) and the six-model downside stack (`max_downside`,
`downside_rating`, `bond_growth`, `earnings_suspect`) no longer feed the Value
quintile. They are still computed as context — for the flags and the D1-D5
rating — but they are not ranked.

Alongside them the model computes `roic_tc`, `roic_5y`, `cash_engine_yield`,
`ocf_yield`, `ocf_yield_core`, `fcf_yield_core`, `normalized_pe`, `div_yield`,
`distributed_yield`, `fcf_margin`, `vol_1y`, `price_vs_200d`, the DSO/DPO day
counts and others. These are absolute measures, useful when looking
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

**Absolute** — every factor column, plus the unranked context measures
(`expected_return`, `max_downside`, `downside_rating`). Meaningful for a single
stock on its own.

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
