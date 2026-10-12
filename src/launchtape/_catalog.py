"""The datasets, their one-line descriptions and their bundled schema docs."""

from __future__ import annotations

from importlib.resources import files
from typing import List, NamedTuple


class Dataset(NamedTuple):
    name: str
    description: str


_DATASETS = (
    Dataset(
        "gradtrades",
        "Every PumpSwap buy and sell in the first 30 minutes after each pump.fun graduation "
        "(wallet, amounts, price, pool reserves, fees)",
    ),
    Dataset(
        "pregrad",
        "Who is behind each pump.fun launch: creator and its funding, curve and graduation-slot "
        "buyers, the cap path, and a +10 min outcome",
    ),
    Dataset(
        "padfeed",
        "Graduations on Meteora DBC and Raydium LaunchLab: migration facts, pool balances every "
        "few seconds, top holders, US$ quote prices",
    ),
    Dataset(
        "meteora-pools",
        "Per-minute price, TVL, volume and exact fees for ~200 Meteora DLMM pools",
    ),
)

DATASETS = tuple(d.name for d in _DATASETS)


def check_dataset(dataset: str) -> str:
    if dataset not in DATASETS:
        raise ValueError(f"unknown dataset {dataset!r}; choose one of: {', '.join(DATASETS)}")
    return dataset


def datasets() -> List[Dataset]:
    """The datasets as (name, description) pairs."""
    return list(_DATASETS)


def schema(dataset: str) -> str:
    """The schema of a dataset as Markdown text (every row type and field)."""
    check_dataset(dataset)
    # one name per joinpath: Python 3.9's Traversable takes a single argument
    return files("launchtape").joinpath("schemas").joinpath(f"{dataset}.md").read_text(encoding="utf-8")
