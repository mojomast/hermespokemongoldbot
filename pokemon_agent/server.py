"""
Pokemon Agent — FastAPI Game Server

Provides HTTP + WebSocket API for controlling a Game Boy / GBA emulator
running a Pokemon ROM, reading game state, and broadcasting events.
"""

import asyncio
import base64
import hashlib
import io
import json
import os
import re
import subprocess
import time
from collections import deque
from functools import partial
from pathlib import Path
from fractions import Fraction
from typing import Any, Optional, Set

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel

__version__ = "0.1.0"

# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------

class GameConfig(BaseModel):
    """Server configuration — set before startup."""
    rom_path: str
    game_type: str = "auto"       # "red", "firered", or "auto"
    port: int = 8765
    data_dir: str = "~/.pokemon-agent"
    load_state: Optional[str] = None  # Save-state name to auto-load on startup


class ActionRequest(BaseModel):
    """Body for POST /action."""
    actions: list[str]


class SaveRequest(BaseModel):
    """Body for POST /save and POST /load."""
    name: str
    overwrite: bool = False


class RunRequest(BaseModel):
    """Body for run save/load/new operations."""
    name: str
    save_current: bool = True


class AutoplayerControlRequest(BaseModel):
    enabled: Optional[bool] = None
    engine: Optional[str] = None
    objective: Optional[str] = None
    movement_bias: Optional[str] = None
    dialogue_speed: Optional[str] = None
    guidance_prompt: Optional[str] = None
    dry_run: Optional[bool] = None
    allow_overworld_movement: Optional[bool] = None
    allow_battle_actions: Optional[bool] = None
    auto_handoff_enabled: Optional[bool] = None
    auto_handoff_v1_fallback: Optional[str] = None
    auto_handoff_v2_fallback: Optional[str] = None


class TunnelStartRequest(BaseModel):
    """Body for POST /tunnel/start."""
    path: str = "/dashboard/watch.html"


class RomSelectRequest(BaseModel):
    """Body for POST /roms/select."""
    path: str


class RTCSessionDescriptionRequest(BaseModel):
    """Browser WebRTC offer for POST /rtc/offer."""
    sdp: str
    type: str


# ---------------------------------------------------------------------------
# Global state
# ---------------------------------------------------------------------------

_config: Optional[GameConfig] = None
_emulator = None          # Emulator instance
_reader = None            # GameMemoryReader subclass instance
_start_time: float = 0.0
_loop: Optional[asyncio.AbstractEventLoop] = None
_rtc_peers: set[object] = set()
_emulator_lock: Optional[asyncio.Lock] = None
_rtc_media_pump: Optional[object] = None
_watch_clients: Set[WebSocket] = set()
_watch_viewer_clients: Set[WebSocket] = set()
_watch_chat_messages: deque[dict[str, Any]] = deque(maxlen=80)

# WebSocket clients
_ws_clients: Set[WebSocket] = set()


def _watch_viewer_payload() -> dict[str, Any]:
    return {
        "type": "watch_stats",
        "viewers": len(_watch_viewer_clients),
        "rtc_peers": len(_rtc_peers),
        "updated_at": time.time(),
    }


async def _broadcast_watch(payload: dict[str, Any]) -> None:
    stale = []
    for ws in list(_watch_clients):
        try:
            await ws.send_json(payload)
        except Exception:
            stale.append(ws)
    for ws in stale:
        _watch_clients.discard(ws)


async def _broadcast_watch_stats() -> None:
    await _broadcast_watch(_watch_viewer_payload())


def _safe_save_name(name: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "_", name.strip()).strip("._")
    if not cleaned:
        raise HTTPException(status_code=400, detail="Save name is required")
    return cleaned[:64]


def _runs_dir() -> Path:
    return _data_dir() / "runs"


def _current_run_path() -> Path:
    return _runs_dir() / "current_run.json"


def _run_path(name: str) -> Path:
    return _runs_dir() / f"{_safe_save_name(name)}.json"


def _save_path(name: str) -> Path:
    return _data_dir() / "saves" / f"{_safe_save_name(name)}.state"


def _roms_dir() -> Path:
    return _data_dir() / "roms"


def _safe_rom_name(name: str) -> str:
    original = Path(name or "").name
    ext = Path(original).suffix.lower()
    if ext not in {".gb", ".gbc", ".gba"}:
        raise HTTPException(status_code=400, detail="ROM must be a .gb, .gbc, or .gba file")
    stem = re.sub(r"[^A-Za-z0-9_.-]+", "_", Path(original).stem).strip("._")
    if not stem:
        raise HTTPException(status_code=400, detail="ROM filename is required")
    return f"{stem[:80]}{ext}"


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _rom_info(path: Path) -> dict[str, Any]:
    path = path.expanduser().resolve()
    ext = path.suffix.lower()
    supported_extension = ext in {".gb", ".gbc", ".gba"}
    try:
        game_type = _detect_game_type(str(path)) if supported_extension else "unsupported"
    except Exception:
        game_type = "unsupported"
    port = _config.port if _config is not None else 9876
    data_dir = _data_dir().expanduser().resolve()
    sha256 = _sha256_file(path) if path.exists() else None
    profile_data_dir = data_dir / "games" / f"{game_type}-{sha256[:12] if sha256 else 'unknown'}"
    launch_command = (
        f"pokemon-agent serve --rom {json.dumps(str(path))} "
        f"--port {port} --data-dir {json.dumps(str(profile_data_dir))}"
    )
    return {
        "name": path.name,
        "path": str(path),
        "size_bytes": path.stat().st_size if path.exists() else 0,
        "sha256": sha256,
        "extension": ext,
        "game_type": game_type,
        "supported": supported_extension and game_type != "unsupported",
        "autoplayer_profile": "gold_silver" if game_type == "gold" else ("red_blue" if game_type == "red" else "generic"),
        "active": bool(_config and Path(_config.rom_path).expanduser().resolve() == path),
        "profile_data_dir": str(profile_data_dir),
        "launch_command": launch_command,
        "dashboard_url": f"http://localhost:{port}/dashboard/",
        "watch_url": f"http://localhost:{port}/dashboard/watch.html",
    }

# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------

app = FastAPI(
    title="Pokemon Agent Server",
    version=__version__,
    description="HTTP + WebSocket API for Pokemon emulator control",
)

# CORS — allow everything for local dev
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _detect_game_type(rom_path: str) -> str:
    """Pick reader type based on file extension."""
    name = Path(rom_path).name.lower()
    if "pokemon_gold" in name or "pokemon gold" in name or "gold" in name or "silver" in name:
        return "gold"
    ext = Path(rom_path).suffix.lower()
    if ext == ".gb":
        return "red"
    if ext == ".gbc":
        return "generic"
    elif ext == ".gba":
        return "firered"
    raise ValueError(f"Unrecognised ROM extension: {ext}")


def _ensure_emulator():
    """Raise 503 if the emulator isn't ready."""
    if _emulator is None:
        raise HTTPException(status_code=503, detail="Emulator not initialised")


def _get_emulator_lock() -> asyncio.Lock:
    global _emulator_lock
    if _emulator_lock is None:
        _emulator_lock = asyncio.Lock()
    return _emulator_lock


async def _run_sync(func, *args):
    """Run an emulator call synchronously on the event-loop thread.

    PyBoy is not reliably thread-safe when the instance is created during
    FastAPI startup but tick/screen calls are dispatched to worker threads;
    generic GBC mode could get stuck on blank frames. These calls are short
    enough for the local watch/control server, so keep emulator access on one
    thread.
    """
    async with _get_emulator_lock():
        return func(*args)


def _rtc_bool_env(name: str, default: bool) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


class _PokemonVideoTrackBase:
    pass


class _PokemonAudioTrackBase:
    pass


def _load_rtc_classes():
    try:
        import av
        import numpy as np
        from aiortc import AudioStreamTrack, RTCPeerConnection, RTCSessionDescription, VideoStreamTrack
    except Exception as exc:
        raise HTTPException(
            status_code=501,
            detail="WebRTC support is not installed. Install pokemon-agent[rtc].",
        ) from exc

    class PokemonMediaPump:
        """Ticks the emulator and captures direct video/audio for WebRTC."""

        def __init__(self) -> None:
            self.capture_fps = 60
            self.video_fps = max(1, min(60, int(os.environ.get("POKEMON_AGENT_RTC_VIDEO_FPS", "60"))))
            self.tick_enabled = _rtc_bool_env("POKEMON_AGENT_RTC_TICK", True)
            self.audio_tracks: set[Any] = set()
            self.latest_image = None
            self.latest_frame_count = 0
            self._task: asyncio.Task | None = None
            self._running = False
            self.frames_captured = 0
            self.long_intervals = 0
            self.max_interval_ms = 0.0
            self.avg_capture_ms = 0.0
            self._last_capture_at: float | None = None

        def add_audio_track(self, audio_track: Any) -> None:
            self.audio_tracks.add(audio_track)

        def remove_audio_track(self, audio_track: Any) -> None:
            self.audio_tracks.discard(audio_track)

        def start(self) -> None:
            if self._task is None:
                self._running = True
                self._task = asyncio.create_task(self._run())

        async def stop(self) -> None:
            self._running = False
            if self._task is not None:
                self._task.cancel()
                try:
                    await self._task
                except asyncio.CancelledError:
                    pass
                self._task = None

        async def _run(self) -> None:
            interval = 1.0 / self.capture_fps
            next_tick = time.monotonic()
            video_stride = max(1, round(self.capture_fps / self.video_fps))
            while self._running:
                wait = next_tick - time.monotonic()
                if wait > 0:
                    await asyncio.sleep(wait)
                next_tick = max(next_tick + interval, time.monotonic())
                capture_started = time.monotonic()
                if self._last_capture_at is not None:
                    interval_ms = (capture_started - self._last_capture_at) * 1000
                    self.max_interval_ms = max(self.max_interval_ms, interval_ms)
                    if interval_ms > 35:
                        self.long_intervals += 1
                self._last_capture_at = capture_started

                def capture():
                    if self.tick_enabled:
                        _emulator.tick(1)
                    audio_data = None
                    if self.audio_tracks and hasattr(_emulator, "get_audio_samples"):
                        try:
                            audio_data = _emulator.get_audio_samples()
                        except Exception:
                            audio_data = None
                    image = None
                    frame_count = int(getattr(_emulator, "frame_count", 0) or 0)
                    if frame_count % video_stride == 0 or self.latest_image is None:
                        screen = _emulator.get_screen()
                        try:
                            from PIL import Image
                            if not isinstance(screen, Image.Image):
                                screen = Image.fromarray(screen)
                            image = screen.convert("RGB")
                        except Exception:
                            image = screen
                    return image, audio_data, frame_count

                image, audio_data, frame_count = await _run_sync(capture)
                if image is not None:
                    self.latest_image = image
                    self.latest_frame_count = frame_count
                if audio_data is not None:
                    for audio_track in tuple(self.audio_tracks):
                        audio_track.offer_audio(audio_data)
                capture_ms = (time.monotonic() - capture_started) * 1000
                self.avg_capture_ms = (self.avg_capture_ms * 0.95) + (capture_ms * 0.05)
                self.frames_captured += 1

        def stats(self) -> dict[str, Any]:
            return {
                "running": self._running,
                "capture_fps": self.capture_fps,
                "video_fps": self.video_fps,
                "tick_enabled": self.tick_enabled,
                "frames_captured": self.frames_captured,
                "latest_frame_count": self.latest_frame_count,
                "long_intervals": self.long_intervals,
                "max_interval_ms": round(self.max_interval_ms, 2),
                "avg_capture_ms": round(self.avg_capture_ms, 2),
                "subscribers": len(self.audio_tracks),
            }

        async def get_image(self):
            while self.latest_image is None:
                await asyncio.sleep(1 / self.capture_fps)
            return self.latest_image

    class PokemonVideoTrack(VideoStreamTrack, _PokemonVideoTrackBase):
        """WebRTC video track backed by the emulator framebuffer."""

        def __init__(self, pump: PokemonMediaPump) -> None:
            super().__init__()
            self.fps = pump.video_fps
            self.pump = pump
            self._frame_index = 0
            self._next_frame_at = time.monotonic()

        async def recv(self):
            _ensure_emulator()
            wait = self._next_frame_at - time.monotonic()
            if wait > 0:
                await asyncio.sleep(wait)
            self._next_frame_at = max(self._next_frame_at + 1.0 / self.fps, time.monotonic())

            image = await self.pump.get_image()
            frame = av.VideoFrame.from_image(image)
            frame.pts = self._frame_index
            frame.time_base = Fraction(1, self.fps)
            self._frame_index += 1
            return frame

    class PokemonAudioTrack(AudioStreamTrack, _PokemonAudioTrackBase):
        """System audio capture with silence fallback.

        Set POKEMON_AGENT_AUDIO_DEVICE to a Pulse/PipeWire monitor source name or
        numeric device index to capture emulator/system output. If capture is not
        available, WebRTC still negotiates a silent Opus audio track.
        """

        def __init__(self) -> None:
            super().__init__()
            self.sample_rate = self._direct_sample_rate()
            self.samples_per_frame = 960
            self.channels = 1
            self._pts = 0
            self._queue: asyncio.Queue[Any] = asyncio.Queue(maxsize=8)
            self._stream = None
            self._np = np
            self.gain = max(0.1, min(16.0, float(os.environ.get("POKEMON_AGENT_RTC_AUDIO_GAIN", "6.0"))))
            self._pending_audio = self._np.zeros((0, 1), dtype=self._np.int16)
            self.direct_audio = hasattr(_emulator, "get_audio_samples")
            if not self.direct_audio:
                self._start_capture()

        def _direct_sample_rate(self) -> int:
            try:
                if hasattr(_emulator, "get_audio_sample_rate"):
                    return int(_emulator.get_audio_sample_rate())
            except Exception:
                pass
            return 48000

        def _start_capture(self) -> None:
            if not _rtc_bool_env("POKEMON_AGENT_RTC_AUDIO", True):
                return
            try:
                import sounddevice as sd
                loop = asyncio.get_running_loop()
                device_value = os.environ.get("POKEMON_AGENT_AUDIO_DEVICE")
                device: int | str | None = None
                if device_value:
                    device = int(device_value) if device_value.isdigit() else device_value
                else:
                    for index, info in enumerate(sd.query_devices()):
                        name = str(info.get("name") or "").lower()
                        if "monitor" in name and int(info.get("max_input_channels") or 0) > 0:
                            device = index
                            break

                def callback(indata, frames, _time_info, status):
                    if status:
                        pass
                    data = self._np.asarray(indata[:, :1], dtype=self._np.int16).copy()
                    try:
                        loop.call_soon_threadsafe(self.offer_audio, data)
                    except RuntimeError:
                        pass

                self._stream = sd.InputStream(
                    samplerate=self.sample_rate,
                    channels=1,
                    dtype="int16",
                    blocksize=self.samples_per_frame,
                    device=device,
                    callback=callback,
                )
                self._stream.start()
            except Exception:
                self._stream = None

        def _normalize_audio(self, data: Any) -> Any | None:
            data = self._np.asarray(data)
            if data.size == 0:
                return None
            if data.ndim == 2 and data.shape[1] > 1:
                data = data.astype(self._np.int16).mean(axis=1, keepdims=True).astype(self._np.int16)
            elif data.ndim == 1:
                data = data.reshape((-1, 1))
            if data.dtype == self._np.int8:
                data = data.astype(self._np.int16) * 256
            elif data.dtype != self._np.int16:
                data = data.astype(self._np.int16)
            if self.gain != 1.0:
                data = self._np.clip(data.astype(self._np.float32) * self.gain, -32768, 32767).astype(self._np.int16)
            return data

        def capture_direct_audio(self) -> Any | None:
            try:
                return self._normalize_audio(_emulator.get_audio_samples())
            except Exception:
                return None

        def offer_audio(self, data: Any) -> None:
            data = self._normalize_audio(data)
            if data is None:
                return
            if self._queue.full():
                try:
                    self._queue.get_nowait()
                except asyncio.QueueEmpty:
                    pass
            try:
                self._queue.put_nowait(data)
            except asyncio.QueueFull:
                pass

        async def _next_audio_chunk(self) -> Any:
            deadline = time.monotonic() + 0.2
            while self._pending_audio.shape[0] < self.samples_per_frame:
                timeout = max(0.0, deadline - time.monotonic())
                if timeout <= 0:
                    break
                try:
                    data = await asyncio.wait_for(self._queue.get(), timeout=timeout)
                except asyncio.TimeoutError:
                    break
                self._pending_audio = self._np.concatenate((self._pending_audio, data), axis=0)
                max_pending = self.sample_rate // 2
                if self._pending_audio.shape[0] > max_pending:
                    self._pending_audio = self._pending_audio[-max_pending:]
            if self._pending_audio.shape[0] >= self.samples_per_frame:
                chunk = self._pending_audio[:self.samples_per_frame]
                self._pending_audio = self._pending_audio[self.samples_per_frame:]
                return chunk
            if self._pending_audio.shape[0] > 0:
                needed = self.samples_per_frame - self._pending_audio.shape[0]
                chunk = self._np.concatenate(
                    (self._pending_audio, self._np.zeros((needed, 1), dtype=self._np.int16)),
                    axis=0,
                )
                self._pending_audio = self._np.zeros((0, 1), dtype=self._np.int16)
                return chunk
            return self._np.zeros((self.samples_per_frame, 1), dtype=self._np.int16)

        async def recv(self):
            data = await self._next_audio_chunk()
            frame = av.AudioFrame.from_ndarray(data.T, format="s16", layout="mono")
            frame.sample_rate = self.sample_rate
            frame.pts = self._pts
            frame.time_base = Fraction(1, self.sample_rate)
            self._pts += data.shape[0]
            return frame

        async def stop_capture(self) -> None:
            if self._stream is not None:
                try:
                    self._stream.stop()
                    self._stream.close()
                except Exception:
                    pass
                self._stream = None

    return RTCPeerConnection, RTCSessionDescription, PokemonVideoTrack, PokemonAudioTrack, PokemonMediaPump


async def _close_rtc_peer(peer: object) -> None:
    global _rtc_media_pump
    _rtc_peers.discard(peer)
    audio_track = getattr(peer, "_pokemon_audio_track", None)
    media_pump = getattr(peer, "_pokemon_media_pump", None)
    if media_pump is not None and audio_track is not None and hasattr(media_pump, "remove_audio_track"):
        media_pump.remove_audio_track(audio_track)
    if not _rtc_peers and _rtc_media_pump is not None:
        media_pump = _rtc_media_pump
        _rtc_media_pump = None
    else:
        media_pump = None
    if media_pump is not None and hasattr(media_pump, "stop"):
        await media_pump.stop()
    if audio_track is not None and hasattr(audio_track, "stop_capture"):
        await audio_track.stop_capture()
    close = getattr(peer, "close", None)
    if close is not None:
        await close()


async def broadcast(event: dict):
    """Send a JSON event to every connected WebSocket client."""
    dead: list[WebSocket] = []
    payload = json.dumps(event)
    for ws in _ws_clients:
        try:
            await ws.send_text(payload)
        except Exception:
            dead.append(ws)
    for ws in dead:
        _ws_clients.discard(ws)


def _get_state_dict() -> dict:
    """Build full game state from the memory reader."""
    from pokemon_agent.state.builder import build_game_state
    return build_game_state(_reader, frame_count=getattr(_emulator, "frame_count", None))


def _get_screenshot_bytes() -> bytes:
    """Grab the current frame as PNG bytes."""
    screen = _emulator.get_screen()          # PIL Image or numpy array
    buf = io.BytesIO()
    # If it's a numpy array, convert to PIL first
    try:
        from PIL import Image
        if not isinstance(screen, Image.Image):
            import numpy as np
            screen = Image.fromarray(screen)
        screen.save(buf, format="PNG")
    except ImportError:
        # Fallback: assume screen already has save()
        screen.save(buf, format="PNG")
    return buf.getvalue()


def _make_reader(game_type: str):
    if game_type == "red":
        from pokemon_agent.memory.red import PokemonRedReader
        return PokemonRedReader(_emulator)
    if game_type == "firered":
        from pokemon_agent.memory.firered import PokemonFireRedReader
        return PokemonFireRedReader(_emulator)
    if game_type == "gold":
        from pokemon_agent.memory.gold import PokemonGoldReader
        return PokemonGoldReader(_emulator)
    if game_type == "generic":
        from pokemon_agent.memory.generic import GenericGameBoyReader
        return GenericGameBoyReader(_emulator, game_name=f"Generic mode ({Path(_config.rom_path).name})")
    raise ValueError(f"Unknown game type: {game_type}")


def _load_emulator_from_config() -> str:
    global _emulator, _reader
    if _config is None:
        raise RuntimeError("Server not configured")
    rom = Path(_config.rom_path).expanduser().resolve()
    game_type = _config.game_type
    if game_type == "auto":
        game_type = _detect_game_type(str(rom))
    if _emulator is not None:
        try:
            _emulator.close()
        except Exception:
            pass
    from pokemon_agent.emulator import create_emulator
    _emulator = create_emulator(str(rom))
    _reader = _make_reader(game_type)
    return game_type


def _mount_dashboard() -> None:
    if any(getattr(route, "path", None) == "/dashboard" for route in app.routes):
        return
    try:
        import pokemon_agent.dashboard as dashboard_mod  # noqa: F401
        from fastapi.staticfiles import StaticFiles
        dash_dir = Path(dashboard_mod.__file__).parent / "static"
        if dash_dir.is_dir():
            app.mount("/dashboard", StaticFiles(directory=str(dash_dir), html=True), name="dashboard")
            print("[server] Dashboard mounted at /dashboard")
        else:
            print("[server] Dashboard module found but no static/ directory")
    except ImportError:
        print("[server] Dashboard not installed — /dashboard unavailable")
        print("[server]   Install with: pip install pokemon-agent[dashboard]")


def _data_dir() -> Path:
    if _config is None:
        return Path("~/.pokemon-agent").expanduser()
    return Path(_config.data_dir).expanduser()


def _autoplayer_control_path() -> Path:
    return _data_dir() / "gold_autoplayer_control.json"


def _autoplayer_status_path() -> Path:
    return _data_dir() / "gold_autoplayer_status.json"


def _autoplayer_log_path() -> Path:
    return _data_dir() / "gold_autoplayer.jsonl"


def _autoplayer_v2_log_path() -> Path:
    return _data_dir() / "gold_autoplayer_v2.jsonl"


def _autoplayer_unified_log_path() -> Path:
    return _data_dir() / "pokemon_autoplayer.jsonl"


def _autoplayer_v2_learning_path() -> Path:
    return _data_dir() / "gold_autoplayer_v2_learning.json"


def _autoplayer_unified_learning_path() -> Path:
    return _data_dir() / "pokemon_learning_memory.json"


def _autoplayer_world_path() -> Path:
    return _data_dir() / "gold_world_model.json"


def _autoplayer_supervisor_status_path() -> Path:
    return _data_dir() / "gold_autoplayer_supervisor_status.json"


def _supervisor_health(supervisor: dict) -> dict:
    now = time.time()
    updated = supervisor.get("updated_at") if isinstance(supervisor, dict) else None
    stale_seconds = now - updated if isinstance(updated, (int, float)) else None
    stale = stale_seconds is None or stale_seconds > 10
    available = bool(supervisor)
    return {
        "available": available,
        "healthy": available and supervisor.get("child_running") is True and not stale and not supervisor.get("last_start_error"),
        "stale": stale,
        "stale_seconds": stale_seconds,
    }


def _default_autoplayer_control() -> dict:
    return {
        "enabled": True,
        "engine": "v1",
        "objective": "reach Violet City and win the first gym badge",
        "movement_bias": "west_north",
        "dialogue_speed": "fast",
        "guidance_prompt": "",
        "dry_run": True,
        "allow_overworld_movement": False,
        "allow_battle_actions": False,
        "auto_handoff_enabled": True,
        "auto_handoff_v1_fallback": "adaptive",
        "auto_handoff_v2_fallback": "v1",
    }


def _read_json_file(path: Path, default):
    try:
        return json.loads(path.read_text())
    except Exception:
        return default


def _shared_learning_summary(limit: int = 10) -> dict:
    payload = _read_json_file(_autoplayer_unified_learning_path(), {})
    facts = payload.get("facts") if isinstance(payload, dict) else []
    if not isinstance(facts, list):
        facts = []
    rows = [row for row in facts if isinstance(row, dict)]
    counts: dict[str, int] = {}
    for row in rows:
        category = str(row.get("category") or "PKM:PROGRESS")
        counts[category] = counts.get(category, 0) + 1
    rows.sort(key=lambda row: float(row.get("updated_at") or 0.0), reverse=True)
    return {
        "path": str(_autoplayer_unified_learning_path()),
        "facts": len(rows),
        "counts": counts,
        "recent_facts": rows[:limit],
        "updated_at": payload.get("updated_at") if isinstance(payload, dict) else None,
    }


def _tail_jsonl(path: Path, limit: int = 25) -> list[dict]:
    rows: deque[dict] = deque(maxlen=limit)
    try:
        chunk_size = 256 * 1024
        max_bytes = 8 * 1024 * 1024
        chunks: list[bytes] = []
        bytes_read = 0
        newline_count = 0
        with path.open("rb") as f:
            f.seek(0, os.SEEK_END)
            pos = f.tell()
            while pos > 0 and bytes_read < max_bytes and newline_count <= limit:
                size = min(chunk_size, pos, max_bytes - bytes_read)
                pos -= size
                f.seek(pos)
                chunk = f.read(size)
                chunks.append(chunk)
                bytes_read += len(chunk)
                newline_count += chunk.count(b"\n")
        data = b"".join(reversed(chunks))
        for line in data.splitlines()[-limit:]:
            try:
                rows.append(json.loads(line))
            except Exception:
                continue
    except Exception:
        return []
    return list(rows)


def _write_json_atomic(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True))
    tmp.replace(path)


def _tunnel_log_path() -> Path:
    return Path(os.environ.get("POKEMON_TUNNEL_LOG", "/tmp/pokemon-localhost-run.log"))


def _tunnel_launch_log_path() -> Path:
    return Path(os.environ.get("POKEMON_TUNNEL_LAUNCH_LOG", "/tmp/pokemon-localhost-run-launch.log"))


def _tunnel_script_path() -> Path:
    override = os.environ.get("POKEMON_TUNNEL_SCRIPT")
    if override:
        return Path(override).expanduser()
    return Path(__file__).resolve().parents[1] / "start_pokemon_tunnel.sh"


def _public_tunnel_url() -> Optional[str]:
    log_path = _tunnel_log_path()
    try:
        text = log_path.read_text(errors="replace")[-20000:]
    except Exception:
        return None
    matches = re.findall(r"https://[a-z0-9-]+\.lhr\.life", text)
    return matches[-1] if matches else None


def _tunnel_process_running() -> bool:
    try:
        result = subprocess.run(
            ["pgrep", "-f", "ssh .*nokey@localhost.run"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        return result.returncode == 0
    except Exception:
        return False


def _public_path(path: str) -> str:
    path = (path or "/dashboard/watch.html").strip()
    if not path.startswith("/"):
        path = "/" + path
    return path


def _tunnel_payload(path: str = "/dashboard/watch.html") -> dict[str, Any]:
    base_url = _public_tunnel_url()
    path = _public_path(path)
    return {
        "running": _tunnel_process_running(),
        "base_url": base_url,
        "path": path,
        "url": f"{base_url}{path}" if base_url else None,
        "watch_url": f"{base_url}/dashboard/watch.html" if base_url else None,
        "upload_url": f"{base_url}/dashboard/onboarding.html" if base_url else None,
        "control_url": f"{base_url}/dashboard/" if base_url else None,
        "log_path": str(_tunnel_log_path()),
    }


def _start_tunnel_process() -> None:
    if _tunnel_process_running():
        return
    script = _tunnel_script_path()
    if not script.exists():
        raise HTTPException(status_code=500, detail=f"Tunnel script not found: {script}")
    port = str(_config.port if _config is not None else 9876)
    env = os.environ.copy()
    env["POKEMON_AGENT_PORT"] = port
    env["POKEMON_TUNNEL_LOG"] = str(_tunnel_log_path())
    launch_log = _tunnel_launch_log_path()
    launch_log.parent.mkdir(parents=True, exist_ok=True)
    with launch_log.open("ab") as handle:
        subprocess.Popen(
            [str(script)],
            cwd=str(script.parent),
            env=env,
            stdout=handle,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )


def _v2_readiness(control: dict, status: dict, recent_v2: list[dict], supervisor: dict, supervisor_health: dict) -> dict:
    status_is_v2 = isinstance(status, dict) and status.get("engine") == "v2"
    updated = status.get("updated_at") if status_is_v2 else None
    stale_seconds = time.time() - updated if isinstance(updated, (int, float)) else None
    stale = stale_seconds is None or stale_seconds > 10
    latest_event = recent_v2[-1] if recent_v2 else None
    runner = status.get("runner") if status_is_v2 and isinstance(status.get("runner"), dict) else {}
    readiness = status.get("readiness") if status_is_v2 and isinstance(status.get("readiness"), dict) else {}
    blockers: list[str] = []
    if control.get("engine") not in {"v2", "unified", "adaptive"}:
        blockers.append("engine_not_selected")
    if not status_is_v2:
        blockers.append("v2_status_unavailable")
    elif stale:
        blockers.append("v2_status_stale")
    if control.get("dry_run") is not False:
        blockers.append("dry_run_enabled")
    if control.get("allow_overworld_movement") is not True:
        blockers.append("overworld_movement_disabled")
    if control.get("allow_battle_actions") is not True:
        blockers.append("battle_actions_disabled")
    if supervisor and supervisor.get("active_engine") not in {"v2", "unified", "adaptive"}:
        blockers.append("supervisor_not_running_v2_unified_or_adaptive")
    if supervisor and not supervisor_health.get("healthy"):
        blockers.append("supervisor_unhealthy")
    return {
        "status_available": status_is_v2,
        "status_stale": stale,
        "status_stale_seconds": stale_seconds,
        "latest_event": latest_event,
        "dry_run": control.get("dry_run") is not False,
        "allow_overworld_movement": control.get("allow_overworld_movement") is True,
        "allow_battle_actions": control.get("allow_battle_actions") is True,
        "runner": runner,
        "runner_readiness": readiness,
        "blockers": blockers,
        "ready_for_live_actions": not blockers,
    }


def _run_metadata(name: str, state: dict | None = None) -> dict:
    save_name = f"run_{_safe_save_name(name)}"
    path = _run_path(name)
    existing = _read_json_file(path, {})
    now = time.time()
    return {
        "name": _safe_save_name(name),
        "save_name": save_name,
        "created": existing.get("created", now),
        "modified": now,
        "rom": _config.rom_path if _config else None,
        "snapshot": state or existing.get("snapshot", {}),
    }


def _list_run_metadata() -> list[dict]:
    rows: list[dict] = []
    runs_dir = _runs_dir()
    if not runs_dir.exists():
        return rows
    for path in sorted(runs_dir.glob("*.json")):
        if path.name == "current_run.json":
            continue
        payload = _read_json_file(path, {})
        if isinstance(payload, dict) and payload.get("name"):
            rows.append(payload)
    rows.sort(key=lambda r: r.get("modified", 0), reverse=True)
    return rows


async def _save_run(name: str) -> dict:
    _ensure_emulator()
    run_name = _safe_save_name(name)
    save_name = f"run_{run_name}"
    save_path = _save_path(save_name)
    save_path.parent.mkdir(parents=True, exist_ok=True)
    await _run_sync(_emulator.save_state, str(save_path))
    state = await _run_sync(_get_state_dict)
    meta = _run_metadata(run_name, state=state)
    _write_json_atomic(_run_path(run_name), meta)
    _write_json_atomic(_current_run_path(), {"name": run_name, "save_name": save_name, "updated": time.time()})
    return meta


# ---------------------------------------------------------------------------
# Action parser
# ---------------------------------------------------------------------------

_ACTION_RE = re.compile(
    r"^(?P<kind>press|walk|hold|wait|a_until_dialog_end)(?:_(?P<rest>.+))?$"
)


def _rtc_pump_running() -> bool:
    pump = _rtc_media_pump
    return bool(
        pump is not None
        and getattr(pump, "_running", False)
        and getattr(pump, "tick_enabled", False)
    )


async def _wait_emulator_frames(frames: int) -> None:
    if frames <= 0:
        return
    if _rtc_pump_running():
        start = int(getattr(_emulator, "frame_count", 0) or 0)
        target = start + frames
        deadline = time.monotonic() + max(1.0, frames / 30)
        while int(getattr(_emulator, "frame_count", 0) or 0) < target:
            if time.monotonic() > deadline:
                break
            await asyncio.sleep(1 / 120)
        return
    await _run_sync(_emulator.tick, frames)


async def _hold_button(button: str, frames: int) -> None:
    if hasattr(_emulator, "button_down") and hasattr(_emulator, "button_up") and _rtc_pump_running():
        await _run_sync(_emulator.button_down, button)
        try:
            await _wait_emulator_frames(frames)
        finally:
            await _run_sync(_emulator.button_up, button)
        return
    await _run_sync(_emulator.press, button, frames)


async def _execute_action(action_str: str) -> None:
    """Parse and execute a single action string on the emulator.

    Supported formats:
        press_X       — press button X for 10 frames, wait 20 frames
        walk_X        — press direction for 16 frames, wait 8 frames
        hold_X_N      — hold button X for N frames
        wait_N        — tick N frames with no input
        a_until_dialog_end — press A every 30 frames until dialog clears (max 300)
    """
    action_str = action_str.strip().lower()

    if action_str == "a_until_dialog_end":
        for _ in range(10):  # max 300 frames = 10 * 30
            await _hold_button("a", 1)
            await _wait_emulator_frames(30)
            # Check dialog flag via reader if available
            try:
                state = await _run_sync(_get_state_dict)
                dialog = state.get("dialog") or {}
                active = dialog.get("active")
                # Structured readers expose dialog.active. Generic Gold returns
                # None, so keep pressing for the bounded loop instead of
                # incorrectly breaking after one A press.
                if active is False:
                    break
            except Exception:
                pass
        return

    # Split into tokens
    parts = action_str.split("_")

    if parts[0] == "press" and len(parts) >= 2:
        button = "_".join(parts[1:])
        # Hold button for 8 frames so the game registers the press,
        # then wait 12 frames for the game to process it.
        await _hold_button(button, 8)
        await _wait_emulator_frames(12)
        return

    if parts[0] == "walk" and len(parts) >= 2:
        direction = parts[1]
        # Gen 1/2 movement timing:
        #   - Button must be held >= 4 frames for the game's vblank joypad
        #     poll to register the input reliably.
        #   - wWalkCounter starts at 8, decrements each frame (2 px/frame
        #     = 16 px = 1 tile). Total walk animation = ~16 frames.
        #   - Minimum total frames for a confirmed tile move = 17.
        # Hold just long enough to complete one tile. Longer holds can continue
        # into additional tiles when a manual/dashboard press should be discrete.
        await _hold_button(direction, 20)
        await _wait_emulator_frames(60)
        return

    if parts[0] == "hold" and len(parts) >= 3:
        button = "_".join(parts[1:-1])
        frames = int(parts[-1])
        await _hold_button(button, frames)
        return

    if parts[0] == "wait" and len(parts) == 2:
        frames = int(parts[1])
        await _wait_emulator_frames(frames)
        return

    raise ValueError(f"Unknown action format: {action_str}")


# ---------------------------------------------------------------------------
# Server lifecycle
# ---------------------------------------------------------------------------

def configure(config: GameConfig):
    """Set server configuration (call before app startup)."""
    global _config
    _config = config


@app.on_event("startup")
async def _startup():
    global _emulator, _reader, _start_time, _config, _loop
    _loop = asyncio.get_running_loop()
    _start_time = time.time()

    data_dir = _data_dir().expanduser().resolve()
    (data_dir / "saves").mkdir(parents=True, exist_ok=True)
    (data_dir / "runs").mkdir(parents=True, exist_ok=True)
    (data_dir / "roms").mkdir(parents=True, exist_ok=True)
    _mount_dashboard()

    if _config is None:
        # Config can be injected via environment or set beforehand
        print("[server] WARNING: No GameConfig set — emulator will NOT start.")
        print("[server] Call server.configure(GameConfig(...)) before startup.")
        return

    if not _config.rom_path:
        print("[server] Onboarding mode — no ROM configured, emulator will NOT start.")
        print(f"[server] Upload page: http://localhost:{_config.port}/dashboard/onboarding.html")
        return

    rom = Path(_config.rom_path).expanduser().resolve()
    if not rom.exists():
        print(f"[server] ERROR: ROM not found: {rom}")
        return

    # Auto-detect game type
    game_type = _config.game_type
    if game_type == "auto":
        game_type = _detect_game_type(str(rom))

    print(f"[server] Loading ROM: {rom}")
    print(f"[server] Detected game type: {game_type}")

    _load_emulator_from_config()

    # Auto-load a save state if specified
    if _config.load_state:
        saves_dir = data_dir / "saves"
        state_path = saves_dir / f"{_config.load_state}.state"
        if state_path.exists():
            try:
                _emulator.load_state(str(state_path))
                print(f"[server] Loaded save state: {_config.load_state}")
            except Exception as e:
                print(f"[server] WARNING: Failed to load state '{_config.load_state}': {e}")
        else:
            print(f"[server] WARNING: Save state not found: {state_path}")

    print(f"[server] Ready — listening on port {_config.port}")
    print(f"[server] Endpoints:")
    print(f"[server]   GET  /          — server info")
    print(f"[server]   GET  /state     — game state")
    print(f"[server]   GET  /screenshot — current frame (PNG)")
    print(f"[server]   POST /action    — execute actions")
    print(f"[server]   POST /save      — save state")
    print(f"[server]   POST /load      — load state")
    print(f"[server]   GET  /saves     — list saves")
    print(f"[server]   GET  /minimap   — ASCII minimap")
    print(f"[server]   GET  /health    — health check")
    print(f"[server]   WS   /ws        — live events")
    print(f"[server]   POST /rtc/offer — WebRTC live video/audio")


@app.on_event("shutdown")
async def _shutdown():
    peers = list(_rtc_peers)
    await asyncio.gather(*(_close_rtc_peer(peer) for peer in peers), return_exceptions=True)


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@app.get("/")
async def index():
    """Server info."""
    return {
        "name": "pokemon-agent",
        "version": __version__,
        "game": _config.game_type if _config else None,
        "rom": _config.rom_path if _config else None,
        "uptime_seconds": round(time.time() - _start_time, 1) if _start_time else 0,
        "emulator_ready": _emulator is not None,
        "rtc_peers": len(_rtc_peers),
    }


@app.get("/health")
async def health():
    """Health check."""
    return {
        "status": "ok",
        "emulator_ready": _emulator is not None,
        "rtc_peers": len(_rtc_peers),
        "game": _config.game_type if _config else None,
        "rom": _config.rom_path if _config else None,
        "data_dir": str(_data_dir()),
    }


@app.get("/roms")
async def list_roms():
    """List locally uploaded ROMs and launch instructions."""
    roms_dir = _roms_dir()
    roms_dir.mkdir(parents=True, exist_ok=True)
    rows = []
    for path in sorted(roms_dir.iterdir(), key=lambda p: p.name.lower()):
        if path.is_file() and path.suffix.lower() in {".gb", ".gbc", ".gba"}:
            rows.append(_rom_info(path))
    active_info = None
    if _config and _config.rom_path:
        active_path = Path(_config.rom_path).expanduser()
        if active_path.exists():
            active_info = _rom_info(active_path)
    return {
        "roms": rows,
        "upload_path": str(roms_dir),
        "active_rom": _config.rom_path if _config else None,
        "active": active_info,
        "switching": {
            "mode": "restart_required",
            "reason": "Each ROM uses isolated saves, runs, and autoplayer memory. Start the selected ROM with its profile data directory.",
        },
    }


@app.post("/roms/upload")
async def upload_rom(request: Request):
    """Upload a user-owned ROM as raw request bytes.

    The browser onboarding page sends the selected file as the request body and
    provides the filename in X-ROM-Filename. This avoids a multipart dependency
    and keeps the endpoint usable in onboarding-only installs.
    """
    name = request.headers.get("x-rom-filename") or ""
    safe_name = _safe_rom_name(name)
    body = await request.body()
    if not body:
        raise HTTPException(status_code=400, detail="Uploaded ROM is empty")
    max_bytes = int(os.environ.get("POKEMON_AGENT_MAX_ROM_BYTES", str(64 * 1024 * 1024)))
    if len(body) > max_bytes:
        raise HTTPException(status_code=413, detail=f"ROM exceeds {max_bytes} byte upload limit")
    roms_dir = _roms_dir()
    roms_dir.mkdir(parents=True, exist_ok=True)
    path = roms_dir / safe_name
    path.write_bytes(body)
    return {"ok": True, "rom": _rom_info(path)}


@app.post("/roms/select")
async def select_rom(req: RomSelectRequest):
    """Return safe launch instructions for a ROM profile.

    ROM switching is intentionally restart-based so emulator save states, run
    manifests, and autoplayer memories do not cross-contaminate games.
    """
    path = Path(req.path).expanduser().resolve()
    roms_dir = _roms_dir().expanduser().resolve()
    active_path = Path(_config.rom_path).expanduser().resolve() if _config and _config.rom_path else None
    if active_path and path == active_path:
        return {"ok": True, "active": True, "rom": _rom_info(path), "restart_required": False}
    if not path.exists() or not path.is_file():
        raise HTTPException(status_code=404, detail="ROM not found")
    try:
        path.relative_to(roms_dir)
    except ValueError:
        raise HTTPException(status_code=400, detail="Only uploaded ROMs can be selected from the dashboard")
    info = _rom_info(path)
    return {
        "ok": True,
        "active": False,
        "rom": info,
        "restart_required": True,
        "message": "Restart pokemon-agent with this command to switch games safely.",
    }


@app.get("/tunnel/status")
async def tunnel_status(path: str = "/dashboard/watch.html"):
    """Current localhost.run tunnel URL, if one is active or recently logged."""
    return _tunnel_payload(path)


@app.post("/tunnel/start")
async def tunnel_start(req: TunnelStartRequest):
    """Start or reuse the public localhost.run tunnel and return share links."""
    await asyncio.to_thread(_start_tunnel_process)
    deadline = time.time() + 12
    payload = _tunnel_payload(req.path)
    while time.time() < deadline and not payload.get("base_url"):
        await asyncio.sleep(0.5)
        payload = _tunnel_payload(req.path)
    return payload


@app.get("/state")
async def get_state():
    """Full game state JSON."""
    _ensure_emulator()
    try:
        state = await _run_sync(_get_state_dict)
        return JSONResponse(content=state)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error reading state: {e}")


@app.get("/screenshot")
async def screenshot():
    """Current emulator frame as PNG image."""
    _ensure_emulator()
    try:
        png_bytes = await _run_sync(_get_screenshot_bytes)
        return Response(content=png_bytes, media_type="image/png")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Screenshot error: {e}")


@app.get("/screenshot/base64")
async def screenshot_base64():
    """Current emulator frame as base64-encoded PNG in JSON."""
    _ensure_emulator()
    try:
        png_bytes = await _run_sync(_get_screenshot_bytes)
        b64 = base64.b64encode(png_bytes).decode("ascii")
        return {"image": b64, "format": "png"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Screenshot error: {e}")


@app.post("/rtc/offer")
async def rtc_offer(req: RTCSessionDescriptionRequest):
    """Create a WebRTC peer connection for live emulator video/audio."""
    global _rtc_media_pump
    _ensure_emulator()
    RTCPeerConnection, RTCSessionDescription, PokemonVideoTrack, PokemonAudioTrack, PokemonMediaPump = _load_rtc_classes()
    pc = RTCPeerConnection()
    _rtc_peers.add(pc)
    audio_track = PokemonAudioTrack()
    if _rtc_media_pump is None:
        _rtc_media_pump = PokemonMediaPump()
        _rtc_media_pump.start()
    media_pump = _rtc_media_pump
    media_pump.add_audio_track(audio_track)
    setattr(pc, "_pokemon_audio_track", audio_track)
    setattr(pc, "_pokemon_media_pump", media_pump)
    pc.addTrack(PokemonVideoTrack(media_pump))
    pc.addTrack(audio_track)

    @pc.on("connectionstatechange")
    async def on_connectionstatechange():
        if pc.connectionState in {"failed", "closed", "disconnected"}:
            await _close_rtc_peer(pc)

    try:
        offer = RTCSessionDescription(sdp=req.sdp, type=req.type)
        await pc.setRemoteDescription(offer)
        answer = await pc.createAnswer()
        await pc.setLocalDescription(answer)
        return {
            "sdp": pc.localDescription.sdp,
            "type": pc.localDescription.type,
            "audio": bool(getattr(audio_track, "direct_audio", False) or getattr(audio_track, "_stream", None) is not None),
            "video": True,
        }
    except Exception as exc:
        await _close_rtc_peer(pc)
        raise HTTPException(status_code=500, detail=f"WebRTC offer failed: {exc}") from exc


@app.get("/rtc/debug/audio")
async def rtc_debug_audio():
    """Debug current direct emulator audio samples."""
    _ensure_emulator()
    try:
        import numpy as np
    except Exception as exc:
        raise HTTPException(status_code=501, detail="NumPy unavailable") from exc

    def sample():
        if not hasattr(_emulator, "get_audio_samples"):
            return {"available": False}
        data = _emulator.get_audio_samples()
        arr = np.asarray(data)
        return {
            "available": True,
            "shape": list(arr.shape),
            "dtype": str(arr.dtype),
            "max_abs": int(np.abs(arr).max()) if arr.size else 0,
            "sample_rate": _emulator.get_audio_sample_rate() if hasattr(_emulator, "get_audio_sample_rate") else None,
            "frame_count": getattr(_emulator, "frame_count", None),
        }

    return await _run_sync(sample)


@app.get("/rtc/debug/perf")
async def rtc_debug_perf():
    pump = _rtc_media_pump
    if pump is None or not hasattr(pump, "stats"):
        return {"running": False, "peers": len(_rtc_peers)}
    return {"peers": len(_rtc_peers), "pump": pump.stats()}


@app.get("/watch/status")
async def watch_status():
    return _watch_viewer_payload() | {"recent_chat": list(_watch_chat_messages)[-30:]}


@app.post("/action")
async def execute_actions(req: ActionRequest):
    """Execute a sequence of game actions."""
    _ensure_emulator()
    try:
        executed = 0
        for action_str in req.actions:
            await _execute_action(action_str)
            executed += 1

        state_after = await _run_sync(_get_state_dict)

        screenshot_b64 = None
        if not _rtc_pump_running():
            # Screenshot events are only needed for the non-RTC fallback path.
            try:
                png_bytes = await _run_sync(_get_screenshot_bytes)
                screenshot_b64 = base64.b64encode(png_bytes).decode("ascii")
            except Exception:
                screenshot_b64 = None

        # Broadcast to WebSocket clients
        await broadcast({
            "type": "action",
            "actions": req.actions,
            "actions_executed": executed,
            "state_after": state_after,
            "screenshot_after": {"image": screenshot_b64, "format": "png"} if screenshot_b64 else None,
        })
        # Also push the latest frame so the dashboard updates immediately
        if screenshot_b64:
            await broadcast({
                "type": "screenshot",
                "data": {"image": screenshot_b64, "format": "png"},
            })

        return {
            "success": True,
            "actions_executed": executed,
            "state_after": state_after,
            "screenshot_after": {"image": screenshot_b64, "format": "png"} if screenshot_b64 else None,
        }
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Action error: {e}")


@app.post("/save")
async def save_state(req: SaveRequest):
    """Save emulator state to disk."""
    _ensure_emulator()
    if not _config:
        raise HTTPException(status_code=503, detail="Server not configured")
    try:
        saves_dir = Path(_config.data_dir).expanduser().resolve() / "saves"
        saves_dir.mkdir(parents=True, exist_ok=True)
        save_name = _safe_save_name(req.name)
        save_path = saves_dir / f"{save_name}.state"
        if save_path.exists() and not req.overwrite:
            raise HTTPException(status_code=409, detail=f"Save already exists: {save_name}")
        await _run_sync(_emulator.save_state, str(save_path))
        state_after = await _run_sync(_get_state_dict)
        await broadcast({"type": "key_moment", "description": f"Saved state: {save_name}", "state": state_after})
        return {"success": True, "name": save_name, "path": str(save_path), "state_after": state_after}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Save error: {e}")


@app.post("/load")
async def load_state(req: SaveRequest):
    """Load emulator state from disk."""
    _ensure_emulator()
    if not _config:
        raise HTTPException(status_code=503, detail="Server not configured")
    try:
        saves_dir = Path(_config.data_dir).expanduser().resolve() / "saves"
        save_name = _safe_save_name(req.name)
        save_path = saves_dir / f"{save_name}.state"
        if not save_path.exists():
            raise HTTPException(status_code=404, detail=f"Save not found: {save_name}")
        await _run_sync(_emulator.load_state, str(save_path))
        state_after = await _run_sync(_get_state_dict)

        await broadcast({"type": "state_update", "reason": "load", "state": state_after})

        return {"success": True, "name": save_name, "state_after": state_after}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Load error: {e}")


@app.get("/saves")
async def list_saves():
    """List available save-state files."""
    if not _config:
        raise HTTPException(status_code=503, detail="Server not configured")
    try:
        saves_dir = Path(_config.data_dir).expanduser().resolve() / "saves"
        if not saves_dir.exists():
            return {"saves": []}
        files = sorted(saves_dir.glob("*.state"))
        saves = [
            {
                "name": f.stem,
                "file": f.name,
                "size_bytes": f.stat().st_size,
                "modified": f.stat().st_mtime,
            }
            for f in files
        ]
        return {"saves": saves}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error listing saves: {e}")


@app.get("/runs")
async def list_runs():
    """List named run save slots."""
    current = _read_json_file(_current_run_path(), {})
    return {"runs": _list_run_metadata(), "current_run": current}


@app.post("/runs/save")
async def save_run(req: RunRequest):
    """Save the current emulator state as a named run."""
    try:
        meta = await _save_run(req.name)
        await broadcast({"type": "key_moment", "description": f"Saved run: {meta['name']}"})
        return {"success": True, "run": meta, "current_run": _read_json_file(_current_run_path(), {})}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Run save error: {e}")


@app.post("/runs/load")
async def load_run(req: RunRequest):
    """Load a named run save slot."""
    _ensure_emulator()
    run_name = _safe_save_name(req.name)
    meta = _read_json_file(_run_path(run_name), {})
    if not meta:
        raise HTTPException(status_code=404, detail=f"Run not found: {run_name}")
    save_name = meta.get("save_name") or f"run_{run_name}"
    save_path = _save_path(save_name)
    if not save_path.exists():
        raise HTTPException(status_code=404, detail=f"Run save state not found: {save_name}")
    try:
        await _run_sync(_emulator.load_state, str(save_path))
        state_after = await _run_sync(_get_state_dict)
        meta["modified"] = time.time()
        meta["snapshot"] = state_after
        _write_json_atomic(_run_path(run_name), meta)
        _write_json_atomic(_current_run_path(), {"name": run_name, "save_name": save_name, "updated": time.time()})
        await broadcast({"type": "state_update", "reason": "run_load", "state": state_after})
        return {"success": True, "run": meta, "state_after": state_after, "current_run": _read_json_file(_current_run_path(), {})}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Run load error: {e}")


@app.post("/runs/new")
async def new_run(req: RunRequest):
    """Start a fresh ROM session and store it as a named run."""
    global _emulator, _reader
    try:
        if req.save_current:
            current = _read_json_file(_current_run_path(), {})
            current_name = current.get("name")
            if current_name and _emulator is not None:
                await _save_run(str(current_name))
        _load_emulator_from_config()
        # Advance enough frames for PyBoy to present a stable boot frame.
        await _run_sync(_emulator.tick, 10)
        meta = await _save_run(req.name)
        state_after = await _run_sync(_get_state_dict)
        screenshot_b64 = None
        try:
            screenshot_b64 = base64.b64encode(await _run_sync(_get_screenshot_bytes)).decode("ascii")
        except Exception:
            pass
        await broadcast({"type": "state_update", "reason": "new_run", "state": state_after})
        if screenshot_b64:
            await broadcast({"type": "screenshot", "data": {"image": screenshot_b64, "format": "png"}})
        return {"success": True, "run": meta, "state_after": state_after, "screenshot_after": {"image": screenshot_b64, "format": "png"} if screenshot_b64 else None, "current_run": _read_json_file(_current_run_path(), {})}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"New run error: {e}")


@app.get("/minimap")
async def minimap():
    """Simple ASCII minimap — current map name + player position."""
    _ensure_emulator()
    try:
        state = await _run_sync(_get_state_dict)
        map_info = state.get("map", {})
        player = state.get("player", {})
        map_name = map_info.get("map_name", "Unknown")
        pos = player.get("position", {})
        x = pos.get("x", "?")
        y = pos.get("y", "?")

        lines = [
            f"=== {map_name} ===",
            f"Player position: ({x}, {y})",
            "",
            "  N",
            "W + E",
            "  S",
        ]
        text = "\n".join(lines)
        return Response(content=text, media_type="text/plain")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Minimap error: {e}")


def _build_autoplayer_status_payload() -> dict:
    control = _default_autoplayer_control()
    control.update(_read_json_file(_autoplayer_control_path(), {}))
    status = _read_json_file(_autoplayer_status_path(), {})
    if isinstance(status, dict):
        status.setdefault("engine", control.get("engine", "v1"))
    active_engine = control.get("engine", "v1")
    if active_engine not in {"v1", "v2", "unified", "adaptive"} and isinstance(status, dict):
        active_engine = status.get("engine") or status.get("selected_engine") or "v1"
    active_log_path = _autoplayer_v2_log_path() if active_engine in {"v2", "adaptive"} else (_autoplayer_unified_log_path() if active_engine == "unified" else _autoplayer_log_path())
    recent_v1 = _tail_jsonl(_autoplayer_log_path(), limit=30)
    recent_v2 = _tail_jsonl(_autoplayer_v2_log_path(), limit=30)
    recent_unified = _tail_jsonl(_autoplayer_unified_log_path(), limit=30)
    recent = recent_v2 if active_engine in {"v2", "adaptive"} else (recent_unified if active_engine == "unified" else recent_v1)
    if isinstance(status, dict) and control.get("engine") in {"v2", "unified", "adaptive"} and status.get("engine") != "v2":
        status.setdefault("visibility_warning", "V2/unified/adaptive selected but latest status is not from V2 runner")
    supervisor = _read_json_file(_autoplayer_supervisor_status_path(), {})
    supervisor_health = _supervisor_health(supervisor if isinstance(supervisor, dict) else {})
    v2_readiness = _v2_readiness(control, status if isinstance(status, dict) else {}, recent_v2, supervisor if isinstance(supervisor, dict) else {}, supervisor_health)
    warnings = []
    if supervisor_health["stale"]:
        warnings.append("Supervisor status is stale")
    if isinstance(supervisor, dict) and supervisor:
        if supervisor.get("active_engine") != control.get("engine") and supervisor.get("child_running") is True:
            warnings.append("Selected engine differs from supervisor active engine")
        if supervisor.get("child_running") is False:
            warnings.append("Supervisor child is not running")
        if supervisor.get("last_start_error"):
            warnings.append("Supervisor child failed to start")
        if supervisor.get("last_handoff"):
            handoff = supervisor.get("last_handoff") if isinstance(supervisor.get("last_handoff"), dict) else {}
            warnings.append(f"Auto handoff {handoff.get('from')} -> {handoff.get('to')}: {handoff.get('reason')}")
    shared_learning = _shared_learning_summary()
    mode_switch = {
        "selected_engine": control.get("engine"),
        "active_engine": supervisor.get("active_engine") if isinstance(supervisor, dict) else None,
        "desired_engine": supervisor.get("desired_engine") if isinstance(supervisor, dict) else None,
        "last_handoff": supervisor.get("last_handoff") if isinstance(supervisor, dict) else None,
        "warnings": warnings,
    }
    memory = status.get("memory")
    if not memory:
        world = _read_json_file(_autoplayer_world_path(), {})
        memory = {
            "places_known": len(world.get("places", {})) if isinstance(world, dict) else 0,
            "important_npcs": list((world.get("important_npcs", {}) if isinstance(world, dict) else {}).values())[:6],
        }
    return {
        "control": control,
        "status": status,
        "recent": recent,
        "recent_v1": recent_v1,
        "recent_v2": recent_v2,
        "recent_unified": recent_unified,
        "logs": {
            "active": str(active_log_path),
            "v1": str(_autoplayer_log_path()),
            "v2": str(_autoplayer_v2_log_path()),
            "unified": str(_autoplayer_unified_log_path()),
            "v2_learning": str(_autoplayer_v2_learning_path()),
            "unified_learning": str(_autoplayer_unified_learning_path()),
        },
        "supervisor": supervisor,
        "supervisor_health": supervisor_health,
        "mode_switch": mode_switch,
        "shared_learning": shared_learning,
        "v2_readiness": v2_readiness,
        "warnings": warnings,
        "memory": memory,
    }


@app.get("/autoplayer/status")
async def autoplayer_status():
    """Dashboard-facing status for the goal-directed autoplayer."""
    return await asyncio.to_thread(_build_autoplayer_status_payload)


@app.post("/autoplayer/control")
async def autoplayer_control(req: AutoplayerControlRequest):
    """Update file-backed autoplayer controls read by gold_autoplayer.py."""
    path = _autoplayer_control_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    control = _default_autoplayer_control()
    control.update(_read_json_file(path, {}))
    updates = req.dict(exclude_none=True)
    if "engine" in updates and updates["engine"] not in {"v1", "v2", "unified", "adaptive"}:
        raise HTTPException(status_code=400, detail="engine must be v1, v2, unified, or adaptive")
    for key in ("auto_handoff_v1_fallback", "auto_handoff_v2_fallback"):
        if key in updates and updates[key] not in {"v1", "v2", "unified", "adaptive"}:
            raise HTTPException(status_code=400, detail=f"{key} must be v1, v2, unified, or adaptive")
    if "movement_bias" in updates and updates["movement_bias"] not in {"west_north", "north_east", "balanced"}:
        raise HTTPException(status_code=400, detail="movement_bias must be west_north, north_east, or balanced")
    if "dialogue_speed" in updates and updates["dialogue_speed"] not in {"fast", "normal"}:
        raise HTTPException(status_code=400, detail="dialogue_speed must be fast or normal")
    control.update(updates)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(control, indent=2, sort_keys=True))
    tmp.replace(path)
    return {"ok": True, "control": control}


# ---------------------------------------------------------------------------
# WebSocket
# ---------------------------------------------------------------------------

@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    """Live event stream via WebSocket."""
    await ws.accept()
    _ws_clients.add(ws)
    try:
        # Send a welcome message
        await ws.send_json({
            "type": "connected",
            "version": __version__,
            "emulator_ready": _emulator is not None,
        })
        # Keep alive — wait for client messages (or disconnect)
        while True:
            data = await ws.receive_text()
            # Clients can send a "ping" to keep alive
            if data.strip().lower() == "ping":
                await ws.send_json({"type": "pong"})
    except WebSocketDisconnect:
        pass
    except Exception:
        pass
    finally:
        _ws_clients.discard(ws)


@app.websocket("/watch/ws")
async def watch_websocket_endpoint(ws: WebSocket):
    """Read-only viewer presence and shared chat for the public watch page."""
    await ws.accept()
    role = str(ws.query_params.get("role") or "viewer")
    _watch_clients.add(ws)
    if role != "stats":
        _watch_viewer_clients.add(ws)
    try:
        await ws.send_json(_watch_viewer_payload() | {"recent_chat": list(_watch_chat_messages)[-30:]})
        await _broadcast_watch_stats()
        while True:
            raw = await ws.receive_text()
            try:
                data = json.loads(raw)
            except Exception:
                data = {"type": "chat", "message": raw}
            if data.get("type") == "ping":
                await ws.send_json({"type": "pong", "updated_at": time.time()})
                continue
            if data.get("type") != "chat":
                continue
            message = str(data.get("message") or "").strip()
            if not message:
                continue
            name = str(data.get("name") or "viewer").strip() or "viewer"
            chat = {
                "type": "chat",
                "name": name[:24],
                "message": message[:280],
                "ts": time.time(),
            }
            _watch_chat_messages.append(chat)
            await _broadcast_watch(chat)
    except WebSocketDisconnect:
        pass
    except Exception:
        pass
    finally:
        _watch_clients.discard(ws)
        _watch_viewer_clients.discard(ws)
        await _broadcast_watch_stats()


# ---------------------------------------------------------------------------
# Dashboard fallback — only registered if dashboard static files are missing
# ---------------------------------------------------------------------------

def _register_dashboard_fallback():
    """Register a fallback route for /dashboard if static files aren't available."""
    try:
        import pokemon_agent.dashboard as _dm
        static_dir = Path(_dm.__file__).parent / "static"
        if static_dir.is_dir() and (static_dir / "index.html").exists():
            return  # Dashboard exists — don't register fallback
    except ImportError:
        pass

    @app.get("/dashboard")
    @app.get("/dashboard/{path:path}")
    async def dashboard_fallback(path: str = ""):
        raise HTTPException(
            status_code=404,
            detail="Dashboard not installed. Install with: pip install pokemon-agent[dashboard]",
        )

_register_dashboard_fallback()
