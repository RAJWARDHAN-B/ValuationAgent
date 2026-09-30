from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

Severity = Literal["fail", "warn"]
CheckStatus = Literal["pass", "warn", "fail", "skip"]


class QACheck(BaseModel):
    id: str
    severity: Severity
    status: CheckStatus
    message: str
    values: dict[str, float | str | None] = {}


class QAReport(BaseModel):
    checks: list[QACheck]

    @property
    def passed(self) -> bool:
        return all(c.status != "fail" for c in self.checks)
