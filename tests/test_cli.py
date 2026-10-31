from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from jobwatch.cli import app

runner = CliRunner()


def test_help() -> None:
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "scan" in result.stdout
    assert "add" in result.stdout
    assert "detect" in result.stdout


def test_version() -> None:
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert "jobwatch" in result.stdout


def test_detect_no_fetch() -> None:
    result = runner.invoke(
        app,
        ["detect", "https://job-boards.greenhouse.io/discord", "--no-fetch"],
    )
    assert result.exit_code == 0
    assert "greenhouse" in result.stdout
    assert "discord" in result.stdout


def test_detect_workday_no_fetch() -> None:
    result = runner.invoke(
        app,
        [
            "detect",
            "https://nvidia.wd5.myworkdayjobs.com/en-US/NVIDIAExternalCareerSite",
            "--no-fetch",
        ],
    )
    assert result.exit_code == 0
    assert "workday" in result.stdout
    assert "NVIDIAExternalCareerSite" in result.stdout


def test_init_and_list(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["init", "--dest", str(tmp_path)])
    assert result.exit_code == 0
    assert (tmp_path / "companies.yaml").is_file()
    listed = runner.invoke(app, ["list", "--config", str(tmp_path / "companies.yaml")])
    assert listed.exit_code == 0
    assert "discord" in listed.stdout
    assert "greenhouse" in listed.stdout
    assert "icims" in listed.stdout
