"""Build first, then run this to verify every wheel extra in a fresh environment."""

import os
import subprocess
import tempfile
import venv
from pathlib import Path


def main():
    root = Path(__file__).resolve().parents[1]
    (wheel,) = (root / "dist").glob("*.whl")
    extras = [
        "core",
        "sqlite",
        "env",
        "postgres",
        "oracle",
        "mssql",
        "questdb",
        "opcua",
        "mqtt",
        "graylog",
        "db",
        "all",
    ]
    for extra in extras:
        with tempfile.TemporaryDirectory(prefix="ic-install-") as directory:
            venv.create(directory, with_pip=True)
            python = Path(directory) / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
            requirement = str(wheel) + (f"[{extra}]" if extra != "core" else "")
            subprocess.run(
                [str(python), "-m", "pip", "install", "--disable-pip-version-check", requirement],
                check=True,
            )
            subprocess.run(
                [str(python), str(root / "tools/check_install.py"), extra],
                check=True,
                cwd=directory,
            )


if __name__ == "__main__":
    main()
