"""Parquet copies (0.1.2): sample/fetch with format="parquet", and load/frame/head reading a .parquet file."""

import io
import json

import pytest

import launchtape as lt
from launchtape.cli import main
from conftest import BASE, ROWS, TOKEN, sha

pa = pytest.importorskip("pyarrow")
pq = pytest.importorskip("pyarrow.parquet")

SOL = "So11111111111111111111111111111111111111112"
QPX = [{"type": "qpx", "t": 1791640000000, "usd": {SOL: 105.25, "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v": 1.0}},
       {"type": "px", "t": 1791640001000, "pad": "dbc", "mint": "MintC", "b": 10.0, "q": 2.5}]


def parquet_bytes(rows, maps=()):
    """One table for every row type, like the site's copies: every field a column, null where a row lacks it."""
    names = []
    for r in rows:
        names += [k for k in r if k not in names]
    cols = {}
    for k in names:
        values = [r.get(k) for r in rows]
        if k in maps:
            cols[k] = pa.array([list(v.items()) if v is not None else None for v in values], type=pa.map_(pa.string(), pa.float64()))
        else:
            cols[k] = pa.array(values)
    buf = io.BytesIO()
    pq.write_table(pa.table(cols), buf)
    return buf.getvalue()


def add_parquet_sample(web, dataset, body, window="2026-10-10-1400-1600utc"):
    name = f"{dataset}-{window}.parquet"
    web.pages[f"{BASE}/sample/{name}"] = (200, body)
    sums = web.pages.get(f"{BASE}/sample/SHA256SUMS", (200, b""))[1].decode() + f"{sha(body)}  {name}\n"
    web.pages[f"{BASE}/sample/SHA256SUMS"] = (200, sums.encode())
    return name


def add_parquet_delivery(web, dataset, day, body, listed_sha=None, with_parquet=True):
    base = f"{BASE}/dl/{TOKEN}"
    gz = f"{dataset}-{day}.jsonl.gz"
    entry = {"file": gz, "dataset": dataset, "day": day, "bytes": 123, "sha256": "a" * 64, "rows": len(ROWS)}
    if with_parquet:
        entry["parquet"] = {"file": f"{dataset}-{day}.parquet", "bytes": len(body), "sha256": listed_sha or sha(body)}
    web.pages[f"{base}/{dataset}-{day}.manifest.json"] = (200, json.dumps({"files": [entry]}).encode())
    web.pages[f"{base}/{dataset}-{day}.parquet"] = (200, body)


def test_sample_in_parquet(web, tmp_path):
    web.add_sample("pregrad")                         # the .jsonl.gz is listed too: format picks the other one
    body = parquet_bytes(ROWS)
    name = add_parquet_sample(web, "pregrad", body)
    path = lt.sample("pregrad", format="parquet")
    assert path == tmp_path / "cache" / name
    assert path.read_bytes() == body
    assert list(lt.load(path)) == ROWS                # the same rows as the JSON lines, nulls left out
    assert lt.sample("pregrad").name.endswith(".jsonl.gz")
    with pytest.raises(ValueError):
        lt.sample("pregrad", format="csv")


def test_fetch_parquet_checks_the_manifest_entry(web, tmp_path):
    body = parquet_bytes(ROWS)
    add_parquet_delivery(web, "pregrad", "2026-10-11", body)
    path = lt.fetch(TOKEN, "pregrad", "2026-10-11", dest=tmp_path, format="parquet")
    assert path == tmp_path / "pregrad-2026-10-11.parquet"
    assert path.read_bytes() == body
    assert (tmp_path / "pregrad-2026-10-11.manifest.json").is_file()
    assert lt.frame(path, types="outcome")["crashed"].tolist() == [True, False]


def test_fetch_parquet_refuses_a_bad_hash_or_a_missing_copy(web, tmp_path):
    body = parquet_bytes(ROWS)
    add_parquet_delivery(web, "pregrad", "2026-10-11", body, listed_sha="0" * 64)
    with pytest.raises(lt.ChecksumError):
        lt.fetch(TOKEN, "pregrad", "2026-10-11", dest=tmp_path, format="parquet")
    assert not (tmp_path / "pregrad-2026-10-11.parquet").exists()
    add_parquet_delivery(web, "padfeed", "2026-10-11", body, with_parquet=False)
    with pytest.raises(lt.LaunchTapeError, match="no Parquet copy"):
        lt.fetch(TOKEN, "padfeed", "2026-10-11", dest=tmp_path, format="parquet")


def test_load_parquet_maps_types_and_limit(tmp_path):
    path = tmp_path / "padfeed-2026-10-10.parquet"
    path.write_bytes(parquet_bytes(QPX, maps=("usd",)))
    rows = list(lt.load(path))
    assert rows == QPX                                # a map comes back as a dict
    assert list(lt.load(path, types="px")) == QPX[1:]
    assert list(lt.load(path, limit=1)) == QPX[:1]
    assert list(lt.load(path, limit=0)) == []


def test_frame_parquet_keeps_only_the_kept_rows_fields(pregrad_gz, tmp_path):
    path = tmp_path / "pregrad-2026-10-10.parquet"
    path.write_bytes(parquet_bytes(ROWS))
    df = lt.frame(path, types="record")
    assert len(df) == 2 and set(df.columns) == {"type", "t", "mint", "sym", "instant", "ageS", "curve"}
    assert df.loc[0, "curve"]["coverage"] == "full"
    assert len(lt.frame(path, limit=3)) == 3
    assert set(lt.frame(pregrad_gz, types="record").columns) == set(df.columns)   # same columns as from JSON lines


def test_cli_head_and_fetch_parquet(web, tmp_path, capsys):
    body = parquet_bytes(ROWS)
    add_parquet_sample(web, "pregrad", body)
    assert main(["head", "pregrad", "--format", "parquet", "-t", "record", "-n", "1"]) == 0
    assert [json.loads(line) for line in capsys.readouterr().out.splitlines()] == [ROWS[1]]
    add_parquet_delivery(web, "pregrad", "2026-10-11", body)
    assert main(["fetch", TOKEN, "pregrad", "2026-10-11", "--format", "parquet", "-o", str(tmp_path)]) == 0
    assert capsys.readouterr().out.strip() == str(tmp_path / "pregrad-2026-10-11.parquet")
    path = tmp_path / "pregrad-2026-10-11.parquet"
    assert main(["head", str(path), "-n", "2"]) == 0      # a path is read as it is
    assert [json.loads(line) for line in capsys.readouterr().out.splitlines()] == ROWS[:2]
