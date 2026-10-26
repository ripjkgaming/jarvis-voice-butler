# Brave: Locke theme and extension

- `locke-ext/` - the Locke extension (new tab page, effects, popup).
- `locke-theme/` - dark deep-blue theme with a faint ASCII glyph texture.
- `install.sh` / `revert.sh` - add or remove the launcher override.

## Install

    ./install.sh

Brave cannot install unpacked extensions silently. The script copies
`/usr/share/applications/brave-browser.desktop` to `~/.local/share/applications/`
and adds `--load-extension=<locke-ext>,<locke-theme>` to every Exec line.
It only works when Brave is started from that launcher (menu, taskbar pin).
Starting `brave-browser` in a terminal will not load them.

Quit Brave completely and start it again so the new launcher is used.

Manual option: open `brave://extensions`, turn on Developer mode, click
Load unpacked, and pick `locke-ext` and `locke-theme`.

## Revert

    ./revert.sh

This removes the override only if the installer wrote it. Restart Brave.
