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

Add the developer as a collaborator on this repo first, then:

```powershell
pip install git+https://github.com/jprasham/VQMG-MODEL.git
```

Upgrade later with the same command plus `--upgrade --force-reinstall`.

Pin a release in a project's `requirements.txt`:

```
vqmg @ git+https://github.com/jprasham/VQMG-MODEL.git@v1.0.0
```

## API key

The library reads `FMP_API_KEY` from the environment.

```powershell
# current shell only
$env:FMP_API_KEY = "your_key"

# persist for this Windows user
setx FMP_API_KEY "your_key"
```

In GitHub Actions the key comes from the `FMP_API_KEY` repository secret — see
`.github/workflows/vqmg-run.yml`.

> A GitHub Secret is only readable by Actions running in this repo. It cannot
> hand the key to a developer's laptop. Either each developer gets the key in
> their own environment, or they run the workflow and download the CSV artifact.

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

`vqmg.columns()` returns the full ordered schema. Grouped:

| group | contents |
|---|---|
| `identity` | symbol, company, sector, industry, country, price, mktcap, financials-mode flags |
| `growth` | 3Y revenue CAGR, TTM YoY, acceleration vs trend, gross-profit growth, forward revenue/EPS growth, growth persistence |
| `momentum_business` | latest-quarter YoY, sequential acceleration, gross-margin change, EPS surprise, 3-month target change |
| `momentum_market` | 12-1 momentum, trend smoothness, distance from 52-week high, relative strength vs SPY, down-market resilience, volatility, drawdown references |
| `quality_economics` | gross margin, ROIC (current / 5-year / through-cycle), incremental ROIC, ROIIC, GP/assets, Rule of 40, opex conversion, reinvestment intensity, sustainable growth, balance-sheet capacity |
| `quality_earnings` | accruals, balance-sheet bloat, dilution, SBC/revenue, working-capital flattery, DSO/DPO vs own norm |
| `value` | EV/GP, cash-engine yield, mid-cycle yield, terminal yield, re-rating gap, expected return, peak-margin risk, distributed yield, max downside, downside rating |
| scores | `score_G/B/M/R/Q/V`, `q_*` for each, `score_GRW/MOM/QLT/VAL`, `q_GRW/q_MOM/q_QLT/q_VAL`, `coverage` |

### Absolute vs relative — the one thing to know

**Absolute** — every raw variable, `expected_return`, `max_downside`,
`downside_rating`, `bond_growth`. Meaningful for a single stock on its own.

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

```powershell
python -m pip install pytest
python -m pytest -q
```
