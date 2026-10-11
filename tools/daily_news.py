#!/usr/bin/env python3
"""
daily_news.py — the two commands behind the ONE SwingEdge news routine.

    python tools/daily_news.py brief                       # what is due, with everything needed to do it
    python tools/daily_news.py merge findings.json --commit   # check the answer, publish, one commit

The routine used to be two: a market-news digest, and a gainers-news job that ran fifteen web
searches a day. Both now work the same way - a collector with no model gathers headlines, the
routine only reads them and chooses - so they are one routine and it never searches the web.

`brief` decides what is due; the routine does not have to judge it.
  DIGEST   due when the collected market headlines (data/daily/market_news.json) carry no digest for
           their run date. If the scheduled collection has not landed yet today - GitHub's timer can
           be hours late - it collects here first, so the routine never waits on another job.
  GAINERS  due when the newest scan has candidate headlines (data/daily/gainers_candidates.json)
           that nobody has reviewed. If the candidates were never collected, it collects them here.
It prints only what is due, or NOTHING TO DO. Headlines carry short ids: crude:3, HFCL:2.

`merge` takes one file:
    {"digest":  {"headline": "...", "points": [{"t": "...", "why": "...", "refs": ["crude:3"]}], "watch": ["..."]},
     "gainers": {"HFCL": "HFCL:2", "KANOHAR": null}}
Either key may be absent when that part was not due. A digest point that cites no collected
headline is dropped. A gainer's pick must be one of its own candidate ids, or null for "none of
these is about this company's move"; the published headline is the publisher's wording, never the
model's. The reviewed scan is recorded in news.json, so a weekend does not repeat the work.
With --commit: one commit, rebased on main, pushed. The routine writes only market_digest.json and
news.json; the collectors write only market_news.json and gainers_candidates.json - one writer per
file, so the two sides can commit at the same moment without a conflict.

STDLIB ONLY. Nothing here recommends a stock: the digest reports, the picks are links.
"""
import argparse, datetime as dt, io, json, os, subprocess, sys, time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import gainers_news as gn
import market_news as mn
from rs import utf8_stdio

ROOT = mn.ROOT
IST = mn.IST
# One writer per file: collectors write the headlines and the candidates, this routine writes the digest and news.json.
# The two sides can therefore commit at the same moment without a conflict.
MINE = [os.path.relpath(p, ROOT) for p in (mn.DIGEST, gn.NEWS)]
THEIRS = [os.path.relpath(p, ROOT) for p in (mn.OUT, gn.CANDS)]      # change here only when this run had to collect them itself
PER_TOPIC = 8          # headlines per topic shown to the routine; the page keeps twelve


def git(*a):
    return subprocess.run(['git', *a], cwd=ROOT, capture_output=True, text=True)


def state(heal=True, notes=None):
    """(market doc, digest due?, candidates doc, gainers due?) after trying to fetch whatever the
    scheduled jobs have not delivered."""
    notes = notes if notes is not None else []
    today = dt.datetime.now(IST).date().isoformat()
    m = mn.load()
    if heal and (m.get('run_date') or '') < today:
        mn.QUIET = True
        try:
            fresh, _errs = mn.refresh(30)
            if fresh: m = fresh; notes.append(f'market headlines collected here ({sum(len(t["items"]) for t in m["topics"])}) - the scheduled collection had not landed')
            else: notes.append('market headlines: nothing could be collected here; using the file as it is')
        except Exception as e:
            notes.append(f'market headlines: could not collect here ({type(e).__name__}); using the file as it is')
    digest_due = bool(m.get('topics')) and (mn.load_digest().get('for') != m.get('run_date'))
    g = gn.candidates_doc()
    if heal:
        try:
            newest, need = gn.pending()
            if g.get('scan_date') != newest and need:
                got = gn.collect(quiet=True)
                if got: g = got; notes.append(f'gainer candidates collected here for scan {newest}')
        except SystemExit as e:
            notes.append(f'gainers: {e}')
        except Exception as e:
            notes.append(f'gainers: could not collect candidates here ({type(e).__name__})')
    try: vetted = gn.load(gn.NEWS).get('vetted_scan')
    except Exception: vetted = None
    gain_due = bool(g.get('stocks')) and g.get('scan_date') != vetted and any(s.get('cands') for s in g['stocks'].values())
    return m, digest_due, g, gain_due


def cmd_brief(a):
    if not a.no_pull:
        git('pull', '-q', '--rebase', 'origin', 'main')
    notes = []
    m, digest_due, g, gain_due = state(not a.no_collect, notes)
    for n in notes:
        print('note:', n)
    if not digest_due and not gain_due:
        d = mn.load_digest().get('for')
        print(f"NOTHING TO DO - the digest for {m.get('run_date')} is {'written' if d == m.get('run_date') else 'not possible (no headlines)'}"
              f" and the gainers of scan {g.get('scan_date') or '-'} are reviewed or have no candidates. Stop here.")
        return 0
    if digest_due:
        print(f"\n=== DIGEST - due for {m['run_date']} (headlines collected {m['updated']})")
        print('\n'.join(mn.brief_lines(m, PER_TOPIC)))
    if gain_due:
        print(f"\n=== GAINERS - due for scan {g['scan_date']}: for each stock choose the candidate that reports on THAT company, or null")
        for code, s in g['stocks'].items():
            if not s.get('cands'):
                continue
            print(f"{code} | {s['name']} | {s['r1d']:+.1f}%")
            for k, c in enumerate(s['cands']):
                print(f"  {code}:{k + 1} | {c['date'][5:]} | {c['src']} | {c['t']}")
    print('\n=== write findings.json with ' + ' and '.join(k for k, due in (('"digest"', digest_due), ('"gainers"', gain_due)) if due)
          + ', then: python3 tools/daily_news.py merge findings.json --commit')
    return 0


def cmd_merge(a):
    try:
        f = json.load(io.open(a.findings, encoding='utf-8'))
    except Exception as e:
        print(f'STOP - cannot read {a.findings}: {e}'); return 2
    m, digest_due, g, gain_due = state(heal=False)
    done, msg = [], []
    if f.get('digest') and digest_due:
        digest, dropped = mn.make_digest(m, f['digest'])
        if digest:
            mn.save_digest(digest); done.append('digest')
            msg.append(f"digest {digest['for']} ({len(digest['points'])} points)")
            print(f"digest merged: {len(digest['points'])} point(s) for {digest['for']}" + (f'; dropped {len(dropped)}' if dropped else ''))
        else:
            print('digest NOT merged - no point cited a collected headline by id')
        for d in dropped:
            print('  dropped:', d)
    elif f.get('digest'):
        print('digest ignored - it was not due')
    if isinstance(f.get('gainers'), dict) and gain_due:
        news = gn.load(gn.NEWS); news.setdefault('stocks', {})
        kept, none, bad = [], [], []
        for code, s in g['stocks'].items():
            if not s.get('cands'):
                continue
            pick = f['gainers'].get(code)
            if pick in (None, '', 'null'):
                none.append(code); continue
            try:
                c, k = str(pick).split(':'); c = c.strip().upper(); cand = s['cands'][int(k) - 1] if c == code and int(k) >= 1 else None
            except (ValueError, IndexError):
                cand = None
            if not cand:
                bad.append(f'{code}: {pick!r} is not one of its candidates'); continue
            news['stocks'][code] = [{'t': cand['t'], 'src': cand['src'], 'url': cand['url'], 'date': cand['date']}]
            kept.append(code)
        news.update({'vetted_scan': g['scan_date'], 'vetted_at': dt.datetime.now(IST).strftime('%Y-%m-%dT%H:%M%z')})
        if kept:
            news['updated'] = gn.today().isoformat()
        with open(gn.NEWS, 'w', encoding='utf-8') as fh:
            json.dump(news, fh, ensure_ascii=False, indent=2); fh.write('\n')
        done.append('gainers'); msg.append(f'{len(kept)} gainer headline(s), scan {g["scan_date"]}')
        print(f"gainers merged: {len(kept)} headline(s) ({', '.join(kept) or '-'}); none chosen for {len(none)}" + (f'; rejected {len(bad)}' if bad else ''))
        for b in bad:
            print('  rejected:', b)
    elif f.get('gainers'):
        print('gainers ignored - they were not due')
    if not done:
        print('nothing merged'); return 2
    if not a.commit:
        return 0
    git('add', *[p for p in MINE + THEIRS if os.path.exists(os.path.join(ROOT, p))])
    if git('diff', '--cached', '--quiet').returncode == 0:
        print('nothing changed - not committing'); return 0
    r = git('commit', '-q', '-m', 'Daily news: ' + '; '.join(msg))
    if r.returncode:
        print('commit failed:', (r.stderr or r.stdout).strip()); return 1
    for _ in range(3):
        if git('pull', '--rebase', 'origin', 'main').returncode:
            git('rebase', '--abort'); time.sleep(4); continue
        p = git('push', 'origin', 'HEAD:main')
        if p.returncode == 0:
            print('committed and pushed: Daily news: ' + '; '.join(msg)); return 0
        time.sleep(4)
    print('push failed - the commit is local:', (p.stderr or p.stdout).strip()[:300]); return 1


def main():
    utf8_stdio()
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sp = ap.add_subparsers(dest='cmd', required=True)
    b = sp.add_parser('brief'); b.add_argument('--no-pull', action='store_true'); b.add_argument('--no-collect', action='store_true'); b.set_defaults(fn=cmd_brief)
    g = sp.add_parser('merge'); g.add_argument('findings'); g.add_argument('--commit', action='store_true'); g.set_defaults(fn=cmd_merge)
    a = ap.parse_args()
    return a.fn(a)


if __name__ == '__main__':
    sys.exit(main())
