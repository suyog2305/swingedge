#!/usr/bin/env python3
"""
publish.py — the last step of the report loop: verify, then push. Never the other way round.

    python tools/report/publish.py bodalchem gipcl        # verify + register + commit + push
    python tools/report/publish.py --all                  # every report in the index
    python tools/report/publish.py gipcl --dry-run        # check only, touch nothing
    python tools/report/publish.py gipcl --no-push        # commit locally, do not push

Closes the loop that starts in the app: Edge -> Shortlist -> tick up to 5 -> Copy brief ->
generate the reports per tools/report/REPORT_SPEC.md -> publish them with this.

WHAT IT ENFORCES

  1. The report file exists and its HTML is balanced (unclosed tags break the iframe silently).
  2. It is registered in library/research/index.json.
  3. tools/report/verify_numbers.py reports ZERO mismatches for it.

Only then does it stage, commit and push. A real mismatch aborts before anything is written to
git, so a report with a wrong number cannot reach the site through this path. Stale-price DRIFT
in older reports is not a mismatch and does not block.

The commit message lists what was published and states that the cross-check passed, so the
verification is recorded in history rather than merely having happened.
"""
import argparse, io, json, os, re, subprocess, sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
RESEARCH = os.path.join(ROOT, 'library', 'research')
INDEX = os.path.join(RESEARCH, 'index.json')
PAIRED = ('section', 'div', 'table', 'tr', 'tbody', 'thead')


def run(cmd, **kw):
    env = {**os.environ, 'PYTHONIOENCODING': 'utf-8'}
    return subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, env=env, **kw)


def load_index():
    with io.open(INDEX, encoding='utf-8') as fh:
        return json.load(fh)


def resolve(names, idx):
    """Accept a report id, an NSE code, or a slug; return matching index entries."""
    out, missing = [], []
    by_id = {r['id'].lower(): r for r in idx['reports']}
    by_code = {}
    for r in idx['reports']:
        if r.get('code'):
            by_code.setdefault(r['code'].lower(), r)
    for n in names:
        k = n.lower().strip()
        hit = by_id.get(k) or by_code.get(k) or next(
            (r for r in idx['reports'] if r['id'].split('-')[0].lower() == k), None)
        (out if hit else missing).append(hit or n)
    return out, missing


def check_html(path):
    """Unbalanced tags break the iframe render without any error, so catch them here."""
    s = io.open(path, encoding='utf-8').read()
    bad = []
    for t in PAIRED:
        o = len(re.findall(r'<' + t + r'\b', s))
        c = len(re.findall(r'</' + t + '>', s))
        if o != c:
            bad.append(f'{t} {o}/{c}')
    return bad


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('names', nargs='*', help='report ids or NSE codes')
    ap.add_argument('--all', action='store_true', help='publish every report in the index')
    ap.add_argument('--dry-run', action='store_true', help='verify only; do not touch git')
    ap.add_argument('--no-push', action='store_true', help='commit locally but do not push')
    a = ap.parse_args()

    if not a.names and not a.all:
        ap.error('name at least one report, or pass --all')

    idx = load_index()
    if a.all:
        entries, missing = idx['reports'], []
    else:
        entries, missing = resolve(a.names, idx)
    if missing:
        print('NOT IN THE INDEX — register with tools/build_research.py first:')
        for m in missing:
            print('  -', m)
        return 2
    if not entries:
        print('nothing to publish'); return 2

    print(f'Publishing {len(entries)} report(s)\n')

    # ---- 1. files exist and their HTML is balanced ---------------------------
    problems = []
    for r in entries:
        path = os.path.join(RESEARCH, r['file'].split('/')[-1])
        if not os.path.exists(path):
            problems.append(f"{r['id']}: file missing ({path})"); continue
        bad = check_html(path)
        if bad:
            problems.append(f"{r['id']}: unbalanced HTML — {', '.join(bad)}")
        print(f"  {'ok ' if not bad else 'BAD'} {r['id']:<26} {os.path.getsize(path):>8,} bytes")
    if problems:
        print('\nABORTED — fix these before publishing:')
        for p in problems:
            print('  !!', p)
        return 1

    # ---- 2. cross-check every quoted figure ---------------------------------
    codes = [r['code'] for r in entries if r.get('code')]
    print('\nCross-check (tools/report/verify_numbers.py):')
    v = run([sys.executable, os.path.join('tools', 'report', 'verify_numbers.py')] + codes)
    out = v.stdout or v.stderr
    for line in out.splitlines():
        if line.strip() and ('checks' in line or 'mismatch' in line or '!!' in line or 'not checkable' in line):
            print('  ' + line)
    m = re.search(r'(\d+) reports checked, (\d+) mismatch', out)
    mismatches = int(m.group(2)) if m else (0 if v.returncode == 0 else 1)
    if mismatches:
        print(f'\nABORTED — {mismatches} mismatch(es). Nothing staged, nothing pushed.')
        print('Fix the report, or the scan, and run again.')
        return 1
    print('  -> 0 mismatches')

    if a.dry_run:
        print('\n--dry-run: verified, nothing written.')
        return 0

    # ---- 3. only now, git ----------------------------------------------------
    files = [os.path.join('library', 'research', r['file'].split('/')[-1]) for r in entries]
    files.append(os.path.join('library', 'research', 'index.json'))
    for slug in {r['id'].split('-')[0] for r in entries}:
        body = os.path.join('tools', 'report', 'bodies', slug + '.html')
        if os.path.exists(os.path.join(ROOT, body)):
            files.append(body)

    add = run(['git', 'add'] + files)
    if add.returncode:
        print('git add failed:', add.stderr.strip()); return 1
    if not run(['git', 'diff', '--cached', '--quiet']).returncode:
        print('\nNothing changed — already published.'); return 0

    listed = '\n'.join(f"  {r.get('code') or r['id']:<12} {(r.get('rating') or '')[:60]}" for r in entries)
    msg = (f"Publish {len(entries)} research report(s)\n\n{listed}\n\n"
           f"Cross-checked with tools/report/verify_numbers.py before pushing: "
           f"0 mismatches across {len(codes)} scan-verifiable report(s). Built to "
           f"tools/report/REPORT_SPEC.md.\n\n"
           f"Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>\n")
    c = run(['git', 'commit', '-m', msg])
    if c.returncode:
        print('git commit failed:', (c.stderr or c.stdout).strip()); return 1
    print('\ncommitted')

    if a.no_push:
        print('--no-push: not pushed.'); return 0
    run(['git', 'pull', '--rebase', 'origin', 'main'])
    pu = run(['git', 'push', 'origin', 'main'])
    if pu.returncode:
        print('git push failed:', (pu.stderr or pu.stdout).strip()); return 1
    print('pushed — live on GitHub Pages in about a minute.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
