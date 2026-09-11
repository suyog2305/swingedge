#!/usr/bin/env python3
"""
eod.py — the whole end-of-day refresh behind one command, so the button in the app has
something short to hand you.

    python tools/eod.py                  # fetch, rebuild everything, show what changed
    python tools/eod.py --push           # ...and commit + push it
    python tools/eod.py --dry-run        # show the plan, fetch nothing
    python tools/eod.py --skip-fetch     # rebuild from the export already on disk

WHAT IT RUNS, IN ORDER

  1. fetch_screener.py   three pulls of the same universe (broad / returns / volume),
     merge_exports.py    unioned by exact code because screener exports at most two
                         extra columns per pull; then build_scan.py -> data/scans/<date>.json
  2. build_shortlist.py  rank the universe into the 20 daily candidates
  3. build_s2history.py  append today to the Stage 2 journal

Each step is reported pass/fail with its own line. A failed step STOPS the run — a shortlist
built on yesterday's scan looks perfectly fine and is silently wrong, which is exactly the
failure worth refusing.

THE COOKIE

Step 1 needs your screener.in session cookie, in .secrets/screener_cookie.txt or the
SCREENER_COOKIE environment variable (both gitignored). Without it screener.in redirects to
its login page and this stops at step 1 with a clear message — nothing partial is written.
That is also why this is a button you press while you are at the screen, rather than a
scheduled job: the cookie expires, and when it does somebody has to notice.
"""
import argparse, datetime as dt, io, json, os, subprocess, sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SHORTLIST = os.path.join(ROOT, 'data', 'daily', 'shortlist.json')


def run(step, cmd, dry):
    print(f'\n--- {step}')
    print('    ' + ' '.join(cmd[1:] if cmd[0] == sys.executable else cmd))
    if dry:
        print('    (dry-run, not executed)')
        return True
    env = {**os.environ, 'PYTHONIOENCODING': 'utf-8'}
    p = subprocess.run(cmd, cwd=ROOT, env=env)
    ok = p.returncode == 0
    print(f'    -> {"ok" if ok else "FAILED (exit %d)" % p.returncode}')
    return ok


def snapshot():
    """What the shortlist says right now, so the run can report what actually moved."""
    try:
        with io.open(SHORTLIST, encoding='utf-8') as fh:
            d = json.load(fh)
        return d.get('scan_date'), [r.get('code') for r in d.get('rows', [])]
    except Exception:
        return None, []


def same_data_as_previous(merged_csv, date):
    """Non-trading-day guard. screener serves the last close on weekends and holidays, so a
    pull on such a day reproduces yesterday's prices under today's date and quietly pads the
    archive with duplicates. Compare the merged export against the newest scan carrying a
    DIFFERENT date; if every shared price matches, there is nothing new."""
    import csv, glob
    try:
        rows = list(csv.reader(io.open(merged_csv, encoding='utf-8-sig', newline='')))
    except OSError:
        return False, 0, None
    h = {c: k for k, c in enumerate(rows[0])}
    ci, pi = h.get('NSE Code'), h.get('Current Price')
    if ci is None or pi is None:
        return False, 0, None
    today = {r[ci].upper(): r[pi] for r in rows[1:] if len(r) > max(ci, pi) and r[ci]}
    prev = None
    for path in sorted(glob.glob(os.path.join(ROOT, 'data', 'scans', '20*.json')), reverse=True):
        try:
            d = json.load(io.open(path, encoding='utf-8'))
        except Exception:
            continue
        if d.get('date') != date and len(d.get('universe') or []) >= 500:
            prev = d; break
    if not prev:
        return False, 0, None
    old = {r['code'].upper(): r.get('price') for r in prev['universe'] if r.get('code')}
    common = [c for c in today if c in old]
    if len(common) < 200:
        return False, len(common), prev.get('date')

    def same(a, b):
        try: return abs(float(a) - float(b)) < 1e-6
        except (TypeError, ValueError): return False
    matches = sum(1 for c in common if same(today[c], old[c]))
    return matches == len(common), len(common), prev.get('date')


def stale_partial_refresh(merged_csv, date):
    """screener.in refreshes its fields in STAGES after the close: prices, day/week returns and the
    52-week distances first; moving averages, 3/6-month returns and volume later in the evening.
    A pull in between captures today prices against YESTERDAY averages, so every "200-DMA rising"
    check fails and the trend template empties. Seen 11 Sep 2026 at 15:40: prices changed on 98.8%
    of names, dma200 unchanged on 99.9%, template passes 388 -> 1. Compare the merged export with
    the newest scan of a different date and refuse to build if the averages have not moved."""
    import csv, glob
    try:
        rows = list(csv.reader(io.open(merged_csv, encoding='utf-8-sig', newline='')))
    except OSError:
        return False, {}
    h = {c: k for k, c in enumerate(rows[0])}
    ci, pi, di = h.get('NSE Code'), h.get('Current Price'), h.get('DMA 200')
    if None in (ci, pi, di):
        return False, {}
    today = {r[ci].upper(): (r[pi], r[di]) for r in rows[1:] if len(r) > max(ci, pi, di) and r[ci]}
    prev = None
    for path in sorted(glob.glob(os.path.join(ROOT, 'data', 'scans', '20*.json')), reverse=True):
        try:
            d = json.load(io.open(path, encoding='utf-8'))
        except Exception:
            continue
        if d.get('date') != date and len(d.get('universe') or []) >= 500:
            prev = d; break
    if not prev:
        return False, {}
    old = {r['code'].upper(): (r.get('price'), r.get('dma200')) for r in prev['universe'] if r.get('code')}
    common = [c for c in today if c in old]
    if len(common) < 200:
        return False, {}

    def f(v):
        try: return round(float(v), 4)
        except (TypeError, ValueError): return None
    price_changed = sum(1 for c in common if f(today[c][0]) is not None and f(today[c][0]) != f(old[c][0])) / len(common)
    dma_same = sum(1 for c in common if f(today[c][1]) is not None and f(today[c][1]) == f(old[c][1])) / len(common)
    return (price_changed > 0.5 and dma_same > 0.95), dict(price_changed=price_changed, dma_same=dma_same,
                                                            prev=prev.get('date'), n=len(common))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--date', help='scan date (default: today)')
    ap.add_argument('--push', action='store_true', help='commit and push the refreshed data')
    ap.add_argument('--dry-run', action='store_true', help='print the plan, run nothing')
    ap.add_argument('--skip-fetch', action='store_true', help='rebuild from the merged export already on disk')
    ap.add_argument('--top', type=int, default=20, help='how many shortlist candidates (default 20)')
    a = ap.parse_args()

    date = a.date or dt.date.today().isoformat()
    py = sys.executable
    print(f'SwingEdge EOD refresh — {date}')
    before_date, before = snapshot()

    # screener.in appends AT MOST TWO query terms as export columns, in query order — verified
    # 2026-09-09 by swapping them. So no single pull gives coverage AND the extra columns. Pull
    # three variants of the same universe and union them: the broad query decides who is
    # visible; the others only add columns. merge_exports matches on exact code, never on name.
    BROAD   = 'Market Capitalization > 1000'
    RETURNS = BROAD + ' AND Return over 6months > -1000 AND Return over 1year > -1000'
    VOLUME  = BROAD + ' AND Volume > 0 AND Volume 1month average > 0'
    E = lambda sfx: os.path.join('exports', f'screener_{date}{sfx}.csv')
    base, returns, volume, m1, merged = E('_base'), E('_returns'), E('_volume'), E('_m1'), E('_merged')
    fetch = [py, os.path.join('tools', 'fetch_screener.py'), '--date', date, '--no-build']
    merge = [py, os.path.join('tools', 'merge_exports.py')]

    def go(steps):
        for name, cmd in steps:
            if not run(name, cmd, a.dry_run):
                print(f'\nSTOPPED at "{name}". Nothing further was rebuilt, so the app still shows '
                      f'the last good data rather than a half-refreshed mix.')
                if 'pull' in name:
                    print('If this was the cookie: log in to screener.in, copy the sessionid cookie into '
                          '.secrets/screener_cookie.txt, and run again.')
                return False
        return True

    if not a.skip_fetch:
        if not go([
            ('pull the broad universe (decides coverage)',        fetch + ['--query', BROAD,   '--suffix', '_base']),
            ('pull the returns variant (r6m + r1y)',              fetch + ['--query', RETURNS, '--suffix', '_returns']),
            ('pull the volume variant (volume + 1-month average)', fetch + ['--query', VOLUME,  '--suffix', '_volume']),
            ('union: broad + returns',                            merge + ['--base', base, '--overlay', returns, '--out', m1]),
            ('union: + volume',                                   merge + ['--base', m1,   '--overlay', volume,  '--out', merged]),
        ]):
            return 1
    if not a.dry_run:
        same, n, prev = same_data_as_previous(os.path.join(ROOT, merged), date)
        if same:
            print('\n--- no new data')
            print(f'    all {n} shared prices match the {prev} scan exactly - screener is still serving '
                  f'that close, so today is a non-trading day. Nothing written, nothing committed.')
            return 0
        stale, det = stale_partial_refresh(os.path.join(ROOT, merged), date)
        if stale:
            print('\n--- screener has not finished its end-of-day refresh')
            print(f'    prices changed on {det["price_changed"]:.0%} of {det["n"]} shared names, but the 200-DMA is '
                  f'unchanged on {det["dma_same"]:.0%} against the {det["prev"]} scan.')
            print('    A scan built now would carry today prices against yesterday averages and fail every '
                  '"200-DMA rising" check. Nothing written, nothing committed. Run again later in the evening.')
            return 2

    if not go([
        ('build the scan', [py, os.path.join('tools', 'build_scan.py'), '--date', date, '--screener', merged,
                            '--screen-name', 'Market cap > 1000 (broad + returns + volume, merged)']),
        ('rank the daily shortlist', [py, os.path.join('tools', 'build_shortlist.py'), '--date', date,
                                      '--top', str(a.top), '--quiet']),
        ('append to the Stage 2 journal', [py, os.path.join('tools', 'build_s2history.py'), '--quiet']),
    ]):
        return 1

    if a.dry_run:
        print('\n--dry-run: nothing was fetched, built, or pushed.')
        return 0

    # ---- what actually changed -------------------------------------------------
    after_date, after = snapshot()
    print('\n--- what changed')
    if before_date == after_date:
        print(f'    scan date unchanged ({after_date}) — rebuilt in place')
    else:
        print(f'    scan {before_date or "(none)"} -> {after_date}')
    joined = [c for c in after if c not in before]
    dropped = [c for c in before if c not in after]
    print(f'    shortlist: {len(after)} names, {len(joined)} new, {len(dropped)} gone')
    if joined:
        print('      in : ' + ', '.join(joined))
    if dropped:
        print('      out: ' + ', '.join(dropped))

    if a.push:
        print('\n--- publish')
        subprocess.run(['git', 'add', 'data'], cwd=ROOT)
        if subprocess.run(['git', 'diff', '--cached', '--quiet'], cwd=ROOT).returncode == 0:
            print('    nothing changed — not committing')
            return 0
        msg = (f'EOD data refresh {after_date}\n\n'
               f'Shortlist {len(after)} names ({len(joined)} new, {len(dropped)} gone). '
               f'Built by tools/eod.py: three screener pulls unioned by exact code.\n\n'
               f'Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>\n')
        if subprocess.run(['git', 'commit', '-q', '-m', msg], cwd=ROOT).returncode:
            print('    commit failed'); return 1
        # A failed rebase must never leave the repo mid-rebase: tomorrow's run would then fail
        # at git add with no obvious cause. Abort, keep the local commit, and say so.
        if subprocess.run(['git', 'pull', '--rebase', 'origin', 'main'], cwd=ROOT).returncode:
            subprocess.run(['git', 'rebase', '--abort'], cwd=ROOT)
            print('    rebase failed — aborted so the repo is not left mid-rebase. The commit is local; '
                  'pull by hand and push.')
            return 1
        if subprocess.run(['git', 'push', 'origin', 'main'], cwd=ROOT).returncode:
            print('    push failed — the commit is local, try again')
            return 1
        print('    pushed — live in about a minute')
    else:
        print('\nRun again with --push to commit and publish.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
