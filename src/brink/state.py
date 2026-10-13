"""Small shared value types: units, layers and the per-turn rules context."""
from __future__ import annotations

from dataclasses import dataclass, field

MILITARY = "M"
CIVIL = "C"
UNIT_TYPES = ("A", "F", "C", "I")
TYPE_NAMES = {"A": "Army", "F": "Fleet", "C": "Capital", "I": "Influence"}


def layer_of(utype: str) -> str:
    return MILITARY if utype in ("A", "F") else CIVIL


def prov_of(loc: str) -> str:
    """'SPA/NC' -> 'SPA'."""
    return loc.split("/", 1)[0]


@dataclass(frozen=True)
class Unit:
    power: str
    type: str
    loc: str  # province id, or coast id ('SPA/NC') for a fleet on a multi-coast province

    @property
    def prov(self) -> str:
        return prov_of(self.loc)

    @property
    def layer(self) -> str:
        return layer_of(self.type)

    @property
    def slot(self) -> tuple[str, str]:
        return (self.prov, self.layer)

    def ref(self) -> str:
        return f"{self.type} {self.loc}"

    def to_json(self) -> dict:
        return {"power": self.power, "type": self.type, "at": self.loc}


@dataclass(frozen=True)
class Resolution:
    """An Assembly resolution. kind: sanctions | peacekeeping | navigation | accord | recognition."""

    kind: str
    arg: str | None = None

    KINDS = ("sanctions", "peacekeeping", "navigation", "accord", "recognition")

    def text(self) -> str:
        return self.kind.upper() + (f" {self.arg}" if self.arg else "")


@dataclass
class Effects:
    """Assembly effects in force for the current year."""

    sanctions_regime: set[str] = field(default_factory=set)   # powers whose Capital can't move
    peacekeeping: set[str] = field(default_factory=set)       # provinces closed to Military moves
    navigation: set[str] = field(default_factory=set)         # chokepoints that can't be closed
    recognition: set[str] = field(default_factory=set)        # provinces closed to civil moves
    climate_accord: bool = False

    @classmethod
    def from_resolution(cls, res: Resolution | None) -> "Effects":
        e = cls()
        if res is None:
            return e
        if res.kind == "sanctions":
            e.sanctions_regime.add(res.arg)
        elif res.kind == "peacekeeping":
            e.peacekeeping.add(res.arg)
        elif res.kind == "navigation":
            e.navigation.add(res.arg)
        elif res.kind == "recognition":
            e.recognition.add(res.arg)
        elif res.kind == "accord":
            e.climate_accord = True
        return e


@dataclass
class TurnContext:
    """Everything outside the unit positions that decides whether an order is legal."""

    year: int
    ownership: dict[str, str | None]
    umbrellas: set[tuple[str, str]]        # (protector, protected)
    effects: Effects = field(default_factory=Effects)
