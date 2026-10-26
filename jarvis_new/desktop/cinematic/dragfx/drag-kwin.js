// Read-only compositor bridge. Only primitive geometry and window IDs leave KWin.
var nextId = 0;
function send(s, g, id) {
    var c = workspace.cursorPos;
    callDBus('org.jarvis.DragFx', '/Drag', 'org.jarvis.DragFx', 'Report',
        JSON.stringify({s: s, id: id, x: g.x, y: g.y, w: g.width, h: g.height, cx: c.x, cy: c.y}));
}
function attach(w) {
    if (!w || !w.normalWindow) return;
    var id = ++nextId, dragging = false, returning = false;
    w.interactiveMoveResizeStarted.connect(function () {
        returning = false;
        dragging = w.move && !w.resize && !w.fullScreen;
        if (dragging) send('start', w.frameGeometry, id);
    });
    w.interactiveMoveResizeStepped.connect(function (g) {
        if (dragging) send('step', g, id);
    });
    w.interactiveMoveResizeFinished.connect(function () {
        if (!dragging) return;
        dragging = false;
        returning = true;
        send('end', w.frameGeometry, id);
    });
    // Snapping/restoring can commit a different rectangle after move-finished.
    // Keep the reform target attached to the actual window while it settles.
    w.frameGeometryChanged.connect(function () {
        if (returning) send('target', w.frameGeometry, id);
    });
}
workspace.windowList().forEach(attach);
workspace.windowAdded.connect(attach);
