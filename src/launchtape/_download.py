"""Downloads: the free samples and paid day files. A file is kept only after its SHA-256 matches the published one."""

from __future__ import annotations

import datetime as _dt
import hashlib
import json
import os
import re
import sys
import warnings
from pathlib import Path
from typing import Dict, Optional, Union
from urllib.parse import urlsplit

import requests

from ._catalog import check_dataset
from ._version import __version__

DEFAULT_URL = "https://launchtape.io"
CHUNK = 1 << 20
TIMEOUT = (15, 120)  # connect, read (seconds)

PathLike = Union[str, "os.PathLike[str]"]

_HEX64 = re.compile(r"[0-9a-fA-F]{64}")
_TOKEN = re.compile(r"[0-9a-f]{32}")
_NAME = re.compile(r"[a-z][a-z0-9-]*")
_DAY = re.compile(r"\d{4}-\d{2}-\d{2}")


class LaunchTapeError(Exception):
    """A download could not be completed or checked."""


class ChecksumError(LaunchTapeError):
    """A downloaded file's SHA-256 does not match the published one (the file is not kept)."""


def base_url() -> str:
    """The site to download from: $LAUNCHTAPE_URL, else https://launchtape.io."""
    return os.environ.get("LAUNCHTAPE_URL", DEFAULT_URL).rstrip("/")


def cache_dir() -> Path:
    """Where samples are kept: $LAUNCHTAPE_CACHE, else ~/.cache/launchtape."""
    env = os.environ.get("LAUNCHTAPE_CACHE")
    return Path(env).expanduser() if env else Path.home() / ".cache" / "launchtape"


def sha256_file(path: PathLike) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(CHUNK), b""):
            h.update(block)
    return h.hexdigest()


def _get(url: str, stream: bool = False) -> requests.Response:
    try:
        r = requests.get(url, stream=stream, timeout=TIMEOUT,
                         headers={"User-Agent": f"launchtape-python/{__version__}"})
    except requests.RequestException as e:
        raise LaunchTapeError(f"could not reach {url}: {e}") from e
    if r.status_code != 200:
        r.close()
        hint = ""
        if r.status_code == 404 and "/dl/" in url:
            hint = " (download links last 7 days and list only the files you bought; write to us through launchtape.io)"
        raise LaunchTapeError(f"{url}: HTTP {r.status_code}{hint}")
    return r


def _progress(name: str, done: int, total: Optional[int]) -> None:
    size = f"{done / 1e6:,.1f} / {total / 1e6:,.1f} MB" if total else f"{done / 1e6:,.1f} MB"
    sys.stderr.write(f"\r{name}: {size}")
    sys.stderr.flush()


def _download(url: str, target: Path, sha256: str, total: Optional[int] = None, progress: bool = False) -> Path:
    """Stream url to target, hashing on the way; the file appears under its name only if the hash matches."""
    target.parent.mkdir(parents=True, exist_ok=True)
    part = target.with_name(f"{target.name}.{os.getpid()}.part")
    h = hashlib.sha256()
    done = 0
    try:
        with _get(url, stream=True) as r, open(part, "wb") as out:
            total = total or int(r.headers.get("content-length") or 0) or None
            for chunk in r.iter_content(CHUNK):
                out.write(chunk)
                h.update(chunk)
                done += len(chunk)
                if progress:
                    _progress(target.name, done, total)
        if progress:
            sys.stderr.write("\n")
        got = h.hexdigest()
        if got != sha256.lower():
            raise ChecksumError(f"{target.name}: SHA-256 {got} does not match the published {sha256.lower()}; "
                                "the download was discarded, please try again")
        os.replace(part, target)
    except BaseException:
        try:
            part.unlink()
        except FileNotFoundError:
            pass
        raise
    return target


def _parse_sums(text: str) -> Dict[str, str]:
    """`sha256sum` output -> {file name: hex digest}."""
    sums = {}
    for line in text.splitlines():
        parts = line.strip().split(None, 1)
        if len(parts) == 2 and _HEX64.fullmatch(parts[0]):
            sums[parts[1].lstrip("*")] = parts[0].lower()
    return sums


def sample(dataset: str, *, cache: Optional[PathLike] = None, progress: bool = False) -> Path:
    """Download the free 2-hour sample of a dataset (checked against the site's SHA256SUMS) and return its path.

    A sample already in the cache is reused when its hash still matches. If the site cannot be reached, a sample
    already in the cache (checked when it was downloaded) is returned with a warning.
    """
    check_dataset(dataset)
    folder = Path(cache).expanduser() if cache is not None else cache_dir()
    pattern = re.compile(rf"{re.escape(dataset)}-\d{{4}}-\d{{2}}-\d{{2}}-\d{{4}}-\d{{4}}utc\.jsonl\.gz")
    kept = sorted(p for p in folder.glob(f"{dataset}-*utc.jsonl.gz") if pattern.fullmatch(p.name))
    base = base_url()
    try:
        with _get(f"{base}/sample/SHA256SUMS") as r:
            sums = _parse_sums(r.text)
    except LaunchTapeError as e:
        if kept:
            warnings.warn(f"{e}; using the {dataset} sample already in {folder}", stacklevel=2)
            return kept[-1]
        raise
    names = sorted(n for n in sums if pattern.fullmatch(n))
    if not names:
        raise LaunchTapeError(f"no {dataset} sample is listed in {base}/sample/SHA256SUMS")
    name = names[-1]
    target = folder / name
    if not (target.is_file() and sha256_file(target) == sums[name]):
        _download(f"{base}/sample/{name}", target, sums[name], progress=progress)
    for old in kept:  # an older sample window of the same dataset
        if old != target:
            old.unlink()
    return target


def _delivery_base(url_or_token: str) -> str:
    s = str(url_or_token).strip()
    if _TOKEN.fullmatch(s):
        return f"{base_url()}/dl/{s}"
    parts = urlsplit(s)
    m = re.match(r"/dl/([0-9a-f]{32})(?:/|$)", parts.path)
    if parts.scheme not in ("http", "https") or not parts.netloc or not m:
        raise ValueError(f"not a Launch Tape delivery link or token: {url_or_token!r} "
                         "(expected https://launchtape.io/dl/<token> or the 32-character token)")
    return f"{parts.scheme}://{parts.netloc}/dl/{m.group(1)}"


def _day(day: Union[str, _dt.date]) -> str:
    if isinstance(day, _dt.datetime):
        day = day.date()
    if isinstance(day, _dt.date):
        return day.isoformat()
    s = str(day).strip()
    if _DAY.fullmatch(s):
        try:
            _dt.date.fromisoformat(s)
            return s
        except ValueError:
            pass
    raise ValueError(f"day must be a UTC date like 2026-10-11, got {day!r}")


def fetch(url_or_token: str, dataset: str, day: Union[str, _dt.date], dest: Optional[PathLike] = None,
          *, progress: bool = False) -> Path:
    """Download one paid day file from a delivery link and return its path.

    url_or_token: the link from your delivery mail (https://launchtape.io/dl/<token>) or just its token.
    day: the UTC day, "YYYY-MM-DD" or a date. dest: a folder (default: the current folder) or a file path ending
    in .gz. The file is checked against the SHA-256 in <dataset>-<day>.manifest.json, which is saved next to it.
    A file already at the destination with the right hash is not downloaded again.
    """
    if not _NAME.fullmatch(str(dataset)):
        raise ValueError(f"not a dataset name: {dataset!r}")
    stem = f"{dataset}-{_day(day)}"
    fname = f"{stem}.jsonl.gz"
    base = _delivery_base(url_or_token)
    with _get(f"{base}/{stem}.manifest.json") as r:
        raw = r.content
    try:
        manifest = json.loads(raw)
    except ValueError as e:
        raise LaunchTapeError(f"{stem}.manifest.json is not valid JSON: {e}") from None
    files = manifest.get("files") if isinstance(manifest, dict) else None
    entry = next((f for f in files or [] if isinstance(f, dict) and f.get("file") == fname), None)
    sha = entry.get("sha256") if entry else None
    if not isinstance(sha, str) or not _HEX64.fullmatch(sha):
        raise LaunchTapeError(f"{stem}.manifest.json has no SHA-256 for {fname}")
    size = entry.get("bytes")
    if dest is None:
        target = Path.cwd() / fname
    else:
        target = Path(dest).expanduser()
        if target.is_dir() or not target.name.endswith(".gz"):
            target = target / fname
    if not (target.is_file() and sha256_file(target) == sha.lower()):
        _download(f"{base}/{fname}", target, sha, total=size if isinstance(size, int) else None, progress=progress)
    (target.parent / f"{stem}.manifest.json").write_bytes(raw)
    return target
