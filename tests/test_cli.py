import json

from brink.cli import main
from brink.record import validate_record
from helpers import FIXTURE


def test_play_writes_valid_record(tmp_path, capsys):
    out = tmp_path / "x" / "game.json"
    assert main(["play", "--map", str(FIXTURE), "--seed", "4", "--out", str(out)]) == 0
    rec = json.loads(out.read_text())
    assert not validate_record(rec) and rec["seed"] == 4
    assert str(out) in capsys.readouterr().out


def test_validate_orders(capsys):
    rc = main(["validate-orders", "--map", str(FIXTURE), "USA: C TEX - MEX", "USA: F WAS - TEX",
               "CHN: A BEI - KOR", "JPN: banana"])
    out = capsys.readouterr().out
    assert rc == 1
    assert "OK       USA: C TEX - MEX" in out and "OK       USA: F WAS - TEX" in out
    assert "VOID     CHN: A BEI - KOR: military units cannot enter an unaligned swing state" in out
    assert "INVALID" in out
