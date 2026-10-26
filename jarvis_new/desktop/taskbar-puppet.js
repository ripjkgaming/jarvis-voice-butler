// New running-task appearances only; independent of the close bridge.
'use strict';
var pending={}, shown={}, current='', currentWindow=null,currentRect=null,last=0;
function app(w){return String(w.desktopFileName||w.resourceClass||'');}
function allowed(w){return w.normalWindow&&!w.skipTaskbar&&!w.specialWindow&&!w.fullScreen&&!/jarvis[-_ ]?watch|kscreenlocker|ksmserver|polkit|kdesu|pinentry|kwallet|spectacle|plasmashell/i.test(app(w));}
function cancel(){pending={};callDBus('org.jarvis.TaskbarPuppet','/Puppet','org.jarvis.TaskbarPuppet','Cancel');}
function sample(){
 var now=Date.now();if(now-last<60)return;last=now;
 var windows=workspace.windowList();
 if(currentWindow&&currentRect&&!currentWindow.deleted){
  var cr=currentWindow.iconGeometry;
  if(!cr||cr.width<=0||Math.abs(cr.x-currentRect.x)>2||Math.abs(cr.y-currentRect.y)>2){
   callDBus('org.jarvis.TaskbarPuppet','/Puppet','org.jarvis.TaskbarPuppet','Cancel');currentWindow=null;currentRect=null;
  }
 }
 Object.keys(pending).forEach(function(id){
  var item=pending[id],w=item.w;
  if(now-item.time>2200||w.deleted){delete pending[id];return;}
  var r=w.iconGeometry,s=w.output&&w.output.geometry;
  if(!r||r.width<=0||r.height<=0||!s)return;
  var x=r.x+r.width/2,y=r.y+r.height/2;
  if(x<s.x||x>s.x+s.width||y<s.y||y>s.y+s.height)return;
  var panel=null;
  for(var i=0;i<windows.length;i++){
   var v=windows[i],g=v.frameGeometry;
   if(v.dock&&g&&x>=g.x&&x<=g.x+g.width&&y>=g.y&&y<=g.y+g.height){panel={x:g.x,y:g.y,width:g.width,height:g.height};break;}
  }
  if(!panel)return;
  delete pending[id];shown[id]=true;current=id;currentWindow=w;currentRect={x:r.x,y:r.y};
  callDBus('org.jarvis.TaskbarPuppet','/Puppet','org.jarvis.TaskbarPuppet','Play',JSON.stringify({app:id,x:r.x,y:r.y,width:r.width,height:r.height,panel:panel,screen:{x:s.x,y:s.y,width:s.width,height:s.height}}));
 });
}
function attach(w){
 if(w.dock)w.damaged.connect(sample);
 if(w.normalWindow){w.activeChanged.connect(sample);w.minimizedChanged.connect(sample);if(w.iconGeometryChanged)w.iconGeometryChanged.connect(sample);}
}
workspace.windowList().forEach(function(w){attach(w);if(allowed(w))shown[app(w)]=true;});
workspace.windowAdded.connect(function(w){
 attach(w);if(!allowed(w))return;
 var id=app(w);if(!id||shown[id]||pending[id])return;
 if(workspace.windowList().some(function(v){return v!==w&&!v.deleted&&allowed(v)&&app(v)===id;}))return;
 if(Object.keys(pending).length>=16)return;
 pending[id]={w:w,time:Date.now()};last=0;sample();
});
workspace.windowRemoved.connect(function(w){
 var id=app(w);if(!workspace.windowList().some(function(v){return v!==w&&!v.deleted&&allowed(v)&&app(v)===id;})){
  delete shown[id];delete pending[id];if(current===id)cancel();
 }
});
workspace.currentDesktopChanged.connect(cancel);workspace.currentActivityChanged.connect(cancel);workspace.screensChanged.connect(cancel);
