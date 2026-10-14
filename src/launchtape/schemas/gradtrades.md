# gradtrades schema

One JSON object per line. Every row has `type` and `t` (ms since 1970 UTC, when our recorder received it). SOL amounts in SOL, token amounts in whole tokens (6 decimals). Source: Solana public RPC (PumpSwap program logs), first 30 minutes after each pump.fun graduation.

**Parquet:** every day file also ships as `gradtrades-YYYY-MM-DD.parquet`, with the same fields as columns: one table for all row types (a field a row does not have is null), `t` integer ms, nested objects as structs and arrays as lists.

| type | field | type | meaning |
|---|---|---|---|
| grad | t | number | graduation time (ms; chain block time for late pick-ups) |
| grad | bt | number | block time (s) |
| grad | slot / sig | number / string | slot and signature of the graduation (migration) transaction |
| grad | mint | string | the coin's token mint |
| grad | pool | string | its PumpSwap pool |
| grad | coinCreator | string | wallet that receives the coin's creator fees |
| grad | poolBase / poolQuote | number | starting tokens / SOL in the pool |
| grad | virtualQuote | number | PumpSwap's extra virtual SOL in the price math (~17.6 SOL, Oct 2026) |
| grad | price | number | SOL per token at graduation |
| grad | capSol | number | price x 1e9 (market cap in SOL, ~496 for a normal graduate) |
| grad | curveSol | number | SOL raised by the bonding curve |
| grad | late | bool | picked up late; trades before `followFrom` are missing |
| grad | followFrom | number | ms; trades are complete from here on |
| grad | follow | bool | false = coin not followed (cap outside 300-600 SOL, or found too late) |
| trade | bt, slot, sig, i | number, number, string, number | block time (s), slot, signature, index of the PumpSwap trade inside the transaction. A `sig` of 64 `1`s means the RPC did not report the signature (rare: a few rows per million) |
| trade | mint | string | coin |
| trade | side | "buy" / "sell" | direction |
| trade | user | string | wallet that traded |
| trade | baseAmount | number | tokens bought or sold |
| trade | quoteAmount | number | SOL paid (buy, fees included) or received (sell, after fees) |
| trade | price | number | quoteAmount / baseAmount (effective price, fees included) |
| trade | poolBase / poolQuote | number | pool tokens / real SOL after the trade |
| trade | priceAfter | number | pool price after the trade: (poolQuote + virtualQuote) / poolBase |
| trade | virtualQuote | number | only when the pool's virtual SOL changes (rare) |
| trade | userTokensBefore | number | the wallet's balance of this coin just before the trade |
| trade | lpFee / protocolFee / creatorFee | number | fees in SOL |
| trade | creator | bool | true when the trader is the coin's creator |
| trade | ix | string | instruction variant, e.g. `buy_exact_quote_in`; absent for a normal buy |
| trade | recovered | bool | the log was truncated; trade read from the full transaction |
| end | mint, why, buys, sells | string, string, number, number | coin no longer followed: `window` (30 min done), `evicted` (cap of 30 coins), `budget` (data budget); counts recorded |
| down / up | t | number | a feed connection dropped / came back: trades in between may be missing |
| resume | mint | string | coin picked up again after a restart; the gap before `t` is missing |

Failed transactions are not included. Coverage gaps are marked, never filled in.
