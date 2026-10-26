// Read-only compositor bridge: report only the active Brave client rectangle.
function reportBraveGeometry() {
    var w = workspace.activeWindow;
    var name = w ? String(w.resourceClass || w.desktopFileName || '') : '';
    var payload = {};
    if (w && /brave/i.test(name) && w.normalWindow && !w.minimized && !w.fullScreen) {
        var r = w.clientGeometry;
        payload = {focused:true,state:'normal',left:r.x,top:r.y,width:r.width,height:r.height,outerWidth:w.bufferGeometry.width};
    }
    callDBus('org.jarvis.BraveMotion','/Motion','org.jarvis.BraveMotion','SetWindow',JSON.stringify(payload));
}
function attachBrave(w) {
    if (!/brave/i.test(String(w.resourceClass || w.desktopFileName || ''))) return;
    w.frameGeometryChanged.connect(reportBraveGeometry);
    w.minimizedChanged.connect(reportBraveGeometry);
    w.fullScreenChanged.connect(reportBraveGeometry);
}
workspace.windowList().forEach(attachBrave);
workspace.windowAdded.connect(attachBrave);
workspace.windowActivated.connect(reportBraveGeometry);
workspace.screensChanged.connect(reportBraveGeometry);
workspace.currentDesktopChanged.connect(reportBraveGeometry);
reportBraveGeometry();
