"""Typed configuration backed by a human-editable TOML file.

The file at ``~/.config/local-whisper/config.toml`` is the source of truth: the
settings window writes it, the daemon watches it, and hand-editing it is a
supported workflow. Unknown keys are preserved on load but dropped on save, so
the file stays readable.
"""

from __future__ import annotations

import dataclasses
import tomllib
from dataclasses import dataclass, field, fields, is_dataclass
from pathlib import Path
from typing import Any

from . import paths


@dataclass
class AudioConfig:
    #: PortAudio device name substring, or "" for the system default.
    device: str = ""
    sample_rate: int = 16000
    #: Stop recording automatically after this many seconds of silence
    #: (0 disables; only used in toggle/hands-free mode).
    silence_timeout: float = 2.5
    #: RMS below this counts as silence (0.0-1.0).
    silence_threshold: float = 0.012
    #: Hard cap so a stuck hotkey cannot fill the disk.
    max_duration: float = 300.0
    #: Ignore clips shorter than this — usually an accidental tap.
    min_duration: float = 0.35
    #: Play a short cue when recording starts/stops.
    sound_cues: bool = True


@dataclass
class ModelConfig:
    #: tiny, base, small, medium, large-v3, distil-large-v3, turbo …
    name: str = "small"
    #: auto | cpu | cuda
    device: str = "auto"
    #: auto | int8 | int8_float16 | float16 | float32
    compute_type: str = "auto"
    #: "" means let Whisper detect the language per utterance.
    language: str = "en"
    beam_size: int = 5
    #: Bias the decoder towards your jargon (max ~200 chars is effective).
    initial_prompt: str = ""
    #: Keep the model resident between dictations (fast, costs RAM/VRAM).
    keep_loaded: bool = True
    #: Load and warm up the model at startup instead of on first use.
    preload: bool = True
    cpu_threads: int = 0  # 0 = let CTranslate2 decide


@dataclass
class HotkeyConfig:
    #: toggle  — tap to start, tap again to stop (works without extra permissions)
    #: hold    — push-to-talk, needs the evdev backend
    mode: str = "toggle"
    #: Key combination for the evdev backend, e.g. "super+alt" or "ctrl+alt+space".
    combo: str = "super+alt"
    #: Backend for grabbing the hotkey: auto | evdev | none
    #: "none" means the daemon only reacts to `local-whisper toggle` (KDE shortcut).
    backend: str = "auto"
    #: Shortcut registered with KDE for `local-whisper toggle`.
    kde_shortcut: str = "Meta+Alt+D"
    #: Second KDE shortcut that cancels a running dictation.
    kde_cancel_shortcut: str = "Meta+Alt+X"
    #: Double-tap the hold key within this window to latch hands-free mode.
    double_tap_latch: bool = True
    double_tap_window: float = 0.4


@dataclass
class InsertConfig:
    #: auto | type | paste | clipboard
    #: "paste" puts the text on the clipboard and sends Ctrl+V — the most
    #: reliable option for unicode and non-US layouts.
    method: str = "auto"
    #: Preferred injector: auto | xdotool | wtype | ydotool | uinput | clipboard
    backend: str = "auto"
    #: Left-click at the pointer before inserting, to focus the field under the
    #: mouse. Off by default: most apps keep the caret where you left it.
    click_to_focus: bool = False
    #: Put the clipboard back the way we found it after pasting.
    restore_clipboard: bool = True
    #: Delay between focusing and typing, in seconds. Some toolkits need it.
    pre_insert_delay: float = 0.06
    #: Typing speed for the character-by-character backends (ms per key).
    type_delay_ms: int = 4
    #: Always copy the result to the clipboard, even when typing worked.
    always_copy: bool = True


@dataclass
class TextConfig:
    #: Drop "um", "uh", … from the transcript.
    remove_fillers: bool = True
    #: Collapse Whisper's occasional double spaces, fix spacing before commas.
    tidy_whitespace: bool = True
    #: Interpret spoken commands like "new line" / "neue Zeile".
    voice_commands: bool = True
    #: Ensure the result starts with a capital letter.
    capitalize_first: bool = True
    #: Add a trailing space so consecutive dictations do not glue together.
    trailing_space: bool = True
    #: Whisper hallucinates these on silence; drop the transcript if it matches.
    drop_hallucinations: bool = True
    #: Literal replacements applied last: {"claude code": "Claude Code"}.
    replacements: dict[str, str] = field(default_factory=dict)


@dataclass
class UIConfig:
    #: bottom | top | cursor — where the overlay pill appears (X11 only; on
    #: Wayland the compositor places it, see docs/wayland.md).
    overlay_position: str = "bottom"
    overlay_margin: int = 90
    show_overlay: bool = True
    #: Show the transcript in the pill for a moment after inserting.
    show_result_preview: bool = True
    result_preview_ms: int = 1800
    #: Accent colour used by the pill and the tray icon.
    accent: str = "#7C5CFF"
    start_hidden: bool = True
    #: Desktop notification when something goes wrong.
    notify_errors: bool = True
    history_limit: int = 500


@dataclass
class Config:
    audio: AudioConfig = field(default_factory=AudioConfig)
    model: ModelConfig = field(default_factory=ModelConfig)
    hotkey: HotkeyConfig = field(default_factory=HotkeyConfig)
    insert: InsertConfig = field(default_factory=InsertConfig)
    text: TextConfig = field(default_factory=TextConfig)
    ui: UIConfig = field(default_factory=UIConfig)

    # ---------------------------------------------------------------- loading

    @classmethod
    def load(cls, path: Path | None = None) -> "Config":
        path = path or paths.config_file()
        if not path.exists():
            return cls()
        try:
            with path.open("rb") as handle:
                raw = tomllib.load(handle)
        except (OSError, tomllib.TOMLDecodeError):
            return cls()
        return cls.from_dict(raw)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "Config":
        kwargs: dict[str, Any] = {}
        for f in fields(cls):
            section = raw.get(f.name)
            factory = f.default_factory  # type: ignore[misc]
            if isinstance(section, dict):
                kwargs[f.name] = _build_section(factory(), section)
            else:
                kwargs[f.name] = factory()
        return cls(**kwargs)

    def to_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)

    # ---------------------------------------------------------------- saving

    def save(self, path: Path | None = None) -> Path:
        path = path or paths.config_file()
        path.parent.mkdir(parents=True, exist_ok=True)
        text = _dumps(self.to_dict())
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(text, encoding="utf-8")
        tmp.replace(path)  # atomic: readers never see a half-written config
        return path


def _build_section(section: Any, raw: dict[str, Any]) -> Any:
    """Copy known keys from ``raw`` onto a dataclass instance, coercing types."""
    for f in fields(section):
        if f.name not in raw:
            continue
        value = raw[f.name]
        target = f.type if not isinstance(f.type, str) else _TYPES.get(f.type, None)
        try:
            if target is float and isinstance(value, int):
                value = float(value)
            elif target is int and isinstance(value, bool):
                continue
            elif target in (str, int, float, bool) and not isinstance(value, target):
                continue
        except TypeError:
            pass
        setattr(section, f.name, value)
    return section


_TYPES: dict[str, Any] = {
    "str": str,
    "int": int,
    "float": float,
    "bool": bool,
    "dict[str, str]": dict,
}


# --------------------------------------------------------------------- TOML out
# A tiny writer beats adding a dependency: our schema is only scalars, string
# maps and one level of tables.

def _fmt(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return repr(value)
    if isinstance(value, str):
        escaped = value.replace("\\", "\\\\").replace('"', '\\"')
        escaped = escaped.replace("\n", "\\n").replace("\t", "\\t")
        return f'"{escaped}"'
    if isinstance(value, list):
        return "[" + ", ".join(_fmt(v) for v in value) + "]"
    raise TypeError(f"unsupported TOML value: {value!r}")


def _dumps(data: dict[str, Any]) -> str:
    lines = ["# local-whisper configuration — edit freely, the daemon reloads on save.", ""]
    for section, values in data.items():
        lines.append(f"[{section}]")
        nested: list[tuple[str, dict]] = []
        for key, value in values.items():
            if isinstance(value, dict):
                nested.append((key, value))
                continue
            lines.append(f"{key} = {_fmt(value)}")
        for key, table in nested:
            lines.append("")
            lines.append(f"[{section}.{key}]")
            for sub_key, sub_value in table.items():
                lines.append(f"{_fmt(sub_key)} = {_fmt(sub_value)}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def is_config_section(obj: Any) -> bool:
    return is_dataclass(obj) and not isinstance(obj, type)
