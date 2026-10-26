/* SPDX-License-Identifier: MIT */
"use strict";
var surfaces = {
    active: [], slots: [], locked: false, recent: [], clickToken: "",
    eligible: function(w) {
        return !surfaces.locked && !effects.hasActiveFullScreenEffect &&
            !w.lockScreen && !w.desktopWindow && !w.dock && !w.splash && !w.outline &&
            !w.fullScreen && !w.criticalNotification &&
            !/kscreenlocker|ksmserver|ksplash|polkit|pinentry|kwallet|kdesu|spectacle|jarvis[-_ ]?watch/i.test(w.windowClass) &&
            (w.popupWindow || w.notification || w.appletPopup || w.onScreenDisplay);
    },
    release: function(w) {
        if(w.jarvisSurface && w.jarvisSurface.elevated)effects.setElevatedWindow(w,false);
        if(w.jarvisSurface && w.jarvisSurface.slot)w.jarvisSurface.slot.busy=false;
        delete w.jarvisSurface;
        var i=surfaces.active.indexOf(w); if(i>=0) surfaces.active.splice(i,1);
    },
    stop: function(w) {
        var state=w.jarvisSurface;if(!state)return;
        surfaces.release(w);cancel(state.ids);
    },
    stopAll: function() {
        surfaces.recent=[];
        while(surfaces.active.length)surfaces.stop(surfaces.active[0]);
    },
    closed: function(w) {
        if(w.jarvisSurface && w.jarvisSurface.launch) {
            if(w.jarvisSurface.opening) {
                // Arrival tracks do not retain a deleted window. Acquire close
                // tracks while windowClosed still owns the native reference.
                surfaces.stop(w);
                surfaces.run(w,false,true);
            }
            return;
        }
        surfaces.run(w,false);
        if(w.notification && w.jarvisSurface) {
            surfaces.recent=surfaces.recent.filter(function(r){return Date.now()-r.time<600;});
            surfaces.recent.push({window:w,time:Date.now()});
        }
    },
    configure: function() {
        var request;
        try {request=JSON.parse(effect.readConfig("ClickRequest","{}"));}catch(ignore){}
        if(!request || !request.token || request.token===surfaces.clickToken ||
            Math.abs(Date.now()-request.time)>600) {surfaces.stopAll();return;}
        surfaces.clickToken=request.token;
        var cursor=effects.cursorPos;
        if(!cursor)return;
        // Only a popup under the click can launch; history actions and unrelated
        // notifications must not borrow another card's texture.
        var candidates=effects.stackingOrder.slice();
        surfaces.recent.forEach(function(r){
            if(Date.now()-r.time<600 && candidates.indexOf(r.window)<0)candidates.push(r.window);
        });
        for(var i=candidates.length-1;i>=0;i--) {
            var w=candidates[i],g=w.geometry;
            if(w.notification && g && cursor.x>=g.x && cursor.x<=g.x+g.width &&
                cursor.y>=g.y && cursor.y<=g.y+g.height) {
                surfaces.run(w,false,true);return;
            }
        }
    },
    launchUniforms: function(w,slot) {
        var g=w.geometry,screen=w.screen.geometry,cursor=effects.cursorPos;
        effect.setUniform(slot.id,"launchSize",[screen.width,screen.height]);
        effect.setUniform(slot.id,"clickOrigin",[
            cursor?Math.max(0,Math.min(1,(cursor.x-g.x)/g.width)):0.5,
            cursor?Math.max(0,Math.min(1,(cursor.y-g.y)/g.height)):0.5]);
    },
    elevateLaunch: function(w) {
        var state=w.jarvisSurface;
        if(!state || !state.launch || state.elevated)return;
        // The expanding card must paint above the rest of Plasma's notification
        // stack. Elevation affects rendering only and ends with these tracks.
        effects.setElevatedWindow(w,true);
        state.elevated=true;
    },
    holdGeometry: function(state) {
        state.held=[state.ids[1],state.ids[2]];
        // Stay exactly screen-sized while the shader performs its final fade.
        redirect(state.held,Effect.Forward,Effect.TerminateAtSource);
    },
    ended: function(w) {
        var state=w.jarvisSurface;
        if(!state)return;
        if(state.held) {
            // A finite visibility track has finished. Release held geometry on
            // the compositor's own pass, avoiding cancellation during iteration.
            // KWin's redirect always removes TerminateAtTarget. Complete a
            // backwards clock at its source to retire these held tracks safely.
            redirect(state.held,Effect.Backward,Effect.TerminateAtSource);
            for(var i=0;i<state.held.length;i++)effect.complete(state.held[i]);
            delete state.held;
        }
        if(--state.remaining<=0)surfaces.release(w);
    },
    run: function(w,opening,launch) {
        // An arrival is still a live window: start the click shader at phase
        // zero instead of reusing an almost-completed reveal clock.
        if(!launch || (w.jarvisSurface && w.jarvisSurface.opening))surfaces.stop(w);
        if(!surfaces.eligible(w) || (!opening && w.skipsCloseAnimation) || (!w.visible && !(launch && w.jarvisSurface)))return;
        var notification=!!w.notification;
        var duration=animationTime(launch?780:notification?(opening?900:620):(opening?180:140));
        if(duration<=1 || effect.readConfig("ReducedMotion",false))return;
        var role=opening?Effect.WindowAddedGrabRole:Effect.WindowClosedGrabRole;
        // Let Plasma's edge-attached sliding surfaces keep their native clip.
        if(!effect.grab(w,role))return;
        var g=w.geometry,screen=w.screen&&w.screen.geometry;
        var sign=screen&&g.x+g.width/2<screen.x+screen.width/2?-1:1;
        var zero={value1:0,value2:0};
        var animations;
        if(notification) {
            // A longer energy reconstruction with a pronounced edge arrival.
            // Bound travel for narrow cards and use this output's coordinates.
            var travel=Math.min(120,Math.max(48,g.width*0.28));
            // Arrival may emerge from the screen edge; a dismissal must stay on
            // its output: clamp the exit slide to the room left before the edge
            // (shadow included) so the card is never cut off mid-dissolve.
            var shell=w.expandedGeometry || g;
            var room=screen?(sign>0?screen.x+screen.width-(shell.x+shell.width):shell.x-screen.x):travel;
            var slide=opening?travel:Math.min(travel*0.8,Math.max(0,room));
            var offset={value1:sign*slide,value2:0};
            animations=[
                {type:Effect.Opacity,from:opening?0:1,to:opening?1:0,
                    duration:animationTime(opening?260:480)},
                {type:Effect.Scale,from:opening?{value1:0.84,value2:0.94}:1,
                    to:opening?1:{value1:0.88,value2:0.94}},
                {type:Effect.Translation,from:opening?offset:zero,to:opening?zero:offset}
            ];
        } else {
            var popupOffset={value1:0,value2:6};
            animations=[
                {type:Effect.Opacity,from:opening?0:1,to:opening?1:0},
                {type:Effect.Scale,from:opening?0.97:1,to:opening?1:0.98},
                {type:Effect.Translation,from:opening?popupOffset:zero,to:opening?zero:popupOffset}
            ];
        }
        // The close/action race must not change the expansion's acceleration.
        if(notification && !opening) {
            animations[1].curve=QEasingCurve.InOutCubic;
            animations[2].curve=QEasingCurve.InOutCubic;
        }
        if(launch) {
            if(!screen || g.width<=0 || g.height<=0)return;
            animations=[
                {type:Effect.Opacity,from:1,to:0},
                {type:Effect.Scale,from:1,to:{value1:screen.width/g.width,value2:screen.height/g.height}},
                {type:Effect.Translation,from:zero,to:{
                    value1:screen.x+screen.width/2-g.x-g.width/2,
                    value2:screen.y+screen.height/2-g.y-g.height/2}}
            ];
        }
        // A closed window's final reference belongs to its existing tracks.
        // Retarget those tracks in place; cancelling them can destroy the pixmap
        // even if a second animate() call has already been made.
        var previous=w.jarvisSurface;
        if(launch && previous) {
            previous.launch=true;
            previous.role=role;
            surfaces.elevateLaunch(w);
            for(var track=0;track<previous.ids.length;track++) {
                var target=track===0?(previous.slot?1:0):track===1?animations[1].to:
                    track===2?animations[2].to:1;
                retarget(previous.ids[track],target,previous.slot && (track===1 || track===2)?animationTime(440):duration);
            }
            if(previous.slot) {
                effect.setUniform(previous.slot.id,"closing",2);
                surfaces.launchUniforms(w,previous.slot);
                surfaces.holdGeometry(previous);
            }
            return;
        }
        var slot=null;
        var expanded=w.expandedGeometry || g;
        if(notification && expanded.width>0 && expanded.height>0) {
            for(var i=0;i<surfaces.slots.length;i++) {
                if(!surfaces.slots[i].busy){slot=surfaces.slots[i];break;}
            }
            if(slot) {
                try {
                    effect.setUniform(slot.id,"canvasSize",[expanded.width,expanded.height]);
                    effect.setUniform(slot.id,"sourceRect",[g.x-expanded.x,g.y-expanded.y,g.width,g.height]);
                    effect.setUniform(slot.id,"edgeSign",sign);
                    effect.setUniform(slot.id,"closing",launch?2:opening?0:1);
                    if(launch)surfaces.launchUniforms(w,slot);
                    slot.busy=true;
                    // The shader owns visibility so its rails survive the dissolve.
                    animations[0]={type:Effect.Opacity,from:1,to:1};
                    animations.push({type:Effect.ShaderUniform,fragmentShader:slot.id,
                        uniform:"phase",from:0,to:1,curve:QEasingCurve.Linear});
                }catch(error){slot.busy=false;slot=null;console.warn("Jarvis surfaces shader fallback: "+error);}
            }
        }
        if(launch && slot) {
            animations[1].duration=animationTime(440);
            animations[2].duration=animationTime(440);
        }
        var state={ids:[],remaining:animations.length,role:role,slot:slot,launch:!!launch,opening:!!opening};
        try {state.ids=animate({window:w,duration:duration,
            curve:launch?QEasingCurve.InOutCubic:notification?(opening?QEasingCurve.OutQuart:QEasingCurve.InCubic):QEasingCurve.OutCubic,
            keepAlive:!opening,animations:animations});
            w.jarvisSurface=state;surfaces.active.push(w);
            surfaces.elevateLaunch(w);
            if(launch && slot)surfaces.holdGeometry(state);
        }catch(error){if(slot)slot.busy=false;console.warn("Jarvis surfaces: "+error);}
    }
};
try {
    for(var i=0;i<6;i++) {
        var shader=effect.addFragmentShader(Effect.MapTexture | Effect.Modulate,"notification.frag");
        if(shader)surfaces.slots.push({id:shader,busy:false});
    }
    console.warn("Jarvis surfaces: "+surfaces.slots.length+" notification shader slots ready");
}catch(error){console.warn("Jarvis surfaces native fallback: "+error);}
effects.windowAdded.connect(function(w){surfaces.run(w,true);});
effects.windowClosed.connect(surfaces.closed);
effects.windowDeleted.connect(function(w){
    surfaces.recent=surfaces.recent.filter(function(r){return r.window!==w;});
    surfaces.release(w);
});
effects.windowDataChanged.connect(function(w,role){if(w.jarvisSurface&&w.jarvisSurface.role===role&&effect.isGrabbed(w,role))surfaces.stop(w);});
effect.animationEnded.connect(surfaces.ended);
effect.configChanged.connect(surfaces.configure);
effects.screenLockingChanged.connect(function(locked){surfaces.locked=locked;surfaces.stopAll();});
effects.activeFullScreenEffectChanged.connect(surfaces.stopAll);
effects.desktopChanged.connect(surfaces.stopAll);
effects.currentActivityChanged.connect(surfaces.stopAll);
effects.virtualScreenGeometryChanged.connect(surfaces.stopAll);
