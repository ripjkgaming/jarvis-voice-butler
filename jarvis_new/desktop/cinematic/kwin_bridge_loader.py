"""Register an owned bridge without colliding with existing KWin script objects."""
from pathlib import Path
import subprocess,time
def call(*args):return subprocess.check_output(['qdbus','org.kde.KWin',*args],text=True).strip()
def unload(name):
 call('/Scripting','org.kde.kwin.Scripting.unloadScript',name)
 for i in range(8):call('/Scripting','org.kde.kwin.Scripting.unloadScript',name+'-reserved-'+str(i))
def load(path,name):
 for attempt in range(8):
  existing=set(call().splitlines())
  number=call('/Scripting','org.kde.kwin.Scripting.loadScript',str(path),name)
  if int(number)<0:raise RuntimeError('Bridge already registered: '+name)
  address='/Scripting/Script'+number
  if address not in existing:
   call(address,'org.kde.kwin.Script.run');return number
  # KWin can reuse an occupied object id after scripts have been removed.
  # Reserve the colliding index with a no-op, then obtain a distinct id.
  call('/Scripting','org.kde.kwin.Scripting.unloadScript',name);time.sleep(.05)
  call('/Scripting','org.kde.kwin.Scripting.loadScript',str(Path(__file__).with_name('kwin-bridge-noop.js')),name+'-reserved-'+str(attempt))
 raise RuntimeError('Could not allocate a distinct KWin bridge object')
