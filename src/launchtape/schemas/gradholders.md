# gradholders schema

Who holds a pump.fun graduate's tokens 1, 10 and 60 minutes after it graduates: the 20 largest token accounts with owners and shares. One JSON object per line. Every row has `type` and `t` (ms since 1970 UTC, when our recorder wrote it; rows are in `t` order). Token amounts in whole tokens; `pct` = % of supply. Source: Solana RPC (`getTokenLargestAccounts` + `getMultipleAccounts`; a keyed RPC since 2026-10-14 ~02:50 UTC, the public RPC as fallback); graduations from our chain feed or pregrad's, whichever came first. Recorded from 2026-10-12 18:23 UTC; **on sale from 2026-10-14**.

**Parquet:** every day file also ships as `gradholders-YYYY-MM-DD.parquet`, with the same fields as columns: one table for all row types (a field a row does not have is null), `t` integer ms, nested objects as structs and arrays as lists.

| type | field | type | meaning |
|---|---|---|---|
| holders | mint, mark | string, number | the coin; minutes after graduation (1, 10, 60) |
| holders | src, tGrad | string, number | the feed that reported the graduation first: `chain` (our curvetape recorder), `pumpportal` (PumpPortal's free feed through pregrad) or, from 2026-10-14, `chainlogs` (pregrad's own detection from the chain's logs); when it was seen (ms) |
| holders | lagS | number | seconds after `tGrad + mark` the snapshot was actually read |
| holders | slot | number | chain slot of the read |
| holders | supply, decimals | number | token supply (whole tokens) and decimals, read from the mint |
| holders | pool | string | the coin's canonical PumpSwap pool (SOL-quoted graduates) |
| holders | n | number | accounts returned (up to 20) |
| holders | top | array | biggest first: `acct` (token account), `owner` (the holder), `amt`, `pct`, `role` (present only on `pool` = the coin's PumpSwap pool, or `curve` = its old bonding curve), `pda` (present only when true: the owner is a program address, not a plain wallet) |
| holders | top10Pct, top20Pct | number | % of supply in the 10 / 20 largest accounts, pool and curve left out |
| holders | top10PctAll | number | % in the 10 largest, all included |
| holders | poolPct, curvePct | number | % in the pool / the curve |
| holders | tries | number | read attempts (0 = given up before a first try) |
| holders | err | string | the snapshot failed (`429` = the public RPC refused the read, `late` = no answer close enough to its mark); then no holder fields |

No holder count (it needs a full scan of the coin's token accounts). Failed snapshots are written as rows with `err` and no holder fields, never guessed: that is this dataset's gap convention (one row per coin and mark either way, so a missing snapshot is always visible). Left out before sale: recorder bookkeeping (`stats` rows).

Why sale starts 2026-10-14: until 2026-10-14 ~02:50 UTC Solana's public RPC refused nearly every `getTokenLargestAccounts` read, so every snapshot of 2026-10-13 is an `err` row (`late`). That day is not sold. In the 2026-10-14 file the rows before ~02:50 UTC are `err` rows too; from then on snapshots succeed (03:00-13:00 UTC: every snapshot, ~100-170 an hour).

Versions: files carry `schemaVersion` 2 in their manifest (see `CHANGELOG.md`).
