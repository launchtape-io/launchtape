# meteora-pools schema

One row per pool per minute, Meteora DLMM pools with TVL >= US$50k, top 200 by 24h fee/TVL. Source: Meteora's public DLMM API (dlmm.datapi.meteora.ag). No RPC, no wallets.

| field | type | meaning |
|---|---|---|
| type | "pool" | row type |
| t | number | ms when fetched |
| pool | string | DLMM pool address |
| price | number | pool's current price (quote per base token, as Meteora reports it) |
| tvlUsd | number | total value locked, US$ |
| vol24hUsd | number | rolling 24 h volume, US$ |
| fees24hUsd | number | rolling 24 h fees, US$ |
| vol24hDeltaUsd | number/null | change in the rolling 24 h volume since the pool's previous row |
| feesMinUsd | number/null | exact fees earned since the previous row (diff of Meteora's all-time cumulative fees); null on a pool's first row or a reset |

Token mints and names are not in the row; look them up from the pool address (Meteora API or chain).
