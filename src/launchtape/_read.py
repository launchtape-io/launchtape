"""Reading the files: one JSON object per line, gzipped (plain .jsonl works too), or their Parquet copies.

A Parquet file is read with pyarrow (pip install 'launchtape[parquet]'); its rows come back like the JSON rows: the fields
a row has, nulls left out, maps as dicts. One difference: a field that is text in some row types and an object or a
number in others (pregrad's curve, src and cross) is a text column in Parquet, so an object comes back as its JSON text."""

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


def _is_parquet(path: Path) -> bool:
    with open(path, "rb") as f:
        return f.read(4) == b"PAR1"


def _pyarrow():
    try:
        import pyarrow.parquet as pq
    except ImportError as e:
        raise ImportError("reading Parquet needs pyarrow: pip install 'launchtape[parquet]'") from e
    return pq


def _drop_nulls(v: Any) -> Any:
    if isinstance(v, dict):
        return {k: _drop_nulls(x) for k, x in v.items() if x is not None}
    if isinstance(v, list):
        return [_drop_nulls(x) for x in v]
    return v


def _parquet_rows(path: Path, wanted: Optional[FrozenSet[str]], limit: Optional[int]) -> Iterator[Dict[str, Any]]:
    if limit == 0:
        return
    pq = _pyarrow()
    n = 0
    for batch in pq.ParquetFile(path).iter_batches(batch_size=10_000):
        for row in batch.to_pylist(maps_as_pydicts="strict"):
            if wanted is not None and row.get("type") not in wanted:
                continue
            yield _drop_nulls(row)
            n += 1
            if limit is not None and n >= limit:
                return


def _open(path: Path) -> IO[str]:
    with open(path, "rb") as f:
        gz = f.read(2) == b"\x1f\x8b"
    return gzip.open(path, "rt", encoding="utf-8") if gz else open(path, "rt", encoding="utf-8")


def load(path_or_dataset: Source, types: Types = None, limit: Optional[int] = None) -> Iterator[Dict[str, Any]]:
    """Stream the rows of a file as dicts.

    path_or_dataset: a .jsonl.gz or .parquet path, or a dataset name to read its free sample (downloaded on first use).
    types: keep only rows whose `type` is this string or one of these strings, e.g. types=["record", "outcome"].
    limit: stop after this many rows (counted after the type filter).
    """
    wanted = _types(types)
    if limit is not None and (not isinstance(limit, int) or limit < 0):
        raise ValueError(f"limit must be a whole number >= 0, got {limit!r}")
    path = _resolve(path_or_dataset)
    return _parquet_rows(path, wanted, limit) if _is_parquet(path) else _rows(path, wanted, limit)


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
    wanted = _types(types)
    if limit is not None and (not isinstance(limit, int) or limit < 0):
        raise ValueError(f"limit must be a whole number >= 0, got {limit!r}")
    path = _resolve(path_or_dataset)
    if _is_parquet(path):  # straight from the columns; only the fields the kept rows have, as with JSON lines
        _pyarrow()
        df = pd.read_parquet(path)
        if wanted is not None:
            df = df[df["type"].isin(wanted)]
        if limit is not None:
            df = df.head(limit)
        return df.dropna(axis=1, how="all").reset_index(drop=True)
    return pd.DataFrame(list(_rows(path, wanted, limit)))
