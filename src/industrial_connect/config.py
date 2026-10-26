"""Explicit environment configuration, with optional .env file parsing."""

import os
from dataclasses import MISSING, fields
from math import isfinite
from pathlib import Path
from typing import Any, ClassVar, Self, get_args, get_type_hints

from ._optional import require


class EnvConfig:
    _env_prefix: ClassVar[str]

    def _validate_numbers(self) -> None:
        """Apply numeric type/finite checks equally to direct and environment configs."""
        hints = get_type_hints(type(self))
        for field in fields(self):  # type: ignore[arg-type]
            value = getattr(self, field.name)
            expected = hints[field.name]
            if expected is int and type(value) is not int:
                raise ValueError(f"{field.name} must be an integer")
            if expected is float and (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not isfinite(value)
            ):
                raise ValueError(f"{field.name} must be a finite number")

    @classmethod
    def from_env(
        cls, *, env_file: str | Path | None = None, prefix: str | None = None, **overrides: Any
    ) -> Self:
        """Load fields with precedence: explicit arguments > environment > file > defaults.

        Keys are the prefix plus each uppercase field name. No process environment
        changes or implicit .env discovery occur. Config construction performs validation.
        """
        source: dict[str, str | None] = {}
        if env_file is not None:
            path = Path(env_file)
            if not path.is_file():
                raise FileNotFoundError(f"Environment file does not exist: {path}")
            source.update(require("dotenv", "env").dotenv_values(path, encoding="utf-8"))
        source.update(os.environ)
        config_fields = fields(cls)  # type: ignore[arg-type]
        names = {field.name for field in config_fields}
        if unknown := overrides.keys() - names:
            raise TypeError(f"Unknown configuration fields: {', '.join(sorted(unknown))}")
        hints = get_type_hints(cls)
        env_prefix = cls._env_prefix if prefix is None else prefix
        values: dict[str, Any] = {}
        missing = []
        for field in config_fields:
            key = env_prefix + field.name.upper()
            if field.name in overrides:
                values[field.name] = overrides[field.name]
            elif (raw := source.get(key)) is not None:
                values[field.name] = _parse(raw, hints[field.name], key)
            elif field.default is MISSING and field.default_factory is MISSING:
                missing.append(key)
        if missing:
            raise ValueError(f"Missing required environment variables: {', '.join(missing)}")
        return cls(**values)


def _parse(value: str, annotation: Any, key: str) -> Any:
    if type(None) in get_args(annotation):
        if value == "":
            return None
        annotation = next(type_ for type_ in get_args(annotation) if type_ is not type(None))
    try:
        if annotation is bool:
            normalized = value.strip().lower()
            if normalized in {"true", "1", "yes", "on"}:
                return True
            if normalized in {"false", "0", "no", "off"}:
                return False
            raise ValueError
        if annotation is int:
            return int(value)
        if annotation is float:
            parsed = float(value)
            if not isfinite(parsed):
                raise ValueError
            return parsed
        return value
    except ValueError:
        raise ValueError(f"Invalid value for environment variable {key}") from None
