// Separate taskbar lifecycle: never modifies a window or Plasma task.
'use strict';
function identity(w) { return String(w.desktopFileName || w.resourceClass || ''); }
function cancelOverlay() { callDBus('org.jarvis.TaskbarSlice','/Slice','org.jarvis.TaskbarSlice','Cancel'); }
var anchors={};
var currentApp="";
var lastSample=0;
function remember() {
    var now=Date.now(); if (now-lastSample<100) return; lastSample=now;
    workspace.windowList().forEach(function(w) {
        var r=w.iconGeometry;
        if (w.normalWindow && r && r.width>0 && r.height>0)
            anchors[String(w.internalId)]={x:r.x,y:r.y,width:r.width,height:r.height};
    });
}
function attach(w) {
    if (w.dock) w.damaged.connect(remember); // panel layout damage, no polling timer
    if (w.normalWindow) {
        w.minimizedChanged.connect(remember);w.activeChanged.connect(remember);

    }
}
workspace.windowList().forEach(attach);remember();
workspace.windowAdded.connect(function(w) {if(w.normalWindow && identity(w)===currentApp)cancelOverlay();attach(w);remember();});
workspace.windowRemoved.connect(function(w) {
    var cached=anchors[String(w.internalId)];delete anchors[String(w.internalId)];
    if (!w.normalWindow || w.skipTaskbar || w.specialWindow || w.fullScreen || w.skipsCloseAnimation ||
        /jarvis[-_ ]?watch|kscreenlocker|ksmserver|polkit|kdesu|pinentry|kwallet|spectacle|plasmashell/i.test(identity(w))) return;
    var id=identity(w), r=w.iconGeometry, output=w.output && w.output.geometry;
    if ((!r || r.width<=0 || r.height<=0) && cached) r=cached;
    if (!id || !r || r.width<=0 || r.height<=0 || !output) return;
    var windows=workspace.windowList();
    for (var i=0;i<windows.length;i++)
        if (windows[i]!==w && !windows[i].deleted && windows[i].normalWindow && identity(windows[i])===id) return;
    var panel=null;
    for (var j=0;j<windows.length;j++) {
        var dock=windows[j],pr=dock.frameGeometry;
        if (dock.dock && pr && r.x+r.width/2>=pr.x && r.x+r.width/2<=pr.x+pr.width &&
            r.y+r.height/2>=pr.y && r.y+r.height/2<=pr.y+pr.height) {
            panel={x:pr.x,y:pr.y,width:pr.width,height:pr.height};break;
        }
    }
    if (!panel) return; // Never sweep an invented screen-wide taskbar.
    var points=[];
    windows.forEach(function(v) {
        var ar=v.iconGeometry;
        if (!ar || ar.width<=0 || ar.height<=0) ar=anchors[String(v.internalId)];
        if (!v.normalWindow || !ar) return;
        var ax=ar.x+ar.width/2, ay=ar.y+ar.height/2;
        if (ax<panel.x || ax>panel.x+panel.width || ay<panel.y || ay>panel.y+panel.height) return;
        if (!points.some(function(q){return Math.abs(q.x-ax)<2&&Math.abs(q.y-ay)<2;})) points.push({x:ax,y:ay});
    });
    var tx=r.x+r.width/2,ty=r.y+r.height/2;
    if (!points.some(function(q){return Math.abs(q.x-tx)<2&&Math.abs(q.y-ty)<2;})) points.push({x:tx,y:ty});
    points.sort(function(a,b){return panel.width>=panel.height?a.x-b.x:a.y-b.y;});
    var targetIndex=points.findIndex(function(q){return Math.abs(q.x-tx)<2&&Math.abs(q.y-ty)<2;});
    points=points.slice(0,targetIndex+1);
    currentApp=id;
    callDBus('org.jarvis.TaskbarSlice','/Slice','org.jarvis.TaskbarSlice','Play' ,JSON.stringify({app:id,
        x:r.x,y:r.y,width:r.width,height:r.height,panel:panel,scan:points,screen:{x:output.x,y:output.y,width:output.width,height:output.height}}));
});
workspace.currentDesktopChanged.connect(cancelOverlay);
workspace.currentActivityChanged.connect(cancelOverlay);
workspace.screensChanged.connect(function(){anchors={};cancelOverlay();});
