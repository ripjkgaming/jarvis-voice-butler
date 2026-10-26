#!/usr/bin/python3
"""Bridge native notification default actions to the finite KWin click transition."""
import configparser
import datetime
import json
from pathlib import Path
import shutil
import subprocess
import sys
import time
import uuid

ROOT = Path(__file__).resolve().parent
DEST = Path.home() / '.local/share/kwin/effects/jarvis-surfaces'
AUTO = Path.home() / '.config/autostart/jarvis-notification-click.desktop'
SERVICE = 'org.jarvis.NotificationClick'


def dbus(*args):
    return subprocess.check_output(['qdbus', *args], text=True).strip()


def stop():
    if SERVICE in [name.strip() for name in dbus().splitlines()]:
        dbus(SERVICE, '/Click', SERVICE + '.Quit')


def start():
    if SERVICE not in [name.strip() for name in dbus().splitlines()]:
        log = Path.home() / '.local/state/jarvis-notification-click.log'
        log.parent.mkdir(parents=True, exist_ok=True)
        with log.open('a') as stream:
            subprocess.Popen(['/usr/bin/python', str(Path(__file__).resolve()), 'serve'],
                             stdout=stream, stderr=stream, start_new_session=True)
        for _ in range(30):
            if SERVICE in [name.strip() for name in dbus().splitlines()]:
                return
            time.sleep(.1)
        raise RuntimeError('Notification click bridge did not start')


def restore(folder):
    state = json.loads((folder / 'state.json').read_text())
    stop()
    dbus('org.kde.KWin', '/Effects', 'org.kde.kwin.Effects.unloadEffect', 'jarvis-surfaces')
    for path, name in ((DEST, 'effect'), (AUTO, 'autostart')):
        if path.is_dir():
            shutil.rmtree(path)
        else:
            path.unlink(missing_ok=True)
        if state[name]:
            path.parent.mkdir(parents=True, exist_ok=True)
            if name == 'effect':
                shutil.copytree(folder / name, path)
            else:
                shutil.copy2(folder / name, path)
    if 'request' in state:
        command = ['kwriteconfig6', '--file', 'kwinrc', '--group',
                   'Effect-jarvis-surfaces', '--key', 'ClickRequest']
        subprocess.run(command + (['--delete'] if state['request'] is None else [state['request']]), check=True)
    if state['loaded']:
        dbus('org.kde.KWin', '/Effects', 'org.kde.kwin.Effects.loadEffect', 'jarvis-surfaces')
    if state['running']:
        start()
    print('Restored notification animations:', folder)


def install():
    folder = Path.home() / '.local/share/jarvis-desktop-backups' / (
        'notification-click-' + datetime.datetime.now().strftime('%Y%m%d-%H%M%S-%f'))
    folder.mkdir(parents=True)
    state = {'effect': DEST.exists(), 'autostart': AUTO.exists(),
             'loaded': dbus('org.kde.KWin', '/Effects', 'org.kde.kwin.Effects.isEffectLoaded', 'jarvis-surfaces') == 'true',
             'running': SERVICE in [name.strip() for name in dbus().splitlines()]}
    config = configparser.RawConfigParser(strict=False)
    config.optionxform = str
    config.read(Path.home() / '.config/kwinrc')
    state['request'] = config.get('Effect-jarvis-surfaces', 'ClickRequest', fallback=None)
    if state['effect']:
        shutil.copytree(DEST, folder / 'effect')
    if state['autostart']:
        shutil.copy2(AUTO, folder / 'autostart')
    (folder / 'state.json').write_text(json.dumps(state, indent=2) + '\n')
    try:
        stop()
        dbus('org.kde.KWin', '/Effects', 'org.kde.kwin.Effects.unloadEffect', 'jarvis-surfaces')
        shutil.copytree(ROOT / 'kwin/effects/jarvis-surfaces', DEST, dirs_exist_ok=True)
        if dbus('org.kde.KWin', '/Effects', 'org.kde.kwin.Effects.loadEffect', 'jarvis-surfaces') != 'true':
            raise RuntimeError('Could not load notification effect')
        AUTO.parent.mkdir(parents=True, exist_ok=True)
        AUTO.write_text('[Desktop Entry]\nType=Application\nName=Jarvis notification click transition\n'
                        'Exec=/usr/bin/python "' + str(Path(__file__).resolve()) + '" start\n'
                        'X-KDE-autostart-after=panel\n')
        start()
    except Exception:
        restore(folder)
        raise
    print('Installed. Backup:', folder)


def serve():
    from PyQt6.QtCore import QCoreApplication, QObject, pyqtClassInfo, pyqtSlot
    from PyQt6.QtDBus import QDBusConnection, QDBusInterface

    app = QCoreApplication([])
    bus = QDBusConnection.sessionBus()

    @pyqtClassInfo('D-Bus Interface', SERVICE)
    class Bridge(QObject):
        @pyqtSlot('uint', str)
        def invoked(self, notification_id, action):
            # Reply/archive/dismiss actions must never look like a redirect.
            if action != 'default':
                return
            request = json.dumps({'token': uuid.uuid4().hex, 'time': round(time.time() * 1000)})
            try:
                subprocess.run(['kwriteconfig6', '--file', 'kwinrc', '--group',
                                'Effect-jarvis-surfaces', '--key', 'ClickRequest', request],
                               check=True, timeout=2)
                QDBusInterface('org.kde.KWin', '/Effects', 'org.kde.kwin.Effects', bus).asyncCall(
                    'reconfigureEffect', 'jarvis-surfaces')
            except (OSError, subprocess.SubprocessError) as error:
                print('Click animation skipped:', error, flush=True)

        @pyqtSlot()
        def Quit(self):
            app.quit()

    bridge = Bridge()
    if not bus.registerService(SERVICE):
        return
    bus.registerObject('/Click', bridge, QDBusConnection.RegisterOption.ExportAllSlots)
    if not bus.connect('org.freedesktop.Notifications', '/org/freedesktop/Notifications',
                       'org.freedesktop.Notifications', 'ActionInvoked', bridge.invoked):
        raise RuntimeError('Cannot subscribe to notification actions')
    app.exec()


if __name__ == '__main__':
    action = sys.argv[1] if len(sys.argv) > 1 else 'start'
    if action == 'restore':
        restore(Path(sys.argv[2]))
    elif action in ('install', 'start', 'stop', 'serve'):
        globals()[action]()
    else:
        raise SystemExit('Usage: notification-click.py install|start|stop|restore BACKUP')
