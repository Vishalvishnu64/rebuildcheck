"""Small data models shared by rebuildcheck's static checks."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class CheckResult:
    """The outcome of one repository check.

    ``details`` deliberately contains only JSON-friendly values.  Checks use it
    for small, structured context such as a relative file name, a line number,
    or the test command that was found.  Keeping the field optional makes the
    model convenient for checks that only need a human-readable explanation.
    """

    passed: bool
    name: str
    reason: str
    fix: str
    priority: int
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serialisable representation of this result."""

        return {
            "passed": self.passed,
            "name": self.name,
            "reason": self.reason,
            "fix": self.fix,
            "priority": self.priority,
            "details": self.details,
        }
