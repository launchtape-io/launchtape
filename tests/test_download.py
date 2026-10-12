import datetime as dt
import json

import pytest

import launchtape as lt
from conftest import BASE, ROWS, TOKEN, sha

# --- sample ---------------------------------------------------------------------------------------------------------


def test_sample_downloads_into_the_cache(web, tmp_path):
    name, body = web.add_sample("padfeed")
    path = lt.sample("padfeed")
    assert path == tmp_path / "cache" / name
    assert path.read_bytes() == body
    assert list(lt.load(path)) == ROWS
    assert lt.cache_dir() == tmp_path / "cache"


def test_sample_picks_its_own_dataset(web):
    web.add_sample("gradtrades", rows=ROWS[:1])
    _, body = web.add_sample("pregrad")
    web.add_sample("meteora-pools", rows=ROWS[:2])
    assert lt.sample("pregrad").read_bytes() == body


def test_sample_reuses_a_good_cached_copy(web):
    name, _ = web.add_sample("pregrad")
    first = lt.sample("pregrad")
    second = lt.sample("pregrad")
    assert first == second
    assert web.calls.count(f"{BASE}/sample/{name}") == 1  # only the checksum list was fetched again


def test_sample_checksum_mismatch_keeps_nothing(web, tmp_path):
    web.add_sample("pregrad", listed_sha="0" * 64)
    with pytest.raises(lt.ChecksumError):
        lt.sample("pregrad")
    assert not any((tmp_path / "cache").iterdir())  # no file, no .part left behind


def test_sample_replaces_a_damaged_cached_copy(web, tmp_path):
    name, body = web.add_sample("pregrad")
    (tmp_path / "cache").mkdir()
    (tmp_path / "cache" / name).write_bytes(b"damaged")
    assert lt.sample("pregrad").read_bytes() == body


def test_sample_drops_an_older_window(web, tmp_path):
    (tmp_path / "cache").mkdir()
    old = tmp_path / "cache" / "pregrad-2026-10-09-1400-1600utc.jsonl.gz"
    old.write_bytes(b"old window")
    other = tmp_path / "cache" / "padfeed-2026-10-09-1400-1600utc.jsonl.gz"
    other.write_bytes(b"another dataset")
    web.add_sample("pregrad")
    lt.sample("pregrad")
    assert not old.exists() and other.exists()


def test_sample_offline_falls_back_to_the_cache(web):
    web.add_sample("pregrad")
    path = lt.sample("pregrad")
    web.down = True
    with pytest.warns(UserWarning, match="already in"):
        assert lt.sample("pregrad") == path


def test_sample_offline_without_cache(web):
    web.down = True
    with pytest.raises(lt.LaunchTapeError, match="could not reach"):
        lt.sample("pregrad")


def test_sample_not_listed(web):
    web.add_sample("padfeed")
    with pytest.raises(lt.LaunchTapeError, match="no pregrad sample"):
        lt.sample("pregrad")


def test_sample_unknown_dataset(web):
    with pytest.raises(ValueError, match="unknown dataset"):
        lt.sample("nope")
    assert web.calls == []


def test_sample_custom_cache(web, tmp_path):
    name, _ = web.add_sample("pregrad")
    assert lt.sample("pregrad", cache=tmp_path / "mine") == tmp_path / "mine" / name


# --- fetch ----------------------------------------------------------------------------------------------------------


def test_fetch_with_a_token(web, tmp_path):
    fname, body = web.add_delivery("pregrad", "2026-10-11")
    path = lt.fetch(TOKEN, "pregrad", "2026-10-11", dest=tmp_path / "days")
    assert path == tmp_path / "days" / fname
    assert path.read_bytes() == body
    manifest = json.loads((tmp_path / "days" / "pregrad-2026-10-11.manifest.json").read_text())
    assert manifest["files"][0]["sha256"] == sha(body)


@pytest.mark.parametrize("link", [
    f"{BASE}/dl/{TOKEN}",
    f"{BASE}/dl/{TOKEN}/",
    f"{BASE}/dl/{TOKEN}/pregrad-2026-10-11.jsonl.gz",
    f"  {BASE}/dl/{TOKEN}?x=1  ",
])
def test_fetch_link_forms(web, tmp_path, link):
    web.add_delivery("pregrad", "2026-10-11")
    assert lt.fetch(link, "pregrad", "2026-10-11", dest=tmp_path).is_file()


def test_fetch_uses_the_links_own_host(web, tmp_path):
    other = f"http://127.0.0.1:3070/dl/{TOKEN}"
    web.add_delivery("padfeed", "2026-10-11", base=other)
    lt.fetch(other, "padfeed", "2026-10-11", dest=tmp_path)
    assert all(u.startswith(other) for u in web.calls)


def test_fetch_day_as_date_and_dest_as_file(web, tmp_path):
    _, body = web.add_delivery("padfeed", "2026-10-11")
    target = tmp_path / "x" / "my.jsonl.gz"
    assert lt.fetch(TOKEN, "padfeed", dt.date(2026, 10, 11), dest=target) == target
    assert target.read_bytes() == body
    assert (tmp_path / "x" / "padfeed-2026-10-11.manifest.json").is_file()


def test_fetch_default_dest_is_the_current_folder(web, tmp_path, monkeypatch):
    fname, _ = web.add_delivery("padfeed", "2026-10-11")
    monkeypatch.chdir(tmp_path)
    assert lt.fetch(TOKEN, "padfeed", "2026-10-11") == tmp_path / fname


def test_fetch_skips_a_file_already_there(web, tmp_path):
    fname, _ = web.add_delivery("pregrad", "2026-10-11")
    lt.fetch(TOKEN, "pregrad", "2026-10-11", dest=tmp_path)
    lt.fetch(TOKEN, "pregrad", "2026-10-11", dest=tmp_path)
    assert web.calls.count(f"{BASE}/dl/{TOKEN}/{fname}") == 1


def test_fetch_checksum_mismatch_keeps_nothing(web, tmp_path):
    web.add_delivery("pregrad", "2026-10-11", listed_sha="f" * 64)
    with pytest.raises(lt.ChecksumError):
        lt.fetch(TOKEN, "pregrad", "2026-10-11", dest=tmp_path / "d")
    assert not any((tmp_path / "d").iterdir())


def test_fetch_manifest_without_the_file(web, tmp_path):
    web.add_delivery("pregrad", "2026-10-11", list_file=False)
    with pytest.raises(lt.LaunchTapeError, match="no SHA-256"):
        lt.fetch(TOKEN, "pregrad", "2026-10-11", dest=tmp_path)


def test_fetch_expired_link(web, tmp_path):
    with pytest.raises(lt.LaunchTapeError, match="7 days"):
        lt.fetch(TOKEN, "pregrad", "2026-10-11", dest=tmp_path)


@pytest.mark.parametrize("link", ["abc", "https://launchtape.io/sample/x", "ftp://launchtape.io/dl/" + TOKEN,
                                  "https://launchtape.io/dl/" + TOKEN.upper()])
def test_fetch_bad_link(web, link):
    with pytest.raises(ValueError, match="delivery link"):
        lt.fetch(link, "pregrad", "2026-10-11")
    assert web.calls == []


@pytest.mark.parametrize("day", ["2026-13-01", "20261011", "2026-10-1", "yesterday"])
def test_fetch_bad_day(web, day):
    with pytest.raises(ValueError, match="UTC date"):
        lt.fetch(TOKEN, "pregrad", day)


def test_fetch_bad_dataset_name(web):
    with pytest.raises(ValueError):
        lt.fetch(TOKEN, "../etc", "2026-10-11")
