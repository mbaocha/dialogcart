"""Navigator — own presentation and browse movement."""

from __future__ import annotations

from typing import Optional

from core.discovery.models import BrowseIntent, PresentedWindow, TrustedResult
from core.discovery.presentation.policy import NavigationPolicy


class Navigator:
    """Derive and advance PresentedWindow via a domain NavigationPolicy.

    Owns navigation state for the current trusted result. Does not search
    or bind selections. Grouping and ordering live in the policy.
    """

    def __init__(self, policy: NavigationPolicy) -> None:
        self._policy = policy
        self._trusted: Optional[TrustedResult] = None
        self._window: Optional[PresentedWindow] = None
        self._last_moved: bool = False

    @property
    def current_window(self) -> Optional[PresentedWindow]:
        return self._window

    @property
    def last_moved(self) -> bool:
        """Whether the most recent ``browse`` advanced the window."""
        return self._last_moved

    def present(self, trusted: TrustedResult) -> PresentedWindow:
        """Create the initial PresentedWindow for ``trusted``."""
        window = self._policy.initial_window(trusted)
        self._trusted = trusted
        self._window = window
        self._last_moved = False
        return window

    def browse(
        self,
        intent: BrowseIntent,
        *,
        trusted: Optional[TrustedResult] = None,
        current: Optional[PresentedWindow] = None,
    ) -> PresentedWindow:
        """Advance presentation according to ``intent``.

        Uses instance state when ``trusted`` / ``current`` are omitted.
        """
        trusted_result = trusted if trusted is not None else self._trusted
        current_window = current if current is not None else self._window
        if trusted_result is None:
            raise ValueError("Navigator.browse requires a TrustedResult")
        if current_window is None:
            raise ValueError("Navigator.browse requires a PresentedWindow")

        next_window = self._policy.advance(trusted_result, current_window, intent)
        self._last_moved = next_window is not current_window and next_window != current_window
        self._trusted = trusted_result
        self._window = next_window
        return next_window
