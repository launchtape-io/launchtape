import gzip
import sys

import pytest

import launchtape as lt
from conftest import ROWS, gz_bytes


def test_load_streams_every_row(pregrad_gz):
    it = lt.load(pregrad_gz)
    assert iter(it) is it  # a lazy iterator, not a list
    assert list(it) == ROWS


def test_load_accepts_str_path(pregrad_gz):
    assert list(lt.load(str(pregrad_gz))) == ROWS


@pytest.mark.parametrize("types, want", [
    ("record", ["record", "record"]),
    (["record", "outcome"], ["record", "record", "outcome", "outcome"]),
    ({"create"}, ["create"]),
    ("nope", []),
    ([], []),
])
def test_types_filter(pregrad_gz, types, want):
    assert [r["type"] for r in lt.load(pregrad_gz, types=types)] == want


def test_limit_counts_after_the_filter(pregrad_gz):
    assert [r["mint"] for r in lt.load(pregrad_gz, types="outcome", limit=1)] == ["MintA"]
    assert len(list(lt.load(pregrad_gz, limit=3))) == 3
    assert list(lt.load(pregrad_gz, limit=0)) == []
    assert len(list(lt.load(pregrad_gz, limit=99))) == len(ROWS)


@pytest.mark.parametrize("limit", [-1, 1.5, "3"])
def test_bad_limit(pregrad_gz, limit):
    with pytest.raises(ValueError):
        lt.load(pregrad_gz, limit=limit)


def test_bad_types(pregrad_gz):
    with pytest.raises(TypeError):
        lt.load(pregrad_gz, types=[1, 2])


def test_missing_file_fails_at_call_time(tmp_path):
    with pytest.raises(FileNotFoundError):
        lt.load(str(tmp_path / "nope.jsonl.gz"))


def test_plain_jsonl_and_blank_lines(tmp_path):
    p = tmp_path / "rows.jsonl"
    p.write_text('{"type":"pool","t":1}\n\n   \n{"type":"pool","t":2}\n', encoding="utf-8")
    assert [r["t"] for r in lt.load(p)] == [1, 2]


def test_bad_json_names_the_line(tmp_path):
    p = tmp_path / "bad.jsonl.gz"
    p.write_bytes(gz_bytes(ROWS[:2], extra_lines=['{"type":"record",oops']))
    with pytest.raises(ValueError, match="line 3"):
        list(lt.load(p))


def test_filter_is_exact_whatever_the_formatting(tmp_path):
    # rows not written as {"type":"..." first: spaces, type not the first key, an escaped character in the type
    lines = [
        '{"type": "record", "t": 1}',
        '{"t": 2, "type": "record"}',
        '{"type":"re\\u0063ord","t":3}',
        '{"type":"outcome","t":4,"note":{"type":"record"}}',
        '{"type":"recordx","t":5}',
    ]
    p = tmp_path / "mixed.jsonl.gz"
    p.write_bytes(gzip.compress(("\n".join(lines) + "\n").encode()))
    assert [r["t"] for r in lt.load(p, types="record")] == [1, 2, 3]


def test_load_dataset_name_reads_the_sample(web):
    web.add_sample("pregrad")
    rows = list(lt.load("pregrad", types="record"))
    assert [r["mint"] for r in rows] == ["MintA", "MintB"]


def test_frame(pregrad_gz, tmp_path):
    pd = pytest.importorskip("pandas")
    df = lt.frame(pregrad_gz, types=["record", "outcome"])
    assert isinstance(df, pd.DataFrame)
    assert len(df) == 4 and {"type", "mint", "instant", "crashed", "minDrop"} <= set(df.columns)
    # the README quickstart: share that touched -80% by instant, from the graduates CSV (not pregrad minDrop, wrong
    # from 2026-10-14 03:40 UTC); a coin with no trades has an empty low and is dropped
    csv = tmp_path / "graduates-2026-10-12.csv"
    csv.write_text("mint,curve_fill_sec,low_10m_pct,high_10m_pct,touched_minus80_10m,touched_2x_10m\n"
                   "A,1.5,-85.2,10,1,0\nB,120,-20,150,0,1\nC,3,,,,\nD,2,-40,0,0,0\n", encoding="utf-8")
    g = pd.read_csv(csv).dropna(subset=["low_10m_pct"])
    g["instant"] = g["curve_fill_sec"] < 5
    rate = g.groupby("instant")["touched_minus80_10m"].agg(["mean", "count"])
    assert rate["mean"].to_dict() == {False: 0.0, True: 0.5} and rate["count"].to_dict() == {False: 1, True: 2}
    rec = lt.frame(pregrad_gz, types="record")[["mint", "instant"]]
    assert len(rec) == 2
    assert lt.frame(pregrad_gz, limit=2).shape[0] == 2
    assert lt.frame(pregrad_gz, types="nope").empty


def test_frame_without_pandas(pregrad_gz, monkeypatch):
    monkeypatch.setitem(sys.modules, "pandas", None)  # import pandas -> ImportError
    with pytest.raises(ImportError, match=r"pip install 'launchtape\[pandas\]'"):
        lt.frame(pregrad_gz)
