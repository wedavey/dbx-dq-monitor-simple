"""Validated names for the framework's own tables."""

from __future__ import annotations

import re
from dataclasses import dataclass

_IDENT = re.compile(r"^[A-Za-z0-9_]+$")


def check_identifier(value: str, what: str = "identifier") -> str:
    if not isinstance(value, str) or not _IDENT.match(value):
        raise ValueError(f"invalid {what}: {value!r} (letters, digits and underscore only)")
    return value


@dataclass(frozen=True)
class TableNames:
    catalog: str
    schema: str

    def __post_init__(self) -> None:
        check_identifier(self.catalog, "catalog")
        check_identifier(self.schema, "schema")

    def _fq(self, name: str) -> str:
        return f"`{self.catalog}`.`{self.schema}`.`{name}`"

    @property
    def metric_catalog(self) -> str:
        return self._fq("metric_catalog")

    @property
    def assignments(self) -> str:
        return self._fq("assignments")

    @property
    def results(self) -> str:
        return self._fq("results")

    @property
    def assignment_issues(self) -> str:
        return self._fq("assignment_issues")
