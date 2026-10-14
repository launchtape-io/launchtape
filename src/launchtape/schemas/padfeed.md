# padfeed schema

Graduations on two non-pump.fun Solana launchpads: `dbc` = Meteora Dynamic Bonding Curve (Bags, Jupiter Studio, ...) -> DAMM v2 pool, `ray` = Raydium LaunchLab (bonk.fun etc.) -> Raydium CPMM pool. Each coin followed 20 min (dbc) / 30 min (ray). Source: Solana RPC; US$ quote prices from Jupiter's free price API.

**Parquet:** every day file also ships as `padfeed-YYYY-MM-DD.parquet`, with the same fields as columns: one table for all row types (a field a row does not have is null), `t` integer ms, nested objects as structs and arrays as lists; `usd` is a map from quote mint to US$ price.

| type | field | type | meaning |
|---|---|---|---|
| all | t | number | ms when recorded |
| grad | pad | "dbc"/"ray" | launchpad |
| grad | platform | string | platform config / name |
| grad | mint, quote | string | coin mint; quote mint (SOL, or a token such as USD1/a tokenized stock) |
| grad | pool, curveAuth, baseVault, quoteVault | string/null | pool and vault accounts; `pool` is null on every dbc row (the DAMM v2 pool is not known when the migration is heard; the vaults identify it) and set on ray rows |
| grad | b, q | number | vault balances (tokens, quote units) at migration |
| grad | px | number | price in quote = q / b |
| grad | supply, decimals, tokenProgram | number, number, string | mint facts (Token or Token-2022) |
| grad | feeBps, freeze, mintAuth | | Token-2022 transfer fee, freeze / mint authorities (null = none) |
| grad | sig, blockTime, lagMs | string, number, number | migration signature, its block time (ms, not seconds like `bt` elsewhere) and how long after the block we heard of it (ms) |
| px | pad, mint, b, q | | vault balances when they changed (every ~6 s, ~2 s for fast-moving coins); price = q / b |
| holders | pad, mint, top10, top20[] | | +60 s and +5 min: 20 largest token accounts (`acct`, `owner`, `amt`); `top10` = share held by top 10 excluding pool vaults and curve |
| qpx | usd | object | every 30 s: US$ price of SOL and of every non-SOL quote mint seen in the last 2 h, keyed by mint |
