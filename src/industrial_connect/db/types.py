"""Shared SQL parameter and row annotations; values retain native driver types."""

from collections.abc import Mapping, Sequence
from typing import Any

type QueryParams = Mapping[str, Any] | Sequence[Any]
type Row = dict[str, Any]
