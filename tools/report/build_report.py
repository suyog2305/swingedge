#!/usr/bin/env python3
"""Assemble a research report from the shared head/foot template + a body fragment.

    python tools/report/build_report.py <body.html> <out-name> "<Footer line>" "<Sources line>"

The head (fonts + the full house stylesheet) and footer shell are lifted verbatim from the
existing reports, so a new report is visually identical to the ones already in the library.
"""
import io, os, sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
TPL  = os.path.join(ROOT, 'tools', 'report')

def main():
    body_path, out_name, foot_line, sources = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4]
    body = io.open(body_path, encoding='utf-8').read()
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
    main()
