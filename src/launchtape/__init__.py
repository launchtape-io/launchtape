"""Launch Tape: daily files of Solana launch data (https://launchtape.io) in Python.

    import launchtape as lt
    path = lt.sample("pregrad")              # free 2-hour sample, SHA-256 checked
    for row in lt.load(path, types="record", limit=3):
        print(row["sym"], row["instant"])
"""

from ._catalog import DATASETS, Dataset, datasets, schema
from ._download import ChecksumError, LaunchTapeError, cache_dir, fetch, sample
from ._read import frame, load
from ._version import __version__
from .backtest import Rules, backtest, backtest_report

__all__ = [
    "DATASETS", "Dataset", "datasets", "schema",
    "sample", "fetch", "cache_dir", "LaunchTapeError", "ChecksumError",
    "load", "frame", "Rules", "backtest", "backtest_report", "__version__",
]
