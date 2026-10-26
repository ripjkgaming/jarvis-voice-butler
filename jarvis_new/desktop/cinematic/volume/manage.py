#!/usr/bin/env python3
"""Install a user-local Plasma 6.7 OSD module; snapshot and restore owned files."""
import configparser
import datetime
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parent
DATA = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share"))
CONFIG = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
DEST = DATA / "jarvis-volume/qml"
DROP = CONFIG / "systemd/user/plasma-plasmashell.service.d/50-jarvis-volume.conf"
MODULE = Path("org/kde/plasma/workspace/osd")


def native_module():
    for base in ("/usr/lib64/qt6/qml", "/usr/lib/qt6/qml", "/usr/lib/x86_64-linux-gnu/qt6/qml"):
        path = Path(base) / MODULE
        if (path / "Osd.qml").exists() and "OsdWindow" in (path / "Osd.qml").read_text():
            return path
    raise RuntimeError("This OSD requires Plasma's native OsdWindow module (Plasma 6.7).")


def stage(destination):
    native = native_module()
    target = destination / MODULE
    target.mkdir(parents=True, exist_ok=True)
    # Keep the installed KDE plugin and fallback matched to this Plasma version.
    shutil.copy2(native / "OsdItem.qml", target / "NativeOsdItem.qml")
    shutil.copy2(native / "OsdItem.qml", target / "OsdItem.qml")
    for name in ("Osd.qml", "VolumeCard.qml"):
        shutil.copy2(ROOT / name, target / name)
    for library in native.glob("*.so"):
        link = target / library.name
        link.unlink(missing_ok=True)
        link.symlink_to(library)
    config = configparser.ConfigParser(interpolation=None)
    config.read(CONFIG / "kwinrc")
    reduced = any(config.getboolean(section, "ReducedMotion", fallback=False)
                  for section in ("Effect-jarvis-energy", "Effect-jarvis-surfaces"))
    (target / "MotionConfig.qml").write_text(
        "pragma Singleton\nimport QtQuick\nQtObject { readonly property bool reducedMotion: "
        + str(reduced).lower() + " }\n")
    lines = [line for line in (native / "qmldir").read_text().splitlines()
             if not line.startswith(("prefer ", "typeinfo "))]
    lines.extend(("singleton MotionConfig 254.0 MotionConfig.qml",
                  "VolumeCard 254.0 VolumeCard.qml", "NativeOsdItem 254.0 NativeOsdItem.qml"))
    (target / "qmldir").write_text("\n".join(lines) + "\n")
    return target


def restart():
    subprocess.run(["systemctl", "--user", "daemon-reload"], check=True)
    subprocess.run(["systemctl", "--user", "restart", "plasma-plasmashell.service"], check=True, timeout=55)


def restore(snapshot):
    state = json.loads((snapshot / "state.json").read_text())
    for key, path in (("qml", DEST), ("dropin", DROP)):
        if path.is_dir():
            shutil.rmtree(path)
        else:
            path.unlink(missing_ok=True)
        if state[key]:
            path.parent.mkdir(parents=True, exist_ok=True)
            if key == "qml":
                shutil.copytree(snapshot / key, path, symlinks=True)
            else:
                shutil.copy2(snapshot / key, path)
    restart()
    print("Restored:", snapshot)


def install():
    native_module()
    subprocess.run(["systemctl", "--user", "is-active", "plasma-plasmashell.service"], check=True)
    snapshot = DATA / "jarvis-desktop-backups" / datetime.datetime.now().strftime("volume-%Y%m%d-%H%M%S-%f")
    snapshot.mkdir(parents=True)
    state = {"qml": DEST.exists(), "dropin": DROP.exists()}
    if state["qml"]:
        shutil.copytree(DEST, snapshot / "qml", symlinks=True)
    if state["dropin"]:
        shutil.copy2(DROP, snapshot / "dropin")
    (snapshot / "state.json").write_text(json.dumps(state, indent=2) + "\n")
    print("Backup:", snapshot, flush=True)
    try:
        stage(DEST)
        # Preserve any existing service-specific import paths ahead of Qt defaults.
        pid = subprocess.check_output(["systemctl", "--user", "show", "plasma-plasmashell.service", "-p", "MainPID", "--value"], text=True).strip()
        environment = Path(f"/proc/{pid}/environ").read_bytes().split(b"\0")
        old = next((s.split(b"=", 1)[1].decode() for s in environment if s.startswith(b"QML_IMPORT_PATH=")), "")
        paths = [str(DEST)] + [p for p in old.split(":") if p and p != str(DEST)]
        value = ":".join(paths).replace("\\", "\\\\").replace('"', '\\"').replace("%", "%%")
        DROP.parent.mkdir(parents=True, exist_ok=True)
        DROP.write_text('[Service]\nEnvironment="QML_IMPORT_PATH=' + value + '"\n')
        restart()
    except Exception:
        restore(snapshot)
        raise
    print("Installed volume animation. Rollback:")
    print(f"/usr/bin/python3 {ROOT / 'manage.py'} restore {snapshot}")


if __name__ == "__main__":
    if sys.argv[1:] == ["install"]:
        install()
    elif len(sys.argv) == 3 and sys.argv[1] == "stage":
        print(stage(Path(sys.argv[2])))
    elif len(sys.argv) == 3 and sys.argv[1] == "restore":
        restore(Path(sys.argv[2]))
    else:
        raise SystemExit("Usage: manage.py install | stage PATH | restore SNAPSHOT")
