import json

import pytest

import launchtape as lt
from launchtape.cli import main
from conftest import BASE, ROWS, TOKEN


def test_datasets():
    ds = lt.datasets()
    assert [d.name for d in ds] == list(lt.DATASETS) == ["gradtrades", "pregrad", "padfeed", "meteora-pools"]
    for name, description in ds:
        assert description and "\n" not in description


@pytest.mark.parametrize("dataset", ["gradtrades", "pregrad", "padfeed", "meteora-pools"])
def test_schema_is_bundled(dataset):
    text = lt.schema(dataset)
    assert text.startswith(f"# {dataset} schema")
    assert "| type |" in text or "| field |" in text


def test_schema_unknown():
    with pytest.raises(ValueError):
        lt.schema("nope")


def test_cli_datasets(capsys):
    assert main(["datasets"]) == 0
    out = capsys.readouterr().out.splitlines()
    assert len(out) == 4 and out[0].startswith("gradtrades ")


def test_cli_schema(capsys):
    assert main(["schema", "pregrad"]) == 0
    assert capsys.readouterr().out.startswith("# pregrad schema")


def test_cli_head_file(capsys, pregrad_gz):
    assert main(["head", str(pregrad_gz), "-n", "2"]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert [json.loads(x) for x in lines] == ROWS[:2]


def test_cli_head_types(capsys, pregrad_gz):
    assert main(["head", str(pregrad_gz), "-t", "outcome", "--type", "create"]) == 0
    types = [json.loads(x)["type"] for x in capsys.readouterr().out.splitlines()]
    assert types == ["create", "outcome", "outcome"]


def test_cli_sample_then_head(capsys, web):
    name, _ = web.add_sample("padfeed")
    assert main(["sample", "padfeed"]) == 0
    assert capsys.readouterr().out.strip().endswith(name)
    assert main(["head", "padfeed", "-n", "1"]) == 0
    assert json.loads(capsys.readouterr().out) == ROWS[0]


def test_cli_fetch(capsys, web, tmp_path):
    fname, _ = web.add_delivery("pregrad", "2026-10-11")
    assert main(["fetch", f"{BASE}/dl/{TOKEN}", "pregrad", "2026-10-11", "-o", str(tmp_path)]) == 0
    assert capsys.readouterr().out.strip() == str(tmp_path / fname)


def test_cli_errors_exit_1(capsys, web):
    assert main(["head", "no-such-file.jsonl.gz"]) == 1
    assert capsys.readouterr().err.startswith("launchtape: error:")
    web.down = True
    assert main(["sample", "pregrad"]) == 1
    assert "could not reach" in capsys.readouterr().err


def test_cli_usage_errors():
    with pytest.raises(SystemExit) as e:
        main(["schema", "nope"])
    assert e.value.code == 2
    with pytest.raises(SystemExit) as e:
        main(["--version"])
    assert e.value.code == 0
