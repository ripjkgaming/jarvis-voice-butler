const vm = require('node:vm');
const fs = require('node:fs');
const assert = require('node:assert/strict');
const signal = {connect() {}};
const context = {QTimer:function() { this.timeout=signal; this.start=()=>{}; },
    workspace:{}, KWin:{MaximizeArea:0}};
vm.createContext(context);
vm.runInContext(fs.readFileSync(__dirname+'/bridge.js','utf8'),context);
for (const area of [{x:0,y:32,width:1920,height:1008}, {x:-2560,y:-500,width:2560,height:1400},
                    {x:100,y:50,width:800,height:600}]) {
    for (let i=0;i<1000;i++) {
        const r=context.randomRect(area,{width:1280,height:820},Math.random(),Math.random());
        assert(r.x>=area.x && r.y>=area.y);
        assert(r.x+r.width<=area.x+area.width && r.y+r.height<=area.y+area.height);
    }
}
for (const single of [true,false]) {
    const primary={name:'main'}, second={name:'laptop'};
    const area={x:1920,y:24,width:1920,height:1016};
    const calls=[];
    const w={internalId:'owned',setMaximize:(v,h)=>calls.push([v,h])};
    context.workspace={screens:single?[primary]:[second,primary],windowList:()=>[w],
        currentDesktop:{},clientArea:()=>area,sendClientToScreen:(v,s)=>assert.equal(s,primary)};
    context.place({id:'owned',primary:'main',size:{width:1280,height:820},rx:.5,ry:.5});
    assert.equal(w.fullScreen,false);
    if(single) assert.deepEqual(calls,[[false,false],[true,true]]);
    else { assert.deepEqual(calls,[[false,false]]); assert(w.frameGeometry.x>=1920); }
    assert.equal(context.workspace.activeWindow,w);
}
console.log('Passed: 3000 placement bounds checks, single-screen maximize, multi-screen primary placement');
