import gzip
import hashlib
import json

import pytest
import requests

BASE = "https://lt.test"
TOKEN = "0123456789abcdef0123456789abcdef"

ROWS = [
    {"type": "create", "t": 1791640000000, "mint": "MintA", "creator": "W1", "name": "Coin A", "sym": "A"},
    {"type": "record", "t": 1791640010000, "mint": "MintA", "sym": "A", "instant": True, "ageS": 2.1,
     "curve": {"coverage": "full", "holders": [{"w": "W2", "pct": 3.5}]}},
    {"type": "record", "t": 1791640020000, "mint": "MintB", "sym": "B", "instant": False, "ageS": 640.0,
     "curve": {"coverage": "partial", "holders": []}},
    {"type": "outcome", "t": 1791640610000, "mint": "MintA", "crashed": True, "maxGain": 10.5},
    {"type": "outcome", "t": 1791640620000, "mint": "MintB", "crashed": False, "maxGain": 80.0},
]


def gz_bytes(rows, extra_lines=()):
    text = "".join(json.dumps(r, separators=(",", ":")) + "\n" for r in rows)
    text += "".join(line + "\n" for line in extra_lines)
    return gzip.compress(text.encode("utf-8"))


def sha(data):
    return hashlib.sha256(data).hexdigest()


class FakeResponse:
    def __init__(self, status, body):
        self.status_code = status
        self.content = body
        self.headers = {"content-length": str(len(body))}

    @property
    def text(self):
        return self.content.decode("utf-8")

    def iter_content(self, size):
        for i in range(0, len(self.content), 7):  # small chunks: the hash must span chunk edges
            yield self.content[i:i + 7]

    def close(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


class FakeWeb:
    """URL -> (status, body); every GET is recorded. down=True makes every request fail like a dead network."""

    def __init__(self):
        self.pages = {}
        self.calls = []
        self.down = False

    def get(self, url, stream=False, timeout=None, headers=None):
        self.calls.append(url)
        if self.down:
            raise requests.ConnectionError("network is down")
        status, body = self.pages.get(url, (404, b"Not found."))
        return FakeResponse(status, body)

    def add_sample(self, dataset, rows=ROWS, window="2026-10-10-1400-1600utc", listed_sha=None):
        body = gz_bytes(rows)
        name = f"{dataset}-{window}.jsonl.gz"
        self.pages[f"{BASE}/sample/{name}"] = (200, body)
        sums = self.pages.get(f"{BASE}/sample/SHA256SUMS", (200, b""))[1].decode()
        sums += f"{listed_sha or sha(body)}  {name}\n"
        self.pages[f"{BASE}/sample/SHA256SUMS"] = (200, sums.encode())
        return name, body

    def add_delivery(self, dataset, day, rows=ROWS, base=f"{BASE}/dl/{TOKEN}", listed_sha=None, list_file=True):
        body = gz_bytes(rows)
        fname = f"{dataset}-{day}.jsonl.gz"
        entry = {"file": fname, "dataset": dataset, "day": day, "bytes": len(body),
                 "sha256": listed_sha or sha(body), "rows": len(rows)}
        manifest = {"product": "Solana Launch Tape", "schemaVersion": "1", "files": [entry] if list_file else []}
        self.pages[f"{base}/{dataset}-{day}.manifest.json"] = (200, json.dumps(manifest).encode())
        self.pages[f"{base}/{fname}"] = (200, body)
        return fname, body


@pytest.fixture(autouse=True)
def web(monkeypatch, tmp_path):
    """No test touches the real network or the real cache."""
    w = FakeWeb()
    monkeypatch.setattr(requests, "get", w.get)
    monkeypatch.setenv("LAUNCHTAPE_URL", BASE)
    monkeypatch.setenv("LAUNCHTAPE_CACHE", str(tmp_path / "cache"))
    return w


@pytest.fixture
def pregrad_gz(tmp_path):
    path = tmp_path / "pregrad-2026-10-10.jsonl.gz"
    path.write_bytes(gz_bytes(ROWS))
    return path
