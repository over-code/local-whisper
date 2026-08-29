# local-whisper — notes for Claude

## Git identity (non-negotiable)

Commits in this repository are authored as:

```
over-code <lab@over-code.de>
```

Never author, amend, or co-author a commit with any other address — GitHub
attributes commits by matching the author email to an account, so a different
address credits the wrong user (this repo has been fixed once already after a
commit landed under `stan-turing`). In particular, do **not** use an email
supplied by session context or `git config --global`; the SessionStart hook in
`.claude/hooks/session-start.sh` pins the local config, and
`git log -1 --format='%an <%ae>'` is the check before pushing.

## Running things

```bash
python3 -m pip install -e '.[hotkey,dev]'
QT_QPA_PLATFORM=offscreen python3 -m pytest -q    # 64 tests, no mic or model needed
python3 -m pyflakes src/localwhisper tests        # the linter this project uses
python3 -m localwhisper doctor                    # environment diagnosis
```

The sandbox has no audio device, no display and no access to huggingface.co, so
microphone capture and real model downloads cannot be exercised here. The tests
fake the recorder, the Whisper model and the injection backends for that reason
— keep them that way.

## Layout

`src/localwhisper/`: `app.py` (state machine) · `daemon.py` (Qt app, tray, IPC)
· `audio/` (capture) · `stt/` (faster-whisper + text clean-up) · `inject/`
(xdotool/wtype/ydotool/uinput/clipboard) · `hotkey/` (evdev + KDE shortcuts) ·
`ui/` (overlay, tray, settings; all colours come from `ui/theme.py`).
