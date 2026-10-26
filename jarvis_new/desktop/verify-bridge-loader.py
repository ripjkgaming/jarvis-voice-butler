from pathlib import Path
from unittest.mock import patch
import kwin_bridge_loader as loader
calls=[];count=0
def fake(*args):
 global count
 calls.append(args)
 if not args:return '/Scripting\n/Scripting/Script2'
 if args[1]=='org.kde.kwin.Scripting.loadScript':
  count+=1;return '2' if count<=2 else '3'
 return 'true'
with patch.object(loader,'call',fake),patch.object(loader.time,'sleep'):
 assert loader.load(Path('owned.js'),'owned')=='3'
assert ('/Scripting/Script3','org.kde.kwin.Script.run') in calls
assert ('/Scripting/Script2','org.kde.kwin.Script.run') not in calls
assert [x for x in calls if len(x)>1 and x[1]=='org.kde.kwin.Scripting.unloadScript']==[('/Scripting','org.kde.kwin.Scripting.unloadScript','owned')]
print('PASS: occupied KWin object id is never run; only owned registration is replaced.')
