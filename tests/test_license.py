"""License consistency checks: LICENSE, pyproject.toml, README and THIRD_PARTY_LICENSES.md
must agree with each other."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def read(name: str) -> str:
    return (ROOT / name).read_text(encoding="utf-8")


def license_holders():
    text = read("LICENSE")
    match = re.search(r"Copyright \(c\) (\d{4}) (.+?)\n\n", text, re.S)
    assert match, "LICENSE needs a line like 'Copyright (c) 2026 Name, Name'"
    holders = [n.strip() for n in " ".join(match.group(2).split()).split(",") if n.strip()]
    return match.group(1), holders


def pyproject_authors():
    block = re.search(r"authors\s*=\s*\[(.*?)\n\]", read("pyproject.toml"), re.S)
    assert block, "pyproject.toml has no authors list"
    return re.findall(r'name\s*=\s*"([^"]+)"', block.group(1))


def readme_section(title: str) -> str:
    match = re.search(rf"^## {title}\s*$(.*?)(?=^## |\Z)", read("README.md"), re.S | re.M)
    assert match, f"README has no '## {title}' section"
    return match.group(1)


def readme_team():
    names = []
    for line in readme_section("Team").splitlines():
        if line.startswith("|"):
            first = line.strip("|").split("|")[0].strip()
            if first and first != "Member" and not set(first) <= set("-: "):
                names.append(first)
    return names


def test_license_file_is_the_standard_mit_text():
    text = read("LICENSE")
    assert text.startswith("MIT License")
    assert "Permission is hereby granted, free of charge" in text
    assert "The above copyright notice and this permission notice shall be included" in text
    assert 'THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND' in text


def test_copyright_line_has_a_year_and_holders():
    year, holders = license_holders()
    assert 2000 <= int(year) <= 2100
    assert len(holders) >= 1


def test_license_holders_match_pyproject_authors():
    _, holders = license_holders()
    authors = pyproject_authors()
    assert len(set(authors)) == len(authors), "duplicate author in pyproject.toml"
    assert set(holders) == set(authors)


def test_license_holders_match_readme_team_table():
    _, holders = license_holders()
    assert set(holders) == set(readme_team())


def test_pyproject_declares_mit():
    text = read("pyproject.toml")
    assert re.search(r'license\s*=\s*\{\s*text\s*=\s*"MIT"\s*\}', text)
    assert "License :: OSI Approved :: MIT License" in text


def test_readme_license_section_says_mit():
    assert "MIT" in readme_section("License")


def _declared_packages():
    text = read("pyproject.toml")
    names = set()
    deps = re.search(r"^dependencies\s*=\s*\[(.*?)\]", text, re.S | re.M)
    extras = re.search(r"\[project\.optional-dependencies\](.*?)(?=^\[|\Z)", text, re.S | re.M)
    for block in (deps, extras):
        if block:
            names |= set(re.findall(r'"([A-Za-z0-9_.-]+)', block.group(1)))
    for path in (ROOT / "src").rglob("*.py"):
        if re.search(r"^\s*import psutil\b", path.read_text(encoding="utf-8"), re.M):
            names.add("psutil")
    return names


def test_third_party_file_lists_every_dependency():
    rows = [ln for ln in read("THIRD_PARTY_LICENSES.md").splitlines() if ln.startswith("|")]
    notice = "\n".join(rows).lower()  # only the table counts, not the prose around it
    names = _declared_packages()
    assert {"ipython", "pytest"} <= {n.lower() for n in names}  # the parser found the real lists
    missing = sorted(n for n in names if n.lower() not in notice)
    assert not missing, f"add these to THIRD_PARTY_LICENSES.md: {missing}"
