# pregrad schema

Who is behind each pump.fun launch, as raw facts (addresses, amounts, times). No wallet labels, no risk flags, no images. Sources: PumpPortal free websocket (creations, migrations), Solana public RPC (bonding curves, transactions). Amounts in SOL; `pct` = % of the 1,000,000,000 supply; times in ms unless the name ends in `S` (seconds).

**Parquet:** every day file also ships as `pregrad-YYYY-MM-DD.parquet`, with the same fields as columns: one table for all row types (a field a row does not have is null), `t` integer ms, nested objects as structs and arrays as lists; `curve`, `src` and `cross` (text in some row types, an object or a number in others) are text columns, an object or number as its JSON text.

| type | field | type | meaning |
|---|---|---|---|
| create | t, mint, sig | number, string, string | coin creation |
| create | creator | string | creator wallet |
| create | name, sym | string | coin name / ticker |
| create | devTokens, devSol | number | creator's own buy at creation |
| create | capSol | number | curve market cap right after creation |
| create | curve | string | bonding-curve account |
| create | mayhem | bool | pump.fun "mayhem mode" launch |
| curvex | slot, curve, mint | number, string, string/null | bonding-curve cap crossing (mint null when not yet mapped) |
| curvex | cap, cross, sol, sinceFirstS | number | cap now (SOL), line crossed (100/200/300/400 SOL), SOL in curve, seconds since first seen |
| ctrade | bt, slot, sig, i, mint, side, user | | bonding-curve trade on a coin near graduation (read from cap 200 SOL up); `i` counts this coin's trades inside the transaction, so (`sig`, `mint`, `i`) is the unique key (one transaction can trade two coins) |
| ctrade | sol, tokens, capAfter, curveSol | number | SOL and tokens of the trade, cap and curve SOL after it. Note: pump.fun also runs curves priced in another token instead of SOL (the PUMP coin and a few others). In files up to 2026-10-12 17:47 UTC those coins' rows carry `sol` 0, `capAfter` null, `curveSol` 0 (the amount really is zero SOL; the trade was in the other token): treat `capAfter` null as "not a SOL curve". From 2026-10-12 17:47 UTC the recorder no longer follows non-SOL curves, so new files hold SOL curves only |
| migrate | mint, sig, pool | string | migration seen by PumpPortal (`pool` = destination AMM) |
| grad | mint, slot, capSol, creator | | graduation as seen on chain |
| record | mint, sym, name, pool, slot0, sig0, t0, bt0 | | one per graduate, written 10-40 s after graduation (t0 = graduation) |
| record | src | object | `first` = which feed saw it first, `gradRow` = a `grad` row exists, `migrate` = ms of the PumpPortal migration (or null), `curveCompleteS` = curve completed how long before t0 |
| record | creator, create | string, object | creator wallet; creation tx (`t`, `sig`, `src`, `slot`, `mayhem`, `capSol`), null when the creation was not seen |
| record | ageS | number | creation to graduation, seconds |
| record | instant | bool | graduated within 5 s of creation |
| record | cap0, curveSol | number | PumpSwap cap at graduation; SOL raised |
| record | devBuyTokens, devBuyPct, devBuySol | number | creator's buy at creation |
| record | creatorPctAtGrad | number/null | creator's holding at graduation |
| record | creatorInfo | object | `txs50` (tx count, capped 50), `capped`, `oldestS`, `nBefore10m`, `funder`, `fundSol`, `fundedBeforeS`, `fundMemo`, `priorCoins24h`, `priorCoins72h` |
| record | gradSlot | object/null | PumpSwap buys in the graduation slot: `pct`, `sol`, `nWallets`, `nTx`, `buys[]` (`w`, `tokens`, `pct`, `sol`, `sig`, `bundleSize`, `payer`, `signers`, `slotOffset`, `creatorInTx`), `whale` = the largest of those buys with its wallet facts (`txs25`, `oldestS`, `lastTxKind`, `funder`, `fundSol`, `fundedBeforeS`, `fundSig`, `fundMemo`, `gainers[]`) |
| record | curve | object | `coverage` (full/partial/none), `holders[]` (top 10: `w`, `pct`, `sol`, `creator`), `creationSlot` (`ok`, `failed`, `curveTxs`, `capped`: transactions in the creation slot), `bundleEst` (`buyers`, `avgPct`, `top5Pct`), `estimated`, `top1NcPct`, `top5NcPct`, `nOver3Pct`, `bundleWallets`, `bundlePct`, `creatorPct`, `path` (`firstCap`, `firstS`, `t100S`..`t400S`, `maxCap`, `updates`, `completeS`: the cap over time), `trades` (`fromCap`, `fromT`, `n`, `buys`, `sells`, `wallets`, `maxCap`, `lastCap`, `completeT`, `topBuyers[]` with `w`, `tokens`, `pct`, `sol`, `creator`: our own count of its curve trades) |
| outcome | mint, sym, t0, recT | | +10 min after graduation |
| outcome | capAt | object | PumpSwap cap (SOL) at +10/45/60/120/180/300/600 s |
| outcome | maxGain, minDrop | number/null | best / worst move in %, first 10 min, vs the price 10 s after graduation (`capAt` "10"; the first trade's price if there was no trade by then). "Touched -80%" = `minDrop` <= -80; "touched 2x" = `maxGain` >= 100. Null when no PumpSwap trades were seen (`trades` 0) |
| outcome | crashed | bool | a trade at 60% of the running high or lower within the first 10 min, i.e. a 40%+ fall from the high (the high starts at the price 10 s after graduation; trades in the first 2 s are not checked). Not an 80% drop: for "touched -80%" use `minDrop` <= -80. False when no PumpSwap trades were seen |
| outcome | trades | number | PumpSwap trades counted |
| outcome | dump, firstBigSell, bigSells[] | object | the first sale breaking a fixed size rule and the large sales: `tS`, `w`, `frac` (share of pool sold), `sol`, `capAfter`, `rule`, `tokensFrom` (where the sold tokens came from: `kind`, `from`, `for`, `tokens`, `pct`, `sig`, `bt`, `account`, `accountTxs`, `beforeDumpS`, `afterGradS`) |
| outcome | src | string | where trades were read from |

Removed before sale: our internal "rogue" wallet list and every field derived from it, `flags`, `priorRugged`, the dumper `role`, picture fingerprints and metadata URIs, heartbeat/stats rows.
