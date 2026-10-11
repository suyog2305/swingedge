#!/usr/bin/env python3
"""Assemble a research report from the shared head/foot template + a body fragment.

    python tools/report/build_report.py <body.html> <out-name> "<Footer line>" "<Sources line>"

The head (fonts + the full house stylesheet) and footer shell are lifted verbatim from the
existing reports, so a new report is visually identical to the ones already in the library.

Refuses a body that quotes the owner's position data (share counts, average cost, "Position in
your book" rows, holdings-screenshot notes): the site is public.
"""
import io, os, re, sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
TPL  = os.path.join(ROOT, 'tools', 'report')
# Keep in step with PRIVATE in publish.py.
PRIVATE = re.compile(r'avg\.? cost|Position in your book|holdings screenshot'
                     r'|\d+ shares? (?:·|&middot;|&#183;)|you (?:hold|own) [\d,]+ shares?', re.I)

def main():
    if len(sys.argv) < 5 or sys.argv[1] in ('-h', '--help'):
        print(__doc__); return 0 if len(sys.argv) > 1 and sys.argv[1] in ('-h', '--help') else 2
    body_path, out_name, foot_line, sources = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4]
    body = io.open(body_path, encoding='utf-8').read()
    hits = [m.group(0) for m in PRIVATE.finditer(body)]
    if hits:
        print(f'REFUSED - {body_path} quotes private position data: ' + '; '.join(hits[:5]))
        print('Holdings are private and the site is public. Remove those lines and build again.')
        return 1
    title = body.split('<!--TITLE:', 1)[1].split('-->', 1)[0].strip()
    head = io.open(os.path.join(TPL, 'head.html'), encoding='utf-8').read().replace('{{TITLE}}', title)
    foot = ('<footer class="footer">\n'
            f'  <div>{foot_line}</div>\n'
            f'  <div>Data Sources: {sources}</div>\n'
            '  <div style="margin-top:6px;font-size:10px;opacity:.5">FOR INFORMATIONAL PURPOSES ONLY. '
            'NOT INVESTMENT ADVICE.</div>\n</footer>\n\n</body>\n</html>\n')
    out = os.path.join(ROOT, 'library', 'research', out_name)
    io.open(out, 'w', encoding='utf-8').write(head + '\n<body>\n' + body + '\n' + foot)
    print(f'{out_name}  {os.path.getsize(out):,} bytes')

if __name__ == '__main__':
    sys.exit(main())
