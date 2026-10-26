// Loaded only while a login/unlock sequence (or inspection) is in progress.
'use strict';
var pending = false, stopped = false, lastPlacement = null;
var motionEffects = ['windowaperture', 'jarvis-energy', 'locke-tech-fade',
    'fade', 'glide', 'scale', 'maximize', 'fullscreen', 'login', 'kscreen',
    'slidingpopups', 'slide', 'screentransform'];
function rect(r) { return {x:r.x, y:r.y, width:r.width, height:r.height}; }
function snapshot() {
    return {
        screens: workspace.screens.map(function(s) { return {name:s.name, geometry:rect(s.geometry)}; }),
        windows: workspace.windowList().filter(function(w) { return w.normalWindow && !w.deleted; }).map(function(w) {
            return {id:String(w.internalId), app:String(w.desktopFileName || ''),
                resourceClass:String(w.resourceClass || ''), caption:String(w.caption || ''),
                minimized:w.minimized, hidden:!!w.hidden, geometry:rect(w.frameGeometry)};
        }),
        busy: motionEffects.some(function(name) { return workspace.isEffectActive(name); }),
        placement:lastPlacement
    };
}
function randomRect(area, size, rx, ry) {
    var margin = Math.min(24, Math.floor(Math.min(area.width, area.height) / 20));
    var width = Math.min(size.width, area.width - 2 * margin);
    var height = Math.min(size.height, area.height - 2 * margin);
    return {x:Math.round(area.x + margin + rx * (area.width - 2 * margin - width)),
        y:Math.round(area.y + margin + ry * (area.height - 2 * margin - height)),
        width:Math.floor(width), height:Math.floor(height)};
}
function place(command) {
    var w = workspace.windowList().find(function(v) { return String(v.internalId) === command.id; });
    if (!w) throw new Error('Window closed before placement');
    var screens = workspace.screens;
    var screen = screens.find(function(s) { return s.name === command.primary; });
    if (!screen) throw new Error('Primary output changed; retry on next unlock');
    var area = workspace.clientArea(KWin.MaximizeArea, screen, workspace.currentDesktop);
    w.fullScreen = false;
    w.setMaximize(false, false);
    workspace.sendClientToScreen(w, screen);
    w.desktops = [workspace.currentDesktop];
    w.minimized = false;
    if (screens.length === 1) {
        w.setMaximize(true, true);
    } else {
        w.frameGeometry = randomRect(area, command.size, command.rx, command.ry);
    }
    workspace.activeWindow = w;
    lastPlacement = {id:command.id, area:rect(area), single:screens.length === 1};
}
var timer = new QTimer();
timer.interval = 200;
timer.timeout.connect(function() {
    if (pending || stopped) return;
    pending = true;
    try {
        callDBus('org.jarvis.SessionLauncher', '/Launcher', 'org.jarvis.SessionLauncher',
            'Step', JSON.stringify(snapshot()), function(reply) {
                pending = false;
                try {
                    var command = JSON.parse(reply);
                    if (command.stop) { stopped = true; timer.stop(); }
                    else if (command.place) place(command);
                } catch(error) {
                    lastPlacement = {error:String(error)};
                }
            });
    } catch(error) { pending = false; stopped = true; timer.stop(); }
});
timer.start();
