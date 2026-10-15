"""Replay simple trading rules on Launch Tape's pump.fun graduates, coin by coin, with honest fills.

    launchtape backtest --gradtrades gradtrades-<day>.jsonl.gz   # the example rules on a day you bought
    launchtape backtest --rules rules.json                # your rules
    launchtape backtest --rules rules.json --gradtrades data/gradtrades-2026-10-11.jsonl.gz

    import launchtape as lt
    result = lt.backtest({"entry_s": 15, "exit_s": 180, "stop_pct": 20})
    print(lt.backtest_report(result))

How a trade is filled (the same steps as the prompt on https://launchtape.io/test):

- Prices are read at the end of each slot (block): the slot's last trade's `priceAfter` and `poolBase`. Several trades
  in one slot can be one bundle (a big dump and its re-buy in the same block), and nobody outside could trade between
  them, so a price inside a slot is not a price you could have had.
- A signal (your entry time, your exit time, a stop, a take-profit) never fills at its own price. It fills at the end of
  the first slot after it: for a time, the first slot whose first trade has a later `t`; for a stop or take-profit seen
  at the end of one slot, the end of the next slot.
- Every buy and every sell pays costs: 1% PumpSwap fee + 0.5% priority fee and slippage by default, per side.
- Your size moves the price: a fill uses that slot's pool (constant product; B = poolBase, Q = priceAfter * B), so
  x SOL (after costs) buys B*x/(Q+x) tokens and n tokens sell for Q*n/(B+n) SOL (before costs).
- The data covers 30 minutes after each graduation: a trade still open at +30 min closes at the last price before it
  (also when the exit was due and no slot came after it: the pool had not moved).
- A trade still open when the file ends (a coin that graduated in the file's last 30 minutes), or when the recorder
  stopped following the coin early, is left out and counted, never guessed.
"""

from __future__ import annotations

import json
import math
import os
import re
import statistics
from dataclasses import asdict, dataclass, fields
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple, Union

from ._read import _is_parquet, _open, _pyarrow, load

WINDOW_S = 1800          # each graduate's trades are recorded for 30 minutes
_RULES_DOC = {
    "entry_s": "buy this many seconds after graduation",
    "exit_s": "sell this many seconds after graduation (1800 or more: hold to the end of the 30 minutes)",
    "stop_pct": "sell when the price falls this % below your entry price (null: no stop)",
    "take_profit_pct": "sell when the price rises this % above your entry price (null: no take-profit)",
    "size_sol": "SOL per trade, the same for every coin",
    "fee_pct": "PumpSwap fee per side, in %",
    "slippage_pct": "priority fee + slippage per side, in %",
    "min_fill_s": "only coins whose bonding curve took at least this many seconds from creation to graduation "
                  "(pregrad record ageS; null: no filter)",
    "max_fill_s": "only coins whose bonding curve filled within this many seconds of creation "
                  "(pregrad record ageS; 5 = the 'instant' launches; null: no filter)",
    "min_dev_pct": "only coins whose creator still held at least this % of supply at graduation "
                   "(pregrad record creatorPctAtGrad; null: no filter)",
    "max_dev_pct": "only coins whose creator held at most this % of supply at graduation (null: no filter)",
}
_FILTERS = ("min_fill_s", "max_fill_s", "min_dev_pct", "max_dev_pct")


@dataclass(frozen=True)
class Rules:
    """The rules to replay. Times are seconds after graduation; percentages are plain numbers (20 = 20%)."""

    entry_s: float = 15.0
    exit_s: float = 180.0
    stop_pct: Optional[float] = 20.0
    take_profit_pct: Optional[float] = None
    size_sol: float = 0.1
    fee_pct: float = 1.0
    slippage_pct: float = 0.5
    min_fill_s: Optional[float] = None
    max_fill_s: Optional[float] = None
    min_dev_pct: Optional[float] = None
    max_dev_pct: Optional[float] = None

    @property
    def cost(self) -> float:
        """Costs per side as a fraction (1% + 0.5% -> 0.015)."""
        return (self.fee_pct + self.slippage_pct) / 100

    @property
    def filters(self) -> Dict[str, float]:
        return {k: getattr(self, k) for k in _FILTERS if getattr(self, k) is not None}


def _number(key: str, v: Any, *, null: bool) -> Optional[float]:
    if v is None:
        if null:
            return None
        raise ValueError(f"rules: {key} needs a number")
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
        raise ValueError(f"rules: {key} must be a number{' or null' if null else ''}, got {v!r}")
    return float(v)


def rules_from(spec: Union[None, Rules, Dict[str, Any], str, "os.PathLike[str]"] = None) -> Rules:
    """Rules from a dict, a JSON file path, or None (the example rules). Unknown keys are refused, so a typo can't
    silently drop a rule."""
    if spec is None:
        return Rules()
    if isinstance(spec, Rules):
        return spec
    if isinstance(spec, (str, os.PathLike)):
        try:
            spec = json.loads(Path(spec).expanduser().read_text(encoding="utf-8"))
        except ValueError as e:
            raise ValueError(f"rules file {os.fspath(spec)!r} is not valid JSON: {e}") from None
    if not isinstance(spec, dict):
        raise ValueError("rules must be a JSON object like {\"entry_s\": 15, \"exit_s\": 180, \"stop_pct\": 20}")
    unknown = sorted(set(spec) - set(_RULES_DOC))
    if unknown:
        raise ValueError(f"rules: unknown key(s) {', '.join(unknown)}; the keys are: {', '.join(_RULES_DOC)}")
    defaults = Rules()
    nullable = {"stop_pct", "take_profit_pct", *_FILTERS}
    vals = {f.name: _number(f.name, spec[f.name], null=f.name in nullable) if f.name in spec else getattr(defaults, f.name)
            for f in fields(Rules)}
    r = Rules(**vals)
    if not 0 <= r.entry_s < WINDOW_S:
        raise ValueError(f"rules: entry_s must be from 0 to under {WINDOW_S} (the data covers 30 minutes)")
    if r.exit_s <= r.entry_s:
        raise ValueError("rules: exit_s must be later than entry_s (both are seconds after graduation)")
    if r.stop_pct is not None and not 0 < r.stop_pct < 100:
        raise ValueError("rules: stop_pct must be between 0 and 100 (20 = sell 20% below your entry), or null")
    if r.take_profit_pct is not None and r.take_profit_pct <= 0:
        raise ValueError("rules: take_profit_pct must be above 0 (100 = sell at 2x), or null")
    if r.size_sol <= 0:
        raise ValueError("rules: size_sol must be above 0")
    for k in ("fee_pct", "slippage_pct"):
        if not 0 <= getattr(r, k) < 50:
            raise ValueError(f"rules: {k} must be from 0 to under 50")
    for k in _FILTERS:
        if getattr(r, k) is not None and getattr(r, k) < 0:
            raise ValueError(f"rules: {k} can't be negative")
    for lo, hi in (("min_fill_s", "max_fill_s"), ("min_dev_pct", "max_dev_pct")):
        a, b = getattr(r, lo), getattr(r, hi)
        if a is not None and b is not None and a > b:
            raise ValueError(f"rules: {lo} is above {hi}, so no coin can pass")
    return r


def rules_template() -> str:
    """The example rules as a JSON file to edit (every key; null = off)."""
    return json.dumps(asdict(Rules()), indent=2) + "\n"


# ---------------------------------------------------------------------------------------------------------------------
# The replay

_FILL = Tuple[int, float, float, Optional[float]]     # a slot's end: (t of its first trade, priceAfter, poolBase, fee rate)


def _fee_rate(r: Dict[str, Any]) -> Optional[float]:
    q = r.get("quoteAmount")
    if not isinstance(q, (int, float)) or q <= 0:
        return None
    return sum(r.get(k) or 0 for k in ("lpFee", "protocolFee", "creatorFee")) / q


class _Honest:
    """One coin's trade the honest way: fills at the end of the next slot, costs, size impact."""

    __slots__ = ("state", "entry", "tokens", "pending", "exit", "reason")

    def __init__(self) -> None:
        self.state = "wait"              # wait -> hold -> done
        self.entry: Optional[_FILL] = None
        self.tokens = 0.0
        self.pending: Optional[str] = None   # "stop" | "take_profit": signalled, fills at the end of the next slot
        self.exit: Optional[_FILL] = None
        self.reason: Optional[str] = None


class _Naive:
    """The same rules the way most bot backtests run them: the signal's own price, no costs."""

    __slots__ = ("state", "entry_px", "exit_px")

    def __init__(self) -> None:
        self.state = "wait"
        self.entry_px: Optional[float] = None
        self.exit_px: Optional[float] = None


class _Coin:
    __slots__ = ("mint", "t0", "entry_at", "exit_at", "end_at", "cur", "last", "honest", "naive", "outcome", "gap",
                 "late_from")

    def __init__(self, g: Dict[str, Any], rules: Rules) -> None:
        self.mint: str = g["mint"]
        self.t0 = int(g["t"])
        self.entry_at = self.t0 + round(rules.entry_s * 1000)
        self.exit_at = self.t0 + round(rules.exit_s * 1000)
        self.end_at = self.t0 + WINDOW_S * 1000
        self.cur: Optional[list] = None         # the slot being read: [slot, t of its first trade, px, base, fee]
        self.last: Optional[_FILL] = None       # the last finished slot inside the window
        self.honest = _Honest()
        self.naive = _Naive()
        self.outcome: Optional[str] = None      # None while running; then traded | never | cut
        self.gap = False
        self.late_from = g.get("followFrom") if g.get("late") else None


class _Replay:
    """Streams a gradtrades file once, coin by coin. Several trades in one slot can be one bundle (a dump and its re-buy
    in the same block), so nobody outside could trade between them: a coin's price is read at the END of each slot (its
    last trade's priceAfter / poolBase), and a signal fills at the end of the next slot after it."""

    def __init__(self, rules: Rules, records: Optional[Dict[str, Dict[str, Any]]]) -> None:
        self.rules = rules
        self.records = records
        self.coins: Dict[str, _Coin] = {}
        self.active: Dict[str, _Coin] = {}
        self.counts = {"graduations": 0, "not_followed": 0, "filtered": 0, "no_record": 0, "late": 0}
        self.seen = set()                        # every graduation row's mint, traded or not
        self.before_file = set()                 # coins with trades but no graduation row (graduated before the file)
        self.gaps: List[List[Optional[int]]] = []   # [down t, up t]
        self.first_t: Optional[int] = None
        self.last_t: Optional[int] = None

    # -- rows ------------------------------------------------------------------------------------------------------
    def see_t(self, t: Any) -> None:
        if isinstance(t, (int, float)) and not isinstance(t, bool):
            t = int(t)
            if self.first_t is None or t < self.first_t:
                self.first_t = t
            if self.last_t is None or t > self.last_t:
                self.last_t = t

    def row(self, r: Dict[str, Any]) -> None:
        ty = r.get("type")
        self.see_t(r.get("t"))
        if ty == "trade":
            self.trade(r)
        elif ty == "grad":
            self.grad(r)
        elif ty == "end":
            c = self.active.get(r.get("mint"))
            if c is not None:
                self.end(c, r.get("why"), r.get("t"))
        elif ty == "down":
            self.gaps.append([r.get("t"), None])
        elif ty == "up":
            for g in reversed(self.gaps):
                if g[1] is None:
                    g[1] = r.get("t")
                    break
        elif ty == "resume":
            c = self.active.get(r.get("mint"))
            if c is not None and (c.honest.state == "hold" or (c.honest.state == "wait" and isinstance(r.get("t"), (int, float))
                                                               and r["t"] > c.entry_at)):
                c.gap = True

    def grad(self, g: Dict[str, Any]) -> None:
        mint = g.get("mint")
        if not isinstance(mint, str) or not isinstance(g.get("t"), (int, float)) or mint in self.seen:
            return
        self.seen.add(mint)
        self.counts["graduations"] += 1
        if g.get("follow") is False:
            self.counts["not_followed"] += 1
            return
        if self.rules.filters:
            rec = (self.records or {}).get(mint)
            if not self.passes(rec):
                self.counts["no_record" if rec is None or not self.checkable(rec) else "filtered"] += 1
                return
        c = _Coin(g, self.rules)
        if isinstance(c.late_from, (int, float)) and c.late_from > c.entry_at:
            self.counts["late"] += 1             # picked up late: trades around the entry are missing
            return
        self.coins[mint] = c
        self.active[mint] = c

    def checkable(self, rec: Dict[str, Any]) -> bool:
        f = self.rules
        if (f.min_fill_s is not None or f.max_fill_s is not None) and not isinstance(rec.get("ageS"), (int, float)):
            return False
        if (f.min_dev_pct is not None or f.max_dev_pct is not None) and not isinstance(rec.get("creatorPctAtGrad"), (int, float)):
            return False
        return True

    def passes(self, rec: Optional[Dict[str, Any]]) -> bool:
        if rec is None or not self.checkable(rec):
            return False
        f, age, dev = self.rules, rec.get("ageS"), rec.get("creatorPctAtGrad")
        return not ((f.min_fill_s is not None and age < f.min_fill_s) or (f.max_fill_s is not None and age > f.max_fill_s)
                    or (f.min_dev_pct is not None and dev < f.min_dev_pct) or (f.max_dev_pct is not None and dev > f.max_dev_pct))

    def trade(self, r: Dict[str, Any]) -> None:
        mint = r.get("mint")
        c = self.active.get(mint)
        if c is None:
            if isinstance(mint, str) and mint not in self.seen:
                self.before_file.add(mint)
            return
        t, px, base, slot = r.get("t"), r.get("priceAfter"), r.get("poolBase"), r.get("slot")
        if not (isinstance(t, (int, float)) and isinstance(px, (int, float)) and isinstance(base, (int, float))
                and px > 0 and base > 0):
            return
        t = int(t)
        if not isinstance(slot, int) or isinstance(slot, bool):
            slot = ("t", t)                       # no slot number: each receive time is its own block
        if c.cur is not None and c.cur[0] == slot:
            c.cur[2], c.cur[3], c.cur[4] = float(px), float(base), _fee_rate(r)   # the slot's price is its last trade's
            return
        if c.cur is not None:
            self.flush(c)                         # a new slot: the one before is complete
            if c.outcome is not None:
                return
        if t > c.end_at:                          # past the 30 minutes: the window is over for this coin
            self.window_end(c)
            return
        c.cur = [slot, t, float(px), float(base), _fee_rate(r)]

    def flush(self, c: _Coin) -> None:
        """Run the finished slot through both trades."""
        cur, c.cur = c.cur, None
        s: _FILL = (cur[1], cur[2], cur[3], cur[4])
        self.step_honest(c, s)
        self.step_naive(c, s)
        c.last = s
        if c.honest.state == "done" and c.naive.state == "done":
            c.outcome = "traded"
            self.active.pop(c.mint, None)

    # -- the honest trade --------------------------------------------------------------------------------------------
    def step_honest(self, c: _Coin, s: _FILL) -> None:
        h, rules = c.honest, self.rules
        t, px, base, _ = s
        if h.state == "wait":
            if t > c.entry_at:                    # the first slot after the entry signal: we buy at its end
                h.entry = s
                h.tokens = _buy(rules.size_sol, px, base, rules.cost)
                h.state = "hold"
            return
        if h.state != "hold":
            return
        if h.pending is not None:                 # a stop / take-profit signalled on the slot before: out at this one
            self.close(h, s, h.pending)
        elif t > c.exit_at:                       # the first slot after the exit time
            self.close(h, s, "time")
        else:
            entry_px = h.entry[1]
            if rules.stop_pct is not None and px <= entry_px * (1 - rules.stop_pct / 100):
                h.pending = "stop"
            elif rules.take_profit_pct is not None and px >= entry_px * (1 + rules.take_profit_pct / 100):
                h.pending = "take_profit"

    def close(self, h: _Honest, s: _FILL, reason: str) -> None:
        h.exit, h.reason, h.state = s, reason, "done"

    # -- the naive trade (for the comparison line) -------------------------------------------------------------------
    def step_naive(self, c: _Coin, s: _FILL) -> None:
        n, rules = c.naive, self.rules
        t, px = s[0], s[1]
        prev = c.last                             # the slot before this one: the price the bot saw at the signal
        if n.state == "wait":
            if t <= c.entry_at:
                return
            n.entry_px = prev[1] if prev else px    # nothing traded before the signal: the first price seen
            n.state = "hold"
        if n.state != "hold":
            return
        if t > c.exit_at:
            n.exit_px = prev[1] if prev else n.entry_px
            n.state = "done"
        elif rules.stop_pct is not None and px <= n.entry_px * (1 - rules.stop_pct / 100):
            n.exit_px, n.state = px, "done"       # out at the stop signal's own price
        elif rules.take_profit_pct is not None and px >= n.entry_px * (1 + rules.take_profit_pct / 100):
            n.exit_px, n.state = px, "done"

    # -- endings -------------------------------------------------------------------------------------------------------
    def window_end(self, c: _Coin) -> None:
        """The coin's 30 minutes are over: an open trade closes at the last price (the pool has not moved since)."""
        if c.cur is not None:
            self.flush(c)
        if c.outcome is not None:
            return
        h, n = c.honest, c.naive
        if h.state == "wait":
            c.outcome = "never"
        else:
            if h.state == "hold":
                self.close(h, c.last, h.pending or ("time" if c.exit_at < c.end_at else "end"))
            if n.state == "hold":
                n.exit_px, n.state = c.last[1], "done"
            c.outcome = "traded"
        self.active.pop(c.mint, None)

    def end(self, c: _Coin, why: Any, t: Any) -> None:
        if why == "window":
            self.window_end(c)
        else:                                     # evicted / budget: the recorder stopped following the coin early
            self.stopped(c, int(t) if isinstance(t, (int, float)) else (c.last[0] if c.last else c.t0))

    def stopped(self, c: _Coin, at: int) -> None:
        """The coin's data stops at `at` before its 30 minutes are over (the file ends, or the recorder let it go)."""
        if c.cur is not None:
            self.flush(c)
        if c.outcome is not None:
            return
        h = c.honest
        if h.state == "done" or (h.state == "hold" and (h.pending is not None or c.exit_at < at)):
            self.window_end(c)    # the trade was closed, or its exit was due and no slot came after it: the last price
        else:
            c.outcome = "cut"     # the data ended before this trade did: left out, never guessed
            self.active.pop(c.mint, None)

    def finish(self) -> None:
        end = self.last_t or 0
        for c in list(self.active.values()):
            if c.end_at <= end:                   # the window was over before the file ended (no end row seen)
                self.window_end(c)
            else:
                self.stopped(c, end)
        for c in self.coins.values():
            if c.outcome == "traded" and not c.gap:
                start, stop = c.entry_at, c.honest.exit[0]
                c.gap = any(d is not None and d <= stop and (u is None or u >= start) for d, u in self.gaps)


def _buy(sol: float, px: float, base: float, cost: float) -> float:
    """Tokens for `sol` SOL bought right after a trade that left the pool at (px, base): costs off first, then the
    constant-product curve (the pool's SOL side, virtual SOL included, is px * base)."""
    x = sol * (1 - cost)
    return base * x / (px * base + x)


def _sell(tokens: float, px: float, base: float, cost: float) -> float:
    """SOL received for selling `tokens` right after a trade that left the pool at (px, base), after costs."""
    return px * base * tokens / (base + tokens) * (1 - cost)


# ---------------------------------------------------------------------------------------------------------------------
# Reading the files

_TRADE = '{"type":"trade"'
_MINT = '"mint":"'
_COLS = ("type", "t", "slot", "mint", "priceAfter", "poolBase", "quoteAmount", "lpFee", "protocolFee", "creatorFee",
         "follow", "late", "followFrom", "why")


def _feed(path: Path, rp: _Replay) -> None:
    if _is_parquet(path):
        pq = _pyarrow()
        f = pq.ParquetFile(path)
        cols = [c for c in _COLS if c in f.schema_arrow.names]
        for batch in f.iter_batches(batch_size=65_536, columns=cols):
            data = [batch.column(i).to_pylist() for i in range(len(cols))]
            for values in zip(*data):
                rp.row({k: v for k, v in zip(cols, values) if v is not None})
        return
    with _open(path) as fh:
        for lineno, line in enumerate(fh, 1):
            if line.startswith(_TRADE):          # most rows: skip the coins we are not trading without parsing them
                i = line.find(_MINT)
                if i > 0:
                    j = line.find('"', i + len(_MINT))
                    mint = line[i + len(_MINT):j]
                    if mint not in rp.active:
                        if mint not in rp.seen:
                            rp.before_file.add(mint)
                        t = _quick_t(line)
                        if t is not None:
                            rp.see_t(t)
                        continue
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
            except ValueError as e:
                raise ValueError(f"{path}: line {lineno} is not valid JSON ({e})") from None
            if isinstance(r, dict):
                rp.row(r)


_T = re.compile(r'"t":(\d+)')


def _quick_t(line: str) -> Optional[int]:
    m = _T.search(line, 0, 64)
    return int(m.group(1)) if m else None


def _pregrad(path: Path) -> Tuple[Dict[str, Dict[str, Any]], List[Dict[str, Any]]]:
    records: Dict[str, Dict[str, Any]] = {}
    outcomes: Dict[str, Dict[str, Any]] = {}
    for r in load(path, types=("record", "outcome")):
        mint = r.get("mint")
        if not isinstance(mint, str):
            continue
        if r.get("type") == "record":
            records[mint] = {"sym": r.get("sym"), "ageS": r.get("ageS"), "creatorPctAtGrad": r.get("creatorPctAtGrad")}
        else:
            dump = r.get("dump")
            outcomes[mint] = {"minDrop": r.get("minDrop"), "maxGain": r.get("maxGain"),
                              "dumpS": dump.get("tS") if isinstance(dump, dict) else None}
    return records, list(outcomes.values())


def pregrad_for(gradtrades: Union[str, "os.PathLike[str]"]) -> Optional[Path]:
    """The pregrad file of the same day/window next to a gradtrades file, if there is one."""
    p = Path(gradtrades)
    if "gradtrades" not in p.name:
        return None
    stem = p.name.replace("gradtrades", "pregrad", 1)
    for cand in (stem, re.sub(r"\.parquet$", ".jsonl.gz", stem), re.sub(r"\.jsonl\.gz$", ".parquet", stem)):
        q = p.with_name(cand)
        if q.is_file():
            return q
    return None


# ---------------------------------------------------------------------------------------------------------------------
# The result

def _pct(x: Optional[float]) -> Optional[float]:
    return None if x is None else round(x * 100, 2)


def _median(xs: List[float]) -> Optional[float]:
    return statistics.median(xs) if xs else None


def _mean(xs: List[float]) -> Optional[float]:
    return statistics.fmean(xs) if xs else None


def backtest(rules: Union[None, Rules, Dict[str, Any], str, "os.PathLike[str]"] = None,
             gradtrades: Union[None, str, "os.PathLike[str]"] = None,
             pregrad: Union[None, str, "os.PathLike[str]"] = None, *, progress: bool = False) -> Dict[str, Any]:
    """Replay the rules on a gradtrades file (default: the free sample) and return the result as a dict.

    rules: a dict, a JSON file path, a Rules, or None for the example rules (buy at +15 s, sell at +180 s, stop -20%).
    gradtrades: a gradtrades .jsonl.gz or .parquet path (a paid day file or the sample); None downloads the sample.
    pregrad: the pregrad file of the same day, for the filters, coin names and the "first 10 minutes" numbers; None
    looks next to the gradtrades file (or downloads the sample's when gradtrades is None too).
    """
    from ._download import sample             # here, so the module imports without the network code being used

    r = rules_from(rules)
    if gradtrades is None:
        gt = sample("gradtrades", progress=progress)
        pg = Path(pregrad) if pregrad is not None else sample("pregrad", progress=progress)
    else:
        gt = Path(gradtrades).expanduser()
        if not gt.is_file():
            raise FileNotFoundError(f"no such gradtrades file: {gt}")
        pg = Path(pregrad).expanduser() if pregrad is not None else pregrad_for(gt)
    if pg is not None and not Path(pg).is_file():
        raise FileNotFoundError(f"no such pregrad file: {pg}")
    if r.filters and pg is None:
        raise ValueError("these rules filter on pregrad facts (fill time / dev holding): pass the pregrad file of the "
                         "same day with --pregrad")
    records, outcomes = _pregrad(Path(pg)) if pg is not None else (None, None)

    rp = _Replay(r, records)
    _feed(gt, rp)
    rp.finish()
    return _result(r, rp, gt, pg, records, outcomes)


def _result(r: Rules, rp: _Replay, gt: Path, pg: Optional[Path], records, outcomes) -> Dict[str, Any]:
    trades = []
    naive: List[float] = []
    rets: List[float] = []                    # exact returns and price moves (the trade rows carry rounded ones)
    moves: List[float] = []
    for c in rp.coins.values():
        if c.outcome != "traded":
            continue
        h = c.honest
        (et, epx, ebase, efee), (xt, xpx, xbase, xfee) = h.entry, h.exit
        sol_out = _sell(h.tokens, xpx, xbase, r.cost)
        rec = (records or {}).get(c.mint) or {}
        trades.append({
            "mint": c.mint, "sym": rec.get("sym"),
            "entryS": round((et - c.t0) / 1000, 1), "exitS": round((xt - c.t0) / 1000, 1),
            "entryPrice": epx, "exitPrice": xpx, "exit": h.reason,
            "solIn": r.size_sol, "solOut": round(sol_out, 9),
            "returnPct": round((sol_out / r.size_sol - 1) * 100, 2),
            "priceMovePct": round((xpx / epx - 1) * 100, 2),       # between the two fills, before costs
            "realFeePct": [None if x is None else round(x * 100, 3) for x in (efee, xfee)],
            "gap": c.gap,
        })
        rets.append(sol_out / r.size_sol - 1)
        moves.append(xpx / epx - 1)
        if c.naive.entry_px and c.naive.exit_px:
            naive.append(c.naive.exit_px / c.naive.entry_px - 1)
    sol_in = r.size_sol * len(trades)
    pnl = sum(t["solOut"] for t in trades) - sol_in
    fees = [x for t in trades for x in t["realFeePct"] if x is not None]
    stops = [t["priceMovePct"] for t in trades if t["exit"] == "stop"]
    outcome = None
    if outcomes is not None:
        n = len(outcomes)
        dumps = [o["dumpS"] for o in outcomes if isinstance(o.get("dumpS"), (int, float))]
        hit = lambda key, test: sum(1 for o in outcomes if isinstance(o.get(key), (int, float)) and test(o[key]))
        outcome = {
            "graduates": n,
            "touchedMinus80Pct": round(hit("minDrop", lambda v: v <= -80) / n * 100, 1) if n else None,
            "touched2xPct": round(hit("maxGain", lambda v: v >= 100) / n * 100, 1) if n else None,
            "touched3xPct": round(hit("maxGain", lambda v: v >= 200) / n * 100, 1) if n else None,
            "firstDumpMedianS": round(_median(dumps), 1) if dumps else None,
            "withDump": len(dumps),
        }
    counts = {**rp.counts,
              "beforeFile": len(rp.before_file - rp.seen),
              "tested": len(rp.coins),
              "neverReachedEntry": sum(1 for c in rp.coins.values() if c.outcome == "never"),
              "cut": sum(1 for c in rp.coins.values() if c.outcome == "cut"),
              "traded": len(trades)}
    exits: Dict[str, int] = {}
    for t in trades:
        exits[t["exit"]] = exits.get(t["exit"], 0) + 1
    span = None
    if rp.first_t is not None:
        a = datetime.fromtimestamp(rp.first_t / 1000, tz=timezone.utc)
        b = datetime.fromtimestamp(rp.last_t / 1000, tz=timezone.utc)
        span = {"from": a.strftime("%Y-%m-%d %H:%M"), "to": b.strftime("%Y-%m-%d %H:%M"),
                "hours": round((rp.last_t - rp.first_t) / 3_600_000, 1)}
    return {
        "rules": asdict(r),
        "files": {"gradtrades": gt.name, "pregrad": Path(pg).name if pg is not None else None, "utc": span},
        "coins": counts,
        "summary": {
            "trades": len(trades),
            "wins": sum(1 for x in rets if x > 0),
            "winRatePct": round(sum(1 for x in rets if x > 0) / len(rets) * 100, 1) if rets else None,
            "medianReturnPct": _pct(_median(rets)), "meanReturnPct": _pct(_mean(rets)),
            "solIn": round(sol_in, 9), "pnlSol": round(pnl, 9),
            "totalReturnPct": round(pnl / sol_in * 100, 2) if sol_in else None,
            "exits": exits,
            "stopFillMedianPct": round(_median(stops), 1) if stops else None,
            "stopFillWorstPct": round(min(stops), 1) if stops else None,
            "realFeeMeanPct": round(_mean(fees), 3) if fees else None,
            "gapTrades": sum(1 for t in trades if t["gap"]),
        },
        "beforeCosts": {"medianReturnPct": _pct(_median(moves)), "meanReturnPct": _pct(_mean(moves))},
        "naive": {"trades": len(naive), "medianReturnPct": _pct(_median(naive)), "meanReturnPct": _pct(_mean(naive)),
                  "winRatePct": round(sum(1 for x in naive if x > 0) / len(naive) * 100, 1) if naive else None},
        "worst": sorted(trades, key=lambda t: t["returnPct"])[:3],
        "best": sorted(trades, key=lambda t: -t["returnPct"])[:1],
        "outcomes": outcome,
        "trades": trades,
    }


# ---------------------------------------------------------------------------------------------------------------------
# The printed report

def _p(x: Optional[float], sign: bool = True) -> str:
    if x is None:
        return "n/a"
    return f"{x:+.1f}%" if sign else f"{x:.1f}%"


def _name(t: Dict[str, Any]) -> str:
    return (t.get("sym") or "").strip()[:12] or t["mint"][:6] + "..."


def _secs(s: float) -> str:
    return f"{s:g} s" if s < 120 or s % 60 else f"{s / 60:g} min"


def _rules_lines(r: Dict[str, Any]) -> List[str]:
    exits = [f"sell {_secs(r['exit_s'])} after graduation" if r["exit_s"] < WINDOW_S else "hold to +30 min"]
    exits.append(f"stop {-r['stop_pct']:g}%" if r["stop_pct"] is not None else "no stop")
    exits.append(f"take profit {r['take_profit_pct']:+g}%" if r["take_profit_pct"] is not None else "no take-profit")
    filt = []
    if r["max_fill_s"] is not None:
        filt.append(f"curve filled within {r['max_fill_s']:g} s")
    if r["min_fill_s"] is not None:
        filt.append(f"curve took {r['min_fill_s']:g} s or more")
    if r["min_dev_pct"] is not None:
        filt.append(f"dev held {r['min_dev_pct']:g}%+ at graduation")
    if r["max_dev_pct"] is not None:
        filt.append(f"dev held {r['max_dev_pct']:g}% or less")
    return [
        f"  entry    buy {_secs(r['entry_s'])} after graduation",
        f"  exit     {', '.join(exits)}",
        f"  size     {r['size_sol']:g} SOL a trade",
        f"  filters  {', '.join(filt) if filt else 'none'}",
        f"  costs    {r['fee_pct']:g}% PumpSwap fee + {r['slippage_pct']:g}% priority/slippage, each side",
    ]


def backtest_report(res: Dict[str, Any]) -> str:
    """The result as the plain-text report the command prints."""
    r, c, s, o = res["rules"], res["coins"], res["summary"], res["outcomes"]
    span = res["files"]["utc"]
    out: List[str] = ["LAUNCH TAPE BACKTEST: your rules on what actually happened", ""]
    if span:
        out.append(f"Data     {res['files']['gradtrades']}")
        out.append(f"         {span['from']} to {span['to'][-5:]} UTC ({span['hours']:g} h)")
    out += ["", "Rules"] + _rules_lines(r) + [
        "  fills    at the end of the first slot after each signal, never at the",
        "           signal's own price; your size moves the pool price too",
        "", "Coins",
        f"  graduations in the file           {c['graduations']:>5}",
        f"  no trades recorded (not followed) {c['not_followed']:>5}",
    ]
    if r and any(r[k] is not None for k in _FILTERS):
        out.append(f"  left out by your filters          {c['filtered']:>5}")
        out.append(f"  no pregrad facts to filter on     {c['no_record']:>5}")
    if c["late"]:
        out.append(f"  picked up late (entry not known)  {c['late']:>5}")
    out += [
        f"  never reached your entry          {c['neverReachedEntry']:>5}",
        f"  file ended before the trade did   {c['cut']:>5}   (left out)",
        f"  coins traded                      {c['traded']:>5}",
        "",
    ]
    if not s["trades"]:
        out += ["No trades: nothing to score. Loosen the rules or use a longer file.", ""]
    else:
        ex = s["exits"]
        exit_txt = ", ".join(f"{ex[k]} {lab}" for k, lab in (("time", "on time"), ("stop", "stop"),
                                                               ("take_profit", "take-profit"), ("end", "at +30 min"))
                             if ex.get(k))
        out += [
            "Results, after costs",
            f"  win rate                {_p(s['winRatePct'], False):>8}   ({s['wins']} of {s['trades']})",
            f"  median return a trade   {_p(s['medianReturnPct']):>8}",
            f"  mean return a trade     {_p(s['meanReturnPct']):>8}",
            f"  total                   {s['pnlSol']:>+8.3f} SOL on {s['solIn']:g} SOL put in ({_p(s['totalReturnPct'])})",
            f"  exits                   {exit_txt}",
        ]
        if s["stopFillMedianPct"] is not None:
            out.append(f"  stops filled at         {_p(s['stopFillMedianPct']):>8}   median price move, stop set at "
                       f"{-r['stop_pct']:g}%")
        if s["realFeeMeanPct"] is not None:
            out.append(f"  real PumpSwap fee       {s['realFeeMeanPct']:>7.2f}%   on these fill trades (assumed "
                       f"{r['fee_pct']:g}%)")
        if s["gapTrades"]:
            out.append(f"  feed gaps               {s['gapTrades']:>8}   trades overlapped a recorded gap (rows may be missing)")
        out.append("  worst 3 trades")
        for t in res["worst"]:
            out.append(f"    {_name(t):<12} {t['returnPct']:>+7.1f}%   in at +{t['entryS']:g} s, out at +{t['exitS']:g} s"
                       f" ({t['exit'].replace('_', '-')})")
        for t in res["best"]:
            out.append(f"  best trade  {_name(t):<12} {t['returnPct']:>+7.1f}%")
        n = res["naive"]
        out += ["", "The same rules at the signal's own price, no costs or size impact",
                f"  median {_p(n['medianReturnPct'])}, mean {_p(n['meanReturnPct'])}, win rate {_p(n['winRatePct'], False)}"
                "  <- what a naive backtest shows", ""]
    if o:
        out += [
            f"What the first 10 minutes did ({o['graduates']} graduates with an outcome row)",
            f"  touched -80%            {_p(o['touchedMinus80Pct'], False):>8}   (vs the price 10 s after graduation)",
            f"  touched 2x              {_p(o['touched2xPct'], False):>8}",
            f"  touched 3x              {_p(o['touched3xPct'], False):>8}",
        ]
        if o["firstDumpMedianS"] is not None:
            out.append(f"  first big dump          {o['firstDumpMedianS'] / 60:>7.1f} min   median after graduation "
                       f"({o['withDump']} coins had one)")
        out.append("")
    out += ["Verdict"] + ["  " + line for line in _wrap(verdict(res), 74)] + [""]
    more = day_offer(res)
    if more:
        out += [more, ""]
    out += ["The 3 mistakes we see most"] + ["  " + line for m in mistakes(res) for line in _wrap(m, 74, "   ")]
    return "\n".join(out) + "\n"


def _wrap(text: str, width: int, indent: str = "") -> List[str]:
    words, lines, cur = text.split(), [], ""
    for w in words:
        if cur and len(cur) + 1 + len(w) > width:
            lines.append(cur)
            cur = indent + w
        else:
            cur = f"{cur} {w}" if cur else w
    if cur:
        lines.append(cur)
    return lines


DAY_URL = "https://launchtape.io/?plan=day1#buy"
_CUT = re.compile(r"-\d{4}-\d{4}utc\.(jsonl\.gz|parquet)$")


def day_offer(res: Dict[str, Any]) -> Optional[str]:
    """On the free sample (a 2-hour cut: "...-1400-1600utc"), one line on getting a full day; None on any other file."""
    span, n = res["files"]["utc"], res["summary"]["trades"]
    if not span or not _CUT.search(res["files"]["gradtrades"] or ""):
        return None
    h = span["hours"]
    return (f"{h:g} hour{'' if h == 1 else 's'}, {n} trade{'' if n == 1 else 's'}. "
            f"A full day of the same rules is one file: $10 at {DAY_URL}")


def verdict(res: Dict[str, Any]) -> str:
    """One plain paragraph on what the numbers say."""
    s, span = res["summary"], res["files"]["utc"]
    n = s["trades"]
    if not n:
        return "These rules made no trades on this file, so there is nothing to judge yet."
    hours = span["hours"] if span else None
    lost = s["pnlSol"] < 0
    parts = [f"On this data your rules {'lost' if lost else 'made'} {abs(s['pnlSol']):.3f} SOL over {n} trades "
             f"({_p(s['totalReturnPct'])} of what went in), after costs."]
    med, mean = s["medianReturnPct"], s["meanReturnPct"]
    if med is not None and mean is not None:
        if med < 0 <= mean:
            parts.append("The typical trade lost; a few big winners carried the average, and those are the hardest "
                         "to count on.")
        elif med >= 0 > mean:
            parts.append("The typical trade won a little, but the losers were bigger than the winners.")
        elif med < 0 and mean < 0:
            parts.append("Most trades lost money and the average did too.")
        else:
            parts.append("Most trades made money, and so did the average.")
    nv = res["naive"]
    if nv["meanReturnPct"] is not None and mean is not None and nv["meanReturnPct"] > mean:
        parts.append(f"Filled the naive way (the signal's own price, no costs) the same rules look "
                     f"{nv['meanReturnPct'] - mean:.1f} points better per trade on average: that gap is money a "
                     "backtest promises and the market does not pay.")
    if hours is not None and hours < 24:
        parts.append(f"{n} trades from {hours:g} hours of data is a small sample: one coin can swing these numbers. "
                     "Run it on full days before you risk real money.")
    elif n < 200:
        parts.append(f"{n} trades is still a small sample; more days will tell you more.")
    return " ".join(parts)


def mistakes(res: Dict[str, Any]) -> List[str]:
    """The three mistakes, with this run's numbers where it has them."""
    s, r, nv, bc, o = res["summary"], res["rules"], res["naive"], res["beforeCosts"], res["outcomes"]
    round_trip = (1 - (1 - (r["fee_pct"] + r["slippage_pct"]) / 100) ** 2) * 100
    one = "1. Filling at the signal's own price. Your bot sees a price, then sends a transaction that lands in a " \
          "later block, and in a dump later is lower."
    moved = (nv["meanReturnPct"] is not None and bc["meanReturnPct"] is not None
             and abs(nv["meanReturnPct"] - bc["meanReturnPct"]) >= 0.5)
    if moved:
        one += f" Here, filling a block later moved the average trade from {_p(nv['meanReturnPct'])} to " \
               f"{_p(bc['meanReturnPct'])} before any costs."
    elif bc["meanReturnPct"] is not None:
        one += f" Here it evened out on average ({_p(nv['meanReturnPct'])} vs {_p(bc['meanReturnPct'])} before costs)"
        one += (f", but not on stops: set at {-r['stop_pct']:g}%, they filled at {_p(s['stopFillMedianPct'])} "
                f"(median), the worst at {_p(s['stopFillWorstPct'])}." if s["stopFillMedianPct"] is not None else ".")
    two = f"2. Leaving out fees. {r['fee_pct']:g}% PumpSwap fee + {r['slippage_pct']:g}% priority/slippage each side " \
          f"is about {round_trip:.1f}% of every round trip"
    if bc["medianReturnPct"] is not None and s["medianReturnPct"] is not None:
        two += f"; here costs took the median trade from {_p(bc['medianReturnPct'])} to {_p(s['medianReturnPct'])}."
    else:
        two += "."
    three = "3. Sizing as if you'll catch the few that 3x."
    if o and o["touched3xPct"] is not None:
        three += f" Only {o['touched3xPct']:.1f}% of these graduates touched 3x in their first 10 minutes, and " \
                 f"{o['touchedMinus80Pct']:.1f}% touched -80%."
    three += " Size every trade as if it's one of the losers, because most are."
    return [one, two, three]
