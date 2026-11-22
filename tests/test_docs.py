"""The documentation must describe the code as it is: version numbers, the
settings table and the command lists are checked against the real thing."""
import re
from pathlib import Path

import loop_autopsy
from loop_autopsy.config import Settings
from loop_autopsy.magics import HELP

ROOT = Path(__file__).resolve().parent.parent
COMMANDS = ("on", "off", "last", "heatmap", "summary", "memory", "config", "reset")


def read(name: str) -> str:
    return (ROOT / name).read_text(encoding="utf-8")


def readme_section(title: str) -> str:
    match = re.search(rf"^## {title}\s*$(.*?)(?=^## |\Z)", read("README.md"), re.S | re.M)
    assert match, f"README has no '## {title}' section"
    return match.group(1)


def table_rows(text: str):
    """Cells of every table row, without the header and the |---| line."""
    rows = [[cell.strip() for cell in line.strip().strip("|").split("|")]
            for line in text.splitlines() if line.startswith("|")]
    return [r for r in rows[1:] if not set("".join(r)) <= set("-: ")]


def test_version_is_written_in_one_place():
    text = read("pyproject.toml")
    assert not re.search(r'^version\s*=\s*"', text, re.M), "the version belongs in __init__.py only"
    assert re.search(r'^dynamic\s*=\s*\["version"\]', text, re.M)
    assert re.search(r'^version\s*=\s*\{\s*attr\s*=\s*"loop_autopsy.__version__"\s*\}', text, re.M)
    assert re.fullmatch(r"\d+\.\d+\.\d+", loop_autopsy.__version__)


def test_readme_settings_table_matches_the_real_defaults():
    documented = {}
    for names, defaults, _meaning in table_rows(readme_section("Settings")):
        names = re.findall(r"`([a-z_]+)`", names)
        defaults = [d.strip().strip("`") for d in defaults.split("/")]
        assert len(names) == len(defaults), f"{names} has {len(defaults)} default(s)"
        documented.update(zip(names, defaults))

    real = Settings().as_dict()
    assert set(documented) == set(real), (
        f"README is missing {sorted(set(real) - set(documented))}, "
        f"and lists unknown {sorted(set(documented) - set(real))}")
    for name, value in real.items():
        if isinstance(value, str):
            assert documented[name] == value, name
        else:
            assert float(documented[name]) == float(value), name


def test_every_setting_named_in_the_readme_exists():
    real = set(Settings().as_dict())
    examples = set(re.findall(r"%autopsy config ([a-z_]+)=", read("README.md"))) - {"key"}  # key=value
    assert examples
    for example in examples:
        assert example in real, f"README shows a setting that does not exist: {example}"


def test_help_and_readme_list_every_command():
    usage = readme_section("Usage")
    for command in COMMANDS:
        assert re.search(rf"%autopsy[^\n]*\b{command}\b", HELP), f"%autopsy help does not mention {command}"
        assert re.search(rf"`%autopsy {command}\b|/ `{command}`", usage), f"README Usage does not mention {command}"
    assert "%%autopsy off" in HELP and "%%autopsy off" in usage


def test_commands_in_the_docs_are_real(shell, capsys):
    """Every ``%autopsy something`` in the README and the demo must be a known command."""
    known = set(COMMANDS) | {"help", "status"}
    text = read("README.md") + read("examples/demo.ipynb")
    used = set(re.findall(r"(?<!%)%autopsy ([a-z]+)", text))
    assert used and used <= known, f"unknown commands in the docs: {sorted(used - known)}"
    for command in sorted(used - {"help"}):  # and the magic really handles each one
        capsys.readouterr()
        shell.run_cell(f"%autopsy {command}")
        assert "loop-autopsy commands" not in capsys.readouterr().out, command


def test_demo_notebook_is_saved_with_its_outputs():
    """GitHub shows saved outputs only. Refresh them with examples/refresh_demo.py."""
    import json

    cells = [c for c in json.loads(read("examples/demo.ipynb"))["cells"] if c["cell_type"] == "code"]
    empty = [first for first, c in ((("".join(c["source"]).splitlines() or [""])[0], c) for c in cells)
             if not c["outputs"] and not first.startswith("%pip")]
    assert not empty, f"run `python examples/refresh_demo.py`; cells without output: {empty}"
    saved = json.dumps([c["outputs"] for c in cells])
    assert "Traceback (most recent call last)" not in saved, "the demo was saved with a crash in it"


def test_readme_has_no_duplicated_lines():
    """A bad merge once left the same sentence in the README twice."""
    lines = [ln.strip() for ln in read("README.md").splitlines()]
    prose = [ln for ln in lines if len(ln) > 40 and not ln.startswith(("|", "```", "!", "["))]
    repeated = sorted({ln for ln in prose if prose.count(ln) > 1})
    assert not repeated, f"repeated lines in README.md: {repeated}"
