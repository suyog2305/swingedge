#!/usr/bin/env python3
"""
rs.py — the one Relative Strength engine shared by the tools. The app carries the same code in
index.html (seRsMode / seRsQuarters / seScore / sePercentiles / seRank) — keep the two in step.

RS rating, in one breath: take each stock's average quarterly return over the last year, counting
the most recent quarter twice, and rank that against every other stock in the scan on a 1–99
scale. 99 = stronger than 99% of the market. This is IBD / MarketSmith's RS Rating — the number
O'Neil's CAN SLIM (RS >= 80) and Minervini's Trend Template (RS >= 70) are written around.

The four quarters, chained out of the cumulative 3-month, 6-month and 1-year returns the scan carries:
    Q1 = last 3 months      weight 0.4   "what has it done lately" counts double
    Q2 = months 4-6         weight 0.2   (1 + 6m) / (1 + 3m) - 1
    Q3 = Q4 = months 7-12   weight 0.2   the (1 + 1y) / (1 + 6m) stretch split into two equal quarters
    each                                 (geometric), since the export has no finer history
    score = 0.4·Q1 + 0.2·Q2 + 0.2·Q3 + 0.2·Q4  — a weighted average quarterly return, IBD's 40/20/20/20
A name with a shorter history (a recent listing) is scored on the quarters it has, with the
weights rescaled so its number stays on the same one-quarter scale as everyone else. Nothing is
rated on less than one quarter of history. A scan with no 6m/1y columns at all falls back to a
3m/1m/1w blend for every row.
"""
import math, sys

Q1_W, Q2_W, Q34_W = 0.4, 0.2, 0.2
BLEND = (('r3m', 0.5), ('r1m', 0.3), ('r1w', 0.2))


def num(v):
    try:
        f = float(v); return None if f != f else f
    except (TypeError, ValueError): return None


def rs_mode(rows):
    """'ibd' when the export carries the 6m and 1y columns, else 'blend'."""
    for r in rows:
        if None not in (num(r.get('r3m')), num(r.get('r6m')), num(r.get('r1y'))): return 'ibd'
    return 'blend'


def quarters(r):
    """(Q1, Q2, Q3=Q4) in percent, chained out of the cumulative returns; None where history runs out."""
    r3, r6, r1y = num(r.get('r3m')), num(r.get('r6m')), num(r.get('r1y'))
    if r3 is None: return None
    q2 = q34 = None
    if r6 is not None:
        q2 = ((1 + r6 / 100) / (1 + r3 / 100) - 1) * 100
        if r1y is not None:
            h2 = (1 + r1y / 100) / (1 + r6 / 100)
            if h2 > 0: q34 = (h2 ** 0.5 - 1) * 100
    return {'q1': r3, 'q2': q2, 'q34': q34}


def momentum(r, mode='ibd'):
    """The raw composite one stock is ranked on: a weighted average quarterly return, in percent."""
    if mode == 'ibd':
        q = quarters(r)
        if q is None: return None
        s, w = Q1_W * q['q1'], Q1_W
        if q['q2'] is not None: s += Q2_W * q['q2']; w += Q2_W
        if q['q34'] is not None: s += 2 * Q34_W * q['q34']; w += 2 * Q34_W
        return s / w
    s = w = 0.0
    for k, wt in BLEND:
        v = num(r.get(k))
        if v is not None: s += wt * v; w += wt
    return s / w if w else None


def percentiles(rows, key, out):
    """Average-rank percentile of rows[key], scaled 1..99 into rows[out]; unscored rows get None."""
    rk = sorted((r for r in rows if num(r.get(key)) is not None), key=lambda r: r[key])
    for r in rows: r[out] = None
    n = len(rk); i = 0
    while i < n:
        j = i
        while j + 1 < n and rk[j + 1][key] == rk[i][key]: j += 1
        pct = 99 if n == 1 else math.floor(1 + 98 * ((i + j) / 2) / (n - 1) + 0.5)   # half up, as the app's Math.round (Python's round() goes to even)
        for k in range(i, j + 1): rk[k][out] = pct
        i = j + 1


def rate(rows, out='_rs', score_key='_m'):
    """Score and rank a universe in place (rows[score_key], rows[out]); returns the mode used."""
    mode = rs_mode(rows)
    for r in rows: r[score_key] = momentum(r, mode)
    percentiles(rows, score_key, out)
    return mode


def utf8_stdio():
    """Console output that survives Windows: stdout/stderr captured with cp1252 (the scheduled task, Claude's
    Bash tool, a pipe) raise UnicodeEncodeError on the first '→', 'Δ' or '₹'. Every tool calls this first thing in
    main(), before argparse can print a --help. Lives here because every tool can already import rs, and a
    tools/_io.py cannot be imported at all: CPython's own built-in `_io` module wins over the file."""
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding='utf-8', errors='replace')
        except Exception:
            pass


def trend_label(passed, total):
    """The trend template as words: every check = Very strong, one miss = Strong, 2-3 = Weak."""
    miss = total - passed
    return 'Very strong' if miss == 0 else 'Strong' if miss == 1 else 'Weak' if miss <= 3 else 'Very weak'
