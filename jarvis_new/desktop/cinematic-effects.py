#!/usr/bin/env python3
"""Apply a scoped, saved window/taskbar animation preset; never restart the desktop."""
import json,shutil,subprocess,sys,time
from pathlib import Path
ROOT=Path(__file__).resolve().parent
def run(*args):subprocess.run(args,check=True)
def apply(snapshot):
 state=json.loads((snapshot/'cinematic-state.json').read_text())
 for name in ['taskbar-puppet-effects.py','taskbar-effects.py']:
  run(sys.executable,str(ROOT/name),'stop')
 for bridge in ['jarvis-taskbar-slice','jarvis-taskbar-slice-v2','jarvis-taskbar-slice-debugfresh']:
  subprocess.run(['qdbus','org.kde.KWin','/Scripting','org.kde.kwin.Scripting.unloadScript',bridge],stdout=subprocess.DEVNULL,check=False)
 for _ in range(40):
  services=subprocess.check_output(['qdbus'],text=True)
  if 'org.jarvis.TaskbarSlice' not in services and 'org.jarvis.TaskbarPuppet' not in services:break
  time.sleep(.1)
 else:raise RuntimeError('Owned overlays did not finish stopping')
 for name in state['files']:
  if Path(name).name!=name:raise ValueError('Invalid snapshot filename')
  shutil.copy2(snapshot/name,ROOT/name)
 run(sys.executable,str(ROOT/'energy-effects.py'),'restore-window',str(snapshot))
 if state['taskbarActive']:run(sys.executable,str(ROOT/'taskbar-effects.py'),'start')
 if state['puppetActive']:run(sys.executable,str(ROOT/'taskbar-puppet-effects.py'),'start')
 print('Scoped animation preset applied: '+str(snapshot))
if __name__=='__main__':
 if len(sys.argv)!=3 or sys.argv[1]!='apply':raise SystemExit('Usage: cinematic-effects.py apply SNAPSHOT')
 apply(Path(sys.argv[2]))
