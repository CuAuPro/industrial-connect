# Release helper

`manage.py` runs checks, builds distributions and supports explicit uploads. Install `.[all,dev]` and
run it with your virtual environment's Python:

The library is licensed under [Apache-2.0](../LICENSE). License text and SPDX metadata
are included in both distributions. Dependencies retain their own licenses.

```shell
python manage.py check                         # lint, formatting, typing, tests
python manage.py build                         # checks, clean build, twine check
python manage.py patch                         # bump patch version and build
python manage.py minor                         # bump minor version and build
python manage.py major                         # bump major version and build
python manage.py publish --repository testpypi  # fresh checked build, then TestPyPI upload
python manage.py publish                       # fresh checked build, then PyPI upload
python manage.py patch --publish                # bump, build and upload to PyPI
```

`build` and version-bump actions do not upload unless `--publish` is supplied. A bump
updates `pyproject.toml`, package `__version__`, and the `db`/`all` self-dependency pins.
If checks fail after a bump, the new source version remains for you to fix and review;
rerun `build` or `publish` without another bump. No commits, tags or changelog entries
are created automatically. The script only uploads the current version's wheel and
source distribution; failed checks/builds prevent upload.

Twine handles authentication through its prompt, keyring, `.pypirc`, or environment
variables. For API-token authentication in PowerShell:

```powershell
$env:TWINE_USERNAME = '__token__'
$env:TWINE_PASSWORD = '<your API token for the selected repository>'
python manage.py publish --repository testpypi
```

PyPI and TestPyPI have separate accounts/tokens. For a private index, use a named
`.pypirc` repository (`--repository company`) or an explicit upload endpoint:

```shell
python manage.py publish --repository-url https://packages.example/upload/
```

The helper never puts credentials in command arguments or reads a .env file automatically.
See [Twine configuration](https://twine.readthedocs.io/en/stable/#configuration) and
[the Python packaging guide](https://packaging.python.org/en/latest/tutorials/packaging-projects/).
