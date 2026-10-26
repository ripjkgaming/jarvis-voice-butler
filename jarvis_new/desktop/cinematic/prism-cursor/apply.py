#!/usr/bin/env python3
"""Apply only the pointer theme, with a scoped, reversible snapshot."""
import argparse
import configparser
from datetime import datetime
import json
from pathlib import Path
import shutil
import subprocess
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent
THEME = 'jarvis-prism-cursors'
DEST = Path.home() / '.local/share/icons' / THEME
CONFIG = Path.home() / '.config'
BACKUPS = Path.home() / '.local/share/jarvis-desktop-backups'
LATEST = BACKUPS / 'prism-cursor-latest'


def run(*args):
    return subprocess.check_output([str(arg) for arg in args], text=True).strip()


def current():
    config = configparser.RawConfigParser(strict=False)
    config.optionxform = str
    config.read(CONFIG / 'kcminputrc')
    return {key: config.get('Mouse', key, fallback=None) for key in ('cursorTheme', 'cursorSize')}


def restore(folder):
    state = json.loads((folder / 'state.json').read_text())
    old = state['settings']
    print(run('plasma-apply-cursortheme', '--size', old['cursorSize'] or '24', old['cursorTheme'] or 'breeze_cursors'))
    for key, value in old.items():
        if value is None:
            run('kwriteconfig6', '--file', 'kcminputrc', '--group', 'Mouse', '--key', key, '--delete')
    if DEST.exists():
        shutil.rmtree(DEST)
    if state['previousPackage']:
        shutil.copytree(folder / 'previous-package', DEST, symlinks=True)
    print('Restored pointer theme from ' + str(folder))


def install():
    settings = current()
    if settings['cursorTheme'] == THEME:
        raise SystemExit('Jarvis Prism is already active. Restore the saved theme before reinstalling.')
    source = ROOT.parent / 'icons' / THEME
    if not (source / 'cursors/left_ptr').is_file():
        raise SystemExit('Build cursor assets first with build.py.')
    stamp = datetime.now(ZoneInfo('Asia/Singapore')).strftime('%Y%m%d-%H%M%S-%f')
    folder = BACKUPS / ('prism-cursor-' + stamp)
    folder.mkdir(parents=True)
    state = {'settings': settings, 'previousPackage': DEST.exists()}
    if DEST.exists():
        shutil.copytree(DEST, folder / 'previous-package', symlinks=True)
    for name in ('kcminputrc', 'gtk-3.0/settings.ini', 'gtk-4.0/settings.ini'):
        src = CONFIG / name
        if src.exists():
            dst = folder / 'reference-config' / name
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
    (folder / 'state.json').write_text(json.dumps(state, indent=2) + '\n')
    try:
        if DEST.exists():
            shutil.rmtree(DEST)
        shutil.copytree(source, DEST, symlinks=True)
        print(run('plasma-apply-cursortheme', '--size', settings['cursorSize'] or '24', THEME))
        assert current()['cursorTheme'] == THEME, 'Theme was not applied'
    except Exception:
        restore(folder)
        raise
    LATEST.write_text(str(folder) + '\n')
    (ROOT / 'installed.json').write_text(json.dumps({'theme': THEME, 'size': current()['cursorSize'], 'backup': str(folder)}, indent=2) + '\n')
    print('Snapshot: ' + str(folder))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=['install', 'restore'])
    parser.add_argument('snapshot', nargs='?', type=Path)
    args = parser.parse_args()
    if args.action == 'install':
        install()
    else:
        restore(args.snapshot or Path(LATEST.read_text().strip()))
