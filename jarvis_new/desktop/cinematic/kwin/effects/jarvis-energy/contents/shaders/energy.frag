#version 140
// SPDX-License-Identifier: MIT
// One source read, analytic accents, no history buffers or persistent rendering.
uniform sampler2D sampler;
uniform vec4 modulation;
uniform float phase;
uniform float mode;
uniform float commanded;
uniform float dragHeld;
uniform float dragFade;
uniform vec2 gravityFrom;
uniform vec2 gravityTo;
uniform float gravityPhase;
uniform float gravityPeriod;
uniform vec2 canvasSize;
uniform vec4 sourceRect;
uniform vec2 target;
uniform vec4 screenRect;
uniform vec4 portalFrom;
uniform vec4 portalTo;
in vec2 texcoord0;
out vec4 fragColor;
#include "colormanagement.glsl"
float box(vec2 p,vec2 h) {
    vec2 d=abs(p)-h;
    return length(max(d,0.0))+min(max(d.x,d.y),0.0);
}
float ink(float d) {return 1.0-smoothstep(0.0,1.2,d);}
float glow(float d,float radius) {return pow(max(0.0,1.0-abs(d)/radius),3.0);}
float segment(vec2 p,vec2 a,vec2 b) {
    vec2 v=b-a;
    return length(p-a-v*clamp(dot(p-a,v)/max(dot(v,v),0.01),0.0,1.0));
}
// Smooth velocity and acceleration at each choreography handoff.
float portalEase(float start,float end,float time) {
    float t=clamp((time-start)/(end-start),0.,1.);
    return t*t*t*(t*(t*6.-15.)+10.);
}
// A rounded rectangle has continuous tangents at the monitor corners.
float portalRadius(vec2 v) {
    float m=max(max(abs(v.x),abs(v.y)),.00001);
    vec2 u=abs(v)/m;
    u*=u;u*=u;u*=u;u*=u;u*=u;
    return m*pow(u.x+u.y,1./32.);
}
void main() {
    float p=clamp(phase,0.0,1.0);
    float e=p*p*(3.0-2.0*p);
    vec2 xy=vec2(texcoord0.x,1.0-texcoord0.y)*canvasSize;
    vec2 center=sourceRect.xy+sourceRect.zw*0.5;
    vec2 pos=center,size=sourceRect.zw,samplePoint=xy;
    float opacity=1.0,accent=0.0;
    float frame=ink(box(xy-center,sourceRect.zw*0.5-vec2(1)));
    float envelope=sin(p*3.14159265);
    float tint=0.0;
    // Three short deterministic slice faults per transition, confined to this window.
    float band=floor((xy.y-sourceRect.y)/18.0);
    float seed=fract(sin(band*17.13+3.7)*4375.58);
    float beat=floor(p*9.0);
    float fault=(step(1.0,beat)-step(2.0,beat))+(step(4.0,beat)-step(5.0,beat))+(step(6.0,beat)-step(7.0,beat));
    float slice=step(0.66,seed)*fault*envelope;
    float offset=(seed-.5)*22.0*slice;

    if(mode<0.5) {
        // Reconstruct through interrupted scan fronts with brief digital slice offsets.
        float build=smoothstep(0.10,0.88,p);
        float front=(sourceRect.w*0.5+6.0)*build;
        float behind=front-abs(xy.y-center.y);
        size=sourceRect.zw*mix(0.94,1.0,smoothstep(0.10,0.95,p));
        opacity=smoothstep(-1.0,1.0,behind)*smoothstep(0.06,0.18,p);
        float wake=glow(max(behind,0.0),64.0)*opacity;
        float rows=1.0-smoothstep(0.15,0.9,abs(mod(xy.y-sourceRect.y,6.0)-3.0));
        float cols=1.0-smoothstep(0.1,0.7,abs(mod(xy.x-sourceRect.x,32.0)-16.0));
        float blueprint=(rows*0.12+cols*0.055)*(1.0-opacity)*envelope;
        float beams=ink(abs(behind)-1.0)*0.98+glow(behind,9.0)*0.28;
        accent=(beams*envelope+wake*(0.16+rows*0.28)*envelope+blueprint)*frame;
        float frameDistance=abs(box(xy-center,sourceRect.zw*0.5-vec2(3)));
        accent=max(accent,(ink(frameDistance-0.45)*0.52+glow(frameDistance,9.0)*0.18)*envelope);
        vec2 inset=abs(xy-center)-(sourceRect.zw*0.5-vec2(5));
        float bracket=ink(abs(max(inset.x,inset.y))-1.0)*step(-30.0,min(inset.x,inset.y));
        accent=max(accent,bracket*smoothstep(0.0,0.12,p)*(1.0-smoothstep(0.82,1.0,p)));
        float seam=ink(abs(mod(xy.y-sourceRect.y,18.0)-1.0)-.3);
        accent=max(accent,seam*slice*.60*frame*step(.35,fract(xy.x/63.0+seed)));
        tint=0.10*envelope;
    } else if(mode<1.5) {
        // Glass release: shallow retreat, staggered transparent strips, fine light.
        // Never flatten the window into a filled rail or replace it with red.
        float retreat=smoothstep(0.06,0.88,p);
        size=sourceRect.zw*mix(1.0,0.975,retreat);
        pos.y-=9.0*retreat;
        float release=commanded>.5
            ? smoothstep(0.28+seed*.12,0.88+seed*.09,p)
            : smoothstep(0.10+seed*0.16,0.67+seed*0.18,p);
        opacity=(1.0-release)*(1.0-0.12*envelope);
        offset*=commanded>.5 ? .95 : .45;
        if(commanded>.5) {
            // Jarvis: acquire the edges, split the glass, then dissolve each strip.
            size=sourceRect.zw*mix(1.0,.94,smoothstep(.24,.98,p));
            float split=sourceRect.z*.5*smoothstep(.08,.74,p);
            float beam=abs(abs(xy.x-center.x)-split);
            accent=(ink(beam-.4)*.48+glow(beam,6.0)*.12)*frame*envelope*(1.0-smoothstep(.72,.96,p));
            float data=ink(abs(mod(xy.y-sourceRect.y,24.0)-2.0)-.25);
            accent=max(accent,data*step(.78,fract(xy.x/59.0+seed))*frame*envelope*.38);
        }
        float edge=abs(box(xy-pos,size*0.5-vec2(3)));
        float remaining=1.0-smoothstep(0.60,0.96,p);
        float breaks=step(0.48,fract((xy.x+xy.y)*0.022+seed));
        accent=max(accent,(ink(edge-0.35)*0.20+glow(edge,5.0)*0.055)*envelope*remaining*breaks);
        float seam=ink(abs(mod(xy.y-sourceRect.y,18.0)-1.0)-0.15);
        float fragments=step(0.79,fract(xy.x/71.0+seed));
        accent=max(accent,seam*slice*0.23*frame*fragments*remaining);
        // A narrow diagonal sheen passes through the disappearing glass.
        float sheen=xy.x-sourceRect.x-sourceRect.z*(p*1.45-0.20)+(xy.y-center.y)*0.32;
        accent=max(accent,glow(sheen,7.0)*0.10*opacity*envelope*frame);
        tint=0.065*envelope;
    } else if(mode<2.5) {
        // A continuous tapered collapse into the centre of the real taskbar.
        // Compression leads the travel; opacity holds until the final landing.
        float travel=smoothstep(0.0,1.0,p);
        bool vertical=abs(target.y-center.y)>=abs(target.x-center.x);
        float narrow=smoothstep(0.0,.88,p),lengthwise=smoothstep(.12,1.0,p);
        pos=mix(center,target,travel);
        size=mix(sourceRect.zw,vec2(2),vertical ? vec2(narrow,lengthwise) : vec2(lengthwise,narrow));
        opacity=1.0-smoothstep(.88,1.0,p);
        vec2 local=xy-pos;
        float leading=vertical ? local.y/max(size.y,1.0)*sign(target.y-center.y) : local.x/max(size.x,1.0)*sign(target.x-center.x);
        float pinch=1.0-.62*envelope*smoothstep(-.5,.5,leading);
        if(vertical)local.x/=pinch;else local.y/=pinch;
        samplePoint=pos+local;
        float d=abs(box(local,max(size*.5-vec2(1),vec2(0))));
        accent=(ink(d-.35)*.48+glow(d,5.0)*.12)*envelope;
        // A small landing glint, kept inside the panel instead of a screen trail.
        vec2 landing=abs(xy-target);
        float reach=mix(22.0,2.0,smoothstep(.65,1.0,p));
        float glint=vertical ? ink(landing.y-.4)*step(landing.x,reach) : ink(landing.x-.4)*step(landing.y,reach);
        accent=max(accent,glint*.42*smoothstep(.65,.9,p)*envelope);
        accent=min(accent,.60);
        tint=0.0;offset=0.0;
    } else if(mode<3.5) {
        // Geometry already eases from the old rectangle. Add only fine glass edges.
        float d=abs(box(xy-center,sourceRect.zw*0.5-vec2(3)));
        float calm=smoothstep(0.0,.20,p)*(1.0-smoothstep(.45,1.0,p));
        accent=(ink(d-.3)*.24+glow(d,5.0)*.05)*calm;
    }
    if(mode>3.5 && mode<4.5) {
        // A warped flow field carries irregular motes in broad, overlapping
        // currents. No fixed dots orbiting a lattice and no persistent buffers.
        float clock=fract(phase);
        if(dragHeld>.5) envelope=smoothstep(0.0,.13,phase)*smoothstep(0.0,1.0,dragFade);
        slice=0.0;offset=0.0;tint=0.0;
        // Keep the field in screen coordinates on release. It loses energy as
        // the window softly returns, instead of squeezing into a rigid rectangle.
        vec2 cloudOrigin=screenRect.xy;
        vec2 cloudSize=screenRect.zw;
        vec2 cloudUV=(xy-cloudOrigin)/max(cloudSize,vec2(1));
        float time=clock*6.2831853;
        vec2 flow=cloudUV;
        float response=1.0-exp(-max(0.0,phase-gravityPhase)*gravityPeriod/90.0);
        vec2 attractor=(mix(gravityFrom,gravityTo,response)-cloudOrigin)/cloudSize;
        vec2 delta=cloudUV-attractor;
        vec2 aspect=cloudSize/min(cloudSize.x,cloudSize.y);
        float distance2=dot(delta*aspect,delta*aspect);
        float pull=1.55*exp(-distance2/.085);
        // Inverse radial contraction increases mote density near the cursor;
        // a small tangential component makes the gathered field curl around it.
        flow=attractor+delta*(1.0+pull)+vec2(-delta.y,delta.x)*pull*.24*sin(time);
        flow.x+=.075*sin(cloudUV.y*9.0+time)+.035*cos(cloudUV.x*11.0-time*2.0);
        flow.y+=.085*cos(flow.x*7.0-time)+.03*sin(cloudUV.y*13.0+time*2.0);
        vec2 grid=max(vec2(12),floor(screenRect.zw/38.0));
        vec2 gridPoint=flow*grid;
        vec2 cell=floor(gridPoint);
        float dust=0.0;
        for(int iy=-1;iy<=1;iy++)for(int ix=-1;ix<=1;ix++) {
            vec2 key=cell+vec2(float(ix),float(iy));
            vec2 random=fract(sin(vec2(dot(key,vec2(127.1,311.7)),dot(key,vec2(269.5,183.3))))*43758.5453);
            // Preserve mote size as the field compresses, so attraction visibly
            // gathers particles instead of shrinking them into invisible specks.
            vec2 point=(gridPoint-key-random)*cloudSize/grid/(1.0+pull);
            float radius=mix(.7,2.5,random.x*random.x);
            // Slightly elongated soft sparks follow the current, with varied depth.
            float mote=length(point/vec2(1.0+random.y*.8,1.0));
            float spark=(1.0-smoothstep(radius*.4,radius+1.0,mote))*.84+glow(mote,7.0)*.22;
            float breathe=.65+.35*sin(time+random.y*6.2831853);
            dust=max(dust,spark*breathe);
        }
        float bounds=smoothstep(0.0,.015,cloudUV.x)*smoothstep(0.0,.015,cloudUV.y)
            *(1.0-smoothstep(.985,1.0,cloudUV.x))*(1.0-smoothstep(.985,1.0,cloudUV.y));
        accent=min(.9,dust)*smoothstep(0.0,1.0,envelope)*bounds;
        vec2 local=xy-sourceRect.xy;
        float dissolve=.5+.25*sin(local.x*.071+sin(local.y*.047)*2.0)+.25*cos(local.y*.093+local.x*.027);
        opacity=1.0-smoothstep(.04+dissolve*.15,.40+dissolve*.3,envelope);
    }
    if(mode>4.5 && mode<5.5) {
        // Focus leaves content untouched: only a fine blue edge breathes in/out.
        float edge=abs(box(xy-center,sourceRect.zw*.5-vec2(3)));
        float topBias=0.55+0.45*(1.0-clamp((xy.y-sourceRect.y)/sourceRect.w,0.0,1.0));
        float calm=smoothstep(0.0,0.24,p)*(1.0-smoothstep(0.42,1.0,p));
        accent=(ink(edge-0.25)*0.22+glow(edge,4.0)*0.045)*calm*topBias;
    }
    // Jarvis commands: a staged perimeter lock and segmented data rails.
    // User-driven transitions stay brief; close accents stay translucent blue.
    if(commanded>0.5 && mode<1.5) {
        vec2 edge=abs(xy-center)-(sourceRect.zw*0.5-vec2(7));
        float lock=ink(abs(max(edge.x,edge.y))-.8)*step(-48.0,min(edge.x,edge.y));
        float stage=smoothstep(0.0,.16,p)*(1.0-smoothstep(.83,1.0,p));
        float ticks=step(.64,fract((xy.x+xy.y)*.035-p*2.0));
        accent=max(accent,lock*stage);
        float rail=ink(abs(abs(xy.x-center.x)-(sourceRect.z*.5-11.0))-.6)*ticks*frame;
        accent=max(accent,rail*envelope*.85);
        tint+=.09*envelope;
    }
    vec4 color;
    if (mode>5.5) {
        // 6,144 fine motes follow continuous paths: window -> source monitor
        // perimeter -> one full clockwise orbit -> destination -> original image.
        float split=portalEase(.015,.12,p);
        float halfShift=portalFrom.z*.13*split;
        bool leftHalf=xy.x<portalFrom.x+portalFrom.z*.5;
        vec2 inverse=xy+vec2(leftHalf?halfShift:-halfShift,0.);
        vec2 oldUV=(inverse-portalFrom.xy)/portalFrom.zw;
        float oldInside=step(0.,oldUV.x)*step(oldUV.x,1.)*step(0.,oldUV.y)*step(oldUV.y,1.);
        oldInside*=leftHalf?1.-step(.5,oldUV.x):step(.5,oldUV.x);
        float grain=fract(sin(dot(floor(oldUV*vec2(128.,72.)),vec2(127.1,311.7)))*43758.5453);
        float halfFade=1.-smoothstep(.10+grain*.035,.19+grain*.035,p);
        color=texture(sampler,clamp(vec2(oldUV.x,1.-oldUV.y),0.,1.))*oldInside*halfFade;
        // Both separating halves become vivid cyan flashes before dissolving.
        float burst=portalEase(.025,.09,p)*(1.-portalEase(.13,.24,p));
        color.rgb=mix(color.rgb,vec3(.20,.91,1.)*color.a,burst*.90);
        float seamX=abs(xy.x-(sourceRect.x+sourceRect.z*.5+(leftHalf?-halfShift:halfShift)));
        float vertical=1.-smoothstep(sourceRect.w*.5,sourceRect.w*.5+20.,abs(xy.y-(sourceRect.y+sourceRect.w*.5)));
        float flash=min(.98,(exp(-seamX*seamX/8.)*.95+exp(-seamX*seamX/180.)*.60)*burst*vertical);
        vec4 flare=vec4(vec3(.48,.96,1.)*flash,flash);
        color=flare+color*(1.-flare.a);

        float spread=portalEase(.16,.37,p);
        float flight=portalEase(.64,.88,p);
        float settle=portalEase(.85,.985,p);
        vec2 sourceCenter=portalFrom.xy+portalFrom.zw*.5;
        vec2 screenCenter=screenRect.xy+screenRect.zw*.5;
        vec2 destCenter=portalTo.xy+portalTo.zw*.5;
        vec2 fieldCenter=mix(mix(sourceCenter,screenCenter,spread),destCenter,flight);
        vec2 fieldHalf=mix(mix(portalFrom.zw*.5,screenRect.zw*.5-vec2(5.),spread),portalTo.zw*.5,flight);
        vec2 travel=normalize(destCenter-screenCenter+vec2(.001));
        // A thin stream bridges displays after completing the screen orbit.
        fieldHalf*=mix(vec2(1.),vec2(.12)+abs(travel)*1.30,sin(flight*3.14159265));
        float edgeHold=spread*(1.-flight)*(1.-settle);
        float radialMin=mix(.035,.965,edgeHold);
        float radialSpan=mix(.96,.030,edgeHold);
        float turn=portalEase(.34,.66,p)*6.2831853;
        float orbitFlow=sin(clamp((p-.34)/.32,0.,1.)*3.14159265);
        float transferFlow=sin(flight*3.14159265);
        float spreadFlow=sin(spread*3.14159265);
        float streak=orbitFlow*11.+transferFlow*14.+spreadFlow*5.;
        vec2 q=(xy-fieldCenter)/max(fieldHalf,vec2(1.));
        float radius=portalRadius(q);
        float angular=atan(q.y,q.x)-turn;
        float lane=(radius-radialMin)/radialSpan*12.-.5;
        float sector=angular/6.2831853*512.-.5;
        float motes=0.;
        float core=0.;
        if(p>.10 && p<1. && radius<1.08 && radius>radialMin-.04) {
            // Invert the rounded ring; local samples include short motion trails.
            // No per-frame particle allocation and no thousands-wide GPU loop.
            for(int iy=-1;iy<=1;iy++)for(int ix=-2;ix<=2;ix++) {
                float row=floor(lane+.5)+float(iy);
                if(row<0. || row>=12.)continue;
                float col=mod(floor(sector+.5)+float(ix),512.);
                float seed=fract(sin(col*127.1+row*311.7)*43758.5453);
                float theta=(col+.5+(seed-.5)*.55)/512.*6.2831853+turn;
                vec2 ray=vec2(cos(theta),sin(theta));
                vec2 ahead=vec2(ray.x*.999998-ray.y*.002,ray.x*.002+ray.y*.999998);
                ahead/=portalRadius(ahead);
                ray/=portalRadius(ray);
                float radial=radialMin+(row+.5+(seed-.5)*.4)/12.*radialSpan;
                vec2 point=fieldCenter+ray*fieldHalf*radial;
                vec2 delta=xy-point;
                float pin=mix(.42,.85,seed);
                vec2 tangent=normalize((ahead-ray)*fieldHalf+vec2(.00001));
                vec2 direction=normalize(tangent*orbitFlow+travel*transferFlow+ray*spreadFlow+vec2(.00001));
                // Analytic short trails bridge frames without a history buffer.
                float behind=clamp(-dot(delta,direction),0.,streak);
                float dist=length(delta+direction*behind);
                float tailFade=1.-.60*behind/max(streak,.001);
                float halo=(exp(-dist*dist/8.)*.65+exp(-dist*dist/45.)*.28)*tailFade;
                float dotCore=(1.-smoothstep(pin*.25,pin+.65,dist))*tailFade;
                // Three brighter flowing swarms make the full circuit readable.
                float swarm=.62+.38*pow(.5+.5*sin(col/512.*18.84956+row*.09),3.);
                motes=max(motes,(dotCore+halo)*swarm);
                core=max(core,dotCore);
            }
        }
        float particleFade=smoothstep(.10,.21,p)*(1.-smoothstep(.93,1.,p));
        float alpha=min(1.,motes)*particleFade;
        vec3 blue=mix(vec3(.035,.80,1.),vec3(.75,1.,1.),core*.85);
        vec4 dust=vec4(blue*alpha,alpha);
        color=dust+color*(1.-dust.a);

        // Thousands of pinpoints settle into image tiles, revealing the window.
        vec2 rebuilt=(xy-portalTo.xy)/portalTo.zw;
        float inDest=step(0.,rebuilt.x)*step(rebuilt.x,1.)*step(0.,rebuilt.y)*step(rebuilt.y,1.);
        float tile=fract(sin(dot(floor(rebuilt*vec2(128.,72.)),vec2(127.1,311.7)))*43758.5453);
        float reveal=smoothstep(.86+tile*.055,.935+tile*.055,p)*inDest;
        vec4 image=texture(sampler,clamp(vec2(rebuilt.x,1.-rebuilt.y),0.,1.))*reveal;
        color=image+color*(1.-image.a);
        // No second compositor pass, no retained screen capture.
        accent=0.;
    } else {
        vec2 displaced=samplePoint;
        if(mode<2.5 || (mode>3.5 && mode<4.5)) displaced.x+=offset;
        vec2 uv=(displaced-pos)/max(size,vec2(1.0))+0.5;
        float inside=step(0.0,uv.x)*step(uv.x,1.0)*step(0.0,uv.y)*step(uv.y,1.0);
        color=texture(sampler,clamp(vec2(uv.x,1.0-uv.y),0.0,1.0))*inside*opacity;
    }
    if(mode<2.5) {
        color.rgb=mix(color.rgb,vec3(0.06,0.39,1.0)*color.a,tint + (mode>1.5 ? 0.0 : 0.08*envelope));
        color*=1.0-(mode>1.5 ? 0.0 : 0.18)*envelope;
    }
    accent=clamp(accent*((mode>1.5 && mode<3.5) || mode>4.5 ? 1.0 : 1.0-.45*slice),0.0,1.0);
    // A long command may hold corner locks, but never an opaque closing plate.
    if(mode>.5 && mode<1.5) accent=min(accent*(commanded>.5 ? .85 : .55),commanded>.5 ? .46 : .28);
    vec3 cyan=mix(vec3(0.08,0.42,1.0),vec3(0.62,0.88,1.0),accent);
    float danger=(mode>.5 && mode<1.5) ? 0.0 : .72*slice;
    if(commanded>.5 && mode>2.5) danger=.30+.45*step(.70,fract(xy.y/54.0));
    if(mode>1.5) danger=0.0;
    vec3 blood=mix(vec3(.48,.008,.04),vec3(.95,.045,.12),accent);
    cyan=mix(cyan,blood,danger);
    color=vec4(cyan*accent,accent)+color*(1.0-accent);
    if(mode>0.5 && mode<2.5) {
        float clip=step(screenRect.x,xy.x)*step(xy.x,screenRect.x+screenRect.z)*step(screenRect.y,xy.y)*step(xy.y,screenRect.y+screenRect.w);
        color*=clip;
    }
    color=encodingToNits(color,sourceNamedTransferFunction,sourceTransferFunctionParams.x,sourceTransferFunctionParams.y);
    color.rgb=(colorimetryTransform*vec4(color.rgb,1.0)).rgb;
    color*=modulation;
    color.rgb=doTonemapping(color.rgb);
    fragColor=nitsToDestinationEncoding(color);
}
