"""The dictation state machine, as one small enum plus its labels."""

from __future__ import annotations

from enum import Enum


class State(str, Enum):
    IDLE = "idle"
    LOADING = "loading"          # model is being loaded or downloaded
    RECORDING = "recording"
    TRANSCRIBING = "transcribing"
    INSERTING = "inserting"
    DONE = "done"                # transient: shows the result, then back to IDLE
    ERROR = "error"
    PAUSED = "paused"            # user switched dictation off from the tray

    @property
    def label(self) -> str:
        return {
            State.IDLE: "Ready",
            State.LOADING: "Loading model",
            State.RECORDING: "Listening",
            State.TRANSCRIBING: "Transcribing",
            State.INSERTING: "Inserting",
            State.DONE: "Done",
            State.ERROR: "Error",
            State.PAUSED: "Paused",
        }[self]

    @property
    def is_busy(self) -> bool:
        return self in (State.LOADING, State.TRANSCRIBING, State.INSERTING)
