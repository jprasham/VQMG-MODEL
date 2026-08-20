"""
vqmg.cli — command line front end.

    vqmg NVDA MSFT ASML -o vqmg.csv
    vqmg --tickers-file tickers.txt --neutral sector -o vqmg.csv
    vqmg NVDA MSFT --weights GRW=0.3,MOM=0.3,QLT=0.3,VAL=0.1 -o vqmg.csv
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import __version__, run
from .fmp import DEFAULT_CACHE_DIR, FMPError


def _parse_weights(s: str | None):
    if not s:
        return None
    out = {}
    for part in s.replace(";", ",").split(","):
        part = part.strip()
        if not part:
            continue
        if "=" not in part:
            raise SystemExit(f"bad --weights item {part!r}; expected e.g. GRW=0.25")
        k, v = part.split("=", 1)
        out[k.strip().upper()] = float(v)
    return out


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="vqmg", description="VQMG model — score a list of tickers.")
    p.add_argument("tickers", nargs="*", help="ticker symbols, space or comma separated")
    p.add_argument("--tickers-file", help="file with one ticker per line ('#' comments allowed)")
    p.add_argument("-o", "--out", default="vqmg_output.csv", help="output CSV path")
    p.add_argument("--neutral", choices=["sector", "industry", "country"],
                   help="compute z-scores within groups instead of universe-wide")
    p.add_argument("--weights", help="opt-in composite, e.g. GRW=0.25,MOM=0.4,QLT=0.25,VAL=0.1")
    p.add_argument("--workers", type=int, default=4, help="parallel tickers (default 4)")
    p.add_argument("--refresh", action="store_true", help="ignore the 24h cache")
    p.add_argument("--cache-dir", default=str(DEFAULT_CACHE_DIR))
    p.add_argument("--version", action="version", version=f"vqmg {__version__}")
    a = p.parse_args(argv)

    tickers = list(a.tickers)
    if a.tickers_file:
        text = Path(a.tickers_file).read_text()
        tickers += [t for t in text.replace(",", " ").split()
                    if t.strip() and not t.startswith("#")]
    if not tickers:
        p.error("give tickers on the command line or via --tickers-file")

    try:
        df = run(tickers, neutral=a.neutral, weights=_parse_weights(a.weights),
                 refresh=a.refresh, workers=a.workers, cache_dir=a.cache_dir)
    except FMPError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2

    out = Path(a.out)
    if out.parent and str(out.parent) not in ("", "."):
        out.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(out, index=False)

    print(f"{len(df)} of {len(set(t.upper() for t in tickers))} tickers scored "
          f"-> {out} ({len(df.columns)} columns)")
    for e in df.attrs.get("errors", []):
        print(f"  [no data] {e['symbol']}: {e['error']}", file=sys.stderr)
    if len(df) < 25:
        print("  [note] score_* and q_* columns are cross-sectional; with fewer than "
              "~25 tickers the quintiles are noisy. The raw variables are unaffected.",
              file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
