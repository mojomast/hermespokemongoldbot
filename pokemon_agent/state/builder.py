"""Game-state orchestrator.

:func:`build_game_state` calls every reader method and assembles the
results into a single JSON-serialisable dictionary.

:func:`build_state_summary` renders that dict as a compact, human-readable
text block suitable for injection into an LLM prompt.
"""

from __future__ import annotations

import datetime
import math
import time
import traceback
from typing import Any, Dict, Optional

from pokemon_agent.memory.reader import GameMemoryReader


def _entropy(hist: list[int], total: int) -> float:
    ent = 0.0
    for count in hist:
        if count:
            p = count / total
            ent -= p * math.log2(p)
    return ent


def _visual_textbox_features(reader: GameMemoryReader) -> dict[str, Any]:
    emu = getattr(reader, "emu", None)
    get_screen = getattr(emu, "get_screen", None)
    if not callable(get_screen):
        return {}
    try:
        from PIL import Image, ImageFilter, ImageStat
        im = get_screen()
        if not isinstance(im, Image.Image):
            im = Image.fromarray(im)
        gray = im.convert("L")
        hist = gray.histogram()
        total = gray.width * gray.height
        entropy = _entropy(hist, total)
        lower = gray.crop((0, 92, 160, 144))
        upper = gray.crop((0, 0, 160, 92))
        lower_stat = ImageStat.Stat(lower)
        upper_stat = ImageStat.Stat(upper)
        lower_edges = lower.filter(ImageFilter.FIND_EDGES)
        edge_stat = ImageStat.Stat(lower_edges)
        lower_colors = lower.getcolors(maxcolors=1_000_000) or []
        dark_lower = sum(c for c, v in lower_colors if v < 72) / (160 * 52)
        bright_lower = sum(c for c, v in lower_colors if v > 180) / (160 * 52)
        palette_levels = len({v // 16 for _c, v in lower_colors})
        contrast_gap = abs(lower_stat.mean[0] - upper_stat.mean[0])
        textbox_like = bright_lower > 0.18 and lower_stat.stddev[0] > 18 and (edge_stat.mean[0] > 12 or contrast_gap > 14)
        striped_floor_like = (
            palette_levels <= 6
            and entropy < 2.35
            and lower_stat.stddev[0] > 35
            and edge_stat.mean[0] > 45
            and contrast_gap > 25
        )
        bright_dialogue_panel = bright_lower > 0.55 and contrast_gap > 60 and edge_stat.mean[0] > 35
        if striped_floor_like and not bright_dialogue_panel:
            textbox_like = False
        menu_like = textbox_like and lower_stat.stddev[0] > 28 and palette_levels <= 8
        screen_class = "menu_or_text" if menu_like else "dialogue" if textbox_like else "overworld_or_battle"
        visual_active = False
        if screen_class in {"dialogue", "menu_or_text"} and dark_lower > 0.12 and bright_lower > 0.5:
            visual_active = bright_dialogue_panel or (dark_lower > 0.14 and contrast_gap > 70)
        return {
            "screen_class": screen_class,
            "dark_lower": round(dark_lower, 3),
            "bright_lower": round(bright_lower, 3),
            "lower_edge_mean": round(edge_stat.mean[0], 3),
            "lower_contrast_gap": round(contrast_gap, 3),
            "bright_dialogue_panel": bright_dialogue_panel,
            "visual_textbox_active": visual_active,
        }
    except Exception:
        return {}


def build_game_state(
    reader: GameMemoryReader,
    frame_count: Optional[int] = None,
) -> Dict[str, Any]:
    """Read all game data and assemble a complete state snapshot.

    Parameters
    ----------
    reader : GameMemoryReader
        An initialised memory reader bound to a running emulator.
    frame_count : int, optional
        Current emulator frame count (injected into metadata).

    Returns
    -------
    dict
        A JSON-serialisable game-state dictionary.  Sections that fail
        to read are ``None`` with an ``"_error"`` key.
    """
    read_started_at = time.perf_counter()
    read_started_frame = getattr(getattr(reader, "emu", None), "frame_count", frame_count)
    state: Dict[str, Any] = {
        "metadata": {
            "game": reader.game_name,
            "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "frame_count": frame_count,
            "read_started_frame": read_started_frame,
        },
    }

    sections = {
        "player": reader.read_player,
        "party": reader.read_party,
        "bag": reader.read_bag,
        "battle": reader.read_battle,
        "dialog": reader.read_dialog,
        "map": reader.read_map_info,
        "flags": reader.read_flags,
    }
    read_menu = getattr(reader, "read_menu", None)
    if callable(read_menu):
        sections["menu"] = read_menu

    for key, fn in sections.items():
        try:
            state[key] = fn()
        except NotImplementedError as exc:
            state[key] = None
            state[f"{key}_error"] = str(exc)
        except Exception as exc:  # noqa: BLE001
            state[key] = None
            state[f"{key}_error"] = (
                f"{type(exc).__name__}: {exc}\n{traceback.format_exc()}"
            )

    visual = _visual_textbox_features(reader)
    if visual:
        state["visual"] = visual
        dialog = state.get("dialog")
        if isinstance(dialog, dict):
            dialog.setdefault("ram_active", dialog.get("active"))
            dialog["visual_active"] = visual.get("visual_textbox_active") is True
            dialog["visual_source"] = "screen_lower_panel"
            if dialog["visual_active"]:
                dialog["active"] = True

    read_finished_frame = getattr(getattr(reader, "emu", None), "frame_count", frame_count)
    state["metadata"].update({
        "read_finished_frame": read_finished_frame,
        "read_consistent": read_started_frame == read_finished_frame if read_started_frame is not None and read_finished_frame is not None else None,
        "read_duration_ms": round((time.perf_counter() - read_started_at) * 1000, 3),
    })

    return state


# -----------------------------------------------------------------------
# Text summary
# -----------------------------------------------------------------------

def build_state_summary(state: Dict[str, Any]) -> str:
    """Render a game state dict as a concise text summary for an LLM prompt.

    Parameters
    ----------
    state : dict
        A dict produced by :func:`build_game_state`.

    Returns
    -------
    str
        Multi-line plain-text summary.
    """
    lines: list[str] = []
    _hr = "=" * 50

    lines.append(_hr)
    lines.append("GAME STATE SNAPSHOT")
    lines.append(_hr)

    # -- metadata --
    meta = state.get("metadata", {})
    lines.append(f"Game      : {meta.get('game', '?')}")
    lines.append(f"Timestamp : {meta.get('timestamp', '?')}")
    if meta.get("frame_count") is not None:
        lines.append(f"Frame     : {meta['frame_count']}")

    # -- map --
    map_info = state.get("map")
    if map_info:
        lines.append(f"Location  : {map_info.get('map_name', '?')} (id={map_info.get('map_id')})")

    # -- player --
    player = state.get("player")
    if player:
        lines.append("")
        lines.append("--- PLAYER ---")
        lines.append(f"Name    : {player.get('name', '?')}")
        lines.append(f"Rival   : {player.get('rival_name', '?')}")
        lines.append(f"Money   : ${player.get('money', 0):,}")
        badges = player.get("badges", [])
        lines.append(f"Badges  : {len(badges)} — {', '.join(badges) if badges else 'none'}")
        pos = player.get("position", {})
        lines.append(f"Position: ({pos.get('x', '?')}, {pos.get('y', '?')})  facing {player.get('facing', '?')}")
        lines.append(f"Playtime: {player.get('play_time', '?')}")
    elif state.get("player_error"):
        lines.append(f"\n[Player read error: {state['player_error']}]")

    # -- party --
    party = state.get("party")
    if party:
        lines.append("")
        lines.append("--- PARTY ---")
        for i, mon in enumerate(party, 1):
            moves_str = ", ".join(
                m["name"] if isinstance(m, dict) else str(m) for m in mon.get("moves", [])
            )
            lines.append(
                f"  {i}. {mon.get('nickname', '?')} "
                f"({mon.get('species', '?')} Lv{mon.get('level', '?')})  "
                f"HP {mon.get('hp', '?')}/{mon.get('max_hp', '?')}  "
                f"Status: {mon.get('status', '?')}"
            )
            lines.append(f"     Moves: {moves_str}")
    elif state.get("party_error"):
        lines.append(f"\n[Party read error: {state['party_error']}]")

    # -- battle --
    battle = state.get("battle")
    if battle and battle.get("in_battle"):
        lines.append("")
        lines.append("--- BATTLE ---")
        lines.append(f"Type: {battle.get('type', '?')}")
        enemy = battle.get("enemy")
        if enemy:
            lines.append(
                f"Enemy: {enemy.get('species', '?')} Lv{enemy.get('level', '?')}  "
                f"HP {enemy.get('hp', '?')}/{enemy.get('max_hp', '?')}  "
                f"Status: {enemy.get('status', '?')}"
            )
            enemy_moves = enemy.get("moves", [])
            if enemy_moves:
                lines.append(f"Enemy moves: {', '.join(str(m) for m in enemy_moves)}")
    elif battle and not battle.get("in_battle"):
        lines.append("\nNot in battle.")
    elif state.get("battle_error"):
        lines.append(f"\n[Battle read error: {state['battle_error']}]")

    # -- dialog --
    dialog = state.get("dialog")
    if dialog and dialog.get("active"):
        lines.append("")
        lines.append("--- DIALOG ---")
        lines.append("Text box is active.")
    elif state.get("dialog_error"):
        lines.append(f"\n[Dialog read error: {state['dialog_error']}]")

    # -- bag --
    bag = state.get("bag")
    if bag:
        lines.append("")
        lines.append("--- BAG ---")
        for entry in bag:
            lines.append(f"  {entry.get('item', '?')} x{entry.get('quantity', '?')}")
    elif state.get("bag_error"):
        lines.append(f"\n[Bag read error: {state['bag_error']}]")

    # -- flags --
    flags = state.get("flags")
    if flags:
        lines.append("")
        lines.append("--- FLAGS ---")
        lines.append(f"Has Pokedex   : {flags.get('has_pokedex', '?')}")
        lines.append(f"Pokedex owned : {flags.get('pokedex_owned', '?')}")
        lines.append(f"Pokedex seen  : {flags.get('pokedex_seen', '?')}")
        lines.append(f"Badges        : {flags.get('badge_count', 0)}")
    elif state.get("flags_error"):
        lines.append(f"\n[Flags read error: {state['flags_error']}]")

    lines.append(_hr)
    return "\n".join(lines)
