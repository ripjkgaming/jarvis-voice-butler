/* SPDX-License-Identifier: MIT */
"use strict";
var energy = {
    slots: [], active: [], locked: false,
    configure: function () {
        energy.reduced = effect.readConfig("ReducedMotion", false);
        energy.closeEnabled = effect.readConfig("CloseEnabled", false);
        energy.openTime = animationTime(560);
        energy.closeTime = animationTime(3000);
        energy.dockTime = animationTime(460);
        energy.stopAll();
    },
    eligible: function (w, docking) {
        return !energy.locked && !effects.hasActiveFullScreenEffect && (w.visible || docking) &&
            w.onCurrentDesktop !== false && w.onCurrentActivity !== false &&
            !w.lockScreen && !w.outline && !w.popupWindow && !w.desktopWindow &&
            !w.dock && !w.fullScreen && !w.splash && !w.notification && !w.onScreenDisplay &&
            !w.hiddenByShowDesktop && w.managed && (w.normalWindow || w.dialog) &&
            !/kscreenlocker|ksmserver|ksplash|jarvis[-_ ]?watch|polkit|kdesu|pinentry|kwallet|spectacle/i.test(w.windowClass) &&
            !(/plasmashell/i.test(w.windowClass) && !w.hasDecoration);
    },
    release: function (w) {
        var s = w.jarvisEnergy;
        if (!s) return;
        delete w.jarvisEnergy;
        if (s.slot) s.slot.busy = false;
        var i = energy.active.indexOf(w);
        if (i >= 0) energy.active.splice(i, 1);
    },
    stop: function (w) {
        var s = w.jarvisEnergy;
        if (!s) return;
        energy.release(w); // release before cancel, which can emit completion
        cancel(s.ids);
    },
    stopAll: function () {
        while (energy.active.length) energy.stop(energy.active[0]);
    },
    geometry: function (w, mode) {
        var g = w.geometry, e = w.expandedGeometry || g;
        // KWin 6.7 exposes output geometry, but not EffectsHandler.clientArea to JS.
        var screen = w.screen && w.screen.geometry;
        if (!screen) return null;
        if (e.width < 1 || e.height < 1 || e.width*e.height>4000000 || e.width>4096 || e.height>4096) return null;
        var cx=g.x+g.width/2, cy=g.y+g.height/2;
        var x=Math.max(screen.x+48,Math.min(screen.x+screen.width-48,cx));
        var y=screen.y+screen.height-20;
        // Choose a real panel on this output, including hidden/autohide docks.
        // Keep the bin upright on every edge; its base sits inside the panel.
        var bestPanel=null, bestScore=Infinity, binScale=1;
        for (var d=0;d<effects.stackingOrder.length;++d) {
            var panel=effects.stackingOrder[d], r=panel.geometry;
            if (!panel.dock || !r || r.width<=0 || r.height<=0 ||
                r.x+r.width<=screen.x || r.x>=screen.x+screen.width ||
                r.y+r.height<screen.y || r.y>screen.y+screen.height) continue;
            var px=Math.max(r.x,Math.min(r.x+r.width,cx));
            var py=Math.max(r.y,Math.min(r.y+r.height,cy));
            var score=Math.pow(px-cx,2)+Math.pow(py-cy,2);
            // An icon on this dock identifies the application's taskbar.
            var ig=w.iconGeometry;
            if (ig && ig.width>0 && ig.height>0 && ig.x+ig.width/2>=r.x &&
                ig.x+ig.width/2<=r.x+r.width && ig.y+ig.height/2>=r.y &&
                ig.y+ig.height/2<=r.y+r.height) score=-1;
            if (score<bestScore) {bestScore=score;bestPanel=r;}
        }
        if (bestPanel) {
            var br=bestPanel;
            if (br.width>br.height) {
                x=Math.max(br.x+32,Math.min(br.x+br.width-32,cx));
                binScale=Math.min(1,Math.max(0.45,(br.height-4)/40));
                y=Math.max(screen.y+20*binScale,Math.min(screen.y+screen.height-20*binScale,br.y+br.height-20*binScale));
            } else {
                x=Math.max(screen.x+28,Math.min(screen.x+screen.width-28,br.x+br.width/2));
                y=Math.max(br.y+80,Math.min(br.y+br.height-24,cy));
            }
        }
        // Use the actual icon center on any panel edge, in global logical coordinates.
        var icon = w.iconGeometry;
        if (mode === 2) {
            var ix=icon ? icon.x+icon.width/2 : 0, iy=icon ? icon.y+icon.height/2 : 0;
            if (icon && icon.width > 0 && icon.height > 0 && ix >= screen.x &&
                ix <= screen.x+screen.width && iy >= screen.y && iy <= screen.y+screen.height) {
                x=ix; y=iy;
            } else {
                // No icon: project onto the nearest actual panel on this monitor.
                var best=Infinity;
                for (var k=0;k<effects.stackingOrder.length;++k) {
                    var dock=effects.stackingOrder[k], dr=dock.geometry;
                    if (!dock.dock || !dr || dr.width<=0 || dr.height<=0 ||
                        dr.x+dr.width<=screen.x || dr.x>=screen.x+screen.width ||
                        dr.y+dr.height<=screen.y || dr.y>=screen.y+screen.height) continue;
                    var dx=Math.max(dr.x+4,Math.min(dr.x+dr.width-4,g.x+g.width/2));
                    var dy=Math.max(dr.y+4,Math.min(dr.y+dr.height-4,g.y+g.height/2));
                    var distance=Math.pow(dx-g.x-g.width/2,2)+Math.pow(dy-g.y-g.height/2,2);
                    if (distance<best) { best=distance;x=dx;y=dy; }
                }
                if (best===Infinity) y=screen.y+screen.height-2;
            }
        }
        if (mode===2) {
            // Enter the panel perpendicularly, keeping the line on its original axis.
            // Do not chase a task icon sideways along a horizontal taskbar.
            if (bestPanel && bestPanel.width>bestPanel.height) {
                x=Math.max(bestPanel.x+4,Math.min(bestPanel.x+bestPanel.width-4,cx));
                y=bestPanel.y+bestPanel.height/2;
            } else if (bestPanel) {
                x=bestPanel.x+bestPanel.width/2;
                y=Math.max(bestPanel.y+4,Math.min(bestPanel.y+bestPanel.height-4,cy));
            } else { x=cx; }
        }
        var left = Math.min(e.x, x - 60), top = Math.min(e.y, y - 100);
        var right = Math.max(e.x + e.width, x + 60), bottom = Math.max(e.y + e.height, y + 26);
        if (mode === 0) { left = e.x; top = e.y; right = e.x + e.width; bottom = e.y + e.height; }
        // Close uses only this output as its canvas, never the virtual desktop.
        if (mode === 1) { left=screen.x; top=screen.y; right=left+screen.width; bottom=top+screen.height; }
        var panelRect=bestPanel || {x:screen.x,y:screen.y+screen.height-4,width:screen.width,height:4};
        var pl=Math.max(screen.x,panelRect.x), pt=Math.max(screen.y,panelRect.y);
        var pr=Math.min(screen.x+screen.width,panelRect.x+panelRect.width);
        var pb=Math.min(screen.y+screen.height,panelRect.y+panelRect.height);
        if (pr-pl<2 || pb-pt<2) { pl=screen.x;pr=screen.x+screen.width;pt=screen.y+screen.height-4;pb=screen.y+screen.height; }
        var width = right - left, height = bottom - top;
        // Bound redirected surface work, especially for 4K and distant panels.
        if (width*height>4000000 || width>4096 || height>4096) return null;
        var sx = width / e.width, sy = height / e.height;
        return {panel:[pl-left,pt-top,pr-pl,pb-pt],binScale:binScale,canvas:[width,height], source:[e.x-left,e.y-top,e.width,e.height],
            target:[x-left,y-top], clip:[screen.x-left,screen.y-top,screen.width,screen.height],
            scale:{value1:sx,value2:sy}, translation:{
                value1:left-g.x-sx*(e.x-g.x)-(1-sx)*g.width/2,
                value2:top-g.y-sy*(e.y-g.y)-(1-sy)*g.height/2}};
    },
    run: function (w, kind) {
        var dock = kind === "minimize" || kind === "restore";
        var previous = w.jarvisEnergy;
        if (dock && previous && previous.mode === 2 && energy.eligible(w, true) && !energy.reduced) {
            var direction = kind === previous.forwardKind ? Effect.Forward : Effect.Backward;
            if (redirect(previous.ids, direction, Effect.TerminateAtSource | Effect.TerminateAtTarget)) {
                previous.kind = kind;
                return;
            }
        }
        energy.stop(w);
        if (!energy.eligible(w, dock) || (kind === "close" && (w.skipsCloseAnimation || w.minimized))) return;
        var role = kind === "open" ? Effect.WindowAddedGrabRole : kind === "close" ? Effect.WindowClosedGrabRole :
            kind === "minimize" ? Effect.WindowMinimizedGrabRole : Effect.WindowUnminimizedGrabRole;
        if (!effect.grab(w, role, true)) return;
        var duration = kind === "open" ? energy.openTime : kind === "close" ? energy.closeTime : energy.dockTime;
        if (energy.reduced || duration <= 1) return;
        var mode = kind === "open" ? 0 : kind === "close" ? 1 : 2;
        var opening = kind === "open" || kind === "restore";
        var geo = energy.geometry(w, mode);
        var slot = null;
        for (var i = 0; i < energy.slots.length; ++i) {
            if (!energy.slots[i].busy) { slot = energy.slots[i]; break; }
        }
        var animations;
        if (slot && geo) {
            try {
                effect.setUniform(slot.id,"canvasSize",geo.canvas);
                effect.setUniform(slot.id,"sourceRect",geo.source);
                effect.setUniform(slot.id,"target",geo.target);
                effect.setUniform(slot.id,"screenRect",geo.clip);
                effect.setUniform(slot.id,"mode",mode);
                effect.setUniform(slot.id,"panelRect",geo.panel);
                slot.busy = true;
                animations = [
                    {type:Effect.Scale,from:geo.scale,to:geo.scale},
                    {type:Effect.Translation,from:geo.translation,to:geo.translation},
                    {type:Effect.ShaderUniform,fragmentShader:slot.id,uniform:"phase",
                     from:kind === "restore" ? 1 : 0,to:kind === "restore" ? 0 : 1}
                ];
            } catch (error) { slot.busy = false; slot = null; console.warn("Jarvis Energy uniform fallback: " + error); }
        } else slot = null;
        if (!animations) {
            // Native, bounded fallback also limits concurrent offscreen textures.
            animations = [{type:Effect.Scale,from:opening ? 0.92 : 1,to:opening ? 1 : 0.92},
                {type:Effect.Opacity,from:opening ? 0 : 1,to:opening ? 1 : 0}];
            duration = Math.min(duration, animationTime(180));
        }
        var state = {slot:slot, mode:mode, kind:kind, forwardKind:kind, role:role, ids:[], remaining:animations.length};
        w.jarvisEnergy = state; energy.active.push(w);
        try {
            state.ids = animate({window:w,duration:duration,curve:QEasingCurve.Linear,
                keepAlive:kind === "close",animations:animations});
        } catch (error) { energy.release(w); console.warn("Jarvis Energy animation failed: " + error); }
    },
    maximizeScan: function(w) {
        if (!energy.eligible(w,false) || energy.reduced || animationTime(200)<=1 || w.jarvisEnergy) return;
        var slot=null;
        for(var i=0;i<energy.slots.length;i++) if(!energy.slots[i].busy){slot=energy.slots[i];break;}
        var geo=energy.geometry(w,0);if(!slot||!geo)return;
        effect.setUniform(slot.id,"canvasSize",geo.canvas);effect.setUniform(slot.id,"sourceRect",geo.source);
        effect.setUniform(slot.id,"target",geo.target);effect.setUniform(slot.id,"screenRect",geo.clip);
        effect.setUniform(slot.id,"mode",3);
        slot.busy=true;
        var state={slot:slot,mode:3,kind:"maximize-scan",role:-1,ids:[],remaining:1};
        w.jarvisEnergy=state;energy.active.push(w);
        try {state.ids=animate({window:w,duration:animationTime(200),curve:QEasingCurve.Linear,
            animations:[{type:Effect.ShaderUniform,fragmentShader:slot.id,uniform:"phase",from:0,to:1}]});}
        catch(error){energy.release(w);}
    },
    attach: function (w) {
        if(w.windowMaximizedStateChanged)w.windowMaximizedStateChanged.connect(function(){energy.maximizeScan(w);});
        w.minimizedChanged.connect(function () { energy.run(w,w.minimized ? "minimize" : "restore"); });
        w.windowFrameGeometryChanged.connect(function () { var scan=w.jarvisEnergy && w.jarvisEnergy.mode===3; energy.stop(w); if(scan)energy.maximizeScan(w); });
    },
    added: function (w) { energy.attach(w); energy.run(w,"open"); },
    changed: function (w,role) {
        var s = w.jarvisEnergy;
        if (s && (role === s.role || (s.mode === 2 &&
            (role === Effect.WindowMinimizedGrabRole || role === Effect.WindowUnminimizedGrabRole))) && effect.isGrabbed(w,role)) energy.stop(w);
    },
    ended: function (w,id) {
        var s = w.jarvisEnergy;
        if (!s) return;
        // KWin 6 emits id=0 for each attribute; count the equal-duration tracks.
        s.remaining -= 1;
        if (s.remaining <= 0) energy.release(w);
    }
};
energy.configure();
try {
    for (var i=0;i<2;++i) {
        var shader = effect.addFragmentShader(Effect.MapTexture | Effect.Modulate,"energy.frag");
        if (shader) energy.slots.push({id:shader,busy:false});
    }
    console.warn("Jarvis Energy: " + energy.slots.length + " shader slots ready");
} catch (error) { console.warn("Jarvis Energy native fallback: " + error); }
effect.configChanged.connect(energy.configure);
effect.animationEnded.connect(energy.ended);
effects.windowAdded.connect(energy.added);
effects.windowClosed.connect(function(w) { if (energy.closeEnabled) energy.run(w,"close"); });
effects.windowDeleted.connect(energy.release);
effects.windowDataChanged.connect(energy.changed);
effects.activeFullScreenEffectChanged.connect(energy.stopAll);
effects.desktopChanged.connect(energy.stopAll);
effects.screenLockingChanged.connect(function(locked) { energy.locked=locked; energy.stopAll(); });
effects.currentActivityChanged.connect(energy.stopAll);
effects.virtualScreenGeometryChanged.connect(energy.stopAll);
effects.showingDesktopChanged.connect(energy.stopAll);
for (var j=0;j<effects.stackingOrder.length;++j) energy.attach(effects.stackingOrder[j]);
