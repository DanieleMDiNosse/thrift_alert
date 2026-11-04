"""List-compatible adapter results with explicit observed scan coverage."""
from __future__ import annotations


class ScanListings(list):
    """Listings fetched in one bounded scan, not a claim of marketplace exhaustiveness."""

    def __init__(self, items=(), *, pages: int = 0, complete: bool = False,
                 reason: str = "page_limit"):
        super().__init__(items)
        self.pages = pages
        self.complete = complete
        self.reason = reason

    @property
    def coverage(self) -> dict:
        return {"pages": self.pages, "complete": self.complete, "reason": self.reason}
