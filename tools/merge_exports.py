#!/usr/bin/env python3
"""
merge_exports.py — union two screener exports so we stop having to choose between
coverage and column depth.

    python tools/merge_exports.py --base broad.csv --overlay returns.csv --out merged.csv

WHY THIS EXISTS

screener.in appends AT MOST TWO query terms to an export as columns, in query order.
Terms beyond the second still filter but never appear. Verified 2026-09-09 by swapping
"Return over 6months" and "Return over 1year" in the query and re-exporting: the first
two arrived, the third did not. (The /user/columns/ EDIT COLUMNS panel changes only the
on-screen table, not the export — also tested, also 2026-09-09.)

That forced a choice:

  plain "Market Capitalization > 1000"      1559 names, but no r6m/r1y, so the RS
                                            composite silently falls back to
                                            0.5*r3m + 0.3*r1m + 0.2*r1w
  ...AND the two return filters             1501 names with r6m and r1y, so the IBD
                                            quarterly weighting engages — but ~58 names
                                            without a full year of history disappear,
                                            and 12 of those pass every template check
                                            except RS

Neither is right. A name with no 1-year history should be VISIBLE and simply fall back to
the short-window RS — not vanish. So: pull both, and union them here.

HOW THE UNION WORKS

  * rows   — every row in either file, keyed by NSE code (BSE code if NSE is blank).
             The base file decides the row set; overlay-only rows are appended.
  * columns— the union of both headers, base order first.
  * values — the base wins for any column both files carry, because both were captured in
             the same session and the base is the wider, less filtered pull. The overlay
             only ever FILLS BLANKS and supplies columns the base does not have.

Codes are matched exactly and never by name — "Foseco Crucible (India)" is not Foseco
India, and a fuzzy join here would silently corrupt a scan.

The result is a single CSV that build_scan.py reads normally: full coverage, with r6m and
r1y present on the names that have them and absent on the ones that genuinely do not.
"""
import argparse, csv, io, os, sys

CODE_COLS = ('NSE Code', 'BSE Code')


def read(path):
    with io.open(path, encoding='utf-8-sig', newline='') as fh:
        rows = list(csv.reader(fh))
    if not rows:
        raise SystemExit(f'{path}: empty')
    return rows[0], rows[1:]


def key_of(header, row):
    """Exact code match only. NSE first, BSE as fallback; never the name."""
    idx = {h: i for i, h in enumerate(header)}
    for c in CODE_COLS:
        i = idx.get(c)
        if i is not None and i < len(row):
            v = (row[i] or '').strip().upper()
            if v:
                return v
    return None


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--base', required=True, help='the wider pull; decides the row set and wins on shared columns')
    ap.add_argument('--overlay', required=True, help='the narrower pull carrying the extra columns')
    ap.add_argument('--out', required=True)
    a = ap.parse_args()

    bh, brows = read(a.base)
    oh, orows = read(a.overlay)

    # column union, base order preserved
    header = list(bh) + [c for c in oh if c not in bh]
    added = [c for c in oh if c not in bh]

    bmap = {}
    for r in brows:
        k = key_of(bh, r)
        if k:
            bmap[k] = r
    omap = {}
    for r in orows:
        k = key_of(oh, r)
        if k:
            omap[k] = r

    bidx = {h: i for i, h in enumerate(bh)}
    oidx = {h: i for i, h in enumerate(oh)}

    def cell(row, idx, col):
        i = idx.get(col)
        return (row[i] if i is not None and i < len(row) else '') or ''

    out, filled, appended = [], 0, 0
    for k, br in bmap.items():
        orow = omap.get(k)
        line = []
        for col in header:
            v = cell(br, bidx, col)
            if not v and orow is not None:                 # overlay only ever fills blanks
                v2 = cell(orow, oidx, col)
                if v2:
                    v = v2
                    filled += 1
            line.append(v)
        out.append(line)

    for k, orow in omap.items():                            # names the base did not have
        if k in bmap:
            continue
        out.append([cell(orow, oidx, col) for col in header])
        appended += 1

    with io.open(a.out, 'w', encoding='utf-8', newline='') as fh:
        w = csv.writer(fh)
        w.writerow(header)
        w.writerows(out)

    both = len(set(bmap) & set(omap))
    print(f'merged -> {a.out}')
    print(f'  base    {len(bmap):>5} rows x {len(bh)} cols   ({os.path.basename(a.base)})')
    print(f'  overlay {len(omap):>5} rows x {len(oh)} cols   ({os.path.basename(a.overlay)})')
    print(f'  result  {len(out):>5} rows x {len(header)} cols')
    print(f'  columns added by the overlay : {", ".join(added) if added else "none"}')
    print(f'  rows present in both         : {both}')
    print(f'  rows only in the overlay     : {appended}')
    print(f'  blank cells filled from it   : {filled}')
    only_base = len(bmap) - both
    if only_base:
        print(f'  rows the overlay lacks       : {only_base} — these keep the short-window RS fallback, '
              f'which is correct: they have no 1-year history to weight.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
