# launchmeta schema

Every new pump.fun coin's metadata (the JSON behind its `uri`) and a sha256 fingerprint of its picture's bytes, at creation and again at graduation. Pictures themselves are never stored or sold. One JSON object per line. Every row has `type` and `t` (ms since 1970 UTC, when our recorder wrote it; rows are in `t` order). Sources: the creation / migration seen on chain (our curvetape feed) or by PumpPortal's free feed, whichever came first; the metadata and picture through public IPFS gateways (2 fetches a second, 10 s timeout). From 2026-10-12 18:23 UTC; first full day 2026-10-13.

**Parquet:** every day file also ships as `launchmeta-YYYY-MM-DD.parquet`, with the same fields as columns: one table for all row types (a field a row does not have is null), `t` integer ms, nested objects as structs and arrays as lists.

| type | field | type | meaning |
|---|---|---|---|
| meta | phase | "create" / "grad" | fetched at creation, or at graduation |
| meta | src | "chain" / "pumpportal" / "chainlogs" | the feed that reported the creation / graduation first: `chain` = our curvetape recorder (the chain's create / migrate events); `pumpportal` = PumpPortal's free feed (through our pregrad recorder); `chainlogs` = pregrad's own detection from the chain's logs, which replaces PumpPortal there from 2026-10-14. A few rows from 2026-10-14 on (about 1 in 400) have no `src`: the feed was not recorded |
| meta | mint, createSig, creator, name, sym | string | the coin, its creation transaction, creator wallet, on-chain name and ticker |
| meta | tCreate, tGrad | number | when the creation / graduation was seen (ms) |
| meta | uri, uriKind, uriFrom | string | the metadata link; `ipfs` / `http` / `data` / `none` / `other` (another scheme); grad rows: where the link came from (`create`, `chain` = read from the mint, `none`) |
| meta | json | object | the metadata JSON's fields, each only when present: `name`, `symbol`, `description` (cut at 2,000 characters), `image`, `website`, `showName`, `createdOn`, and `hasTwitter` / `hasTelegram` (true when the JSON has such a link; the links themselves are left out: no social handles) |
| meta | jsonKeys | array | the JSON's other top-level keys (up to 20) |
| meta | metaSha, metaBytes | string, number | sha256 and size of the metadata JSON bytes |
| meta | imgUrl | string | the picture link from the JSON |
| meta | imgSha, imgBytes, imgType | string, number, string | sha256 of the picture's bytes, its size and Content-Type (the same picture = the same imgSha) |
| meta | gwMeta, gwImg | string | the host that answered |
| meta | ms | number | time spent fetching |
| meta | cachedImg | bool | the same picture link was fingerprinted earlier (same imgSha) |
| meta | cached, changed | bool | grad rows: the creation's result reused (IPFS content cannot change) / the JSON differs from the creation's (http links) |
| meta | err | string | why a fetch failed (`meta: <host> <reason>`, `image: <reason>`, `noUri`); absent when all went well |
| skip | mint, phase, why | string | not fetched: `backlog` (queue full on a very busy hour) |

Failed fetches are rows with `err`, never guessed or retried later; coins not fetched are `skip` rows. There is no other gap row: a coin created while both feeds were off has no row (curvetape's `gap` rows say when the chain feed was off). Left out before sale: recorder bookkeeping (`stats` rows), the twitter / telegram links (replaced by `hasTwitter` / `hasTelegram`).

Versions: files from 2026-10-13 carry `schemaVersion` 2 in their manifest (see `CHANGELOG.md`). Rows a day (2026-10-13): 44,334 `meta` (43,112 at creation, 1,222 at graduation; 1,378 with `err`), no `skip`.
