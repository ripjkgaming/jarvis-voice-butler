/* SPDX-License-Identifier: MIT */
"use strict";
var energy = {
    slots: [], active: [], locked: false, requests: [], seen: [],
    configure: function () {
        var wasReduced = energy.reduced, wasMotionOnly = energy.motionOnly, wasParticleDrag = energy.particleDragEnabled;
        energy.motionOnly = effect.readConfig("WindowMotionOnly", true);
        energy.particleDragEnabled = effect.readConfig("ParticleDragEnabled", false);
        energy.reduced = effect.readConfig("ReducedMotion", false);
        energy.closeEnabled = effect.readConfig("CloseEnabled", false);
        // DragOnly: jarvis-ascii owns every other transition; keep drag motion.
        energy.dragOnly = effect.readConfig("DragOnly", false);
        energy.openTime = animationTime(620);
        energy.closeTime = animationTime(480);
        energy.dockTime = animationTime(600);
        if (wasReduced !== energy.reduced || wasMotionOnly !== energy.motionOnly || wasParticleDrag !== energy.particleDragEnabled) energy.stopAll();
        energy.receive();
    },
    receive: function () {
        try {
            var r=JSON.parse(effect.readConfig("ActionRequest", "{}"));
            if (!r.token || r.expires<Date.now() || r.expires>Date.now()+10000 || energy.seen.indexOf(r.token)>=0) return;
            energy.seen.push(r.token);energy.seen=energy.seen.slice(-32);
            energy.requests=energy.requests.filter(function(x){return x.expires>Date.now();});
            if(r.action==="open" && r.classes && r.classes.length) {
                energy.requests.push(r);energy.requests=energy.requests.slice(-16);return;
            }
            if(r.action==="geometry" && r.windows && r.windows.length<=9) {
                r.windows.forEach(function(identity){
                    var found=effects.stackingOrder.filter(function(w){return w.pid===identity.pid && w.caption===identity.caption;});
                    if(found.length===1)found[0].jarvisCommand=r;
                });return;
            }
            var matches=effects.stackingOrder.filter(function(w){return w.pid===r.pid && w.caption===r.caption;});
            if(matches.length!==1) return;
            var w=matches[0];
            if(r.action==="portal") {energy.preparePortal(w,r);return;}
            if(r.action==="portal-commit") {energy.commitPortal(w);return;}
            if(r.action==="portal-cancel") {if(w.jarvisEnergy && w.jarvisEnergy.kind==="portal")energy.stop(w);return;}
            w.jarvisCommand=r;
            if((r.action==="focus" || r.action==="interact") && !w.minimized) energy.maximizeScan(w,true);
        } catch(ignore) {}
    },
    command: function(w,kind) {
        var r=w.jarvisCommand;
        if(r && r.expires>Date.now() && (r.action===kind || (r.action==="focus" && kind==="restore") || (kind==="interact" && /^(focus|maximize|interact)$/.test(r.action)))) {
            delete w.jarvisCommand;return true;
        }
        if(kind!=="open") return false;
        energy.requests=energy.requests.filter(function(x){return x.expires>Date.now();});
        var names=String(w.windowClass||"").toLowerCase().split(/\s+/);
        for(var i=0;i<energy.requests.length;i++) {
            r=energy.requests[i];
            if(r.classes.some(function(cls){return names.indexOf(cls)>=0;})) {energy.requests.splice(i,1);return true;}
        }
        return false;
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
    // KWin crash guard (kwin_wayland SEGV in AnimationEffect::postPaintScreen):
    // animate()/cancel() must never run from inside animationEnded, which KWin
    // emits while it walks its animation list. A drag track therefore lasts
    // HOLD_MS (longer than any drag) and its rare renewal is queued and flushed
    // from a geometry/pointer event instead. Dead windows are never animated.
    holdMs: 600000,
    renewals: [],
    holdPhase: function() { return energy.holdMs/animationTime(4200); },
    alive: function(w) { try { return !!w && !w.deleted; } catch (e) { return false; } },
    flushRenewals: function() {
        var list=energy.renewals;if(!list.length)return;
        energy.renewals=[];
        list.forEach(function(r){
            var w=r.w;
            if(!energy.alive(w)||w.jarvisEnergy||!(w.move||w.resize))return;
            if(!energy.eligible(w,false)||energy.reduced||animationTime(140)<=1)return;
            energy.dragFrame(w,r.from,r.from+energy.holdPhase(),energy.holdMs,1,false,r.gravity);
        });
    },
    stopAll: function () {
        energy.renewals=[];
        while (energy.active.length) energy.stop(energy.active[0]);
    },
    dockTarget: function(w) {
        var g=w.geometry,screen=w.screen&&w.screen.geometry,icon=w.iconGeometry;
        if(!screen)return {x:g.x+g.width/2,y:g.y+g.height};
        var ix=icon?icon.x+icon.width/2:0,iy=icon?icon.y+icon.height/2:0;
        var validIcon=icon&&icon.width>0&&icon.height>0&&ix>=screen.x&&ix<screen.x+screen.width&&iy>=screen.y&&iy<screen.y+screen.height;
        var target={x:screen.x+screen.width/2,y:screen.y+screen.height-2},best=Infinity;
        effects.stackingOrder.forEach(function(panel) {
            var r=panel.geometry;
            if(!panel.dock||!r||r.width<=0||r.height<=0)return;
            var left=Math.max(r.x,screen.x),right=Math.min(r.x+r.width,screen.x+screen.width);
            var top=Math.max(r.y,screen.y),bottom=Math.min(r.y+r.height,screen.y+screen.height);
            // Hidden panels may sit flush outside their output, but adjacent
            // monitors must never steal the destination.
            if(r.width>=r.height ? right<=left||bottom<top : bottom<=top||right<left)return;
            var x=Math.max(screen.x+1,Math.min(screen.x+screen.width-1,(left+right)/2));
            var y=Math.max(screen.y+1,Math.min(screen.y+screen.height-1,(top+bottom)/2));
            var distance=Math.pow(x-g.x-g.width/2,2)+Math.pow(y-g.y-g.height/2,2);
            // The icon identifies the correct panel, not the landing position.
            if(validIcon&&ix>=r.x&&ix<=r.x+r.width&&iy>=r.y&&iy<=r.y+r.height)distance=-1;
            if(distance<best){best=distance;target={x:x,y:y};}
        });
        // Some shells expose icon geometry without exposing their panel window.
        if(best===Infinity&&validIcon) {
            var dx=Math.min(ix-screen.x,screen.x+screen.width-ix);
            var dy=Math.min(iy-screen.y,screen.y+screen.height-iy);
            target=dy<=dx ? {x:screen.x+screen.width/2,y:iy} : {x:ix,y:screen.y+screen.height/2};
        }
        return target;
    },
    geometry: function (w, mode) {
        var g = w.geometry, e = w.expandedGeometry || g;
        // KWin 6.7 exposes output geometry, but not EffectsHandler.clientArea to JS.
        var screen = w.screen && w.screen.geometry;
        if (!screen) return null;
        var pixelLimit=mode===4?9000000:4000000;
        if (e.width < 1 || e.height < 1 || e.width*e.height>pixelLimit || e.width>4096 || e.height>4096) return null;
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
        // Minimize collapses into the taskbar centre; restore reverses the path.
        if (mode === 2) {
            var destination=energy.dockTarget(w);
            x=destination.x;y=destination.y;
        }
        var left = Math.min(e.x, x - 60), top = Math.min(e.y, y - 100);
        var right = Math.max(e.x + e.width, x + 60), bottom = Math.max(e.y + e.height, y + 26);
        if (mode === 0 || mode === 1) { left = e.x; top = e.y; right = e.x + e.width; bottom = e.y + e.height; }
        if(mode===4){left=screen.x;top=screen.y;right=screen.x+screen.width;bottom=screen.y+screen.height;}
        // Opening and closure stay inside the window, avoiding a screen-sized texture.
        var panelRect=bestPanel || {x:screen.x,y:screen.y+screen.height-4,width:screen.width,height:4};
        var pl=Math.max(screen.x,panelRect.x), pt=Math.max(screen.y,panelRect.y);
        var pr=Math.min(screen.x+screen.width,panelRect.x+panelRect.width);
        var pb=Math.min(screen.y+screen.height,panelRect.y+panelRect.height);
        if (pr-pl<2 || pb-pt<2) { pl=screen.x;pr=screen.x+screen.width;pt=screen.y+screen.height-4;pb=screen.y+screen.height; }
        var width = right - left, height = bottom - top;
        // Bound redirected surface work, especially for 4K and distant panels.
        if (width*height>pixelLimit || width>4096 || height>4096) return null;
        var sx = width / e.width, sy = height / e.height;
        return {panel:[pl-left,pt-top,pr-pl,pb-pt],binScale:binScale,canvas:[width,height], source:[e.x-left,e.y-top,e.width,e.height],
            target:[x-left,y-top], clip:[screen.x-left,screen.y-top,screen.width,screen.height],
            scale:{value1:sx,value2:sy}, translation:{
                value1:left-g.x-sx*(e.x-g.x)-(1-sx)*g.width/2,
                value2:top-g.y-sy*(e.y-g.y)-(1-sy)*g.height/2}};
    },
    run: function (w, kind) {
        if (energy.dragOnly) return;
        var dock = kind === "minimize" || kind === "restore";
        if (energy.motionOnly && !dock) return;
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
        var commanded=energy.command(w,kind);
        if(commanded) duration=kind==="close" ? 1450 : kind==="open" ? 1350 : 1200;
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
                effect.setUniform(slot.id,"commanded",commanded ? 1 : 0);
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
            if(dock) {
                var g=w.geometry,destination=energy.dockTarget(w);
                var tx=destination.x,ty=destination.y;
                var delta={value1:tx-g.x-g.width/2,value2:ty-g.y-g.height/2};
                animations[0]={type:Effect.Scale,from:opening ? .015 : 1,to:opening ? 1 : .015};
                animations.push({type:Effect.Translation,from:opening?delta:0,to:opening?0:delta});
            }
            if(!commanded && !dock)duration = Math.min(duration, animationTime(180));
        }
        if(commanded) console.warn("Jarvis Command: "+kind+" "+duration+"ms");
        var state = {commanded:commanded, slot:slot, mode:mode, kind:kind, forwardKind:kind, role:role, ids:[], remaining:animations.length};
        w.jarvisEnergy = state; energy.active.push(w);
        try {
            state.ids = animate({window:w,duration:duration,curve:!slot && dock ? QEasingCurve.InOutCubic : QEasingCurve.Linear,
                keepAlive:kind === "close",animations:animations});
        } catch (error) { energy.release(w); console.warn("Jarvis Energy animation failed: " + error); }
    },
    maximizeScan: function(w,explicit) {
        if (energy.motionOnly || energy.dragOnly) return;
        if (!energy.eligible(w,false) || energy.reduced || animationTime(240)<=1 || w.jarvisEnergy) return;
        var slot=null;
        for(var i=0;i<energy.slots.length;i++) if(!energy.slots[i].busy){slot=energy.slots[i];break;}
        var geo=energy.geometry(w,0);if(!slot||!geo)return;
        effect.setUniform(slot.id,"canvasSize",geo.canvas);effect.setUniform(slot.id,"sourceRect",geo.source);
        effect.setUniform(slot.id,"target",geo.target);effect.setUniform(slot.id,"screenRect",geo.clip);
        var commanded=explicit===true || energy.command(w,"interact");
        if(explicit===true) delete w.jarvisCommand;
        effect.setUniform(slot.id,"mode",5);
        effect.setUniform(slot.id,"commanded",commanded ? 1 : 0);
        if(commanded) console.warn("Jarvis Command: interact 1200ms");
        slot.busy=true;
        var state={commanded:commanded,slot:slot,mode:5,kind:"focus-edge",role:-1,ids:[],remaining:1};
        w.jarvisEnergy=state;energy.active.push(w);
        try {state.ids=animate({window:w,duration:commanded ? 1200 : animationTime(240),curve:QEasingCurve.Linear,
            animations:[{type:Effect.ShaderUniform,fragmentShader:slot.id,uniform:"phase",from:0,to:1}]});}
        catch(error){energy.release(w);}
    },
    // Cross-display portal: draw one bounded texture across the corridor
    // from source to destination. Move the hidden real window first, then paint
    // from its new geometry so removing the track exposes the same final pixels.
    portalGeometry: function(w,r,origin) {
        var g=w.geometry,e=w.expandedGeometry||g,to=r.to;
        var source=origin ? origin.source : e;
        if(!to || !isFinite(to.x) || !isFinite(to.y) ||
           !isFinite(to.w) || !isFinite(to.h) || to.w<100 || to.h<100) return null;
        // Include outward halves, particle glow and the destination gather.
        var splitPad=source.width*.17+16;
        var monitor=origin ? origin.monitor : ((w.screen&&w.screen.geometry)||effects.clientArea(KWin.ScreenArea,w));
        if(!monitor || monitor.width<100 || monitor.height<100)return null;
        var left=Math.min(source.x-splitPad,to.x-to.w*.06-16);
        var top=Math.min(source.y-16,to.y-to.h*.06-16);
        var right=Math.max(source.x+source.width+splitPad,to.x+to.w*1.06+16);
        var bottom=Math.max(source.y+source.height+16,to.y+to.h*1.06+16);
        left=Math.min(left,monitor.x-8);top=Math.min(top,monitor.y-8);
        right=Math.max(right,monitor.x+monitor.width+8);
        bottom=Math.max(bottom,monitor.y+monitor.height+8);
        var width=right-left,height=bottom-top;
        // Prevent huge redirected textures; falls back to a normal KWin move.
        if(width>8192 || height>2160 || width*height>8500000 ||
           e.width<100 || e.height<100) return null;
        var sx=width/e.width,sy=height/e.height;
        return {canvas:[width,height],
            monitor:[monitor.x-left,monitor.y-top,monitor.width,monitor.height],
            source:[source.x-left,source.y-top,source.width,source.height],
            dest:[to.x-left,to.y-top,to.w,to.h],
            scale:{value1:sx,value2:sy},
            translation:{value1:left-g.x-sx*(e.x-g.x)-(1-sx)*g.width/2,
                         value2:top-g.y-sy*(e.y-g.y)-(1-sy)*g.height/2}};
    },
    preparePortal: function(w,r) {
        if(energy.locked||energy.reduced||!energy.eligible(w,false)||!energy.portalGeometry(w,r))return;
        energy.stop(w);
        var e=w.expandedGeometry||w.geometry;
        var monitor=(w.screen&&w.screen.geometry)||effects.clientArea(KWin.ScreenArea,w);
        var state={kind:"portal",staged:true,role:-1,remaining:1,ids:[],
            origin:{source:{x:e.x,y:e.y,width:e.width,height:e.height},
                monitor:{x:monitor.x,y:monitor.y,width:monitor.width,height:monitor.height}}};
        w.jarvisEnergy=state;energy.active.push(w);
        // Finite fail-safe: a failed caller can never leave a window invisible.
        try {state.ids=animate({window:w,duration:10000,animations:[{type:Effect.Opacity,from:0,to:0}]});}
        catch(error){energy.release(w);}
    },
    commitPortal: function(w) {
        var state=w.jarvisEnergy;
        if(!state||!state.staged)return;
        var e=w.expandedGeometry||w.geometry,origin=state.origin;
        energy.stop(w);
        // Use actual compositor geometry, including shadows, panels and maximize.
        energy.portal(w,{to:{x:e.x,y:e.y,w:e.width,h:e.height}},origin);
    },
    portal: function(w,r,origin) {
        if(energy.locked||energy.reduced||!energy.eligible(w,false))return;
        var geo=energy.portalGeometry(w,r,origin);
        if(!geo)return;
        energy.stop(w);
        var slot=energy.freeSlot();
        if(!slot)return;
        try {
            effect.setUniform(slot.id,"canvasSize",geo.canvas);
            effect.setUniform(slot.id,"sourceRect",geo.source);
            effect.setUniform(slot.id,"portalFrom",geo.source);
            effect.setUniform(slot.id,"portalTo",geo.dest);
            effect.setUniform(slot.id,"screenRect",geo.monitor);
            effect.setUniform(slot.id,"mode",6);
            effect.setUniform(slot.id,"commanded",0);
            slot.busy=true;
            var animations=[
                {type:Effect.Scale,from:geo.scale,to:geo.scale},
                {type:Effect.Translation,from:geo.translation,to:geo.translation},
                {type:Effect.ShaderUniform,fragmentShader:slot.id,uniform:"phase",from:0,to:1}
            ];
            var state={slot:slot,mode:6,kind:"portal",role:-1,remaining:animations.length,ids:[]};
            w.jarvisEnergy=state;energy.active.push(w);
            state.ids=animate({window:w,duration:7200,curve:QEasingCurve.Linear,animations:animations});
            console.warn("Jarvis Portal: "+w.caption+" -> "+r.to.x+","+r.to.y+" (7200ms)");
        } catch(error) {
            energy.release(w);
            console.warn("Jarvis Portal fallback: "+error);
        }
    },
    bind: function(slot,w,mode,commanded) {
        if (energy.motionOnly && !energy.particleDragEnabled && mode===4) return false;
        var geo=energy.geometry(w,mode===4?4:0);if(!geo)return false;
        effect.setUniform(slot.id,"canvasSize",geo.canvas);
        effect.setUniform(slot.id,"sourceRect",geo.source);
        effect.setUniform(slot.id,"target",geo.target);
        effect.setUniform(slot.id,"screenRect",geo.clip);
        effect.setUniform(slot.id,"mode",mode);
        effect.setUniform(slot.id,"commanded",commanded?1:0);
        effect.setUniform(slot.id,"dragHeld",0);
        effect.setUniform(slot.id,"dragFade",1);
        return geo;
    },
    freeSlot: function() {
        for(var i=0;i<energy.slots.length;i++)if(!energy.slots[i].busy)return energy.slots[i];
        return null;
    },
    gravityFor: function(w,phase,previous) {
        var pointer=effects.cursorPos,screen=w.screen&&w.screen.geometry,g=w.geometry;
        if(!screen)return null;
        if(!pointer||!isFinite(pointer.x)||!isFinite(pointer.y))pointer={x:g.x+g.width/2,y:g.y+g.height/2};
        var target={x:Math.max(screen.x,Math.min(screen.x+screen.width,pointer.x)),
            y:Math.max(screen.y,Math.min(screen.y+screen.height,pointer.y))};
        if(previous&&previous.to.x===target.x&&previous.to.y===target.y)return previous;
        var origin=target;
        if(previous) {
            var elapsed=Math.max(0,(phase-previous.phase)*animationTime(4200));
            var ease=1-Math.exp(-elapsed/90);
            origin={x:previous.from.x+(previous.to.x-previous.from.x)*ease,
                y:previous.from.y+(previous.to.y-previous.from.y)*ease};
        }
        return {from:origin,to:target,phase:phase};
    },
    bindGravity: function(w,gravity) {
        var state=w.jarvisEnergy,screen=w.screen.geometry;
        state.gravity=gravity;
        effect.setUniform(state.slot.id,"gravityFrom",[gravity.from.x-screen.x,gravity.from.y-screen.y]);
        effect.setUniform(state.slot.id,"gravityTo",[gravity.to.x-screen.x,gravity.to.y-screen.y]);
        effect.setUniform(state.slot.id,"gravityPhase",gravity.phase);
        effect.setUniform(state.slot.id,"gravityPeriod",animationTime(4200));
    },
    pointerChanged: function() {
        energy.flushRenewals();
        // Geometry events also call this path during native drag, so cursor
        // tracking does not depend on optional global mouse polling support.
        energy.active.forEach(function(w){
            var state=w.jarvisEnergy;
            if(!state||state.mode!==4||!state.persistent)return;
            var phase=energy.dragProgress(state).phase;
            energy.bindGravity(w,energy.gravityFor(w,phase,state.gravity));
        });
    },
    // Every drag track is finite. Completion only frees JS bookkeeping or
    // appends the next batch; it never cancels KWin's animation list mid-traversal.
    dragFrame: function(w,from,to,duration,fadeFrom,settling,previousGravity) {
        if(!energy.alive(w))return;
        if(energy.motionOnly&&!energy.particleDragEnabled)return;
        var slot=energy.freeSlot();if(!slot)return;
        var geo=energy.bind(slot,w,4,false);if(!geo)return;
        effect.setUniform(slot.id,"dragHeld",1);
        effect.setUniform(slot.id,"dragFade",fadeFrom);
        slot.busy=true;
        var animations=[
            {type:Effect.Scale,from:geo.scale,to:geo.scale},
            {type:Effect.Translation,from:geo.translation,to:geo.translation},
            {type:Effect.ShaderUniform,fragmentShader:slot.id,uniform:"phase",from:from,to:to}
        ];
        if(settling)animations.push({type:Effect.ShaderUniform,fragmentShader:slot.id,uniform:"dragFade",from:fadeFrom,to:0});
        var state={slot:slot,mode:4,kind:settling?"drag-settle":"drag",persistent:!settling,
            role:-1,ids:[],remaining:animations.length,clockFrom:from,clockTo:to,
            clockStart:Date.now(),clockDuration:duration,fadeFrom:fadeFrom};
        w.jarvisEnergy=state;energy.active.push(w);
        energy.bindGravity(w,settling&&previousGravity?previousGravity:energy.gravityFor(w,from,previousGravity));
        try {state.ids=animate({window:w,duration:duration,curve:QEasingCurve.Linear,
            keepAlive:false,animations:animations});}
        catch(error){energy.release(w);console.warn("Jarvis drag: "+error);}
    },
    dragProgress: function(state) {
        var t=Math.max(0,Math.min(1,(Date.now()-state.clockStart)/state.clockDuration));
        return {phase:state.clockFrom+(state.clockTo-state.clockFrom)*t,
            fade:state.persistent?1:state.fadeFrom*(1-t),
            remaining:Math.max(1,state.clockDuration*(1-t))};
    },
    beginMove: function(w) {
        if(!energy.alive(w))return;
        energy.flushRenewals();
        if(energy.motionOnly&&!energy.particleDragEnabled){energy.stop(w);return;}
        if(!energy.eligible(w,false)||energy.reduced||animationTime(140)<=1)return;
        energy.stop(w);
        energy.dragFrame(w,0,energy.holdPhase(),energy.holdMs,1,false);
    },
    finishMove: function(w) {
        energy.renewals=[];
        if(!energy.alive(w))return;
        if(energy.motionOnly&&!energy.particleDragEnabled)return;
        var state=w.jarvisEnergy;
        if(!state || state.mode!==4){energy.maximizeScan(w);return;}
        if(!state.persistent)return;
        var progress=energy.dragProgress(state),duration=animationTime(900);
        energy.stop(w);
        if(!energy.eligible(w,false)||energy.reduced||duration<=1)return;
        energy.dragFrame(w,progress.phase,progress.phase+duration/animationTime(4200),duration,1,true,state.gravity);
    },
    geometryChanged: function(w,old) {
        if(!energy.alive(w))return;
        energy.flushRenewals();
        var current=w.jarvisEnergy;
        if(current && current.kind==="portal")return;
        if(current && current.mode===4) {
            // Geometry events happen outside completion: replace the finite paint
            // batch while preserving the flow clock and remaining release time.
            var progress=energy.dragProgress(current),to=current.clockTo,settling=!current.persistent;
            energy.stop(w);
            if(energy.eligible(w,false)&&!energy.reduced&&animationTime(140)>1)
                energy.dragFrame(w,progress.phase,to,progress.remaining,progress.fade,settling,current.gravity);
            return;
        }
        // Minimized/restore pixmaps outlive visibility and position notifications.
        if(current && (current.mode===2 || current.kind==="open" || current.kind==="close"))return;
        if(w.move || w.resize){energy.beginMove(w);return;}
        if(energy.dragOnly)return;
        var g=w.geometry;
        if(!old || old.width<=0 || old.height<=0 || g.width<=0 || g.height<=0 ||
            (old.x===g.x && old.y===g.y && old.width===g.width && old.height===g.height))return;
        if(!energy.eligible(w,false)||energy.reduced||animationTime(420)<=1)return;
        var commanded=energy.command(w,"geometry") || energy.command(w,"interact") || !!(current&&current.commanded);
        // Retarget from the visible in-flight rectangle on rapid toggles.
        if(current && current.kind==="geometry") {
            var progress=Math.max(0,Math.min(1,(Date.now()-current.started)/current.duration));
            var eased=progress<.5 ? 4*progress*progress*progress : 1-Math.pow(-2*progress+2,3)/2;
            var origin=current.origin,destination=current.destination;
            old={x:origin.x+(destination.x-origin.x)*eased,y:origin.y+(destination.y-origin.y)*eased,
                width:origin.width+(destination.width-origin.width)*eased,height:origin.height+(destination.height-origin.height)*eased};
        }
        energy.stop(w);
        var slot=energy.freeSlot(),animations=[
            {type:Effect.Scale,from:{value1:old.width/g.width,value2:old.height/g.height},to:1},
            {type:Effect.Translation,from:{value1:old.x+old.width/2-g.x-g.width/2,value2:old.y+old.height/2-g.y-g.height/2},to:0}
        ];
        if(slot && energy.bind(slot,w,3,commanded)) {
            slot.busy=true;animations.push({type:Effect.ShaderUniform,fragmentShader:slot.id,uniform:"phase",from:0,to:1});
        } else slot=null;
        var state={slot:slot,commanded:commanded,mode:3,kind:"geometry",role:-1,ids:[],remaining:animations.length};
        w.jarvisEnergy=state;energy.active.push(w);
        state.origin={x:old.x,y:old.y,width:old.width,height:old.height};
        state.destination={x:g.x,y:g.y,width:g.width,height:g.height};
        state.started=Date.now();state.duration=commanded?1250:animationTime(420);
        try {state.ids=animate({window:w,duration:state.duration,curve:QEasingCurve.InOutCubic,animations:animations});}
        catch(error){energy.release(w);console.warn("Jarvis geometry: "+error);}
        if(commanded)console.warn("Jarvis Command: geometry 1250ms");
    },
    attach: function (w) {
        if(w.windowMaximizedStateChanged)w.windowMaximizedStateChanged.connect(function(){energy.maximizeScan(w);});
        w.minimizedChanged.connect(function () { energy.run(w,w.minimized ? "minimize" : "restore"); });
        w.windowFrameGeometryChanged.connect(function (window,old) {energy.geometryChanged(w,old);});
        if(w.windowStartUserMovedResized)w.windowStartUserMovedResized.connect(function(){energy.beginMove(w);});
        if(w.windowFinishUserMovedResized)w.windowFinishUserMovedResized.connect(function(){energy.finishMove(w);});
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
        // KWin emits id=0 for each attribute; all tracks in a batch share time.
        s.remaining -= 1;
        if(s.remaining>0)return;
        var renew=s.persistent && (!energy.motionOnly||energy.particleDragEnabled) && energy.eligible(w,false) && !energy.reduced && animationTime(140)>1;
        energy.release(w);
        if(renew&&energy.alive(w))energy.renewals.push({w:w,from:s.clockTo,gravity:s.gravity});
    }
};
energy.configure();
try {
    for (var i=0;i<2;++i) {
        var shader = effect.addFragmentShader(Effect.MapTexture | Effect.Modulate,"energy.frag");
        if (shader) energy.slots.push({id:shader,busy:false});
    }
    console.warn("Jarvis Energy: " + energy.slots.length + " shader slots ready; window-motion-only=" + energy.motionOnly);
} catch (error) { console.warn("Jarvis Energy native fallback: " + error); }
effect.configChanged.connect(energy.configure);
effect.animationEnded.connect(energy.ended);
effects.windowAdded.connect(energy.added);
effects.windowClosed.connect(function(w) {
    if (w.jarvisEnergy && w.jarvisEnergy.mode===4) energy.stop(w);
    if (energy.motionOnly) energy.stop(w);
    else if (energy.closeEnabled) energy.run(w,"close");
});
effects.windowDeleted.connect(energy.release);
if(effects.windowActivated)effects.windowActivated.connect(function(w){if(w)energy.maximizeScan(w);});
effects.windowDataChanged.connect(energy.changed);
effects.activeFullScreenEffectChanged.connect(energy.stopAll);
effects.desktopChanged.connect(energy.stopAll);
effects.screenLockingChanged.connect(function(locked) { energy.locked=locked; energy.stopAll(); });
effects.currentActivityChanged.connect(energy.stopAll);
effects.virtualScreenGeometryChanged.connect(energy.stopAll);
effects.showingDesktopChanged.connect(energy.stopAll);
if(effects.mouseChanged)effects.mouseChanged.connect(energy.pointerChanged);
for (var j=0;j<effects.stackingOrder.length;++j) energy.attach(effects.stackingOrder[j]);
