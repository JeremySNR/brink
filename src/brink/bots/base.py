"""The interface every player (bot or human adapter) implements. Every method returns order text in the
notation of docs/ARCHITECTURE.md; anything illegal is voided by the engine, anything missing defaults."""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover
    from ..game import Game
    from ..state import Unit


class Player:
    name = "holdbot"

    def orders(self, game: "Game", power: str) -> list[str]:
        """Movement phase (Spring/Fall): one order per unit, plus optional 'USA WITHDRAW JPN'.
        Units without an order hold."""
        return []

    def retreats(self, game: "Game", power: str, options: dict["Unit", list[str]]) -> list[str]:
        """'A UKR R BLR' / 'A UKR D' for each dislodged unit of `power` (options: legal destinations).
        Unordered dislodged units are disbanded."""
        return []

    def propose(self, game: "Game", power: str) -> str | None:
        """Called on the Chair only: 'PROPOSE SANCTIONS CHN' etc., or None to propose nothing."""
        return None

    def vote(self, game: "Game", power: str, proposal: str) -> str:
        """'VOTE YES' | 'VOTE NO' | 'VOTE VETO'. Missing/invalid counts as not voting."""
        return "VOTE NO"

    def adjust(self, game: "Game", power: str, change: int) -> list[str]:
        """change > 0: up to that many 'BUILD A WAS'; change < 0: that many 'DISBAND I ROM'
        (shortfalls follow the default disband rule)."""
        return []

    def umbrellas(self, game: "Game", power: str) -> list[str] | None:
        """Nuclear powers only: the full list of umbrellas for next year ('USA UMBRELLA JPN').
        None keeps this power's current umbrellas."""
        return None
