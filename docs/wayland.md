# Wayland notes (KDE Plasma 6)

Wayland deliberately removed the X11 APIs that let any client watch the
keyboard, move its own window, or type into someone else's. That is good for
security and inconvenient for a dictation tool, which needs to do all three.
Here is exactly how each one is solved, and what to do when it does not work.

## Hotkeys

Plasma owns the keyboard. An application asks for a shortcut through
kglobalaccel (KWin embeds `kglobalacceld` on Wayland), and Plasma launches the
bound command when the keys are pressed. So the shortcut runs
`local-whisper toggle`, a tiny client that writes one line to the daemon's
socket.

```bash
local-whisper install-shortcut
```

writes two `.desktop` files into `~/.local/share/applications` and the matching
entries into `~/.config/kglobalshortcutsrc`:

```ini
[services][local-whisper-toggle.desktop]
_k_friendly_name=Local Whisper: Toggle dictation
_launch=Meta+Alt+D,none,Local Whisper: Toggle dictation
```

then restarts `kglobalacceld` so it re-reads them. If that fails (different
Plasma version, unusual packaging), set it manually:

*System Settings → Keyboard → Shortcuts → Add New → Command or Script* →
`~/.local/bin/local-whisper toggle`.

**Why is there no hold-to-talk over this path?** Because the shortcut system
only reports that a shortcut *fired*. There is no release event, so a key you
hold looks exactly like a key you tapped. Push-to-talk therefore reads
`/dev/input/event*` through evdev, which sees both edges — and needs your user
to be in the `input` group:

```bash
sudo usermod -aG input $USER   # log out and back in
```

## Typing into other applications

Three routes work on KWin, in the order the app tries them:

1. **`wtype`** — uses `zwp_virtual_keyboard_manager_v1`, which KWin implements.
   It builds its own keymap, so unicode and non-US layouts are fine.
   `sudo apt install wtype`
2. **`ydotool`** — writes to `/dev/uinput`, below the display server entirely.
   Needs its daemon running: `systemctl --user enable --now ydotoold`.
3. **The built-in uinput backend** — the same idea as ydotool without the extra
   daemon, used only to send `Ctrl+V` (the kernel layer knows nothing about
   keyboard layouts, so typing arbitrary text that way is unreliable).
   Needs `/dev/uinput` to be writable:

   ```bash
   sudo cp packaging/udev/99-local-whisper-uinput.rules /etc/udev/rules.d/
   sudo udevadm control --reload-rules && sudo udevadm trigger
   sudo modprobe uinput && echo uinput | sudo tee /etc/modules-load.d/uinput.conf
   ```

If none of them is available the transcript is copied to the clipboard and the
overlay tells you to press `Ctrl+V`.

`xdotool` is not used on Wayland: it can only see XWayland windows, so it would
work in some applications and silently fail in others.

## The clipboard

A Wayland client normally needs keyboard focus to write the clipboard — which a
background dictation daemon never has. `wl-copy` gets around this with the
`wlr-data-control` protocol, which KWin supports, so
`sudo apt install wl-clipboard` is worth having even if you never paste
manually.

## Where the overlay appears

Wayland clients cannot choose their own position; the compositor places them.
The pill therefore *asks* for the bottom centre and Plasma may put it
elsewhere. To pin it, add the shipped window rule:

*System Settings → Window Management → Window Rules → Import* →
`packaging/kwin/overlay-placement.kwinrule`

It also marks the window as always-on-top, not focusable, and hidden from the
task switcher — which is what you want for a status pill. Adjust the
`position=` line for your resolution.

If you would rather not deal with any of this, set
`ui.overlay_position = "cursor"` or turn the overlay off entirely; the tray
icon still shows the state.

## Quick check

```bash
local-whisper doctor
```

prints, in order: the session type, whether the daemon is up, the microphone
devices, the model status, every insertion backend with the reason it is or is
not usable, and the exact `apt` lines to fix what is missing.
