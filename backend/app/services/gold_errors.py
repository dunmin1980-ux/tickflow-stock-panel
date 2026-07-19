"""Safe machine-readable failures for the Gold data pipeline."""
from __future__ import annotations


class GoldDataError(RuntimeError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)
