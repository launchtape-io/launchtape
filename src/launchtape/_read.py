"""Reading the files: one JSON object per line, gzipped (plain .jsonl works too)."""

from __future__ import annotations

import gzip
import json
import os
from pathlib import Path
from typing import IO, Any, Dict, FrozenSet, Iterable, Iterator, Optional, Union

from ._catalog import DATASETS
from ._download import sample

Source = Union[str, "os.PathLike[str]"]
Types = Union[str, Iterable[str], None]

# Every row is written as {"type":"<type>",...}: when filtering, a row of another type is skipped without parsing it.
_PREFIX = '{"type":"'


def _resolve(src: Source) -> Path:
    if isinstance(src, os.PathLike):
        return Path(src)
    if not isinstance(src, str):
        raise TypeError(f"expected a path or a dataset name, got {type(src).__name__}")
    path = Path(src).expanduser()
    if path.is_file():
        return path
    if src in DATASETS:
        return sample(src)
    raise FileNotFoundError(f"{src!r} is neither a file nor a dataset name ({', '.join(DATASETS)})")


def _types(types: Types) -> Optional[FrozenSet[str]]:
    if types is None:
        return None
    wanted = frozenset([types] if isinstance(types, str) else types)
    if not all(isinstance(t, str) for t in wanted):
        raise TypeError("types must be a string or a list of strings")
    return wanted


def _open(path: Path) -> IO[str]:
    with open(path, "rb") as f:
        gz = f.read(2) == b"\x1f\x8b"
    return gzip.open(path, "rt", encoding="utf-8") if gz else open(path, "rt", encoding="utf-8")


def load(path_or_dataset: Source, types: Types = None, limit: Optional[int] = None) -> Iterator[Dict[str, Any]]:
    """Stream the rows of a file as dicts.

    path_or_dataset: a .jsonl.gz path, or a dataset name to read its free sample (downloaded on first use).
    types: keep only rows whose `type` is this string or one of these strings, e.g. types=["record", "outcome"].
    limit: stop after this many rows (counted after the type filter).
    """
    wanted = _types(types)
    if limit is not None and (not isinstance(limit, int) or limit < 0):
        raise ValueError(f"limit must be a whole number >= 0, got {limit!r}")
    path = _resolve(path_or_dataset)
    return _rows(path, wanted, limit)


def _rows(path: Path, wanted: Optional[FrozenSet[str]], limit: Optional[int]) -> Iterator[Dict[str, Any]]:
    if limit == 0:
        return
    n = 0
    with _open(path) as f:
        for lineno, line in enumerate(f, 1):
            if wanted is not None and line.startswith(_PREFIX):
                end = line.find('"', len(_PREFIX))
                ty = line[len(_PREFIX):end]
                if end > 0 and "\\" not in ty and ty not in wanted:
                    continue
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except ValueError as e:
                raise ValueError(f"{path}: line {lineno} is not valid JSON ({e})") from None
            if wanted is not None and (not isinstance(row, dict) or row.get("type") not in wanted):
                continue
            yield row
            n += 1
            if limit is not None and n >= limit:
                return


def frame(path_or_dataset: Source, types: Types = None, limit: Optional[int] = None):
    """The rows as a pandas DataFrame (same arguments as load). Nested fields stay as dicts/lists in their column.

    A full gradtrades day is millions of rows: pass types= and/or limit= to keep it to what you need.
    """
    try:
        import pandas as pd
    except ImportError as e:
        raise ImportError("launchtape.frame() needs pandas: pip install 'launchtape[pandas]'") from e
    return pd.DataFrame(list(load(path_or_dataset, types=types, limit=limit)))
