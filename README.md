# launchtape

Python access to [Launch Tape](https://launchtape.io): daily files of Solana launch data, one gzipped JSON-lines
file per dataset per UTC day.

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
| `lt.sample(dataset)` | downloads the free sample into `~/.cache/launchtape`, checks it against the site's `SHA256SUMS`, returns its path; a good cached copy is reused |
| `lt.load(path_or_dataset, types=None, limit=None)` | streams the rows as dicts; `types` keeps only rows whose `type` is one of them, `limit` stops after that many rows; a dataset name reads its sample |
| `lt.frame(path_or_dataset, types=None, limit=None)` | the same rows as a pandas DataFrame (nested fields stay dicts/lists) |
| `lt.fetch(link_or_token, dataset, day, dest=None)` | downloads a paid day file from your delivery link, checks it against its `.manifest.json`, returns its path |

```python
for row in lt.load("gradtrades", types="grad", limit=5):      # streams, so a 5-million-row day is fine
    print(row["mint"], row["capSol"])

path = lt.fetch("https://launchtape.io/dl/<token>", "pregrad", "2026-10-11", dest="data/")
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
launchtape head gradtrades -n 5               # first rows of the sample, as JSON lines
launchtape head pregrad -t record -n 2        # only rows of one type
launchtape schema pregrad
launchtape fetch https://launchtape.io/dl/<token> pregrad 2026-10-11 -o data/
```

`python -m launchtape ...` works too.

## Full days: prices and how to buy

Day file (one dataset, one UTC day): 0.05 SOL or 6 USDC. Month of history (one dataset, 30 days): 0.5 SOL or
60 USDC. Daily feed (every dataset, each new day as it closes): 1 SOL or 120 USDC a month. Send SOL or USDC on
Solana to `5vf1PzK8LrF9D5bemYJYPsFYp4nHULSTA1biBampgU58` with a memo giving your email address or Telegram handle
and the datasets and days you want; within 24 hours you get a private download link
(`https://launchtape.io/dl/<token>`, valid 7 days) that `launchtape fetch` reads. Or buy through
[launchtape.io](https://launchtape.io/#buy), which has a Solana Pay QR code and a contact form.

## Licences

- **This package** (the code): MIT, see [LICENSE](LICENSE).
- **The data** it downloads, the free samples included, has its own licence: personal and research use inside
  your own team. No resale, republishing or redistribution of the files, in whole or in part. Results you derive
  from it (charts, papers, models, strategies) are yours. Full text: [launchtape.io](https://launchtape.io/#licence).

The data is market data recorded from public sources, provided as is. It is not financial advice.
