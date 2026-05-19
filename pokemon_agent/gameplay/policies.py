"""Baseline gameplay policies for Pokemon Gold Navigation V2."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from .gold_data import BALL_ITEM_IDS, CATCH_PRIORITY_SPECIES, DANGEROUS_ENEMY_MOVE_IDS, HEALING_ITEM_IDS, HM_ROLE_SPECIES, STATUS_HEAL_ITEM_IDS
from .state_model import GameSnapshot


CatchAction = Literal["throw_ball", "weaken", "run", "fight", "wait"]


@dataclass(frozen=True, slots=True)
class InventorySummary:
    balls: int
    healing_items: int
    status_heals: int

    @property
    def can_catch(self) -> bool:
        return self.balls > 0


@dataclass(frozen=True, slots=True)
class CatchDecision:
    action: CatchAction
    reason: str
    target_species: str | None = None


def summarize_inventory(state: GameSnapshot) -> InventorySummary:
    balls = sum(item.quantity for item in state.bag if item.item_id in BALL_ITEM_IDS)
    healing = sum(item.quantity for item in state.bag if item.item_id in HEALING_ITEM_IDS)
    status_heals = sum(item.quantity for item in state.bag if item.item_id in STATUS_HEAL_ITEM_IDS)
    return InventorySummary(balls=balls, healing_items=healing, status_heals=status_heals)


def choose_catch_action(state: GameSnapshot, owned_species: set[str] | None = None) -> CatchDecision:
    """Return a conservative wild-battle catch action.

    This baseline catches useful/HM-role species when resources allow and avoids
    spending balls on duplicates or unsafe low-HP situations.
    """
    owned_species = owned_species or {mon.species for mon in state.party}
    battle = state.battle
    if not battle.in_battle:
        return CatchDecision("wait", "not in battle")
    if not battle.wild:
        return CatchDecision("fight", "trainer or non-wild battle")

    inventory = summarize_inventory(state)
    if not inventory.can_catch:
        return CatchDecision("run", "no balls available", battle.enemy_species)
    if state.party_full and battle.enemy_species not in CATCH_PRIORITY_SPECIES:
        return CatchDecision("run", "party is full and species is not high priority", battle.enemy_species)
    if battle.enemy_species in owned_species:
        return CatchDecision("run", "species already owned", battle.enemy_species)

    lead = state.lead
    if lead and lead.hp_ratio is not None and lead.hp_ratio < 0.25:
        return CatchDecision("run", "lead HP too low for safe catching", battle.enemy_species)
    if battle.enemy_species in CATCH_PRIORITY_SPECIES or _has_hm_role(battle.enemy_species):
        if battle.enemy_has_status:
            return CatchDecision("throw_ball", "target has status condition", battle.enemy_species)
        if any(move in DANGEROUS_ENEMY_MOVE_IDS for move in battle.enemy_moves):
            return CatchDecision("throw_ball", "target has dangerous move; avoid weakening", battle.enemy_species)
        if battle.enemy_hp_ratio is not None:
            if battle.enemy_hp_ratio <= 0.5:
                return CatchDecision("throw_ball", "target HP is reduced", battle.enemy_species)
            return CatchDecision("weaken", "target HP is high", battle.enemy_species)
        return CatchDecision("throw_ball", "species is useful for progression", battle.enemy_species)
    if battle.enemy_has_status or any(move in DANGEROUS_ENEMY_MOVE_IDS for move in battle.enemy_moves):
        return CatchDecision("throw_ball", "early roster target is safer to catch than weaken", battle.enemy_species)
    if len(state.party) < 3:
        if battle.enemy_hp_ratio is not None:
            if battle.enemy_hp_ratio <= 0.5:
                return CatchDecision("throw_ball", "early roster slot open and target HP is reduced", battle.enemy_species)
            return CatchDecision("weaken", "early roster slot open; weaken new species before catching", battle.enemy_species)
        return CatchDecision("throw_ball", "early roster slot open for new species", battle.enemy_species)
    return CatchDecision("run", "species is not a current catch target", battle.enemy_species)


def _has_hm_role(species: str) -> bool:
    return any(species in candidates for candidates in HM_ROLE_SPECIES.values())


def roster_roles(state: GameSnapshot) -> dict[str, list[str]]:
    roles: dict[str, list[str]] = {"main": [], "fly": [], "surf": [], "strength": [], "flash": []}
    if state.party:
        roles["main"].append(state.party[0].species)
    for mon in state.party:
        for role, candidates in HM_ROLE_SPECIES.items():
            if mon.species in candidates:
                roles[role].append(mon.species)
    return roles
