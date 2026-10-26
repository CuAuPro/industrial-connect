import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

import manage


@pytest.fixture
def release_project(tmp_path, monkeypatch):
    root = tmp_path / "project"
    package = root / "src" / "industrial_connect"
    package.mkdir(parents=True)
    project = root / "pyproject.toml"
    project.write_text(
        '[project]\nname = "industrial-connect"\nversion = "1.2.3"\n'
        "[project.optional-dependencies]\n"
        'db = ["industrial-connect[postgres,oracle]==1.2.3"]\n'
        'all = ["industrial-connect[db,mqtt,env]==1.2.3"]\n',
        encoding="utf-8",
    )
    package_init = package / "__init__.py"
    package_init.write_text('__version__ = "1.2.3"\n', encoding="utf-8")
    monkeypatch.setattr(manage, "ROOT", root)
    monkeypatch.setattr(manage, "PYPROJECT", project)
    monkeypatch.setattr(manage, "PACKAGE_INIT", package_init)
    return root


@pytest.mark.parametrize(
    "part,expected", [("patch", "1.2.4"), ("minor", "1.3.0"), ("major", "2.0.0")]
)
def test_bump_updates_package_metadata_and_aggregate_pins(release_project, part, expected):
    assert manage.bump(part) == expected
    assert manage.current_version() == expected
    metadata = tomllib.loads(manage.PYPROJECT.read_text(encoding="utf-8"))["project"]
    assert metadata["version"] == expected
    assert metadata["optional-dependencies"]["db"] == [
        f"industrial-connect[postgres,oracle]=={expected}"
    ]
    assert metadata["optional-dependencies"]["all"] == [
        f"industrial-connect[db,mqtt,env]=={expected}"
    ]
    assert manage.PACKAGE_INIT.read_text(encoding="utf-8") == f'__version__ = "{expected}"\n'


@pytest.mark.parametrize("bad_pin", ["9.0.0", "1.2.3rc1", "1.2"])
def test_inconsistent_self_pins_fail_before_writing(release_project, bad_pin):
    text = manage.PYPROJECT.read_text(encoding="utf-8").replace("]==1.2.3", f"]=={bad_pin}")
    manage.PYPROJECT.write_text(text, encoding="utf-8")
    before = manage.PACKAGE_INIT.read_bytes()
    with pytest.raises(RuntimeError, match="Aggregate extras"):
        manage.bump("patch")
    assert manage.PYPROJECT.read_text(encoding="utf-8") == text
    assert manage.PACKAGE_INIT.read_bytes() == before


def test_mismatched_package_version_fails_before_writing(release_project):
    manage.PACKAGE_INIT.write_text('__version__ = "9.0.0"\n', encoding="utf-8")
    before = manage.PYPROJECT.read_bytes()
    with pytest.raises(RuntimeError, match="__version__"):
        manage.bump("patch")
    assert manage.PYPROJECT.read_bytes() == before


def test_clean_removes_only_generated_directories(release_project):
    for name in ("build", "dist", "src/industrial_connect.egg-info"):
        directory = release_project / name
        directory.mkdir()
        (directory / "generated.txt").write_text("generated")
    source = release_project / "src/industrial_connect/__init__.py"
    manage.clean_build_outputs()
    assert source.is_file()
    assert not (release_project / "dist").exists()
    assert not (release_project / "build").exists()
    assert not (release_project / "src/industrial_connect.egg-info").exists()


def test_redirected_output_is_rejected_before_any_deletion(release_project, monkeypatch):
    build = release_project / "build"
    build.mkdir()
    marker = build / "preserved.txt"
    marker.write_text("preserve")
    original = Path.resolve

    def redirected(path, *args, **kwargs):
        if path == release_project / "dist":
            return release_project.parent / "outside"
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "resolve", redirected)
    with pytest.raises(RuntimeError, match="redirected"):
        manage.clean_build_outputs()
    assert marker.is_file()


def create_artifacts(root):
    directory = root / "dist"
    directory.mkdir()
    files = [
        directory / "industrial_connect-1.2.3-py3-none-any.whl",
        directory / "industrial_connect-1.2.3.tar.gz",
    ]
    for path in files:
        path.write_bytes(b"test artifact")
    (directory / "old-0.0.1.whl").write_bytes(b"stale")
    (directory / ".gitignore").write_text("*")
    return [str(path) for path in files]


def test_build_checks_then_cleans_builds_and_checks_current_artifacts(release_project, monkeypatch):
    calls = []
    monkeypatch.setattr(manage, "check", lambda: calls.append("check"))
    monkeypatch.setattr(manage, "clean_build_outputs", lambda: calls.append("clean"))
    files = create_artifacts(release_project)
    monkeypatch.setattr(manage, "run", lambda *args: calls.append(args))
    manage.build()
    assert calls == ["check", "clean", ("-m", "build"), ("-m", "twine", "check", *files)]


@pytest.mark.parametrize(
    "repository,url,target",
    [
        ("pypi", None, ("--repository", "pypi")),
        ("testpypi", None, ("--repository", "testpypi")),
        ("company", None, ("--repository", "company")),
        (
            "pypi",
            "https://packages.example/upload/",
            ("--repository-url", "https://packages.example/upload/"),
        ),
    ],
)
def test_publish_targets_only_current_artifacts(
    release_project, monkeypatch, repository, url, target
):
    files = create_artifacts(release_project)
    calls = []
    monkeypatch.setattr(manage, "run", lambda *args: calls.append(args))
    manage.publish(repository, url)
    assert calls == [("-m", "twine", "upload", *target, *files)]


def test_publish_refuses_missing_current_artifacts(release_project, monkeypatch):
    def unexpected(*args):
        pytest.fail("No upload should occur without current artifacts")

    monkeypatch.setattr(manage, "run", unexpected)
    with pytest.raises(RuntimeError, match="Missing wheel"):
        manage.publish()


@pytest.mark.parametrize(
    "argv,expected",
    [
        (["build"], ["build"]),
        (["patch"], ["patch", "build"]),
        (
            ["minor", "--publish", "--repository", "testpypi"],
            ["minor", "build", ("testpypi", None)],
        ),
        (["publish"], ["build", ("pypi", None)]),
        (["check"], ["check"]),
    ],
)
def test_cli_upload_is_explicit_and_follows_build(monkeypatch, argv, expected):
    calls = []
    monkeypatch.setattr(manage, "build", lambda: calls.append("build"))
    monkeypatch.setattr(manage, "check", lambda: calls.append("check"))
    monkeypatch.setattr(manage, "bump", lambda part: calls.append(part))
    monkeypatch.setattr(manage, "publish", lambda repository, url: calls.append((repository, url)))
    manage.main(argv)
    assert calls == expected


def test_failed_build_prevents_upload(monkeypatch):
    def fail():
        raise subprocess.CalledProcessError(1, ["pytest"])

    def unexpected(*args):
        pytest.fail("Upload must not occur after failed checks/build")

    monkeypatch.setattr(manage, "build", fail)
    monkeypatch.setattr(manage, "publish", unexpected)
    with pytest.raises(subprocess.CalledProcessError):
        manage.main(["publish"])


def test_run_uses_active_interpreter_and_project_directory(release_project, monkeypatch):
    calls = []
    monkeypatch.setattr(
        manage.subprocess, "run", lambda *args, **kwargs: calls.append((args, kwargs))
    )
    manage.run("-m", "build")
    assert calls == [(([sys.executable, "-m", "build"],), {"cwd": release_project, "check": True})]


def test_check_uses_active_interpreter_for_type_resolution(release_project, monkeypatch):
    calls = []
    monkeypatch.setattr(manage, "run", lambda *args: calls.append(args))
    manage.check()
    assert calls[0][:3] == ("-m", "ruff", "check")
    assert calls[1][:4] == ("-m", "ruff", "format", "--check")
    assert calls[2][:4] == ("-m", "pyright", "--pythonpath", sys.executable)
    assert calls[3] == ("-m", "pytest", "-q")


def test_check_publish_is_rejected():
    with pytest.raises(SystemExit) as error:
        manage.parse_args(["check", "--publish"])
    assert error.value.code == 2
