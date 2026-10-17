"""backtest (0.1.3): honest replays of simple rules on gradtrades, coin by coin, at slot ends."""

import io
import json

import pytest

import launchtape as lt
from launchtape.backtest import (MINDROP_BAD_FROM_MS, Rules, backtest, backtest_report, graduates_for, pregrad_for,
                                  rules_from, rules_template)
from launchtape.cli import main
from conftest import gz_bytes

T0 = 1_791_640_000_000
COST = 0.015          # 1% fee + 0.5% slippage, each side
BASE = 100_000_000.0  # pool tokens


def grad(mint="M1", s=0.0, **kw):
    return {"type": "grad", "t": T0 + int(s * 1000), "mint": mint, "price": 1.0e-6, "poolBase": BASE, "poolQuote": 85.0,
            **kw}


def trade(mint, s, slot, px, base=BASE, side="buy", **kw):
    return {"type": "trade", "t": T0 + int(round(s * 1000)), "slot": slot, "mint": mint, "side": side,
            "priceAfter": px, "poolBase": base, "quoteAmount": 1.0, "lpFee": 0.002, "protocolFee": 0.0093,
            "creatorFee": 0.0007, **kw}


def end(mint, s, why="window"):
    return {"type": "end", "t": T0 + int(s * 1000), "mint": mint, "why": why, "buys": 1, "sells": 1}


def record(mint, ageS=0.1, dev=79.3, sym=None):
    return {"type": "record", "t": T0 + 20_000, "mint": mint, "sym": sym or mint, "ageS": ageS, "creatorPctAtGrad": dev}


def outcome(mint, minDrop, maxGain, dumpS=None):
    return {"type": "outcome", "t": T0 + 600_000, "mint": mint, "minDrop": minDrop, "maxGain": maxGain,
            "dump": {"tS": dumpS} if dumpS is not None else None}


def expected(size, px_in, base_in, px_out, base_out, cost=COST):
    """The prompt's formula: costs off, then constant product on the fill slot's pool (Q = px * B)."""
    x = size * (1 - cost)
    tokens = base_in * x / (px_in * base_in + x)
    sol = px_out * base_out * tokens / (base_out + tokens) * (1 - cost)
    return sol / size - 1


def run(tmp_path, gt_rows, pg_rows=(), rules=None):
    gt = tmp_path / "gradtrades-2026-10-10.jsonl.gz"
    gt.write_bytes(gz_bytes(gt_rows))
    pg = tmp_path / "pregrad-2026-10-10.jsonl.gz"
    pg.write_bytes(gz_bytes(list(pg_rows)))
    return backtest(rules, gt, pg)


# One coin, the example rules (buy +15 s, sell +180 s, stop -20%): the bot "sees" 1.1 at the signal, fills at 1.25
BASIC = [
    grad("M1"),
    trade("M1", 10.0, 100, 1.0e-6),
    trade("M1", 14.9, 101, 1.1e-6),                       # the price at the entry signal (+15 s)
    trade("M1", 15.2, 102, 1.2e-6),
    trade("M1", 15.21, 102, 1.25e-6),                     # same slot: the slot's price is its last trade's
    trade("M1", 100.0, 103, 1.3e-6),
    trade("M1", 179.0, 104, 1.4e-6),                      # the price at the exit signal (+180 s)
    trade("M1", 181.0, 105, 1.5e-6, base=BASE * 0.9),     # the fill: end of the first slot after +180 s
    end("M1", 1801.0),
]


def test_fills_at_the_end_of_the_next_slot_with_costs_and_impact(tmp_path):
    res = run(tmp_path, BASIC)
    (t,) = res["trades"]
    assert (t["entryS"], t["exitS"], t["exit"]) == (15.2, 181.0, "time")
    assert t["entryPrice"] == 1.25e-6 and t["exitPrice"] == 1.5e-6
    assert t["returnPct"] == pytest.approx(expected(0.1, 1.25e-6, BASE, 1.5e-6, BASE * 0.9) * 100, abs=0.01)
    assert t["priceMovePct"] == pytest.approx(20.0)
    assert t["realFeePct"] == [1.2, 1.2]
    s = res["summary"]
    assert s["trades"] == 1 and s["wins"] == 1 and s["winRatePct"] == 100.0
    assert s["pnlSol"] == pytest.approx(t["solOut"] - 0.1, abs=1e-9)
    assert res["coins"]["traded"] == 1 and res["coins"]["graduations"] == 1


def test_naive_comparison_uses_the_signals_own_price(tmp_path):
    res = run(tmp_path, BASIC)
    assert res["naive"]["trades"] == 1
    assert res["naive"]["medianReturnPct"] == pytest.approx((1.4 / 1.1 - 1) * 100, abs=0.01)   # 1.1 in, 1.4 out, no costs
    assert res["beforeCosts"]["medianReturnPct"] == pytest.approx(20.0, abs=0.01)


def test_a_bundle_inside_one_slot_is_not_a_price(tmp_path):
    rows = BASIC[:6] + [
        trade("M1", 30.0, 110, 0.3e-6, side="sell"),        # a dump and its re-buy in one slot (one bundle)
        trade("M1", 30.01, 110, 1.3e-6),
    ] + BASIC[6:]
    (t,) = run(tmp_path, rows)["trades"]
    assert t["exit"] == "time" and t["entryPrice"] == 1.25e-6   # no stop on the dip nobody could trade at


def test_a_stop_fills_at_the_next_slot_and_can_gap_through(tmp_path):
    rows = BASIC[:5] + [
        trade("M1", 30.0, 103, 0.85e-6, side="sell"),       # -32% from our 1.25 (-23% from the naive 1.1): the signal
        trade("M1", 31.0, 104, 0.5e-6, side="sell"),        # filled here, at -60%
        trade("M1", 40.0, 105, 1.4e-6),
        end("M1", 1801.0),
    ]
    res = run(tmp_path, rows)
    (t,) = res["trades"]
    assert (t["exit"], t["exitS"], t["exitPrice"]) == ("stop", 31.0, 0.5e-6)
    assert t["priceMovePct"] == pytest.approx(-60.0)
    assert res["summary"]["stopFillMedianPct"] == pytest.approx(-60.0)
    assert res["summary"]["stopFillWorstPct"] == pytest.approx(-60.0)
    assert res["naive"]["medianReturnPct"] == pytest.approx((0.85 / 1.1 - 1) * 100, abs=0.01)  # naive: out at the signal


def test_take_profit(tmp_path):
    rows = BASIC[:5] + [trade("M1", 50.0, 103, 2.6e-6), trade("M1", 51.0, 104, 2.4e-6), end("M1", 1801.0)]
    (t,) = run(tmp_path, rows, rules={"take_profit_pct": 100})["trades"]
    assert (t["exit"], t["exitPrice"]) == ("take_profit", 2.4e-6)


def test_hold_to_the_end_of_the_window(tmp_path):
    rows = BASIC[:5] + [
        trade("M1", 600.0, 103, 1.6e-6),
        trade("M1", 1799.0, 104, 1.7e-6),                   # the last price inside the 30 minutes
        trade("M1", 1801.0, 105, 9.9e-6),                   # after +30 min: not ours
        end("M1", 1802.0),
    ]
    (t,) = run(tmp_path, rows, rules={"exit_s": 1800, "stop_pct": None})["trades"]
    assert (t["exit"], t["exitPrice"], t["exitS"]) == ("end", 1.7e-6, 1799.0)


def test_exit_due_with_no_slot_after_it_sells_at_the_last_price(tmp_path):
    rows = BASIC[:6] + [end("M1", 1801.0)]                  # nothing trades after +100 s
    (t,) = run(tmp_path, rows)["trades"]
    assert (t["exit"], t["exitPrice"], t["exitS"]) == ("time", 1.3e-6, 100.0)


def test_trade_open_when_the_file_ends_is_left_out(tmp_path):
    rows = BASIC[:6] + [trade("OTHER", 120.0, 900, 1e-6)]   # the file ends at +120 s, before the +180 s exit
    res = run(tmp_path, rows)
    assert res["trades"] == [] and res["coins"]["cut"] == 1 and res["coins"]["traded"] == 0
    # the same file running past the exit time: the exit was due and nothing traded after it, so the last price
    res = run(tmp_path, BASIC[:6] + [trade("OTHER", 200.0, 900, 1e-6)])
    assert res["coins"]["cut"] == 0 and res["trades"][0]["exit"] == "time"


def test_recorder_letting_go_early_is_left_out(tmp_path):
    res = run(tmp_path, BASIC[:6] + [end("M1", 120.0, why="budget")])
    assert res["coins"]["cut"] == 1 and res["trades"] == []


def test_not_followed_and_never_reached(tmp_path):
    rows = [
        grad("NF", follow=False),
        grad("M2"), trade("M2", 5.0, 200, 1e-6), trade("M2", 10.0, 201, 1e-6), end("M2", 1801.0),
    ]
    res = run(tmp_path, rows)
    c = res["coins"]
    assert (c["graduations"], c["not_followed"], c["neverReachedEntry"], c["traded"]) == (2, 1, 1, 0)


def test_graduated_before_the_file(tmp_path):
    res = run(tmp_path, BASIC + [trade("OLD", 20.0, 300, 1e-6)])
    assert res["coins"]["beforeFile"] == 1 and res["coins"]["traded"] == 1


def test_late_pickup_is_left_out(tmp_path):
    rows = [grad("M1", late=True, followFrom=T0 + 20_000)] + BASIC[1:]
    res = run(tmp_path, rows)
    assert res["coins"]["late"] == 1 and res["trades"] == []


def coin(mint, slot0):
    return [grad(mint), trade(mint, 16.0, slot0, 1e-6), trade(mint, 181.0, slot0 + 1, 1.1e-6), end(mint, 1801.0)]


def test_filters_on_pregrad_records(tmp_path):
    gt = coin("FAST", 10) + coin("SLOW", 20) + coin("NOREC", 30)
    pg = [record("FAST", ageS=0.1, dev=79.3), record("SLOW", ageS=600, dev=2.0)]
    res = run(tmp_path, gt, pg, rules={"max_fill_s": 5})
    assert [t["mint"] for t in res["trades"]] == ["FAST"]
    assert (res["coins"]["filtered"], res["coins"]["no_record"]) == (1, 1)
    res = run(tmp_path, gt, pg, rules={"min_fill_s": 5, "max_dev_pct": 5})
    assert [t["mint"] for t in res["trades"]] == ["SLOW"]
    res = run(tmp_path, gt, pg, rules={"min_dev_pct": 5})
    assert [t["sym"] for t in res["trades"]] == ["FAST"]


def test_filters_need_the_pregrad_file(tmp_path):
    gt = tmp_path / "rows.jsonl.gz"                         # no pregrad file next to it
    gt.write_bytes(gz_bytes(BASIC))
    with pytest.raises(ValueError, match="pregrad"):
        backtest({"max_fill_s": 5}, gt)
    res = backtest(None, gt)                                # without filters it runs; no outcome section
    assert res["outcomes"] is None and res["summary"]["trades"] == 1


def test_bigger_size_gets_a_worse_fill(tmp_path):
    small = run(tmp_path, BASIC, rules={"size_sol": 0.1})["trades"][0]["returnPct"]
    big = run(tmp_path, BASIC, rules={"size_sol": 20})["trades"][0]["returnPct"]
    assert big < small
    zero_cost = run(tmp_path, BASIC, rules={"fee_pct": 0, "slippage_pct": 0, "size_sol": 1e-9})["trades"][0]
    assert zero_cost["returnPct"] == pytest.approx(20.0, abs=0.01)   # tiny size, no costs: just the price move


def test_feed_gap_is_flagged(tmp_path):
    rows = BASIC[:6] + [{"type": "down", "t": T0 + 50_000}, {"type": "up", "t": T0 + 51_000}] + BASIC[6:]
    res = run(tmp_path, rows)
    assert res["summary"]["gapTrades"] == 1 and res["trades"][0]["gap"] is True


def test_outcome_numbers(tmp_path):
    pg = [outcome("A", -85, 10, dumpS=100), outcome("B", -10, 150, dumpS=200), outcome("C", -50, 250), outcome("D", -90, 0)]
    o = run(tmp_path, BASIC, pg)["outcomes"]
    assert o == {"graduates": 4, "touchedMinus80Pct": 50.0, "touched2xPct": 50.0, "touched3xPct": 25.0,
                 "firstDumpMedianS": 150.0, "withDump": 2, "source": "pregrad", "leftOut": 0}


CSV_HEAD = ("mint,symbol,name,grad_time_utc,pool,cap_sol_at_grad,followed,curve_fill_sec,dev_hold_pct_at_grad,tape_sec,"
            "trades_10m,buyers_10m,sellers_10m,ref_cap_sol,low_10m_pct,high_10m_pct,touched_minus80_10m,touched_2x_10m,"
            "first_big_sell_sec,first_big_sell_pool_pct,dump_sec,end_10m_pct,end_30m_pct,data_gap")


def csv_row(mint, low, high, dump=""):
    lo, hi = ("" if low is None else low), ("" if high is None else high)
    t80 = "" if low is None else int(low <= -80)
    t2x = "" if high is None else int(high >= 100)
    return f"{mint},{mint},{mint},2026-10-14 12:00:00,P,496,1,30,5,1800,10,5,5,500,{lo},{hi},{t80},{t2x},,,{dump},0,0,0"


def test_outcome_numbers_from_the_graduates_csv(tmp_path):
    # Oct 17 2026: the graduates CSV (low/high from the +10 s price, chain order) wins over pregrad minDrop, found next to
    # the gradtrades file; a coin with no trades (empty low/high) has no prices and is left out
    pg = [outcome("A", -95, 10), outcome("B", -90, 150), outcome("C", -85, 250)]       # the fake -80s
    (tmp_path / "graduates-2026-10-10.csv").write_text(
        "\n".join([CSV_HEAD, csv_row("A", -20.5, 10, 100), csv_row("B", -85.0, 150, 200), csv_row("C", -50, 250),
                   csv_row("D", None, None)]) + "\n", encoding="utf-8")
    res = run(tmp_path, BASIC, pg)
    assert res["outcomes"] == {"graduates": 3, "touchedMinus80Pct": 33.3, "touched2xPct": 66.7, "touched3xPct": 33.3,
                               "firstDumpMedianS": 150.0, "withDump": 2, "source": "graduates", "leftOut": 0}
    assert res["files"]["graduates"] == "graduates-2026-10-10.csv"
    assert "3 graduates with prices, from graduates-2026-10-10.csv" in backtest_report(res)
    assert graduates_for(tmp_path / "gradtrades-2026-10-10.jsonl.gz") == tmp_path / "graduates-2026-10-10.csv"
    assert graduates_for(tmp_path / "gradtrades-2026-10-11.jsonl.gz") is None
    assert graduates_for(tmp_path / "pregrad-2026-10-10.jsonl.gz") is None
    # passed by hand; a file that is not a graduates CSV is refused
    other = tmp_path / "x.csv"
    other.write_text("mint,minDrop\nA,-90\n", encoding="utf-8")
    with pytest.raises(ValueError, match="not a Launch Tape graduates CSV"):
        backtest(None, tmp_path / "gradtrades-2026-10-10.jsonl.gz", tmp_path / "pregrad-2026-10-10.jsonl.gz", other)
    with pytest.raises(FileNotFoundError, match="graduates CSV"):
        backtest(None, tmp_path / "gradtrades-2026-10-10.jsonl.gz", None, tmp_path / "nope.csv")


def test_pregrad_mindrop_left_out_from_oct_14_0340_utc(tmp_path):
    # no graduates CSV: pregrad outcome rows of graduations from 2026-10-14 03:40 UTC on are not counted (minDrop is wrong)
    late = MINDROP_BAD_FROM_MS
    pg = [outcome("A", -85, 10), outcome("B", -10, 150),
          {**outcome("C", -90, 0), "t0": late, "t": late + 600_000},
          {**outcome("D", -88, 0), "t0": late + 5_000, "t": late + 605_000}]
    res = run(tmp_path, BASIC, pg)
    o = res["outcomes"]
    assert (o["graduates"], o["touchedMinus80Pct"], o["touched2xPct"], o["leftOut"], o["source"]) == (2, 50.0, 50.0, 2, "pregrad")
    text = " ".join(backtest_report(res).split())
    assert "2 graduates from 2026-10-14 03:40 UTC on are left out: pregrad minDrop is wrong" in text
    assert "--graduates" in text
    # just before the cutoff still counts
    o = run(tmp_path, BASIC, [{**outcome("E", -85, 0), "t0": late - 1}])["outcomes"]
    assert (o["graduates"], o["touchedMinus80Pct"], o["leftOut"]) == (1, 100.0, 0)
    # an Oct 14+ day without the CSV: no touched numbers at all rather than wrong ones
    o = run(tmp_path, BASIC, [{**outcome("F", -90, 0), "t0": late + 1}])["outcomes"]
    assert o["touchedMinus80Pct"] is None and o["leftOut"] == 1
    assert "n/a" in backtest_report(run(tmp_path, BASIC, [{**outcome("F", -90, 0), "t0": late + 1}]))


@pytest.mark.parametrize("spec, msg", [
    ({"entry": 15}, "unknown key"),
    ({"entry_s": 200, "exit_s": 100}, "later than entry_s"),
    ({"stop_pct": 120}, "stop_pct"),
    ({"stop_pct": True}, "must be a number"),
    ({"size_sol": 0}, "size_sol"),
    ({"take_profit_pct": -5}, "take_profit_pct"),
    ({"min_dev_pct": 10, "max_dev_pct": 5}, "no coin can pass"),
    ({"entry_s": 1800}, "entry_s"),
    ([1, 2], "JSON object"),
])
def test_bad_rules(spec, msg):
    with pytest.raises(ValueError, match=msg):
        rules_from(spec)


def test_rules_file_and_template(tmp_path):
    assert rules_from(None) == Rules()
    p = tmp_path / "rules.json"
    p.write_text(rules_template(), encoding="utf-8")
    assert rules_from(str(p)) == Rules()
    p.write_text('{"entry_s": 5, "exit_s": 60, "stop_pct": null, "max_fill_s": 5}', encoding="utf-8")
    r = rules_from(p)
    assert (r.entry_s, r.exit_s, r.stop_pct, r.filters) == (5.0, 60.0, None, {"max_fill_s": 5.0})
    p.write_text("{oops", encoding="utf-8")
    with pytest.raises(ValueError, match="not valid JSON"):
        rules_from(p)


def test_report_text(tmp_path):
    pg = [record("M1", sym="COIN"), outcome("M1", -85, 150, dumpS=120)]
    text = backtest_report(run(tmp_path, BASIC, pg))
    for want in ("coins traded", "win rate", "median return a trade", "mean return a trade", "worst 3 trades", "COIN",
                 "never reached your entry", "touched -80%", "touched 2x", "Verdict", "The 3 mistakes we see most",
                 "signal's own price", "Leaving out fees", "3x"):
        assert want in text, want
    assert "small sample" in " ".join(text.split())


def test_report_with_no_trades(tmp_path):
    text = backtest_report(run(tmp_path, [grad("NF", follow=False)]))
    assert "No trades" in text and "nothing to judge" in text


def test_pregrad_for(tmp_path):
    gt = tmp_path / "gradtrades-2026-10-11.jsonl.gz"
    gt.write_bytes(b"")
    assert pregrad_for(gt) is None
    (tmp_path / "pregrad-2026-10-11.parquet").write_bytes(b"")
    assert pregrad_for(gt) == tmp_path / "pregrad-2026-10-11.parquet"
    (tmp_path / "pregrad-2026-10-11.jsonl.gz").write_bytes(b"")
    assert pregrad_for(gt) == tmp_path / "pregrad-2026-10-11.jsonl.gz"


def test_default_is_the_free_sample(web):
    web.add_sample("gradtrades", rows=BASIC)
    web.add_sample("pregrad", rows=[record("M1", sym="COIN"), outcome("M1", -85, 150)])
    res = lt.backtest()
    assert res["trades"][0]["sym"] == "COIN" and res["files"]["gradtrades"].startswith("gradtrades-2026-10-10-1400")


def test_cli_backtest(tmp_path, capsys):
    gt = tmp_path / "gradtrades-2026-10-10.jsonl.gz"
    gt.write_bytes(gz_bytes(BASIC))
    (tmp_path / "pregrad-2026-10-10.jsonl.gz").write_bytes(gz_bytes([record("M1", sym="COIN"), outcome("M1", -85, 150)]))
    assert main(["backtest", "--gradtrades", str(gt)]) == 0              # finds the pregrad file next to it
    out = capsys.readouterr().out
    assert "LAUNCH TAPE BACKTEST" in out and "coins traded                          1" in out
    assert main(["backtest", "--gradtrades", str(gt), "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["summary"]["trades"] == 1
    assert main(["backtest", "--template"]) == 0
    assert json.loads(capsys.readouterr().out)["entry_s"] == 15
    rules = tmp_path / "rules.json"
    rules.write_text('{"exit_s": 5}', encoding="utf-8")
    assert main(["backtest", "--rules", str(rules), "--gradtrades", str(gt)]) == 1
    assert "exit_s must be later than entry_s" in capsys.readouterr().err


def test_parquet_gives_the_same_result(tmp_path):
    pa = pytest.importorskip("pyarrow")
    pq = pytest.importorskip("pyarrow.parquet")
    rows = BASIC + [{"type": "down", "t": T0 + 50_000}, {"type": "up", "t": T0 + 51_000}]
    names = []
    for r in rows:
        names += [k for k in r if k not in names]
    buf = io.BytesIO()
    pq.write_table(pa.table({k: pa.array([r.get(k) for r in rows]) for k in names}), buf)
    pqf = tmp_path / "gradtrades-2026-10-10.parquet"
    pqf.write_bytes(buf.getvalue())
    gz = tmp_path / "gradtrades-2026-10-10.jsonl.gz"
    gz.write_bytes(gz_bytes(rows))
    a, b = backtest(None, gz), backtest(None, pqf)
    a.pop("files"), b.pop("files")
    assert a == b


def test_free_sample_report_offers_a_full_day(tmp_path):
    """0.1.6: on the free sample (a 2-hour cut) the verdict is followed by one line on a full day; not on a day file."""
    pg = [record("M1", sym="COIN"), outcome("M1", -85, 150)]
    day = backtest_report(run(tmp_path, BASIC, pg))
    assert "plan=day1" not in day
    gt = tmp_path / "gradtrades-2026-10-10-1400-1600utc.jsonl.gz"
    gt.write_bytes(gz_bytes(BASIC))
    pgf = tmp_path / "pregrad-2026-10-10-1400-1600utc.jsonl.gz"
    pgf.write_bytes(gz_bytes(pg))
    res = backtest(None, gt, pgf)
    text = backtest_report(res)
    line = "0.5 hours, 1 trade. A full day of the same rules is one file: $10 at https://launchtape.io/?plan=day1#buy"
    assert line in text.splitlines()
    assert text.index("Verdict") < text.index(line) < text.index("The 3 mistakes we see most")
