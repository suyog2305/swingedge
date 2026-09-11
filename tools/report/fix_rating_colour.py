#!/usr/bin/env python3
"""
fix_rating_colour.py — make the masthead's Rating cell carry the colour of the call, not the
colour of its position.

    python tools/report/fix_rating_colour.py            # apply to head.html, every body, every published report
    python tools/report/fix_rating_colour.py --check    # report only

THE DEFECT

Every report's masthead has three cells: Rating / Bull Case / Bear Case, styled by the classes
buy / bull / bear. Those classes were only ever positional — the first cell was always "buy",
so a report rated "Hold" or "Avoid" rendered its call on a green background. 43 of 54
published reports did this. The Research Desk card was right all along, because index.html
derives its colour from the rating text (rdRatingCls); only the report header lied.

THE RULE

Ported verbatim from rdRatingCls in index.html, so the header and the card can never disagree:

    sell   if the text contains sell / reduce / avoid / underweight
    hold   if it STARTS with hold / neutral, or contains "below" / "at cmp" / a standalone "hold"
    buy    if it contains buy / accumulate / add / overweight
    hold   otherwise

"Accumulate on Dips · Hold at CMP" is therefore hold, and "Accumulate / Buy on Dips" is buy —
the same reading the card gives.

Published reports are self-contained pages with their own <style>, so the two new classes are
injected into each file next to the existing .rating-cell.buy rule; head.html gets them so
every future report inherits them. Idempotent: running it twice changes nothing the second time.
"""
import argparse, glob, html, io, os, re, sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
HEAD = os.path.join(ROOT, 'tools', 'report', 'head.html')
BODIES = glob.glob(os.path.join(ROOT, 'tools', 'report', 'bodies', '*.html'))
PUBLISHED = glob.glob(os.path.join(ROOT, 'library', 'research', '*.html'))

CELL = re.compile(r'(<div class="rating-cell )(\w+)("><div class="rating-tag">Rating</div><div class="rating-val">)([^<]*)(</div>)')
BUY_CSS = '.rating-cell.buy  { background: rgba(26,107,53,.4); }'
NEW_CSS = ('.rating-cell.hold { background: rgba(200,151,58,.38); }\n'
           '  .rating-cell.sell { background: rgba(160,16,32,.5); }')


def rating_cls(text):
    """rdRatingCls, ported."""
    s = html.unescape(text or '').lower()
    if re.search(r'(sell|reduce|avoid|underweight)', s): return 'sell'
    if re.match(r'^\s*(hold|neutral)', s) or re.search(r'\b(below|at cmp)\b', s) or re.search(r'\bhold\b', s): return 'hold'
    if re.search(r'(buy|accumulate|add|overweight)', s): return 'buy'
    return 'hold'


def fix(path, inject_css, apply):
    s = io.open(path, encoding='utf-8', newline='').read()
    nl = '\r\n' if '\r\n' in s else '\n'
    changes = []
    m = CELL.search(s)
    if m:
        want = rating_cls(m.group(4))
        if m.group(2) != want:
            changes.append(f'{m.group(2)} -> {want}  | {html.unescape(m.group(4))[:60]}')
            s = s[:m.start()] + m.group(1) + want + m.group(3) + m.group(4) + m.group(5) + s[m.end():]
    if inject_css and BUY_CSS in s and '.rating-cell.hold' not in s:
        s = s.replace(BUY_CSS, BUY_CSS + nl + '  ' + NEW_CSS.replace('\n', nl), 1)
        changes.append('css: +hold +sell')
    if changes and apply:
        io.open(path, 'w', encoding='utf-8', newline='').write(s)
    return changes


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--check', action='store_true', help='report what would change; write nothing')
    a = ap.parse_args()
    apply = not a.check

    # 1) the template every future report is built from
    ch = fix(HEAD, inject_css=True, apply=apply)
    print(f"head.html            {'; '.join(ch) or 'already carries hold/sell'}")

    # 2) bodies (no <style> of their own — class only) and 3) published pages (class + embedded css)
    tally = {'sell': 0, 'hold': 0, 'buy': 0}
    touched = 0
    for group, files, css in (('bodies', BODIES, False), ('published', PUBLISHED, True)):
        n = 0
        for p in sorted(files):
            ch = fix(p, inject_css=css, apply=apply)
            if ch:
                n += 1
                for c in ch:
                    if '->' in c:
                        tally[c.split('->')[1].split('|')[0].strip()] += 1
                        print(f'  {os.path.basename(p):<28} {c}')
        print(f'{group:<20} {n} file(s) {"changed" if apply else "would change"} of {len(files)}')
        touched += n
    print(f'\nrating cells recoloured: {tally}')
    print('write nothing (--check)' if a.check else f'{touched} file(s) written')
    return 0


if __name__ == '__main__':
    sys.exit(main())
