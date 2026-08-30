#!/usr/bin/env python3
"""
eod.py — the whole end-of-day refresh behind one command, so the button in the app has
something short to hand you.

    python tools/eod.py                  # fetch, rebuild everything, show what changed
    python tools/eod.py --push           # ...and commit + push it
    python tools/eod.py --dry-run        # show the plan, fetch nothing
    python tools/eod.py --skip-fetch     # rebuild from the export already on disk

WHAT IT RUNS, IN ORDER

  1. fetch_screener.py   pull today's screener export and build data/scans/<date>.json
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


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--date', help='scan date (default: today)')
    ap.add_argument('--push', action='store_true', help='commit and push the refreshed data')
    ap.add_argument('--dry-run', action='store_true', help='print the plan, run nothing')
    ap.add_argument('--skip-fetch', action='store_true', help='rebuild from the export already on disk')
    ap.add_argument('--top', type=int, default=20, help='how many shortlist candidates (default 20)')
    a = ap.parse_args()

    date = a.date or dt.date.today().isoformat()
    py = sys.executable
    print(f'SwingEdge EOD refresh — {date}')
    before_date, before = snapshot()

    steps = []
    if not a.skip_fetch:
        steps.append(('fetch screener export + build scan',
                      [py, os.path.join('tools', 'fetch_screener.py'), '--date', date]))
    steps += [
        ('rank the daily shortlist', [py, os.path.join('tools', 'build_shortlist.py'), '--date', date,
                                      '--top', str(a.top), '--quiet']),
        ('append to the Stage 2 journal', [py, os.path.join('tools', 'build_s2history.py'), '--quiet']),
    ]
    for name, cmd in steps:
        if not run(name, cmd, a.dry_run):
            print(f'\nSTOPPED at "{name}". Nothing further was rebuilt, so the app still shows '
                  f'the last good data rather than a half-refreshed mix.')
            if 'fetch' in name:
                print('If this was the cookie: log in to screener.in, copy the sessionid cookie into '
                      '.secrets/screener_cookie.txt, and run again.')
            return 1

    if a.dry_run:
        print('\n--dry-run: nothing was fetched, built, or pushed.')
        return 0

    # ---- what actually changed -------------------------------------------------
    after_date, after = snapshot()
    print('\n--- what changed')
    if before_date == after_date:
        print(f'    scan date unchanged ({after_date}) — the export may be the same one as last run')
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
               f'Built by tools/eod.py.\n\nCo-Authored-By: Claude Opus 5 <noreply@anthropic.com>\n')
        subprocess.run(['git', 'commit', '-m', msg], cwd=ROOT)
        subprocess.run(['git', 'pull', '--rebase', 'origin', 'main'], cwd=ROOT)
        if subprocess.run(['git', 'push', 'origin', 'main'], cwd=ROOT).returncode:
            print('    push failed — the commit is local, try again')
            return 1
        print('    pushed — live in about a minute')
    else:
        print('\nRun again with --push to commit and publish.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
