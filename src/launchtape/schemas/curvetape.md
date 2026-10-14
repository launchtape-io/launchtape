# curvetape schema

Every trade on every pump.fun bonding curve, from the coin's creation to its graduation (or until it goes quiet), plus creations, graduations, migrations to PumpSwap and the fee picture of each creation transaction. One JSON object per line. Every row has `type` and `t` (ms since 1970 UTC, when our recorder wrote it; rows are in `t` order). Chain time is `bt` (seconds) and `slot`. SOL amounts in SOL, token amounts in whole tokens (6 decimals); fields ending in `Q`, `qAmt`, `vq`, `rq`, `vs`, `vt`, `rt`, `supply` are raw integers (base units). Source: Solana public RPC (pump.fun program logs, `confirmed`), from 2026-10-12 18:16 UTC; first full day 2026-10-13.

**Parquet:** every day file also ships as `curvetape-YYYY-MM-DD.parquet`, with the same fields as columns: one table for all row types (a field a row does not have is null), `t` integer ms, nested objects as structs and arrays as lists.

| type | field | type | meaning |
|---|---|---|---|
| create | slot, sig, bt | number, string, number | the creation transaction |
| create | mint, curve | string | the coin and its bonding-curve account |
| create | user, creator | string | wallet that sent the creation / the coin's creator (receives creator fees) |
| create | name, sym, uri | string | name, ticker, metadata link, as written on chain |
| create | tokenProgram | string | Token-2022 or classic token program |
| create | mayhem | bool | pump.fun "mayhem mode" coin |
| create | quote | string/null | null = priced in SOL; else the mint the curve is priced in |
| create | vt, vs, rt, supply, vq | number | starting virtual tokens, virtual SOL, real tokens, supply, virtual quote reserves (raw) |
| trade | slot, sig, i, bt | number, string, number, number | the transaction; `i` = index of this trade among pump.fun trades in it, so (`sig`, `i`) is unique |
| trade | off | number/null | slots since the coin's creation slot (null when the creation was not seen) |
| trade | mint, side, wallet, tokens | string, "buy"/"sell", string, number | who traded how many tokens |
| trade | sol | number | SOL curves: SOL into (buy) / out of (sell) the curve, WITHOUT fees (a buyer paid `sol + fee + cfee`, a seller got `sol - fee - cfee`) |
| trade | fee, cfee | number | SOL curves: pump.fun protocol fee / creator fee, SOL (0.95 % / 0.30 % in Oct 2026; mayhem coins differ) |
| trade | capAfter, curveSol | number | SOL curves: market cap in SOL after the trade (price x 1e9 supply); real SOL in the curve after it |
| trade | vs, vt | number | virtual SOL (SOL curves only) / virtual token reserves after the trade (raw) |
| trade | quote, qAmt, vq, rq, feeQ, cfeeQ | string, number | curves priced in another token: its mint; the amount, virtual / real quote reserves and the two fees in its raw units. These rows have NO `sol` / `capAfter` / `curveSol` / `fee` (never a fake 0). `quote` "?" = amounts unreadable |
| trade | ix | string | instruction: `buy`, `sell`, `buy_exact_sol_in`, `buy_exact_quote_in`, `multi_hop_swap` (a swap routed through the curve's quote token in one instruction; almost all on curves priced in another token) |
| trade | byCreator | bool | present (true) when the trader is the coin's creator |
| trade | mayhem | bool | present (true) on mayhem-mode coins |
| trade | trunc, rec | bool | the transaction's logs were cut off (later trades in it come as `rec` rows read from the full transaction, or are missing) |
| adopt | slot, mint, why, creator, quote | | a coin followed from a trade, not its creation: `noCreate` (created before recording / during a gap: earlier trades missing) or `revive` (traded again after ending idle); `quote` null = a SOL curve |
| complete | slot, sig, bt, mint, curve, user | | the bonding curve completed (graduation) |
| migrate | slot, sig, bt, mint, pool, quote | | the coin moved to its PumpSwap pool |
| migrate | solAmount / quoteAmount, mintAmount, poolMigrationFee, user | number, string | SOL (or raw quote) and tokens moved into the pool, migration fee (SOL), sender |
| end | mint, why, src | string | coin no longer followed: `graduated`, `idle` (60 min without a trade), `evicted` (over 20,000 followed); `src` create / adopt |
| end | trades, buys, sells, vol, volQ, lastCap, maxCap, firstT, lastTradeT | number | totals seen while followed (vol in SOL for SOL curves, volQ raw otherwise; caps in SOL) |
| tx | sig, mint, role, slot, off | | fee picture of one transaction: `role` create (every coin's creation, as the rate allows) or early (a buy within 2 slots of the creation) |
| tx | version, payer, nsig | | transaction version (0, 1, "legacy"), fee payer, signatures |
| tx | txFee, prioFee, jitoTip | number | network fee paid, priority part of it (fee minus 5,000 lamports a signature), SOL sent to Jito tip accounts; SOL |
| tx | cuPrice, cuLimit, cuUsed, prioCfg | number | compute-unit price (micro-lamports, v0 transactions), limit, used; `prioCfg` = a v1 transaction's own priorityFee setting |
| tx | failed | bool | the transaction failed |
| gap | from, to, why, ms | number, number, string, number | the live feed was off between `from` and `to`. why: socket, yield413 / boxcap / selfcap (backed off to stay inside the shared public-RPC allowance), refused, start, stop. Since 2026-10-14 a `backfill` row follows: when it says `complete: true` nothing in the window is missing |
| create, trade, complete, migrate | src | "backfill" | since 2026-10-14: present only on rows repaired after a gap, read from whole blocks; the same fields and values as live rows. Live rows have no `src` |
| backfill | from, to, why | number, number, string | since 2026-10-14: one gap repaired from the chain (whole blocks via a paid RPC): its window (ms) and reason (`restart` = the recorder restarted) |
| backfill | slotFrom, slotTo, blocks, skipped, failed, txs, rows | number, object | slots read, blocks read, slots with no block, slots that could not be read, transactions with pump.fun events, rows written by type |
| backfill | complete, stop | bool, string | `complete: true` = every slot of the window was read; false: `stop` says why (`nocredit`, `maxblocks`, `noslot`, `nohelius`) and slots after `slotTo` stay missing |

Failed transactions are not included. Coverage gaps are written as `gap` rows, never filled in: a `gap` row says the live feed was off from `from` to `to`; trades in that window are missing unless a `backfill` row for it says `complete: true`. Left out before sale: recorder bookkeeping (`start`, `stats` rows).

Versions: files from 2026-10-13 carry `schemaVersion` 2 in their manifest (see `CHANGELOG.md`). The 2026-10-13 file has no `src` / `backfill` rows (gap repair started 2026-10-14 02:16 UTC); its 5 `gap` rows (why `boxcap`) are real holes. Rows a day (2026-10-13): 4,480,064 (trade 4,325,090, end 75,317, create 42,722, adopt 32,597, tx 2,343, migrate 1,161, complete 829, gap 5).

Checks we ran (2026-10-12 18:32-18:42 UTC): on SOL curves outside gaps, every trade's `vs` follows exactly from the previous trade's (`vs` before = `vs` after the previous trade; 17,061 of 17,061 consecutive pairs), so no trade was missed while the feed was on. Mayhem-mode coins (`mayhem` true) are the exception: pump.fun also moves their virtual reserves between trades, so their `vs` does not chain. `sol + fee + cfee` matched a real 0.1 SOL buy to the lamport.
