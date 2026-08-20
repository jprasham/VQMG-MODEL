"""
VQMG — Growth / Momentum / Quality / Value engine
=================================================
Computation core. Every metric definition, bound, z-score, sub-factor blend and
quintile rule below is carried over UNCHANGED from the source model. Do not edit
this file to "improve" a factor — the methodology is fixed by design.

Two deliberate departures from the source, both structural rather than
methodological:

  1. No default super-factor weights. `rank_universe()` returns the four super
     factor scores and quintiles; `composite` / `core_rank` appear only if the
     caller passes an explicit `weights=` mapping.
  2. The data layer lives in `vqmg.fmp` (FMP /stable/ endpoints), not here.
     `compute_metrics()` is pure: blob in, metrics out.

Sub factors:  G growth | B business momentum | M market reaction
              R reinvestment economics | Q earnings quality | V value
Super factors: GRW <- G | MOM <- B,M | QLT <- R,Q | VAL <- V
"""
from __future__ import annotations

import datetime as dt
import math

import numpy as np
import pandas as pd

TODAY = dt.date.today()

SUBS = ["G", "B", "M", "R", "Q", "V"]
SUPERS = {   # super factor <- {sub factor: blend weight}
    "GRW": {"G": 1.0},
    "MOM": {"B": 0.5, "M": 0.5},
    "QLT": {"R": 2 / 3, "Q": 1 / 3},
    "VAL": {"V": 1.0},
}

SUPER_LABELS = {"GRW": "GROWTH", "MOM": "MOMENTUM", "QLT": "QUALITY", "VAL": "VALUE"}


# ----------------------------------------------------------------------------
# Factor computation  (verbatim from the source model)
# ----------------------------------------------------------------------------

def _first(lst):
    return lst[0] if isinstance(lst, list) and lst else None


def safe(fn, default=np.nan):
    try:
        v = fn()
        if v is None or (isinstance(v, float) and not math.isfinite(v)):
            return default
        return v
    except Exception:
        return default


def cagr(new, old, years):
    if old is None or new is None or old <= 0 or new <= 0 or years <= 0:
        return np.nan
    return (new / old) ** (1 / years) - 1


def compute_metrics(ticker, blob, bench=None):
    """Raw metric dict for one company. NaN where data missing."""
    m = {"symbol": ticker}
    prof = _first(blob.get("profile"))
    quote = _first(blob.get("quote"))
    inc_a = blob.get("income_a") or []
    inc_q = blob.get("income_q") or []
    bal_a = blob.get("balance_a") or []
    cf_a = blob.get("cashflow_a") or []
    cf_q = blob.get("cashflow_q") or []
    est = blob.get("estimates") or []
    px = (blob.get("prices") or {}).get("historical") if isinstance(blob.get("prices"), dict) else None
    surp = blob.get("surprises") or []

    m["company"] = safe(lambda: prof["companyName"], ticker)
    m["sector"] = safe(lambda: prof["sector"], "Unclassified") or "Unclassified"
    m["industry"] = safe(lambda: prof.get("industry", ""), "") or ""
    _ind = (m["industry"] or "").lower()
    is_fin = any(k in _ind for k in ("bank", "insurance", "mortgage", "lending"))
    m["fin_mode"] = 1 if is_fin else 0
    m["cap_bar"] = 0.12 if is_fin else 0.15   # ROE bar for balance-sheet financials
    m["country"] = safe(lambda: prof["country"], "Unknown") or "Unknown"
    m["industry"] = safe(lambda: prof["industry"], "")
    m["price"] = safe(lambda: quote["price"])
    m["mktcap"] = safe(lambda: quote["marketCap"])
    m["fwd_pe"] = np.nan  # filled below from estimates

    # --- trailing twelve months from quarterlies ---
    def ttm(rows, field, n=4, offset=0):
        vals = [r.get(field) for r in rows[offset:offset + n]]
        if len(vals) < n or any(v is None for v in vals):
            return np.nan
        return float(sum(vals))

    rev_ttm = ttm(inc_q, "revenue")
    rev_ttm_1y = ttm(inc_q, "revenue", offset=4)
    gp_ttm = ttm(inc_q, "grossProfit")
    gp_ttm_1y = ttm(inc_q, "grossProfit", offset=4)
    ni_ttm = ttm(inc_q, "netIncome")
    eps_ttm = ttm(inc_q, "epsdiluted")
    rd_ttm = ttm(inc_q, "researchAndDevelopmentExpenses")
    cfo_ttm = ttm(cf_q, "operatingCashFlow")
    capex_ttm = ttm(cf_q, "capitalExpenditure")
    fcf_ttm = safe(lambda: cfo_ttm + capex_ttm)  # capex negative in FMP
    sbc_ttm = ttm(cf_q, "stockBasedCompensation")
    div_ttm = ttm(cf_q, "dividendsPaid")                 # negative outflow in FMP
    bb_ttm = ttm(cf_q, "commonStockRepurchased")         # negative outflow in FMP
    wc_ttm = ttm(cf_q, "changeInWorkingCapital")         # positive = cash RELEASED from WC (timing, not operations)

    # ============ G: GROWTH ============
    rev0 = safe(lambda: inc_a[0]["revenue"])
    rev3 = safe(lambda: inc_a[3]["revenue"])
    m["rev_cagr_3y"] = cagr(rev0, rev3, 3)
    m["rev_yoy_ttm"] = safe(lambda: rev_ttm / rev_ttm_1y - 1)
    m["rev_accel"] = safe(lambda: m["rev_yoy_ttm"] - m["rev_cagr_3y"])
    m["gp_growth_ttm"] = safe(lambda: gp_ttm / gp_ttm_1y - 1)
    # forward growth: NEAREST future fiscal year (FMP returns newest-first;
    # sort ascending or the loop grabs the farthest-out FY)
    fwd_rev = np.nan
    fwd_eps = np.nan
    dated = []
    for e in est:
        d = safe(lambda: dt.date.fromisoformat(e["date"][:10]), None)
        if d:
            dated.append((d, e))
    for d, e in sorted(dated, key=lambda x: x[0]):
        if d >= TODAY - dt.timedelta(days=45):
            fwd_rev = e.get("estimatedRevenueAvg") or np.nan
            fwd_eps = e.get("estimatedEpsAvg") or np.nan
            break
    m["fwd_rev_growth"] = safe(lambda: fwd_rev / rev_ttm - 1)
    m["fwd_eps_growth"] = safe(lambda: fwd_eps / eps_ttm - 1
                               if fwd_eps and eps_ttm and fwd_eps > 0 and eps_ttm > 0 else np.nan)
    m["fwd_pe"] = safe(lambda: m["price"] / fwd_eps if fwd_eps and fwd_eps > 0 else np.nan)
    # persistence: share of last 8 quarters with YoY revenue growth > 5%
    def persistence():
        hits, tot = 0, 0
        for i in range(min(8, len(inc_q) - 4)):
            a, b = inc_q[i].get("revenue"), inc_q[i + 4].get("revenue")
            if a and b and b > 0:
                tot += 1
                if a / b - 1 > 0.05:
                    hits += 1
        return hits / tot if tot else np.nan
    m["growth_persistence"] = safe(persistence)

    # ============ B: BUSINESS MOMENTUM (the recent numbers) ============
    def q_yoy(i, field="revenue"):
        a, b = inc_q[i].get(field), inc_q[i + 4].get(field)
        return a / b - 1 if a and b and b > 0 else np.nan
    m["rev_yoy_q0"] = safe(lambda: q_yoy(0))            # latest quarter YoY
    m["rev_accel_q"] = safe(lambda: q_yoy(0) - q_yoy(1))  # YoY growth, this Q vs last Q
    def gm(i):
        r, g = inc_q[i].get("revenue"), inc_q[i].get("grossProfit")
        return g / r if r and g is not None and r > 0 else np.nan
    m["gm_change_yoy"] = safe(lambda: gm(0) - gm(4))    # gross margin pp change vs year-ago Q
    # denominator floor: a surprise off a near-zero estimate is noise, not signal
    m["eps_surprise"] = safe(lambda: (surp[0]["actualEarningResult"] - surp[0]["estimatedEarning"]) / abs(surp[0]["estimatedEarning"])
                             if abs(surp[0]["estimatedEarning"]) >= 0.02 else np.nan)
    # analyst target momentum: median target published in last 90d vs the 90-270d window
    tgt = blob.get("targets") or []
    def target_change():
        now, prior = [], []
        for a in tgt:
            d = safe(lambda: dt.date.fromisoformat(a["publishedDate"][:10]), None)
            pt = a.get("adjPriceTarget") or a.get("priceTarget")
            if not d or not pt:
                continue
            age = (TODAY - d).days
            if 0 <= age <= 90:
                now.append(pt)
            elif age <= 270:
                prior.append(pt)
        if len(now) >= 2 and len(prior) >= 2:
            return float(np.median(now) / np.median(prior) - 1)
        return np.nan
    m["target_chg_3m"] = safe(target_change)
    cons = _first(blob.get("target_cons")) if isinstance(blob.get("target_cons"), list) else blob.get("target_cons")
    m["target_upside"] = safe(lambda: cons["targetConsensus"] / m["price"] - 1)  # memo only, not ranked

    # ============ R: REINVESTMENT ECONOMICS / ABILITY TO GROW ============
    assets0 = safe(lambda: bal_a[0]["totalAssets"])
    m["gp_to_assets"] = safe(lambda: (inc_a[0]["grossProfit"]) / assets0)

    def invested_capital(b):
        return (b.get("totalDebt") or 0) + (b.get("totalStockholdersEquity") or 0) - (b.get("cashAndShortTermInvestments") or 0)

    def nopat(i):
        rate = 0.21
        return (i.get("operatingIncome") or np.nan) * (1 - rate)

    def inc_roic():
        k = min(4, len(inc_a) - 1, len(bal_a) - 1)
        if k < 2:
            return np.nan
        d_nopat = nopat(inc_a[0]) - nopat(inc_a[k])
        d_ic = invested_capital(bal_a[0]) - invested_capital(bal_a[k])
        if not math.isfinite(d_nopat):
            return np.nan
        # denominator floor: a tiny positive capital delta makes the ratio explode;
        # treat anything under 2% of assets as the capital-light case
        if d_ic <= max(0.02 * (assets0 if math.isfinite(assets0) else 0), 0):
            # capital-light: growing NOPAT while shrinking capital is the best case,
            # not a data error — cap at 100% rather than excluding
            return 1.0 if d_nopat > 0 else np.nan
        return d_nopat / d_ic
    m["incremental_roic"] = safe(inc_roic)
    # ROIIC, Huber-style (Saber Capital): earnings growth over the window divided
    # by capital ACTUALLY RETAINED (cum. net income less dividends and buybacks) —
    # flow-based, so buybacks and write-offs don't pollute the denominator
    cf_by_year = {c.get("date", ""): c for c in cf_a}
    def roiic_flow():
        k = min(4, len(inc_a) - 1, len(cf_a) - 1)
        if k < 2:
            return np.nan
        d_ni = (inc_a[0].get("netIncome") or np.nan) - (inc_a[k].get("netIncome") or np.nan)
        if not math.isfinite(d_ni):
            return np.nan
        retained = 0.0
        for i in range(k):           # the years whose retained capital funded the growth
            ni = inc_a[i].get("netIncome") or 0
            cf = cf_a[i] if i < len(cf_a) else {}
            retained += ni + (cf.get("dividendsPaid") or 0) + (cf.get("commonStockRepurchased") or 0)
        floor = 0.02 * (assets0 if math.isfinite(assets0) else 0)
        if retained <= max(floor, 0):
            # grew earnings while retaining nothing (returned it all) — elite, cap
            return 1.0 if d_ni > 0 else np.nan
        return d_ni / retained
    m["roiic"] = safe(roiic_flow)
    # intrinsic compounding rate ~ ROIIC x retention (Huber identity) — memo
    def intrinsic_compound():
        k = min(4, len(inc_a) - 1, len(cf_a) - 1)
        if k < 2 or not math.isfinite(m.get("roiic", np.nan)):
            return np.nan
        tot_ni, retained = 0.0, 0.0
        for i in range(k):
            ni = inc_a[i].get("netIncome") or 0
            cf = cf_a[i] if i < len(cf_a) else {}
            tot_ni += ni
            retained += ni + (cf.get("dividendsPaid") or 0) + (cf.get("commonStockRepurchased") or 0)
        if tot_ni <= 0:
            return np.nan
        retention = float(np.clip(retained / tot_ni, 0, 1))
        return m["roiic"] * retention
    m["intrinsic_compound"] = safe(intrinsic_compound)
    m["fcf_margin"] = safe(lambda: fcf_ttm / rev_ttm)
    m["rule_of_40"] = safe(lambda: m["rev_yoy_ttm"] + m["fcf_margin"])
    # opex conversion: delta GP / delta (R&D + SG&A) over 2 fiscal years
    def opex(i):
        return (i.get("researchAndDevelopmentExpenses") or 0) + (i.get("sellingGeneralAndAdministrativeExpenses") or 0)
    m["opex_conversion"] = safe(lambda: (inc_a[0]["grossProfit"] - inc_a[2]["grossProfit"]) / (opex(inc_a[0]) - opex(inc_a[2]))
                                if (opex(inc_a[0]) - opex(inc_a[2])) > 0 else np.nan)
    # --- ability to grow / internally funded growth ---
    m["gross_margin"] = safe(lambda: gp_ttm / rev_ttm)   # margin LEVEL, not just change
    m["roic"] = safe(lambda: nopat(inc_a[0]) / invested_capital(bal_a[0])
                     if invested_capital(bal_a[0]) > 0 else np.nan)
    def roic_5y():
        vals = []
        for i in range(min(5, len(inc_a), len(bal_a))):
            ic = invested_capital(bal_a[i])
            npt = nopat(inc_a[i])
            if ic > 0 and math.isfinite(npt):
                vals.append(npt / ic)
        return float(np.mean(vals)) if len(vals) >= 3 else np.nan
    m["roic_5y"] = safe(roic_5y)
    # through-cycle ROIC: the LOWER of current and 5y average — returns must be
    # adequate now AND through the cycle before growth or reinvestment earns credit
    m["roic_tc"] = safe(lambda: min(m["roic"], m["roic_5y"]) if math.isfinite(m["roic_5y"]) else m["roic"])
    m["reinvest_intensity"] = safe(lambda: (-capex_ttm + (rd_ttm if math.isfinite(rd_ttm) else 0)) / rev_ttm)
    def sustainable_growth():
        ic0 = invested_capital(bal_a[0])
        np0 = nopat(inc_a[0])
        if not math.isfinite(np0) or np0 <= 0 or ic0 <= 0:
            return np.nan
        roic = np0 / ic0
        reinvest_rate = float(np.clip((ic0 - invested_capital(bal_a[1])) / np0, -0.25, 1.5))
        return roic * reinvest_rate   # classic: g = ROIC x reinvestment rate
    m["sustainable_growth"] = safe(sustainable_growth)
    m["balance_capacity"] = safe(lambda: ((bal_a[0].get("cashAndShortTermInvestments") or 0) - (bal_a[0].get("totalDebt") or 0)) / m["mktcap"])

    # ------------------------------------------------------------------
    # FINANCIALS MODE (banks / insurers / lenders): the balance sheet IS the
    # business, so invested-capital, EV and FCF metrics are meaningless.
    # Swap to the ROE frame; NaN what has no meaning (shrinkage absorbs it).
    # ------------------------------------------------------------------
    if is_fin:
        def _roe(i):
            eq = _avg_eq(i)
            ni_ = inc_a[i].get("netIncome") if i < len(inc_a) else None
            return (ni_ / eq) if (eq and ni_ is not None) else np.nan
        def _avg_eq(i):
            e0 = bal_a[i].get("totalStockholdersEquity") if i < len(bal_a) else None
            e1 = bal_a[i+1].get("totalStockholdersEquity") if i+1 < len(bal_a) else e0
            if not e0 or e0 <= 0:
                return None
            return (e0 + (e1 if e1 and e1 > 0 else e0)) / 2   # ERP: NI / AVERAGE book value
        m["roic"] = safe(lambda: ni_ttm / _avg_eq(0) if _avg_eq(0) else np.nan)
        def roe_5y():
            vals = [v for v in (_roe(i) for i in range(min(5, len(inc_a), len(bal_a)))) if math.isfinite(v)]
            return float(np.mean(vals)) if len(vals) >= 3 else np.nan
        m["roic_5y"] = safe(roe_5y)
        m["roic_tc"] = safe(lambda: min(m["roic"], m["roic_5y"]) if math.isfinite(m["roic_5y"]) else m["roic"])
        def fin_sustainable():
            if not math.isfinite(ni_ttm) or ni_ttm <= 0 or not math.isfinite(m.get("roic_tc", np.nan)):
                return np.nan
            retained = ni_ttm + (div_ttm if math.isfinite(div_ttm) else 0) + (bb_ttm if math.isfinite(bb_ttm) else 0)
            retention = float(np.clip(retained / ni_ttm, 0, 1))
            return m["roic_tc"] * retention   # classic bank sustainable growth: ROE x retention
        m["sustainable_growth"] = safe(fin_sustainable)
        for k in ("gross_margin", "gm_change_yoy", "gp_growth_ttm", "gp_to_assets",
                  "fcf_margin", "rule_of_40", "opex_conversion", "reinvest_intensity",
                  "accruals", "balance_capacity", "incremental_roic"):
            m[k] = np.nan
    # ---- ERP normalized valuation set (memo): revenue trended 3y at 5y avg
    # growth, 5y average margins applied to the forecast revenue base ----
    def _rev_g5():
        gs = [inc_a[i]["revenue"] / inc_a[i+1]["revenue"] - 1 for i in range(min(4, len(inc_a)-1))
              if inc_a[i+1].get("revenue") and inc_a[i+1]["revenue"] > 0 and inc_a[i].get("revenue")]
        return float(np.mean(gs)) if len(gs) >= 3 else np.nan
    def _margin5(field):
        ms = [inc_a[i][field] / inc_a[i]["revenue"] for i in range(min(5, len(inc_a)))
              if inc_a[i].get("revenue") and inc_a[i]["revenue"] > 0 and inc_a[i].get(field) is not None]
        return float(np.mean(ms)) if len(ms) >= 3 else np.nan
    def normalized_pe():
        g5, nm5 = _rev_g5(), _margin5("netIncome")
        if not (math.isfinite(g5) and math.isfinite(nm5)) or nm5 <= 0:
            return np.nan
        norm_earn = inc_a[0]["revenue"] * (1 + float(np.clip(g5, -0.15, 0.30))) ** 3 * nm5
        return m["mktcap"] / norm_earn if norm_earn > 0 else np.nan
    m["normalized_pe"] = safe(normalized_pe)
    def normalized_fcf_yield():
        if is_fin:
            return np.nan
        g5 = _rev_g5()
        fms = [(cf_a[i].get("freeCashFlow") or np.nan) / inc_a[i]["revenue"]
               for i in range(min(5, len(inc_a), len(cf_a))) if inc_a[i].get("revenue")]
        fms = [v for v in fms if math.isfinite(v)]
        if not math.isfinite(g5) or len(fms) < 3:
            return np.nan
        fwd_rev = inc_a[0]["revenue"] * (1 + float(np.clip(g5, -0.15, 0.30))) ** 3
        return (float(np.mean(fms)) * fwd_rev) / m["mktcap"]
    m["normalized_fcf_yield"] = safe(normalized_fcf_yield)
    # ERP: above-trend capital spending — latest capex/revenue vs prior 3y trend
    def above_trend_capex():
        if is_fin or len(cf_a) < 4:
            return np.nan
        def cr(i):
            cx, rv = cf_a[i].get("capitalExpenditure"), inc_a[i].get("revenue") if i < len(inc_a) else None
            return (-cx / rv) if (cx is not None and rv and rv > 0) else np.nan
        cur, prior = cr(0), [cr(i) for i in (1, 2, 3)]
        prior = [v for v in prior if math.isfinite(v)]
        return cur - float(np.mean(prior)) if math.isfinite(cur) and len(prior) >= 2 else np.nan
    m["above_trend_capex"] = safe(above_trend_capex)
    m["cash_to_mktcap"] = safe(lambda: (bal_a[0].get("cashAndShortTermInvestments") or 0) / m["mktcap"])
    # the only true "paid to wait": cash actually distributed to holders
    m["div_yield"] = safe(lambda: prof["lastDiv"] / m["price"]
                          if prof and prof.get("lastDiv") and m["price"] else np.nan)   # from FMP profile
    m["buyback_yield"] = safe(lambda: -bb_ttm / m["mktcap"] if math.isfinite(bb_ttm) else np.nan)
    m["distributed_yield"] = safe(lambda: (-(div_ttm if math.isfinite(div_ttm) else 0)
                                           - (bb_ttm if math.isfinite(bb_ttm) else 0)) / m["mktcap"]
                                  if (math.isfinite(div_ttm) or math.isfinite(bb_ttm)) else np.nan)

    # ============ Q: EARNINGS QUALITY (higher = worse) ============
    avg_assets = safe(lambda: (bal_a[0]["totalAssets"] + bal_a[1]["totalAssets"]) / 2)
    m["accruals"] = safe(lambda: (ni_ttm - cfo_ttm) / avg_assets if not is_fin else np.nan)   # a bank is made of accruals
    # WC-flattered cash flow: the share of OCF that is working-capital release
    # (stretched payables, pulled-forward receivables, inventory drawdown) —
    # timing, reversible, not operations. Asymmetric: only the flattering side
    # is penalized; investing INTO working capital is not (growth needs WC).
    m["wc_flatter"] = safe(lambda: max(0.0, wc_ttm) / avg_assets if (not is_fin and math.isfinite(wc_ttm)) else np.nan)
    # receivable / payable days vs the COMPANY'S OWN NORM (avg of prior 3 years).
    # Level-based: catches multi-year creep that single-year flows hide. Own norm,
    # not cross-sectional, so structurally negative-WC models (Amazon) are not
    # penalized for being themselves.
    def _days(i, num_field, den):
        b = bal_a[i] if i < len(bal_a) else {}
        v = b.get(num_field)
        return (v / den * 365) if (v is not None and den and den > 0) else np.nan
    def _cogs(i):
        if i >= len(inc_a):
            return None
        c = (inc_a[i].get("revenue") or 0) - (inc_a[i].get("grossProfit") or 0)
        return c if c > 0 else None
    def _norm_gap(num_field, den_fn):
        now = _days(0, num_field, den_fn(0))
        hist = [_days(i, num_field, den_fn(i)) for i in (1, 2, 3)]
        hist = [v for v in hist if math.isfinite(v)]
        if not math.isfinite(now) or len(hist) < 2:
            return np.nan
        return now - float(np.mean(hist))
    m["dpo_change_days"] = safe(lambda: _norm_gap("accountPayables", _cogs))          # + = paying slower than own norm
    m["dso_change_days"] = safe(lambda: _norm_gap("netReceivables", lambda i: inc_a[i].get("revenue") if i < len(inc_a) else None))  # - = collecting faster than own norm
    # the Tice decomposition: current vs normal days, and what each day is worth
    m["dpo_now_days"] = safe(lambda: _days(0, "accountPayables", _cogs(0)))
    m["dpo_norm_days"] = safe(lambda: m["dpo_now_days"] - m["dpo_change_days"])
    m["dso_now_days"] = safe(lambda: _days(0, "netReceivables", inc_a[0].get("revenue")))
    m["dso_norm_days"] = safe(lambda: m["dso_now_days"] - m["dso_change_days"])
    # each day of payables ~ COGS/365 of cash; each day of receivables ~ revenue/365 — in yield terms
    m["pay_day_value"] = safe(lambda: (rev_ttm - gp_ttm) / 365 / m["mktcap"]
                              if math.isfinite(rev_ttm) and math.isfinite(gp_ttm) and m["mktcap"] and (rev_ttm - gp_ttm) > 0 else np.nan)
    m["rec_day_value"] = safe(lambda: rev_ttm / 365 / m["mktcap"] if math.isfinite(rev_ttm) and m["mktcap"] else np.nan)
    # level-based flattery estimate: cash retained by the days deviation
    def _days_strip():
        # PAYABLES ONLY: a sustained stretch above own norm must eventually
        # unwind, so its benefit is stripped. Faster-than-norm COLLECTION is
        # not stripped — improved receivables efficiency can persist (it gets
        # a verify-flag instead, since factoring looks identical from here).
        cogs_t = rev_ttm - gp_ttm if math.isfinite(rev_ttm) and math.isfinite(gp_ttm) else np.nan
        pay = max(0.0, m.get("dpo_change_days") or 0) / 365 * cogs_t if math.isfinite(cogs_t) else 0.0
        return pay if math.isfinite(pay) and pay > 0 else 0.0
    asset_growth = safe(lambda: bal_a[0]["totalAssets"] / bal_a[1]["totalAssets"] - 1)
    m["bs_bloat"] = safe(lambda: asset_growth - m["rev_yoy_ttm"])  # ERP big-loser signature
    m["dilution"] = safe(lambda: inc_q[0]["weightedAverageShsOutDil"] / inc_q[4]["weightedAverageShsOutDil"] - 1)
    m["sbc_to_rev"] = safe(lambda: sbc_ttm / rev_ttm)

    # ============ M: MARKET REACTION (pure price) ============
    if px:
        closes = pd.Series([p["close"] for p in reversed(px)], dtype=float)
        if len(closes) > 230:
            m["mom_12_1"] = safe(lambda: closes.iloc[-22] / closes.iloc[-252] - 1)
            logp = np.log(closes.iloc[-189:])          # ~9 months
            x = np.arange(len(logp))
            slope, intercept = np.polyfit(x, logp, 1)
            resid = logp - (slope * x + intercept)
            r2 = 1 - resid.var() / logp.var() if logp.var() > 0 else 0
            # ERP: risk-adjusted nine-month price trend — average monthly return
            # measured from nine months ago EXCLUDING the latest month, scaled by
            # the standard deviation of monthly returns over the same period
            if len(closes) >= 200:
                m["price_vs_200d"] = float(closes.iloc[-1] / closes.iloc[-200:].mean() - 1)
            # extreme month vs OWN history: current 21d return ranked against the
            # distribution of rolling 21d returns over up to 5 years
            if len(closes) >= 273:   # >= ~13 months of history beyond the current month
                r21 = closes.pct_change(21).dropna()
                cur = float(r21.iloc[-1])
                hist = r21.iloc[:-21]                      # exclude the current month's windows
                if len(hist) >= 230:
                    m["move_1m"] = cur
                    m["move_1m_pctile"] = float((hist < cur).mean())
                    m["worst_month_5y"] = float(hist.min())
            if len(closes) >= 252:
                m["dist_to_52w_low"] = float(closes.iloc[-252:].min() / closes.iloc[-1] - 1)
                dr = closes.iloc[-252:].pct_change().dropna()
                if len(dr) >= 200:
                    m["vol_1y"] = float(dr.std() * math.sqrt(252))
            mclose = closes.iloc[::21]                     # ~monthly sampling
            mret = mclose.pct_change().dropna()
            win = mret.iloc[-10:-1] if len(mret) >= 10 else mret.iloc[:-1]
            if len(win) >= 6 and win.std() > 0:
                m["trend_smoothness"] = float(win.mean() / win.std())
            else:
                m["trend_smoothness"] = np.nan
            m["dist_from_high"] = safe(lambda: closes.iloc[-1] / closes.iloc[-252:].max() - 1)  # true 52w
    # correction behavior vs benchmark: who gets accumulated on down days
    bh = (bench or {}).get("historical") if isinstance(bench, dict) else None
    if px and bh:
        def corr_metrics():
            bs = pd.Series({b["date"]: b["close"] for b in bh}, dtype=float)
            ss = pd.Series({p["date"]: p["close"] for p in px}, dtype=float)
            common = bs.index.intersection(ss.index)
            bs, ss = bs[common].sort_index(), ss[common].sort_index()
            if len(bs) < 150:
                return np.nan, np.nan
            rel = float((ss.iloc[-1] / ss.iloc[-126] - 1) - (bs.iloc[-1] / bs.iloc[-126] - 1))
            sr, br = ss.pct_change().iloc[-189:], bs.pct_change().iloc[-189:]
            thr = br.quantile(0.15)                # SPY's worst ~15% of days
            down = br <= thr
            resil = float(sr[down].mean()) if int(down.sum()) >= 8 else np.nan
            return rel, resil
        m["rel_strength"], m["down_resilience"] = safe(lambda: corr_metrics(), (np.nan, np.nan))
    m.setdefault("mom_12_1", np.nan)
    m.setdefault("trend_smoothness", np.nan)
    m.setdefault("dist_from_high", np.nan)
    m.setdefault("rel_strength", np.nan)
    m.setdefault("down_resilience", np.nan)

    # ============ V: VALUATION CHECK ============
    net_debt = safe(lambda: (bal_a[0].get("totalDebt") or 0) - (bal_a[0].get("cashAndShortTermInvestments") or 0), 0)
    ev = safe(lambda: m["mktcap"] + net_debt)
    m["ev"] = ev
    m["ev_to_gp"] = safe(lambda: ev / gp_ttm if (not is_fin and gp_ttm and gp_ttm > 0) else np.nan)
    # ERP definitions: gross cash flow yield (recurring ops, pre-capex) and free
    # cash flow yield relative to MARKET VALUE OF EQUITY. The RANKED versions are
    # CORE: material working-capital release (>15% of OCF) is removed first —
    # the value model does not pay for timing. Reported versions kept for display.
    _flow = max(0.0, wc_ttm) if math.isfinite(wc_ttm) else 0.0            # flattery within the TTM window
    _level = safe(_days_strip, 0.0) or 0.0                                # multi-year creep vs own norm
    _wc_strip = 0.0
    if not is_fin and math.isfinite(cfo_ttm) and abs(cfo_ttm) > 0:
        cand = max(_flow, _level)                                          # take the larger estimate, haircut only
        if cand > 0.15 * abs(cfo_ttm):
            _wc_strip = cand
    m["wc_strip_applied"] = _wc_strip
    m["ocf_yield_reported"] = safe(lambda: cfo_ttm / m["mktcap"] if (not is_fin and m["mktcap"]) else np.nan)
    m["fcf_yield_reported"] = safe(lambda: fcf_ttm / m["mktcap"] if (not is_fin and m["mktcap"]) else np.nan)
    m["ocf_yield"] = safe(lambda: (cfo_ttm - _wc_strip) / m["mktcap"] if (not is_fin and m["mktcap"]) else np.nan)
    m["fcf_yield"] = safe(lambda: (fcf_ttm - _wc_strip) / m["mktcap"] if (not is_fin and m["mktcap"]) else np.nan)
    # investor-choice cash yield: what the business generates, credited back for
    # investment made at high returns. Negative FCF at high ROIC is a choice,
    # not a deficiency (the Amazon case): use OCF yield when ROIC clears 15%
    # and reinvestment is real; else plain FCF yield.
    def cap_return_gate():
        """Blend of through-cycle book returns and Huber ROIIC when both known —
        the marginal-question capital-return measure (see BOOK vs MARGINAL flag)."""
        vals = [v for v in (m.get("roic_tc", np.nan), m.get("roiic", np.nan)) if math.isfinite(v)]
        return float(np.mean(vals)) if vals else np.nan

    def choice_yield():
        if is_fin:
            # banks: no meaningful FCF/EV — the engine is the earnings yield (E/P)
            return ni_ttm / m["mktcap"] if math.isfinite(ni_ttm) and m["mktcap"] else np.nan
        # yields are already CORE (material WC release removed at source)
        rg_, ri = cap_return_gate(), m.get("reinvest_intensity", np.nan)
        if math.isfinite(rg_) and rg_ > m["cap_bar"] and math.isfinite(ri) and ri > 0.05:
            return m["ocf_yield"]
        return m["fcf_yield"]
    m["cash_engine_yield"] = safe(choice_yield)
    g = m["fwd_rev_growth"] if math.isfinite(m.get("fwd_rev_growth", np.nan)) else m.get("rev_yoy_ttm", np.nan)
    m["ev_gp_growth_adj"] = safe(lambda: m["ev_to_gp"] / (1 + max(g, -0.5)) if math.isfinite(g) else np.nan)
    # peak-margin risk: current GM far above own 5y average -> the yield is
    # cyclical; distrust cheapness (the 2008-oil trap on the ERP pages)
    def gm_5y_avg():
        gms = [i["grossProfit"] / i["revenue"] for i in inc_a[:5]
               if i.get("revenue") and i.get("grossProfit") is not None and i["revenue"] > 0]
        return float(np.mean(gms)) if len(gms) >= 3 else np.nan
    _gm5 = safe(gm_5y_avg)
    if is_fin:
        # peak-profitability risk for a bank: ROE above its own 5y average is
        # usually the credit cycle flattering earnings — same trap, ROE frame
        m["peak_margin_risk"] = safe(lambda: max(0.0, m["roic"] - m["roic_5y"])
                                     if math.isfinite(m.get("roic_5y", np.nan)) else np.nan)
    else:
        m["peak_margin_risk"] = safe(lambda: max(0.0, m["gross_margin"] - _gm5))
    # implied expected return — full Grinold-Kroner style decomposition:
    #   R = cycle-normalized cash yield + ROIC-gated growth + annualized multiple drift
    # Growth only counts when funded at adequate returns (ROIC gate); the starting
    # yield is haircut to mid-cycle margins (never boosted); and every name's yield
    # converges to TERMINAL_YIELD over REV_HORIZON years — paying a giant multiple
    # costs annual de-rate, buying a durable fat yield earns re-rate.
    def expected_return():
        y = m.get("cash_engine_yield", np.nan)
        if not math.isfinite(y):
            return np.nan
        if is_fin:
            r0, r5 = m.get("roic", np.nan), m.get("roic_5y", np.nan)
            if math.isfinite(r0) and math.isfinite(r5) and r0 > 0:
                y = y * float(np.clip(r5 / r0, 0.5, 1.0))   # normalize peak-ROE earnings; floor 0.5 (beyond that, distrust the input, not the company)
        else:
            gm, gm5 = m.get("gross_margin", np.nan), _gm5
            if math.isfinite(gm) and math.isfinite(gm5) and gm > 0:
                y = y * float(np.clip(gm5 / gm, 0.5, 1.0))  # cycle-normalize; floor 0.5
        cg = min(m.get("fwd_rev_growth", np.nan), m.get("rev_cagr_3y", np.nan))
        if not math.isfinite(cg):
            cg = m.get("rev_yoy_ttm", np.nan)
        if not math.isfinite(cg):
            return np.nan
        cg = float(np.clip(cg, -0.10, 0.25))
        rg = cap_return_gate()
        gate = float(np.clip((rg if math.isfinite(rg) else 0) / m["cap_bar"], 0, 1))
        # durability: persistence scales how much of the growth we bank —
        # a 100%-persistence grower earns full credit, a flickering one 60%
        pers = m.get("growth_persistence", np.nan)
        dur = 0.6 + 0.4 * pers if math.isfinite(pers) else 0.8
        credit = cg * gate * dur if cg > 0 else cg
        m["midcycle_yield"] = y
        # terminal yield set by franchise quality (through-cycle ROIC as moat
        # proxy): a 40%-ROIC business terminally deserves ~3.5% (29x cash flow),
        # a commodity ~7.5% (13x). The terminal multiple IS the quality question.
        rtc = m.get("roic_tc", np.nan)
        if is_fin:
            # bank terminal multiples are structurally lower (leverage, cyclicality):
            # 12% ROE -> ~14x terminal earnings, 30%+ ROE compounder -> ~22x
            ty = float(np.clip(0.085 - 0.125 * rtc, 0.045, 0.085)) if math.isfinite(rtc) else 0.07
        else:
            ty = float(np.clip(0.075 - 0.10 * rtc, 0.035, 0.075)) if math.isfinite(rtc) else 0.06
        m["terminal_yield"] = ty
        # re-rating gap: TOTAL distance from mid-cycle yield to the quality-set
        # terminal yield, no horizon asserted
        m["rerating_gap"] = float(np.clip(math.log(max(y, 0.005) / ty), -1.5, 1.5))
        return y + credit          # pure annual carry, no fake convergence clock
    m["expected_return"] = safe(expected_return)
    m.setdefault("terminal_yield", np.nan)

    # ------------------------------------------------------------------
    # MAX DOWNSIDE (est.): distance to the best CREDIBLE floor, minus
    # leverage and extension penalties. The discipline factor: "buy only
    # if the downside is contained" becomes computable.
    # ------------------------------------------------------------------
    RF = 0.043   # 10y treasury assumption; override with --rf

    def weighted_median(pairs):
        """pairs: [(estimate, weight)] — robust to any single extreme model,
        respects that methods differ in strength."""
        pairs = sorted(pairs)
        tot = sum(w for _, w in pairs)
        cum = 0.0
        for v, w in pairs:
            cum += w
            if cum >= tot / 2:
                return v
        return pairs[-1][0]

    def max_downside():
        cg = min(m.get("fwd_rev_growth", np.nan), m.get("rev_yoy_ttm", np.nan))
        if not math.isfinite(cg):
            cg = m.get("rev_yoy_ttm", np.nan)
        not_degrowing = math.isfinite(cg) and cg >= -0.02
        models = []   # (downside estimate <= 0, strength weight)
        # FCF vs treasury (w3): the strongest floor — real buyers arrive there
        fy = m.get("fcf_yield", np.nan)
        if not_degrowing and math.isfinite(fy) and fy > 0:
            models.append((min(0.0, fy / RF - 1), 3.0))
        # gross CF vs treasury — THE KEY INDICATOR: a going concern whose
        # pre-capex cash yield matches the 10-year is a bond with growth; large
        # downside then requires the earnings themselves to collapse. Weight 4
        # when the engine gate proves capex discretionary, 3 otherwise (capex
        # uncertainty is one notch of doubt, not a disqualification).
        oy = m.get("ocf_yield", np.nan)
        rg_ = np.nan
        try:
            rg_ = cap_return_gate()
        except Exception:
            pass
        gate_ok = (math.isfinite(rg_) and rg_ > m["cap_bar"]
                   and math.isfinite(m.get("reinvest_intensity", np.nan)) and m["reinvest_intensity"] > 0.05)
        if not_degrowing and math.isfinite(oy) and oy > 0:
            models.append((min(0.0, oy / RF - 1), 4.0 if gate_ok else 3.0))
        m["bond_growth"] = 1 if (not_degrowing and math.isfinite(oy) and oy >= 0.9 * RF) else 0
        # trough multiple (w2): strong but assumption-laden
        y_eng, ty = m.get("cash_engine_yield", np.nan), m.get("terminal_yield", np.nan)
        if math.isfinite(y_eng) and y_eng > 0 and math.isfinite(ty):
            models.append((min(0.0, y_eng / (1.3 * ty) - 1), 2.0))
        # 52-week low (w2): the price the market already cleared once
        lo = m.get("dist_to_52w_low", np.nan)
        if math.isfinite(lo):
            models.append((min(0.0, lo), 2.0))
        # own worst month (w1): a rate-of-fall reference, not a destination
        wm = m.get("worst_month_5y", np.nan)
        if math.isfinite(wm):
            models.append((min(0.0, wm), 1.0))
        # retrace to trend (w1): minimum air for extended stocks
        pv = m.get("price_vs_200d", np.nan)
        if math.isfinite(pv) and pv > 0:
            models.append((-pv, 1.0))
        if len(models) < 2:
            return np.nan
        strong = [v for v, w in models if w >= 2]
        m["worst_strong_downside"] = min(strong) if strong else np.nan
        base = weighted_median(models)
        # balance sheet: adjustment, not a model
        bc = m.get("balance_capacity", np.nan)
        if math.isfinite(bc):
            base += 0.5 * max(0.0, bc) - 0.7 * max(0.0, -bc)
        return float(np.clip(base, -0.80, 0.0))
    m["max_downside"] = safe(max_downside)

    def downside_rating():
        """D1 (most protected) .. D5 (no floor) — buckets on the composite,
        notched for the stock's own character: volatility and dip behavior."""
        mdw = m.get("max_downside", np.nan)
        if not math.isfinite(mdw):
            return np.nan
        if mdw >= -0.10: r = 1
        elif mdw >= -0.20: r = 2
        elif mdw >= -0.35: r = 3
        elif mdw >= -0.55: r = 4
        else: r = 5
        vol = m.get("vol_1y", np.nan)
        wm = m.get("worst_month_5y", np.nan)
        res = m.get("down_resilience", np.nan)
        violent = (math.isfinite(vol) and vol > 0.45) or (math.isfinite(wm) and wm < -0.28)
        calm = math.isfinite(vol) and vol < 0.20 and math.isfinite(res) and res > 0
        if violent: r += 1     # floors get overrun in names that move like this
        if calm: r -= 1        # stable behavior with buyers underneath
        # SAFETY VETO — the rating's main job is identifying D4/D5, so danger
        # promotion is easy and safety promotion is hard: if ANY strong model
        # (weight >= 2) sees -50% or worse, the name cannot rate better than D3
        ws = m.get("worst_strong_downside", np.nan)
        veto = math.isfinite(ws) and ws <= -0.50
        if veto:
            r = max(r, 3)
        # BOND WITH GROWTH: pre-capex cash ~ treasury, growing, and the earnings
        # look real (no peak margins, no WC flattery, no heavy accruals) — the
        # downside case requires an earnings collapse, so cap at D2. The safety
        # veto still wins: a strong model at -50% is collapse evidence.
        suspect = ((math.isfinite(m.get("peak_margin_risk", np.nan)) and m["peak_margin_risk"] > 0.05)
                   or (math.isfinite(m.get("wc_flatter", np.nan)) and m["wc_flatter"] > 0.015)
                   or (math.isfinite(m.get("accruals", np.nan)) and m["accruals"] > 0.05)
                   or (math.isfinite(m.get("guid_net_dir", np.nan)) and m["guid_net_dir"] < -0.3))
        m["earnings_suspect"] = 1 if suspect else 0
        if m.get("bond_growth") == 1 and not suspect and not veto:
            r = min(r, 2)          # true bond with growth: capped at D2
        if suspect:
            r = max(r, 3)          # the floor rests on the earnings, and the
                                   # earnings look engineered — the coupon IS the risk
        return int(np.clip(r, 1, 5))
    m["downside_rating"] = safe(downside_rating)
    m.setdefault("midcycle_yield", np.nan)
    m.setdefault("rerating_gap", np.nan)
    return m


# ----------------------------------------------------------------------------
# Ranking: metric z-scores -> sub scores -> super factors -> quintiles
# (verbatim from the source model, except the opt-in composite noted above)
# ----------------------------------------------------------------------------

FACTOR_SPEC = {
    #  metric               factor  direction (+1 = higher is better)
    "rev_cagr_3y":         ("G", +1),
    "rev_yoy_ttm":         ("G", +1),
    "rev_accel":           ("G", +1),
    "gp_growth_ttm":       ("G", +1),
    "fwd_rev_growth":      ("G", +1),
    "fwd_eps_growth":      ("G", +1),
    "growth_persistence":  ("G", +1),
    "rev_yoy_q0":          ("B", +1),
    "rev_accel_q":         ("B", +1),
    "gm_change_yoy":       ("B", +1),
    "eps_surprise":        ("B", +1),
    "target_chg_3m":       ("B", +1),
    "guid_net_dir":        ("B", +1),
    "gp_to_assets":        ("R", +1),
    "gross_margin":        ("R", +1),
    "roic":                ("R", +1),
    "incremental_roic":    ("R", +1),
    "roiic":               ("R", +1),
    "rule_of_40":          ("R", +1),
    "opex_conversion":     ("R", +1),
    "reinvest_intensity":  ("R", +1),
    "sustainable_growth":  ("R", +1),
    "balance_capacity":    ("R", +1),
    "accruals":            ("Q", -1),
    "bs_bloat":            ("Q", -1),
    "dilution":            ("Q", -1),
    "sbc_to_rev":          ("Q", -1),
    "wc_flatter":          ("Q", -1),
    "mom_12_1":            ("M", +1),
    "trend_smoothness":    ("M", +1),
    "dist_from_high":      ("M", +1),
    "rel_strength":        ("M", +1),
    "down_resilience":     ("M", +1),
    "ev_to_gp":            ("V", -1),
    "expected_return":     ("V", +1),
    "rerating_gap":        ("V", +1),
    "ocf_yield":           ("V", +1),
    "fcf_yield":           ("V", +1),
    "peak_margin_risk":    ("V", -1),
    "max_downside":        ("V", +1),
    "ev_gp_growth_adj":    ("V", -1),
}


# economic sanity bounds: values outside these are data errors or ratio
# pathologies, not information. Applied before any cross-sectional statistics.
BOUNDS = {
    "rev_cagr_3y": (-0.9, 3.0), "rev_yoy_ttm": (-0.9, 3.0), "rev_accel": (-1.5, 1.5),
    "gp_growth_ttm": (-0.9, 3.0), "fwd_rev_growth": (-0.9, 2.0), "fwd_eps_growth": (-0.9, 3.0),
    "rev_yoy_q0": (-0.9, 3.0), "rev_accel_q": (-1.0, 1.0), "gm_change_yoy": (-0.3, 0.3),
    "eps_surprise": (-2.0, 2.0), "target_chg_3m": (-0.8, 0.8),
    "guid_net_dir": (-1.0, 1.0), "guid_cred": (0.0, 1.0), "guid_beat_rate": (0.0, 1.0),
    "gp_to_assets": (0.0, 2.0), "gross_margin": (0.0, 1.0), "roic": (-1.0, 1.5),
    "roic_tc": (-1.0, 1.5), "incremental_roic": (-1.0, 1.0), "roiic": (-1.0, 1.0), "intrinsic_compound": (-0.3, 0.8), "rule_of_40": (-1.0, 1.5),
    "opex_conversion": (-3.0, 5.0), "reinvest_intensity": (0.0, 1.0),
    "sustainable_growth": (-0.3, 0.8), "balance_capacity": (-1.0, 1.0),
    "accruals": (-0.5, 0.5), "bs_bloat": (-1.0, 2.0), "dilution": (-0.3, 0.5),
    "sbc_to_rev": (0.0, 0.6), "wc_flatter": (0.0, 0.25), "dso_change_days": (-90.0, 90.0), "dpo_change_days": (-90.0, 90.0), "mom_12_1": (-0.95, 5.0), "trend_smoothness": (-2.5, 2.5),
    "dist_from_high": (-0.95, 0.0), "rel_strength": (-2.0, 3.0), "down_resilience": (-0.05, 0.05),
    "ev_to_gp": (0.3, 150.0), "ocf_yield": (-0.5, 0.6), "fcf_yield": (-0.5, 0.6),
    "cash_engine_yield": (-0.5, 0.6), "midcycle_yield": (-0.5, 0.6),
    "expected_return": (-0.6, 0.9), "ev_gp_growth_adj": (0.2, 150.0),
    "peak_margin_risk": (0.0, 0.6), "max_downside": (-0.8, 0.0), "price_vs_200d": (-0.8, 3.0), "worst_month_5y": (-0.8, 0.0), "dist_to_52w_low": (-0.9, 0.0), "vol_1y": (0.05, 2.0), "distributed_yield": (-0.05, 0.20), "div_yield": (0.0, 0.15), "buyback_yield": (-0.10, 0.20), "normalized_pe": (1.0, 300.0), "normalized_fcf_yield": (-0.4, 0.5), "above_trend_capex": (-0.3, 0.3), "cash_to_mktcap": (0.0, 1.5),
}


def apply_bounds(df: pd.DataFrame) -> pd.DataFrame:
    for col, (lo, hi) in BOUNDS.items():
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce").clip(lo, hi)
    return df


def winsor_z(s: pd.Series) -> pd.Series:
    """Robust z: median/MAD, clipped at +-3. Immune to fat tails and small
    universes where 2/98 winsorization barely clips anything."""
    s = s.astype(float)
    med = s.median()
    mad = (s - med).abs().median() * 1.4826
    if not mad or not math.isfinite(mad) or mad == 0:
        sd = s.std()
        if not sd or sd == 0:
            return s * 0
        return ((s - s.mean()) / sd).clip(-3, 3)
    return ((s - med) / mad).clip(-3, 3)


def quintile(score: pd.Series) -> pd.Series:
    """1 = best (highest score), 5 = worst. NaN-safe."""
    r = score.rank(pct=True, na_option="keep")
    q = pd.cut(r, bins=[0, .2, .4, .6, .8, 1.0], labels=[5, 4, 3, 2, 1], include_lowest=True)
    return q.astype("float")


MIN_GROUP = 6   # groups smaller than this fall back to universe-wide scoring


def rank_universe(df: pd.DataFrame, neutral=None, weights=None) -> pd.DataFrame:
    """neutral: None | 'sector' | 'industry' | 'country' — z-scores computed
    within each group so a financial is judged against financials, a tech name
    against tech. Quintiles remain universe-wide so ranks stay comparable."""
    df = apply_bounds(df)
    if neutral and neutral in df.columns:
        _uni_z = winsor_z
        def zfun(s):
            out = _uni_z(s)
            for g, idx in df.groupby(neutral).groups.items():
                sub = s.loc[idx]
                if sub.notna().sum() >= MIN_GROUP:
                    out.loc[idx] = _uni_z(sub)
            return out
    else:
        zfun = winsor_z
    # 1) sub-factor scores + quintiles (kept for the detail view).
    #    Scores shrink toward the universe average by sqrt(known/total metrics):
    #    a name known on 1 of 5 metrics is judged mostly average, not on one number.
    sub_scores = {f: [] for f in SUBS}
    for metric, (fac, sign) in FACTOR_SPEC.items():
        if metric in df.columns:
            sub_scores[fac].append(sign * zfun(df[metric]))
    for fac, cols in sub_scores.items():
        if cols:
            zdf = pd.concat(cols, axis=1)
            frac = zdf.notna().sum(axis=1) / len(cols)
            df[f"score_{fac}"] = zdf.mean(axis=1) * np.sqrt(frac)
        else:
            df[f"score_{fac}"] = np.nan
        df[f"q_{fac}"] = quintile(df[f"score_{fac}"])
    # 2) super factors: re-standardize each sub score, blend, renormalizing
    #    blend weights over available subs per row
    def rez(s):
        sd = s.std()
        return (s - s.mean()) / sd if sd and sd > 0 else s * 0
    for sup, blend in SUPERS.items():
        zdf = pd.concat({fac: rez(df[f"score_{fac}"]) for fac in blend}, axis=1)
        wser = pd.Series(blend)
        wsum = zdf.notna().mul(wser, axis=1).sum(axis=1)
        df[f"score_{sup}"] = zdf.mul(wser, axis=1).sum(axis=1, skipna=True) / wsum.replace(0, np.nan)
        df[f"q_{sup}"] = quintile(df[f"score_{sup}"])
    # 3) coverage: how many of the four super factors this name is scored on
    df["coverage"] = df[[f"q_{s}" for s in SUPERS]].notna().sum(axis=1)
    # 4) composite on super quintiles, weights renormalized over available.
    #    OPT-IN ONLY. VQMG ships no default super-factor weights: the composite
    #    and core_rank columns exist only when the caller passes weights=.
    if weights:
        missing = set(weights) - set(SUPERS)
        if missing:
            raise ValueError(f"unknown super factors in weights: {sorted(missing)}; expected {sorted(SUPERS)}")
        qs = df[[f"q_{s}" for s in weights]]
        w = pd.Series({f"q_{s}": weights[s] for s in weights})
        wsum = qs.notna().mul(w, axis=1).sum(axis=1)
        comp = qs.mul(w, axis=1).sum(axis=1, skipna=True) / wsum.replace(0, np.nan)
        comp[df["coverage"] < 2] = np.nan            # one factor alone can't set a rank
        df["composite"] = comp
        df["core_rank"] = quintile(-df["composite"])  # invert so low composite -> rank 1
    return df
