const vm=require('node:vm'),assert=require('node:assert/strict'),cp=require('node:child_process');
const source=cp.execFileSync('/usr/bin/python',['-c',"import sys;sys.path.insert(0,'jarvis_new/src');from system.window_layout import _motion_gate;print(_motion_gate('picked'))"],{cwd:require('node:path').resolve(__dirname,'../../..'),encoding:'utf8'});
for(const behavior of ['reply','missing','throw']){
 let applied=0,timeout,reply;
 function Timer(){this.timeout={connect(fn){timeout=fn}};this.start=()=>{};this.stop=()=>{};}
 const context={picked:[{pid:123,caption:'owned'}],QTimer:Timer,applyGeometry(){applied++},callDBus(...args){reply=args.at(-1);if(behavior==='throw')throw Error('unavailable');if(behavior==='reply')reply(true);}};
 vm.runInNewContext(source,context);timeout();if(reply)reply(true);assert.equal(applied,1,behavior);
}
console.log('PASS: arrange executes exactly once on reply, absent service, thrown error and late replies.');
