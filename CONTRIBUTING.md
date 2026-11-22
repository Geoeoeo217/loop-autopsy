# Working on loop-autopsy as a team

## Who owns what

Each member looks after their own modules and their tests. To change someone
else's module, open an issue or ask them first.

| Member | Files |
|---|---|
| NATHANAEL George Steindhart | `pyproject.toml`, `README.md`, `CHANGELOG.md`, `CONTRIBUTING.md`, `config.py`, `transformer.py`, `monitor.py`, `magics.py`, `__init__.py`, `examples/demo.ipynb`, `tests/test_config.py`, `tests/test_transformer.py`, `tests/test_end_to_end.py` |
| SETIAWAN Matthew Henson | `detectors.py`, `recursion.py`, `loop_notes.py`, `tests/test_detectors.py`, `tests/test_recursion.py`, `tests/test_loop_notes.py`, `tests/test_false_positives.py` |
| LATHIF Muhammad Akmal | `growth.py`, `growth_check.py`, `growth_diff.py`, `memory_inspect.py`, `extras/growth_trace.py`, `THIRD_PARTY_LICENSES.md`, `docs/growth-check.md`, `docs/growth-trace.md`, `tests/test_growth.py`, `tests/test_growth_check.py`, `tests/test_growth_diff.py`, `tests/test_growth_trace.py`, `tests/test_memory_inspect.py`, `tests/test_memory_overview.py`, `tests/test_license.py` |
| KWAN Kendrick Matthew | `report.py`, `report_accessibility.py`, `report_metrics.py`, `extras/__init__.py`, `docs/extras.md`, `extras/report_export.py`, `extras/report_i18n.py`, `extras/report_theme.py`, `extras/sparkline.py`, and their tests (`tests/test_report*.py`, `tests/test_sparkline.py`) |
| HANDAYA Clement Steward | `heatmap.py`, `.github/workflows/tests.yml`, `benchmarks/`, `examples/refresh_demo.py`, `tests/test_heatmap.py`, `tests/test_magics.py`, `tests/test_monitor.py`, `tests/test_docs.py`, `tests/test_real_kernel.py`, `tests/test_slow_and_endless.py` |

## One-time setup

```bash
git clone https://github.com/Geoeoeo217/loop-autopsy
cd loop-autopsy
pip install -e ".[test]"
```

`pip install -e .` is an "editable" install: Python uses the files in this
folder directly, so your edits take effect without reinstalling. `pytest`
also works without it.

## Making a change

```bash
git pull                          # start from everyone's latest work
git switch -c short-name          # a branch for this change
# edit your files
pytest                            # every test must pass
git add path/to/changed/files
git commit -m "Say what the change does, in one line"
git push -u origin short-name
```

Then open a pull request on GitHub and ask one teammate to review it. Merge it
once it is approved and the **tests** check is green.

If `git push` is rejected, someone pushed before you: run `git pull`, then
`git push` again.

## Good commits

- One change per commit, with a message that says what it does:
  "Add `%autopsy memory` command", not "upload" or "Update file.py".
- Commit a little and often instead of one large upload at the end.
- Add or update a test with every change to the code.
- If you change a command or a setting, update the README too:
  `tests/test_docs.py` checks that the README matches the code.
- Add a line under **Unreleased** in `CHANGELOG.md`.

## Releasing a new version

1. Raise `__version__` in `src/loop_autopsy/__init__.py`. It is the only
   place the version is written; `pyproject.toml` reads it from there.
2. In `CHANGELOG.md`, rename **Unreleased** to the new version and date.
3. `python -m build` creates the files to upload in `dist/`.
4. `twine upload dist/*` uploads them to PyPI; it asks for an API token.
