import os, sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SCRIPTS = os.path.join(ROOT, 'scripts', 'swing_edge')
FIXTURES = os.path.join(ROOT, 'tests', 'fixtures', 'swing_edge')
if SCRIPTS not in sys.path:
    sys.path.insert(0, SCRIPTS)


def fixture(name):
    with open(os.path.join(FIXTURES, name), encoding='utf-8') as fh:
        return fh.read()
