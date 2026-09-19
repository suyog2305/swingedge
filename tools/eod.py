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
import argparse, datetime as dt, glob, io, json, os, subprocess, sys

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


S2_GLOB = 'Stage 2*.xls*'


def week_ending(d):
    """The Friday on or before d. A Stage 2 file stamped on the weekend describes the week that
    has just closed, so it applies to that Friday's scan and every daily scan after it."""
    return d - dt.timedelta(days=(d.weekday() - 4) % 7)


S2_UNREADABLE = []   # (file, why) - reported by the caller; a silent None cost three scans their list


def stage2_asof(path):
    """The list's own date: the newest 'Earliest Date' in the file. The provider stamps that
    week's New Additions with the day the list was cut, so this needs no filename parsing.

    STDLIB ONLY, on purpose. This first shipped with `import openpyxl`, which lives in the user
    site-packages - and had been pip-installed from inside a packaged desktop app, whose writes to
    %APPDATA% Windows redirects to a private per-app copy. Every shell launched from that app saw
    the package; Task Scheduler did not, the ImportError was swallowed, and the 16 and 18 Sep 2026
    scans were built with no Stage 2 list while every manual test passed. build_weekly.read_xlsx
    reads .xlsx with zipfile + ElementTree, as the rest of the pipeline always has. Test changes to
    anything the scheduled task runs with `python -s` (user site disabled)."""
    try:
        here = os.path.dirname(os.path.abspath(__file__))
        if here not in sys.path:
            sys.path.insert(0, here)
        from build_weekly import read_table, norm
        from build_scan import excel_date
        headers, rows = read_table(path)
        names = {norm(x) for x in ('earliest date', 'entry date', 'since', 'first date', 'date')}
        i = next((k for k, h in enumerate(headers) if norm(h) in names), None)
        if i is None:
            S2_UNREADABLE.append((os.path.basename(path), 'no "Earliest Date" column'))
            return None
        best = max((d for d in (excel_date(r[i]) for r in rows if i < len(r)) if d), default=None)
        if not best:
            S2_UNREADABLE.append((os.path.basename(path), 'no dates in the "Earliest Date" column'))
            return None
        return dt.date.fromisoformat(best)
    except BaseException as e:                        # SystemExit too: read_table raises it on an empty file
        if isinstance(e, KeyboardInterrupt):
            raise
        S2_UNREADABLE.append((os.path.basename(path), f'{type(e).__name__}: {e}'))
        return None


def default_scan_date(now=None):
    """The session whose close screener is serving right now, as YYYY-MM-DD - or None while a
    session is live. The task is set to start as soon as possible after a missed run, so a 20:00
    run that the machine slept through fires at the next logon, usually the next morning. Before
    the open screener still serves the previous session's fully refreshed close; dating that
    "today" wrote 17 Sep 2026's close as 2026-09-18 at 08:05, and the true 17 Sep close was lost
    when the 20:00 run rebuilt the file. While the market is open the prices are live and partial,
    and no date is right. Holidays need no calendar here: the same-data guard sees screener still
    serving the last close and writes nothing. Times are local; this machine runs on IST."""
    now = now or dt.datetime.now()
    d, t = now.date(), now.time()

    def prev_weekday(x):
        x -= dt.timedelta(days=1)
        while x.weekday() >= 5:
            x -= dt.timedelta(days=1)
        return x
    if d.weekday() >= 5 or t < dt.time(9, 10):
        return prev_weekday(d).isoformat()
    if t < dt.time(15, 40):
        return None
    return d.isoformat()


def newest_stage2_file(date):
    """The most recent provider list whose week had closed by `date`: (path, asof, dirs searched).
    The weekly Stage 2 file is downloaded by hand, so this looks where it lands: exports/ in the
    repo (gitignored), the folder above the repo, and stage2_dir from tools/screener_config.json
    when set. A list is never attached to a scan dated before the week it describes."""
    dirs = [os.path.join(ROOT, 'exports'), os.path.dirname(ROOT)]
    try:
        cfg = json.load(io.open(os.path.join(ROOT, 'tools', 'screener_config.json'), encoding='utf-8'))
        if cfg.get('stage2_dir'):
            dirs.insert(0, cfg['stage2_dir'])
    except Exception:
        pass
    D = dt.date.fromisoformat(date)
    cands = []
    for dd in dirs:
        for p in glob.glob(os.path.join(dd, S2_GLOB)):
            if os.path.basename(p).startswith('~$'):
                continue                                   # Excel lock file
            asof = stage2_asof(p)
            if asof and week_ending(asof) <= D:
                cands.append((asof, p))
    if not cands:
        return None, None, dirs
    asof, p = max(cands)
    return p, asof, dirs


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--date', help='scan date (default: today)')
    ap.add_argument('--push', action='store_true', help='commit and push the refreshed data')
    ap.add_argument('--dry-run', action='store_true', help='print the plan, run nothing')
    ap.add_argument('--skip-fetch', action='store_true', help='rebuild from the merged export already on disk')
    ap.add_argument('--top', type=int, default=20, help='how many shortlist candidates (default 20)')
    ap.add_argument('--stage2', help='attach this Stage 2 file instead of auto-picking the newest one on disk')
    ap.add_argument('--no-stage2', action='store_true', help='build the scan without any Stage 2 list')
    a = ap.parse_args()

    date = a.date or default_scan_date()
    if date is None:
        print('SwingEdge EOD refresh — the market is open')
        print('    screener is serving live, partial prices, so a scan built now would be neither '
              "yesterday's close nor today's. Nothing fetched, nothing written. Run after 15:40 IST, "
              'or pass --date YYYY-MM-DD to override.')
        return 3
    py = sys.executable
    print(f'SwingEdge EOD refresh — {date}'
          + ('' if a.date or date == dt.date.today().isoformat()
             else f"   (run on {dt.date.today().isoformat()}: screener is still serving that session's close)"))
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

    # ---- the weekly Stage 2 list rides along on every daily scan --------------------------
    # The provider's list is weekly and downloaded by hand; the scan is daily. Without this, a
    # scan built on a Tuesday carried no list at all (9-11 Sep 2026 did not), so the RS Screen
    # showed no Stage 2 marks, the shortlist scored nobody for being on it, and the tracker was
    # blank. Now the newest list whose week has closed is attached, and its own date is stamped
    # on the scan so the app can show how old it is.
    build_cmd = [py, os.path.join('tools', 'build_scan.py'), '--date', date, '--screener', merged,
                 '--screen-name', 'Market cap > 1000 (broad + returns + volume, merged)']
    print('\n--- Stage 2 list')
    if a.no_stage2:
        print('    --no-stage2: the scan is built without a provider list')
    else:
        s2file, s2asof, s2dirs = (a.stage2, stage2_asof(a.stage2), []) if a.stage2 else newest_stage2_file(date)
        if s2file:
            we = week_ending(s2asof) if s2asof else None
            age = (dt.date.fromisoformat(date) - we).days if we else None
            print(f'    {os.path.relpath(s2file, ROOT) if s2file.startswith(ROOT) else s2file}')
            print(f'    list of {s2asof or "?"} for the week ending {we or "?"}'
                  + (f'; {age} day(s) since that week closed' if age is not None else ''))
            if age is not None and age > 9:
                print('    NOTE: more than a week old. The list is weekly - download the newest file and it '
                      'will be picked up automatically.')
            build_cmd += ['--stage2', s2file]
        else:
            print('    none found in: ' + '; '.join(s2dirs))
        for f, why in S2_UNREADABLE:
            print(f'    COULD NOT READ {f}: {why}')
        if not s2file:
            print('    the scan is built without a Stage 2 list (save the weekly file as "Stage 2_<date>.xlsx" '
                  'in one of those folders)')

    if not go([
        ('build the scan', build_cmd),
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
    try:
        sc = json.load(io.open(os.path.join(ROOT, 'data', 'scans', f'{after_date}.json'), encoding='utf-8'))
        n_s2 = len(sc.get('stage2') or [])
        print(f'    Stage 2 list: {n_s2} names from {(sc.get("sources") or {}).get("stage2") or "-"}'
              + (f' (list of {sc["stage2_asof"]})' if sc.get('stage2_asof') else ''))
    except Exception:
        pass
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
