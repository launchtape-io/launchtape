"""Command line: launchtape datasets | sample | head | schema | fetch | backtest."""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import List, Optional

from ._catalog import DATASETS, datasets, schema
from ._download import FORMATS, LaunchTapeError, fetch, sample
from ._read import load
from ._version import __version__


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="launchtape", description="Launch Tape: daily files of Solana launch data "
                                "(https://launchtape.io).")
    p.add_argument("--version", action="version", version=f"launchtape {__version__}")
    sub = p.add_subparsers(dest="cmd", metavar="command", required=True)

    sub.add_parser("datasets", help="list the datasets")

    s = sub.add_parser("sample", help="download a dataset's free sample, if the site lists one (SHA-256 checked), and print its path")
    s.add_argument("dataset", nargs="+", choices=DATASETS)
    s.add_argument("--format", choices=FORMATS, default="jsonl", help="jsonl (gzipped JSON lines, default) or parquet")

    s = sub.add_parser("head", help="print the first rows of a dataset's sample, or of a file, as JSON lines")
    s.add_argument("source", help="a dataset name (reads its free sample) or a .jsonl.gz / .parquet path")
    s.add_argument("-n", type=int, default=10, help="number of rows (default 10)")
    s.add_argument("-t", "--type", dest="types", action="append", metavar="TYPE",
                   help="only rows of this type (repeat for several)")
    s.add_argument("--format", choices=FORMATS, default="jsonl",
                   help="which sample a dataset name reads: jsonl (default) or parquet (needs pyarrow); a path is read as it is")

    s = sub.add_parser("schema", help="print a dataset's schema (Markdown)")
    s.add_argument("dataset", choices=DATASETS)

    s = sub.add_parser("fetch", help="download a paid day file from your delivery link and print its path")
    s.add_argument("link", help="https://launchtape.io/dl/<token> from your delivery mail, or the token")
    s.add_argument("dataset")
    s.add_argument("day", help="UTC day, YYYY-MM-DD")
    s.add_argument("-o", "--dest", help="folder or file path (default: current folder)")
    s.add_argument("--format", choices=FORMATS, default="jsonl", help="jsonl (gzipped JSON lines, default) or parquet")

    s = sub.add_parser("backtest", help="replay your trading rules on pump.fun graduates with honest fills and print "
                       "a report", description="Replay simple rules (buy N s after graduation; sell at a time, a stop "
                       "or a take-profit) coin by coin on gradtrades, filling every signal at the end of the next "
                       "slot with fees, slippage and your size's price impact. Point --gradtrades at a day file (one day is $10 at "
                       "https://launchtape.io/?plan=day1#buy). Same steps as the prompt on https://launchtape.io/test.")
    s.add_argument("--rules", metavar="FILE", help="a rules JSON file (see --template); default: the example rules")
    s.add_argument("--gradtrades", metavar="PATH", help="a gradtrades .jsonl.gz or .parquet file, e.g. a paid day "
                   "(default: the free sample, when the site lists one)")
    s.add_argument("--pregrad", metavar="PATH", help="the pregrad file of the same day, for filters and coin names "
                   "(default: found next to --gradtrades)")
    s.add_argument("--graduates", metavar="PATH", help="the graduates CSV of the same day (graduates-<day>.csv from "
                   "your download page), for the first-10-minutes numbers (default: found next to --gradtrades)")
    s.add_argument("--json", action="store_true", help="print the full result (every trade) as JSON instead")
    s.add_argument("--template", action="store_true", help="print the example rules as JSON to edit, and exit")
    return p


def _run(a: argparse.Namespace) -> int:
    progress = sys.stderr.isatty()
    if a.cmd == "datasets":
        width = max(len(d.name) for d in datasets())
        for d in datasets():
            print(f"{d.name:<{width}}  {d.description}")
    elif a.cmd == "sample":
        for ds in a.dataset:
            print(sample(ds, progress=progress, format=a.format))
    elif a.cmd == "head":
        if a.n < 0:
            raise ValueError("-n must be 0 or more")
        src = a.source
        if src in DATASETS and not os.path.isfile(src):
            src = sample(src, progress=progress, format=a.format)  # the free sample, with download progress on a terminal
        for row in load(src, types=a.types, limit=a.n):
            print(json.dumps(row, separators=(",", ":")))
    elif a.cmd == "schema":
        sys.stdout.write(schema(a.dataset))
    elif a.cmd == "fetch":
        print(fetch(a.link, a.dataset, a.day, a.dest, progress=progress, format=a.format))
    elif a.cmd == "backtest":
        from .backtest import backtest, backtest_report, rules_template
        if a.template:
            sys.stdout.write(rules_template())
            return 0
        res = backtest(a.rules, a.gradtrades, a.pregrad, a.graduates, progress=progress)
        sys.stdout.write(json.dumps(res, indent=1) + "\n" if a.json else backtest_report(res))
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    args = _parser().parse_args(argv)
    try:
        return _run(args)
    except BrokenPipeError:  # e.g. `launchtape head gradtrades | head -1`
        os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())
        return 0
    except KeyboardInterrupt:
        return 130
    except (LaunchTapeError, ValueError, OSError, EOFError, ImportError) as e:
        print(f"launchtape: error: {e}", file=sys.stderr)
        return 1
