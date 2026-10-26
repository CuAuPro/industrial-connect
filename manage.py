"""Validate, build, version and explicitly publish industrial-connect."""

from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PYPROJECT = ROOT / "pyproject.toml"
PACKAGE_INIT = ROOT / "src" / "industrial_connect" / "__init__.py"
VERSION_PATTERN = re.compile(r'^version = "(\d+\.\d+\.\d+)"$', re.MULTILINE)
INIT_VERSION_PATTERN = re.compile(r'^__version__ = "(\d+\.\d+\.\d+)"$', re.MULTILINE)
SELF_PIN_PATTERN = re.compile(r"(industrial-connect\[[^\]]+\]==)(\d+\.\d+\.\d+)")


def run(*arguments: str) -> None:
    subprocess.run([sys.executable, *arguments], cwd=ROOT, check=True)


def current_version() -> str:
    project = PYPROJECT.read_text(encoding="utf-8")
    metadata = tomllib.loads(project)["project"]
    version = metadata["version"]
    matches = VERSION_PATTERN.findall(project)
    if matches != [version]:
        raise RuntimeError('Expected one version = "major.minor.patch" in pyproject.toml')
    if INIT_VERSION_PATTERN.findall(PACKAGE_INIT.read_text(encoding="utf-8")) != [version]:
        raise RuntimeError("Package __version__ does not match pyproject.toml")
    pins = SELF_PIN_PATTERN.findall(project)
    self_requirements = [
        requirement
        for requirements in metadata.get("optional-dependencies", {}).values()
        for requirement in requirements
        if requirement.startswith("industrial-connect[")
    ]
    if (
        len(pins) != len(self_requirements)
        or any(pin != version for _, pin in pins)
        or any(not requirement.endswith(f"=={version}") for requirement in self_requirements)
    ):
        raise RuntimeError("Aggregate extras must pin the current package version")
    return version


def bump(part: str) -> str:
    previous = current_version()
    major, minor, patch = map(int, previous.split("."))
    if part == "major":
        major, minor, patch = major + 1, 0, 0
    elif part == "minor":
        minor, patch = minor + 1, 0
    elif part == "patch":
        patch += 1
    else:
        raise ValueError("Version part must be major, minor or patch")
    version = f"{major}.{minor}.{patch}"
    project = VERSION_PATTERN.sub(
        f'version = "{version}"', PYPROJECT.read_text(encoding="utf-8"), count=1
    )
    project = SELF_PIN_PATTERN.sub(lambda match: match[1] + version, project)
    package_init = INIT_VERSION_PATTERN.sub(
        f'__version__ = "{version}"', PACKAGE_INIT.read_text(encoding="utf-8"), count=1
    )
    PYPROJECT.write_text(project, encoding="utf-8")
    PACKAGE_INIT.write_text(package_init, encoding="utf-8")
    print(f"Version bumped from {previous} to {version}")
    return version


def clean_build_outputs() -> None:
    root = ROOT.resolve()
    paths = [ROOT / "build", ROOT / "dist", ROOT / "src" / "industrial_connect.egg-info"]
    # Check every target before deleting anything, including redirected directories.
    for path in paths:
        if path.is_symlink() or path.is_junction() or not path.resolve().is_relative_to(root):
            raise RuntimeError(f"Refusing to clean redirected build output: {path}")
    for path in paths:
        if path.exists():
            shutil.rmtree(path)


def check() -> None:
    current_version()
    targets = ("manage.py", "src", "tests", "examples", "tools")
    run("-m", "ruff", "check", *targets)
    run("-m", "ruff", "format", "--check", *targets)
    run(
        "-m",
        "pyright",
        "--pythonpath",
        sys.executable,
        "src/industrial_connect",
        "tests/typing/db_usage.py",
        "examples",
    )
    run("-m", "pytest", "-q")


def artifacts() -> list[str]:
    version = current_version()
    expected = [
        ROOT / "dist" / f"industrial_connect-{version}-py3-none-any.whl",
        ROOT / "dist" / f"industrial_connect-{version}.tar.gz",
    ]
    if not all(path.is_file() for path in expected):
        raise RuntimeError(f"Missing wheel or source distribution for version {version}")
    return [str(path) for path in expected]


def build() -> None:
    check()
    clean_build_outputs()
    run("-m", "build")
    run("-m", "twine", "check", *artifacts())


def publish(repository: str = "pypi", repository_url: str | None = None) -> None:
    # Credentials stay in Twine's environment/keyring/configuration, never command arguments.
    target = (
        ("--repository-url", repository_url) if repository_url else ("--repository", repository)
    )
    run("-m", "twine", "upload", *target, *artifacts())


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("check", "build", "patch", "minor", "major", "publish"))
    parser.add_argument("--publish", action="store_true", help="Upload after a successful build")
    target = parser.add_mutually_exclusive_group()
    target.add_argument(
        "--repository", default="pypi", help="Twine repository name (default: pypi)"
    )
    target.add_argument("--repository-url", help="Explicit upload URL for a private registry")
    args = parser.parse_args(argv)
    if args.action == "check" and args.publish:
        parser.error("Use build --publish to validate, build and upload")
    return args


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    if args.action == "check":
        check()
        return
    if args.action in {"patch", "minor", "major"}:
        bump(args.action)
    build()
    if args.action == "publish" or args.publish:
        publish(args.repository, args.repository_url)


if __name__ == "__main__":
    try:
        main()
    except subprocess.CalledProcessError as exc:
        raise SystemExit(exc.returncode) from None
    except (RuntimeError, ValueError, OSError) as exc:
        raise SystemExit(str(exc)) from None
