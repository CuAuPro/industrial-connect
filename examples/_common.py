"""CLI plumbing shared by the runnable examples, not part of the library API."""

import argparse
import math
import sys
from collections.abc import Callable

from industrial_connect import IndustrialConnectError


def finite_number(value: str) -> float:
    parsed = float(value)
    if not math.isfinite(parsed):
        raise argparse.ArgumentTypeError("Enter a finite number")
    return parsed


def positive_seconds(value: str) -> float:
    parsed = finite_number(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("Duration must be positive")
    return parsed


def parser(description: str | None, *, database: bool = False) -> argparse.ArgumentParser:
    cli = argparse.ArgumentParser(description=description)
    cli.add_argument("--env-file", help="Explicit .env file; otherwise use process variables")
    if database:
        cli.add_argument("--initialize", action="store_true", help="Create the example table")
        cli.add_argument("--record", action="store_true", help="Insert one sample reading")
        cli.add_argument("--equipment", default="MILL_1")
        cli.add_argument("--temperature", type=finite_number, default=72.5)
    return cli


def run(action: Callable[[], None]) -> None:
    try:
        action()
    except KeyboardInterrupt:
        print("Stopped", file=sys.stderr)
        raise SystemExit(130) from None
    except (IndustrialConnectError, ValueError, OSError) as error:
        # Avoid printing native driver exception chains, which may include credentials.
        print(f"{type(error).__name__}: {error}", file=sys.stderr)
        raise SystemExit(1) from None
