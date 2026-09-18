#!/usr/bin/env python3
"""
fetch_prices.py — refresh the Swing Edge price cards from public, key-free daily feeds.

    python scripts/swing_edge/fetch_prices.py                       # every module, today only
    python scripts/swing_edge/fetch_prices.py --module commodities  # one module
    python scripts/swing_edge/fetch_prices.py --backfill 90         # also write 90 days of history rows
    python scripts/swing_edge/fetch_prices.py --dry-run             # fetch + score, write nothing
    python scripts/swing_edge/fetch_prices.py --verify-symbols      # check every NSE code in config.json resolves
    python scripts/swing_edge/fetch_prices.py --label close         # tag the run (auto: UTC<08 morning, else close)

Writes data/swing_edge/<module>_latest.json and upserts data/swing_edge/<module>_history.csv.
Re-running for the same date is idempotent. A source that fails never aborts the run; the
field carries its last good value forward with stale=true. One log line per field:
    field=<id> source=<source> status=<ok|stale|fallback|roll|failed|manual>
"""
import argparse, datetime as dt, importlib, os, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common  # noqa: E402

MODULES = ['commodities']


def run_label(arg):
    if arg and arg != 'auto':
        return arg
    return 'morning' if dt.datetime.now(dt.timezone.utc).hour < 8 else 'close'


def verify_symbols(fetcher, cfg):
    bad = 0
    for name in MODULES:
        mod = importlib.import_module(name)
        for code in mod.stock_symbols(cfg):
            sym = code + '.NS'
            try:
                series = common.parse_yahoo_chart(fetcher.get(common.yahoo_url(sym, '5d')))
                common.log(code, f'yahoo:{sym}', 'ok', f'last={series[-1][0]} close={series[-1][1]}')
            except Exception as e:  # noqa: BLE001
                bad += 1
                common.log(code, f'yahoo:{sym}', 'failed', str(e)[:100])
    print(f'symbols: {"all resolved" if not bad else f"{bad} unresolved"}')
    return 0 if not bad else 1


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--module', choices=MODULES, action='append', help='module to refresh (default: all)')
    ap.add_argument('--backfill', type=int, default=0, metavar='DAYS', help='write DAYS of history rows, not just today')
    ap.add_argument('--dry-run', action='store_true', help='fetch and score but write nothing')
    ap.add_argument('--label', default='auto', help='run label: auto | morning | close | manual')
    ap.add_argument('--out', default=common.DATA_DIR, help='output folder (default data/swing_edge)')
    ap.add_argument('--date', default=None, help='treat this YYYY-MM-DD as today (tests)')
    ap.add_argument('--verify-symbols', action='store_true', help='check config stock codes on Yahoo and exit')
    ap.add_argument('--timeout', type=int, default=15)
    a = ap.parse_args(argv)

    cfg = common.load_config()
    fetcher = common.Fetcher(timeout=a.timeout)
    if a.verify_symbols:
        return verify_symbols(fetcher, cfg)

    today = dt.date.fromisoformat(a.date) if a.date else dt.datetime.now(dt.timezone.utc).date()
    label = run_label(a.label)
    overrides_all = common.read_json(os.path.join(a.out, 'manual_overrides.json'), {}) or {}
    exit_code = 0
    for name in (a.module or MODULES):
        mod = importlib.import_module(name)
        print(f'\n=== {name} · {today} · run={label}{" · DRY RUN" if a.dry_run else ""}')
        latest_path = os.path.join(a.out, f'{name}_latest.json')
        hist_path = os.path.join(a.out, f'{name}_history.csv')
        history = common.read_history(hist_path)
        prev = common.read_json(latest_path)
        doc, updates = mod.build(fetcher, cfg, history, overrides_all.get(name) or {}, prev, today, label, a.backfill)
        c = doc['counts']
        print(f'--- {name}: ok={c["ok"]} fallback={c["fallback"]} stale={c["stale"]} failed={c["failed"]} '
              f'roll={c["roll"]} manual={c["manual"]} data_date={doc["data_date"]}')
        if c['failed'] and c['failed'] >= max(1, len(doc['indicators']) // 2):
            exit_code = 2  # most fields dead: keep the files, but make the run visibly red
        if a.dry_run:
            continue
        for iid, days in updates.items():
            history.setdefault(iid, {}).update(days)
        common.write_history(hist_path, history)
        common.write_json(latest_path, doc)
        print(f'wrote {os.path.relpath(latest_path, common.ROOT)} and {os.path.relpath(hist_path, common.ROOT)}')
    return exit_code


if __name__ == '__main__':
    sys.exit(main())
