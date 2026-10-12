"""Command line: launchtape datasets | sample | head | schema | fetch."""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import List, Optional

from ._catalog import DATASETS, datasets, schema
from ._download import LaunchTapeError, fetch, sample
from ._read import load
from ._version import __version__


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="launchtape", description="Launch Tape: daily files of Solana launch data "
                                "(https://launchtape.io).")
    p.add_argument("--version", action="version", version=f"launchtape {__version__}")
    sub = p.add_subparsers(dest="cmd", metavar="command", required=True)

    sub.add_parser("datasets", help="list the datasets")

    s = sub.add_parser("sample", help="download a free 2-hour sample (SHA-256 checked) and print its path")
    s.add_argument("dataset", nargs="+", choices=DATASETS)

    s = sub.add_parser("head", help="print the first rows of a dataset's sample, or of a file, as JSON lines")
    s.add_argument("source", help="a dataset name (reads its free sample) or a .jsonl.gz path")
    s.add_argument("-n", type=int, default=10, help="number of rows (default 10)")
    s.add_argument("-t", "--type", dest="types", action="append", metavar="TYPE",
                   help="only rows of this type (repeat for several)")

    s = sub.add_parser("schema", help="print a dataset's schema (Markdown)")
    s.add_argument("dataset", choices=DATASETS)

    s = sub.add_parser("fetch", help="download a paid day file from your delivery link and print its path")
    s.add_argument("link", help="https://launchtape.io/dl/<token> from your delivery mail, or the token")
    s.add_argument("dataset")
    s.add_argument("day", help="UTC day, YYYY-MM-DD")
    s.add_argument("-o", "--dest", help="folder or .gz file path (default: current folder)")
    return p


def _run(a: argparse.Namespace) -> int:
    progress = sys.stderr.isatty()
    if a.cmd == "datasets":
        width = max(len(d.name) for d in datasets())
        for d in datasets():
            print(f"{d.name:<{width}}  {d.description}")
    elif a.cmd == "sample":
        for ds in a.dataset:
            print(sample(ds, progress=progress))
    elif a.cmd == "head":
        if a.n < 0:
            raise ValueError("-n must be 0 or more")
        src = a.source
        if src in DATASETS and not os.path.isfile(src):
            src = sample(src, progress=progress)  # the free sample, with download progress on a terminal
        for row in load(src, types=a.types, limit=a.n):
            print(json.dumps(row, separators=(",", ":")))
    elif a.cmd == "schema":
        sys.stdout.write(schema(a.dataset))
    elif a.cmd == "fetch":
        print(fetch(a.link, a.dataset, a.day, a.dest, progress=progress))
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
    except (LaunchTapeError, ValueError, OSError, EOFError) as e:
        print(f"launchtape: error: {e}", file=sys.stderr)
        return 1
