#!/usr/bin/env python3
"""
check_page.py — is index.html still a page that loads? Run it after every edit to index.html.

    python tools/check_page.py

Four checks, none of which needs a browser:
  syntax     every inline <script> parses (node --check)
  load       the script runs top to bottom in a stubbed page (tools/smoke_toplevel.js). This is the
             one a syntax check cannot do: a `const` used above its own declaration parses fine and
             then stops the whole app at load. It happened on 3 Oct 2026.
  ids        no element id is used twice in the static markup
  nav        every sidebar entry has its section, and every show()/seGo() target exists
Exit code 0 only when all four pass. Needs node on the PATH.
"""
import io, os, re, subprocess, sys, tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PAGE = os.path.join(ROOT, 'index.html')


def main():
    s = io.open(PAGE, encoding='utf-8').read()
    ok = True
    for k, m in enumerate(re.finditer(r'<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>', s, flags=re.S)):
        body = m.group(1)
        if len(body) < 200:
            continue
        f = tempfile.NamedTemporaryFile('w', suffix='.js', delete=False, encoding='utf-8'); f.write(body); f.close()
        r = subprocess.run(['node', '--check', f.name], capture_output=True, text=True)
        line0 = s[:m.start(1)].count('\n')
        os.unlink(f.name)
        if r.returncode:
            ok = False
            print('syntax: FAIL\n' + re.sub(re.escape(f.name) + r':(\d+)', lambda mm: f'index.html:{int(mm.group(1)) + line0}', r.stderr)[:1500])
        else:
            print(f'syntax: ok ({len(body):,} chars, from line {line0 + 1})')
    r = subprocess.run(['node', os.path.join(ROOT, 'tools', 'smoke_toplevel.js'), PAGE], capture_output=True, text=True)
    print('load:   ' + (r.stdout.strip() or r.stderr.strip()[:400])); ok &= r.returncode == 0
    static = s[:s.find('<script>')] if '<script>' in s else s
    ids = re.findall(r'\bid="([^"${}]+)"', static)
    dup = sorted({i for i in ids if ids.count(i) > 1})
    print('ids:    ' + ('ok' if not dup else 'DUPLICATED ' + ', '.join(dup))); ok &= not dup
    navs, secs = set(re.findall(r'id="nav-([a-z0-9]+)"', s)), set(re.findall(r'id="sec-([a-z0-9]+)"', s))
    alias = set(re.findall(r"(\w+): '\w+'", (re.search(r'const NAV_ALIAS = \{([^}]*)\}', s) or [None, ''])[1]))
    shown = (set(re.findall(r"show\('([a-z0-9]+)'\)", s)) | set(re.findall(r"seGo\('([a-z0-9]+)'", s))) - alias
    bad = sorted(navs - secs) + sorted(t for t in shown if t not in secs)
    print('nav:    ' + ('ok' if not bad else 'NO SECTION FOR ' + ', '.join(bad)) + (f'  (pages without a sidebar entry: {", ".join(sorted(secs - navs))})' if secs - navs else ''))
    ok &= not bad
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main())
