# VQMG — Developer Guide

Give it tickers, get back the Growth / Momentum / Quality / Value model. One row per ticker, 70 columns.

## Setup

```powershell
pip install git+https://github.com/jprasham/VQMG-MODEL.git
setx FMP_API_KEY "your_own_fmp_key"        # reopen PowerShell afterwards
```

Needs your own FMP key (Ultimate plan). In your repo's GitHub Actions, store it as a secret and pass it as the `FMP_API_KEY` env var. Update the library with `pip install --upgrade --force-reinstall git+https://github.com/jprasham/VQMG-MODEL.git`.

## Use

```python
import vqmg

df = vqmg.run(["NVDA", "MSFT", "ASML", "V"])     # the model
m  = vqmg.metrics("NVDA")                        # one company, dict, no ranking
```

```powershell
vqmg NVDA MSFT ASML V -o out.csv
vqmg --tickers-file tickers.txt --neutral sector -o out.csv
```

| `run()` argument | what it does |
|---|---|
| `neutral` | `None` / `'sector'` / `'industry'` / `'country'` — z-scores computed within groups, so a bank is judged against banks. Groups under 6 names fall back to universe-wide. |
| `weights` | opt-in composite, e.g. `{"GRW":.3,"MOM":.3,"QLT":.3,"VAL":.1}`. Adds `composite` and `core_rank`. No weights ship with the package. |
| `full=True` | also returns the diagnostics the model computes but does not rank (`roic_tc`, `cash_engine_yield`, `downside_rating`, `normalized_pe`, `vol_1y`, DSO/DPO days, and others) |
| `refresh=True` | ignore the 24h cache in `.vqmg_cache/` and refetch |
| `workers` | parallel tickers, default 4. Each ticker is ~12 FMP calls. |

## Read this before you trust a number

**Scores are relative, not absolute.** Every `score_*` and `q_*` column ranks a name *against the other names in the same call*. Four tickers give you four quintiles that mean nothing. Pass 25+ names, or score against a saved reference universe. The raw factor columns and `expected_return` / `max_downside` are absolute and fine on a single name.

**Quintile 1 is best**, everywhere. `coverage` = how many of the four super factors the name was scored on, out of 4.

**Missing data is `NaN`, never a filled-in guess.** A ticker that returns no data doesn't appear as a row — it lands in `df.attrs["errors"]`. A bad API key raises `vqmg.FMPError` immediately.

**Financials are scored differently.** When the FMP sector is `Financial Services`, `fin_mode` = 1: ROIC becomes ROE, the return bar drops to 12%, and the metrics that have no meaning for a balance-sheet business (gross margin, GP/assets, FCF and OCF yields, EV/GP, accruals, incremental ROIC, Rule of 40, opex conversion, reinvestment intensity, balance-sheet capacity) are `NaN` by design. This is sector-wide, so it also catches payment networks, exchanges and asset managers.

## The 70 columns

**Identity (9)** — `symbol` `company` `sector` `industry` `country` `price` `mktcap` `fin_mode` `cap_bar`

**GROWTH → `score_GRW`, `q_GRW`** (7, higher is better)

| column | meaning |
|---|---|
| `rev_cagr_3y` | compound revenue growth over 3 fiscal years |
| `rev_yoy_ttm` | trailing-twelve-month revenue growth |
| `rev_accel` | TTM growth minus 3Y CAGR — speeding up or slowing against its own trend |
| `gp_growth_ttm` | gross profit dollar growth; harder to fake than revenue |
| `fwd_rev_growth` | consensus revenue growth, next fiscal year |
| `fwd_eps_growth` | consensus EPS growth, next fiscal year |
| `growth_persistence` | share of the last 8 quarters that grew >5% YoY |

**MOMENTUM → `score_MOM`, `q_MOM`** — business (5) and market (5), blended 50/50

| column | meaning |
|---|---|
| `rev_yoy_q0` | latest-quarter revenue YoY — the freshest growth reading |
| `rev_accel_q` | change in YoY rate between consecutive quarters |
| `gm_change_yoy` | gross margin expansion in percentage points |
| `eps_surprise` | latest reported quarter vs consensus |
| `target_chg_3m` | are analysts raising or cutting price targets |
| `mom_12_1` | 12-month price return excluding the last month |
| `trend_smoothness` | return per unit of volatility — rewards a steady climb |
| `dist_from_high` | distance below the 52-week high (always ≤ 0, closer to 0 is better) |
| `rel_strength` | 6-month return minus SPY |
| `down_resilience` | average return on the market's worst 15% of days |

**QUALITY → `score_QLT`, `q_QLT`** — returns on capital (10) and earnings quality (5), blended 2/3–1/3

| column | meaning |
|---|---|
| `gp_to_assets` | gross profit per dollar of assets |
| `gross_margin` | TTM gross margin |
| `roic` | NOPAT / invested capital (ROE for financials) |
| `incremental_roic` | return on *new* capital, not capital ever invested |
| `roiic` | earnings growth per dollar retained after dividends and buybacks |
| `rule_of_40` | revenue growth + FCF margin |
| `opex_conversion` | gross profit gained per extra dollar of R&D + SG&A |
| `reinvest_intensity` | (capex + R&D) / revenue — higher scores better |
| `sustainable_growth` | growth fundable from own cash flow |
| `balance_capacity` | net cash as a share of market cap |
| `accruals` | profit not backed by cash — **lower is better** |
| `bs_bloat` | assets growing faster than revenue — **lower is better** |
| `dilution` | YoY growth in diluted share count — **lower is better** |
| `sbc_to_rev` | stock comp / revenue — **lower is better** |
| `wc_flatter` | cash flow that came from payment timing — **lower is better** |

**VALUE → `score_VAL`, `q_VAL`** (8)

| column | meaning |
|---|---|
| `ev_to_gp` | EV / gross profit — **lower is better** |
| `ev_gp_growth_adj` | the same multiple divided by growth — **lower is better** |
| `ocf_yield` | operating cash flow / market cap, working-capital flattery stripped out |
| `fcf_yield` | the same after capex |
| `expected_return` | what accrues to an owner per year: mid-cycle yield + believed growth |
| `rerating_gap` | how far the multiple sits from where this quality of franchise belongs |
| `peak_margin_risk` | how far margins sit above their own 5-year history — **lower is better** |
| `max_downside` | distance to the best credible floor, as a negative percentage |

**Scores and quintiles (21)** — `score_G` `score_B` `score_M` `score_R` `score_Q` `score_V` and `q_G` `q_B` `q_M` `q_R` `q_Q` `q_V` (sub-factors: Growth, Business momentum, Market momentum, Returns on capital, earnings Quality, Value), then `score_GRW` `score_MOM` `score_QLT` `score_VAL`, `q_GRW` `q_MOM` `q_QLT` `q_VAL`, and `coverage`.

Sub-factor scores are shrunk toward the average by √(metrics known ÷ metrics in the factor), so a name known on 1 of 5 metrics is judged mostly average rather than on one number.

## Handy

```python
vqmg.columns()          # the full ordered schema
vqmg.COLUMN_GROUPS      # columns grouped as above
vqmg.RANKED             # ranked factors per sub-factor
df.attrs["errors"]      # tickers that returned no data
```

Do not edit `vqmg/engine.py`. Changing a factor definition changes what everyone's numbers mean.
