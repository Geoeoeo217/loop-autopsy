# Third-party licenses

loop-autopsy itself is released under the [MIT License](LICENSE).

No code from the packages below is copied into this repository. `pip` installs
them separately, so each one stays under its own license.

| Package | How loop-autopsy uses it | License |
|---|---|---|
| [IPython](https://ipython.org) | **Required.** Runs the loop watcher (`dependencies` in `pyproject.toml`). | BSD-3-Clause |
| [psutil](https://github.com/giampaolo/psutil) | **Optional.** `growth.py` uses it, only if installed, to read total RAM and the memory used by the kernel. Without it the code falls back to `/proc` and `resource`. | BSD-3-Clause |
| [pytest](https://pytest.org) | **Development only.** Runs the tests (`[test]` extra in `pyproject.toml`). | MIT |
| [pytest-cov](https://github.com/pytest-dev/pytest-cov) | **Development only.** Reports which lines the tests cover, in CI (`[test]` extra in `pyproject.toml`). | MIT |

Licenses are as declared in each package's own metadata when this file was written.

pandas and NumPy are **not** dependencies. `growth.size_of` recognises their
objects by their attributes (`memory_usage`, `nbytes`) and never imports them.

## Keeping this file up to date

`tests/test_license.py` fails if a package named in `pyproject.toml` (or
imported as `psutil` in `src/`) is missing from this file. When you add a
dependency, add a row here with its license.
