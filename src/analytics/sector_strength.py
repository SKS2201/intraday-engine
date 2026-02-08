from __future__ import annotations

from dataclasses import dataclass

from src.providers.base import SectorSnapshot


@dataclass(frozen=True)
class SectorStrengthResult:
    scoreboard: list[SectorSnapshot]
    strongest_sector: str
    it_outperforming: bool


def compute_sector_strength(sectors: dict[str, SectorSnapshot]) -> SectorStrengthResult:
    if not sectors:
        return SectorStrengthResult(scoreboard=[], strongest_sector="UNKNOWN", it_outperforming=False)
    board = sorted(sectors.values(), key=lambda x: x.rel_vs_nifty, reverse=True)
    strongest = board[0].name
    it = sectors.get("IT") or sectors.get("NIFTY IT")
    it_outperforming = bool(it and it.rel_vs_nifty > 0)
    return SectorStrengthResult(board, strongest, it_outperforming)
