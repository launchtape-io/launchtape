# launchtape

Python access to [Launch Tape](https://launchtape.io): daily files of Solana launch data, one file per dataset per
UTC day, as gzipped JSON lines (`.jsonl.gz`) and as Parquet (`.parquet`, the same rows as typed columns).

| Dataset | What it is |
|---|---|
| `gradtrades` | Every PumpSwap buy and sell in the first 30 minutes after each pump.fun graduation (wallet, amounts, price, pool reserves, fees) |
| `pregrad` | Who is behind each pump.fun launch: creator and its funding, curve and graduation-slot buyers, the cap path, and a +10 min outcome |
| `padfeed` | Graduations on Meteora DBC and Raydium LaunchLab: migration facts, pool balances every few seconds, top holders, US$ quote prices |
| `meteora-pools` | Per-minute price, TVL, volume and exact fees for ~200 Meteora DLMM pools |

A free 2-hour sample of every dataset is on the site; `launchtape` downloads it, checks its SHA-256 and reads it.

## Install

```sh
pip install launchtape              # needs only requests
pip install "launchtape[pandas]"    # adds pandas for launchtape.frame()
pip install "launchtape[parquet]"   # adds pyarrow, to read the Parquet files
```

Python 3.9 or newer.

## Quickstart

Crash rate of pump.fun graduates, split by "instant" launches (graduated within 5 s of creation):

```python
import launchtape as lt

path = lt.sample("pregrad")                                    # free sample, SHA-256 checked, cached
rec = lt.frame(path, types="record")[["mint", "sym", "instant", "ageS"]]
out = lt.frame(path, types="outcome")[["mint", "crashed", "maxGain"]]
df = rec.merge(out, on="mint")                                 # one row per graduate seen in both
print(df.groupby("instant")["crashed"].agg(["mean", "count"]))
print(lt.schema("pregrad"))                                    # every row type and field
```

The sample is two hours, so the counts are small: it shows the shape of the data, not a result.

## API

| Call | Does |
|---|---|
| `lt.datasets()` | the datasets as `(name, description)` pairs |
| `lt.schema(dataset)` | the schema as Markdown text (bundled, works offline) |
| `lt.sample(dataset, format="jsonl")` | downloads the free sample into `~/.cache/launchtape`, checks it against the site's `SHA256SUMS`, returns its path; a good cached copy is reused; `format="parquet"` for the Parquet copy |
| `lt.load(path_or_dataset, types=None, limit=None)` | streams the rows as dicts (a `.jsonl.gz` or `.parquet` path); `types` keeps only rows whose `type` is one of them, `limit` stops after that many rows; a dataset name reads its sample |
| `lt.frame(path_or_dataset, types=None, limit=None)` | the same rows as a pandas DataFrame (nested fields stay dicts/lists) |
| `lt.fetch(link_or_token, dataset, day, dest=None, format="jsonl")` | downloads a paid day file from your delivery link, checks it against its `.manifest.json`, returns its path; `format="parquet"` for the Parquet copy |

```python
for row in lt.load("gradtrades", types="grad", limit=5):      # streams, so a 5-million-row day is fine
    print(row["mint"], row["capSol"])

path = lt.fetch("https://launchtape.io/dl/<token>", "pregrad", "2026-10-11", dest="data/")
pq = lt.fetch("https://launchtape.io/dl/<token>", "gradtrades", "2026-10-11", dest="data/", format="parquet")
```

### Parquet

Every day file and sample also comes as Parquet: the same rows, checked against the `.jsonl.gz` before it ships, as
one table whose columns are every field of every row type (null where a row does not have the field). `t` stays integer
milliseconds, nested objects are structs and arrays lists, padfeed's `usd` is a map keyed by quote mint, and pregrad's
`curve`, `src` and `cross` (text in some row types, an object or a number in others) are text columns, an object or
number as its JSON text. Read it with pandas directly, or through `lt.load` / `lt.frame` (needs pyarrow):

```python
import pandas as pd
df = pd.read_parquet(lt.sample("pregrad", format="parquet"))   # loads in a fraction of the JSON time
cols = ["type", "bt", "mint", "side", "user", "quoteAmount"]
trades = pd.read_parquet("data/gradtrades-2026-10-11.parquet", columns=cols)   # only the columns you need
```

Times: `t` is milliseconds since 1970 UTC (`pd.to_datetime(df["t"], unit="ms", utc=True)`). Coverage gaps are rows
in the files (`down`, `up`, `resume`, `end`), never filled in: see each schema.

A full `gradtrades` day is about 5 million rows; for a DataFrame, pass `types=` and/or `limit=`, or stream with
`load()`. A download that does not match its SHA-256 raises `launchtape.ChecksumError` and is not kept.
Environment: `LAUNCHTAPE_CACHE` changes the cache folder.

## Command line

```sh
launchtape datasets
launchtape sample gradtrades                  # prints the cached path
launchtape sample pregrad --format parquet    # the Parquet copy of the sample
launchtape head gradtrades -n 5               # first rows of the sample, as JSON lines
launchtape head pregrad -t record -n 2        # only rows of one type
launchtape schema pregrad
launchtape fetch https://launchtape.io/dl/<token> pregrad 2026-10-11 -o data/
launchtape fetch https://launchtape.io/dl/<token> pregrad 2026-10-11 -o data/ --format parquet
```

`python -m launchtape ...` works too.

## Full days: prices and how to buy

- **Starter, $10 a month:** all 4 datasets, each new day's files as the UTC day closes, plus the last 7 days on file.
- **Full history, $49 a month:** everything in Starter, plus every day on file back to October 3 2026.

Buy at [launchtape.io](https://launchtape.io/#buy): pick a plan, enter your email, press Pay (USDC or SOL on Solana; your
wallet opens with everything filled in). The private download link (`https://launchtape.io/dl/<token>`) arrives by email,
and `launchtape fetch` reads it.

## Licences

- **This package** (the code): MIT, see [LICENSE](LICENSE).
- **The data** it downloads, the free samples included, has its own licence: personal and research use inside
  your own team. No resale, republishing or redistribution of the files, in whole or in part. Results you derive
  from it (charts, papers, models, strategies) are yours. Full text: [launchtape.io](https://launchtape.io/#licence).

The data is market data recorded from public sources, provided as is. It is not financial advice.
