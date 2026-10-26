#version 140
// SPDX-License-Identifier: MIT
// Bounded analytic trails; two source reads only during close, no history buffers.
uniform sampler2D sampler;
uniform vec4 modulation;
uniform float phase;
uniform float mode;
uniform float binScale;
uniform vec2 canvasSize;
uniform vec4 sourceRect;
uniform vec2 target;
uniform vec4 screenRect;
uniform vec4 panelRect;
in vec2 texcoord0;
out vec4 fragColor;
#include "colormanagement.glsl"
float box(vec2 p,vec2 halfSize) {
    vec2 d=abs(p)-halfSize;
    return length(max(d,0.0))+min(max(d.x,d.y),0.0);
}
float ink(float d) { return 1.0-smoothstep(0.0,1.2,d); }
float line(vec2 p,vec2 a,vec2 b,float width) {
    vec2 v=b-a;
    return ink(length(p-a-v*clamp(dot(p-a,v)/max(dot(v,v),0.01),0.0,1.0))-width);
}

const float TAU=6.28318530718;
vec2 perimeter(float t,vec4 r) {
    float d=fract(t)*2.0*(r.z+r.w);
    if(d<r.z)return r.xy+vec2(d,0);
    d-=r.z;if(d<r.w)return r.xy+vec2(r.z,d);
    d-=r.w;if(d<r.z)return r.xy+vec2(r.z-d,r.w);
    return r.xy+vec2(0,r.w-(d-r.z));
}
float segmentDistance(vec2 p,vec2 a,vec2 b) {
    vec2 v=b-a;
    return length(p-a-v*clamp(dot(p-a,v)/max(dot(v,v),0.01),0.0,1.0));
}
float perimeterCoordinate(vec2 xy,vec4 r) {
    vec2 q=clamp(xy-r.xy,vec2(0),r.zw), d=min(q,r.zw-q);
    float along;
    if(d.y<=d.x) along=q.y<r.w*0.5?q.x:r.z+r.w+r.z-q.x;
    else along=q.x>r.z*0.5?r.z+q.y:2.0*r.z+r.w+r.w-q.y;
    return along/(2.0*(r.z+r.w));
}
float shortest(float a,float b) { return mod(b-a+1.5,1.0)-0.5; }
vec2 perimeterTangent(float t,vec4 r) {
    return normalize(perimeter(t+0.0001,r)-perimeter(t,r)+vec2(0.000001));
}
vec4 splitClose(vec2 xy,float p) {
    vec4 screen=vec4(screenRect.xy+vec2(2),screenRect.zw-vec2(4));
    vec4 panel=vec4(panelRect.xy+vec2(1),max(panelRect.zw-vec2(2),vec2(1)));
    vec2 original=sourceRect.xy+sourceRect.zw*0.5;
    vec2 pc=panel.xy+panel.zw*0.5;
    // Nearest output edge provides an axis-aligned bridge to a floating panel.
    vec4 gaps=vec4(pc.y-screen.y,screen.y+screen.w-pc.y,pc.x-screen.x,screen.x+screen.z-pc.x);
    float nearest=min(min(gaps.x,gaps.y),min(gaps.z,gaps.w));
    vec4 result=vec4(0);
    for(int i=0;i<2;i++) {
        float side=float(i);
        float cy=clamp(original.y+(side-0.5)*sourceRect.w*0.5,screen.y+4.0,screen.y+screen.w-4.0);
        vec2 start=vec2(screen.x+side*screen.z,cy);
        float st=perimeterCoordinate(start,screen);
        vec2 landing,bridge;
        if(nearest==gaps.y) { landing=panel.xy+vec2(side*panel.z,panel.w);bridge=vec2(landing.x,screen.y+screen.w); }
        else if(nearest==gaps.x) { landing=panel.xy+vec2(side*panel.z,0);bridge=vec2(landing.x,screen.y); }
        else if(nearest==gaps.z) { landing=panel.xy+vec2(0,side*panel.w);bridge=vec2(screen.x,landing.y); }
        else { landing=panel.xy+vec2(panel.z,side*panel.w);bridge=vec2(screen.x+screen.z,landing.y); }
        bridge=clamp(bridge,screen.xy,screen.xy+screen.zw);
        float bt=perimeterCoordinate(bridge,screen),pt=perimeterCoordinate(landing,panel);
        vec2 bottom=screen.xy+vec2(side*screen.z,screen.w);
        float end=perimeterCoordinate(bottom,screen);
        vec2 pos=start,tangent=vec2(0,1),tailStart=start;
        vec4 track=screen;
        float coordinate=st,travel=0.0,direction=1.0;
        bool onPerimeter=true;
        if(p<0.14) {
            onPerimeter=false;
            float flatten=smoothstep(0.0,0.07,p),reach=smoothstep(0.07,0.14,p);
            vec2 origin=vec2(original.x,cy);
            tangent=vec2(side*2.0-1.0,0);pos=mix(origin,start-tangent*9.0,reach);
            vec2 size=vec2(mix(sourceRect.z,18.0,reach),mix(sourceRect.w*0.5,1.0,flatten));
            vec2 local=(xy-pos)/max(size,vec2(1))+0.5;
            float inside=step(0.0,local.x)*step(local.x,1.0)*step(0.0,local.y)*step(local.y,1.0);
            if(p<0.07 && inside>0.0) {
                vec2 uv=vec2(local.x,1.0-(local.y+side)*0.5);
                vec4 tile=texture(sampler,uv)*(1.0-flatten);
                result=tile+result*(1.0-tile.a);
            }
            tailStart=pos-tangent*size.x*0.5;pos+=tangent*size.x*0.5;
        } else if(p<0.52) {
            travel=smoothstep(0.14,0.52,p);coordinate=st+travel;
        } else if(p<0.65) {
            float delta=shortest(st,bt);direction=sign(delta);
            travel=abs(delta)*smoothstep(0.52,0.65,p);coordinate=st+direction*travel;
        } else if(p<0.68) {
            onPerimeter=false;pos=mix(bridge,landing,smoothstep(0.65,0.68,p));tailStart=bridge;
            tangent=normalize(landing-bridge+vec2(0.00001));
        } else if(p<0.88) {
            track=panel;travel=smoothstep(0.68,0.88,p);coordinate=pt+travel;
        } else if(p<0.90) {
            onPerimeter=false;pos=mix(landing,bridge,smoothstep(0.88,0.90,p));tailStart=landing;
            tangent=normalize(bridge-landing+vec2(0.00001));
        } else if(p<0.97) {
            float delta=shortest(bt,end);direction=sign(delta);
            travel=abs(delta)*smoothstep(0.90,0.97,p);coordinate=bt+direction*travel;
        } else {
            onPerimeter=false;pos=bottom+vec2(0,32.0*smoothstep(0.97,1.0,p));tailStart=pos-vec2(0,14);tangent=vec2(0,1);
        }
        float distance,fade=1.0;
        if(onPerimeter) {
            pos=perimeter(coordinate,track);tangent=perimeterTangent(coordinate,track)*direction;
            float total=2.0*(track.z+track.w);
            float age=mod(direction*(coordinate-perimeterCoordinate(xy,track))*total+total,total);
            distance=abs(box(xy-(track.xy+track.zw*0.5),track.zw*0.5));
            fade=(1.0-smoothstep(0.0,min(240.0,total*0.2),age))*step(age,travel*total);
        } else {
            vec2 v=pos-tailStart;float len=length(v);
            vec2 boundedStart=pos-v*(min(len,240.0)/max(len,0.001));
            distance=segmentDistance(xy,boundedStart,pos);
            fade=smoothstep(0.015,0.07,p);
        }
        vec2 normal=vec2(-tangent.y,tangent.x);
        float arrow=min(segmentDistance(xy,pos,pos-tangent*9.0+normal*4.0),segmentDistance(xy,pos,pos-tangent*9.0-normal*4.0));
        if(arrow<distance) {distance=arrow;fade=smoothstep(0.015,0.07,p);}
        fade*=1.0-smoothstep(0.988,1.0,p);
        float core=ink(distance-0.35)*fade;
        float glow=pow(max(0.0,1.0-distance/8.0),3.0)*0.30*fade;
        float alpha=max(core,glow);
        vec3 rgb=vec3(1.0,0.025,0.045)*glow+vec3(1.0,0.32,0.22)*core;
        vec4 filament=vec4(min(rgb,vec3(alpha)),alpha);
        result=filament+result*(1.0-filament.a);
    }
    return result;
}

void main() {
    float p=clamp(phase,0.0,1.0);
    // KWin's redirected FBO has bottom-left texture coordinates. Screen geometry
    // is top-left. Separate these spaces; never use raw texcoord for drawn shapes.
    vec2 xy=vec2(texcoord0.x,1.0-texcoord0.y)*canvasSize;
    vec2 center=sourceRect.xy+sourceRect.zw*0.5;
    vec2 pos=center, size=sourceRect.zw;
    vec3 red=vec3(1.0,0.025,0.045);
    float content=1.0, reveal=1.0, charge=0.0;
    float shapeEdge=0.0, halo=0.0, coreAlpha=0.0, hotness=0.0;
    float collapse=smoothstep(0.0,0.39,p);
    float flight=smoothstep(0.34,0.80,p);
    float absorb=smoothstep(0.79,0.94,p);
    if (mode>2.5) {
        float sweep=sourceRect.y+sourceRect.w*p;
        float insideFrame=ink(box(xy-center,sourceRect.zw*0.5));
        shapeEdge=ink(abs(xy.y-sweep)-0.8)*sin(p*3.14159265)*0.40*insideFrame;
    } else if (mode>1.5) {
        // Horizontal signal-line collapse, then retire the line to its task icon.
        float flatten=smoothstep(0.0,0.48,p);
        float travel=smoothstep(0.40,0.94,p);
        pos=mix(center,target,travel);
        size=vec2(mix(sourceRect.z,3.0,travel),mix(sourceRect.w,2.0,flatten));
        content=1.0-smoothstep(0.94,1.0,p);
        charge=0.10*sin(p*3.14159265);
        float edge=box(xy-pos,max(size*0.5-vec2(1),vec2(0.0)))-1.0;
        float scan=ink(abs(xy.y-pos.y+(0.5-flatten)*size.y)-0.6);
        float scanlines=(1.0-smoothstep(0.0,0.7,abs(mod(xy.y,7.0)-3.5)))*0.10;
        shapeEdge=max(ink(abs(edge)-0.65)*0.72,max(scan*.35,scanlines))*sin(p*3.14159265);
        shapeEdge*=ink(edge);
    } else if (mode<0.5) {
        // Reconstruct the original pixels strictly behind a top-to-bottom front.
        float eased=p*p*(3.0-2.0*p);
        float front=sourceRect.y-3.0+(sourceRect.w+6.0)*eased;
        float behind=front-xy.y;
        reveal=smoothstep(-1.0,1.0,behind);
        float frame=ink(box(xy-center,sourceRect.zw*0.5));
        float envelope=sin(p*3.14159265);
        float lines=(1.0-smoothstep(0.3,0.9,abs(mod(xy.y-sourceRect.y,4.0)-2.0)));
        float wake=1.0-smoothstep(0.0,72.0,max(behind,0.0));
        shapeEdge=max(ink(abs(behind)-0.9)*0.95,
            lines*reveal*(0.045+0.12*wake))*frame*envelope;
        float gradient=pow(max(0.0,1.0-max(behind,0.0)/64.0),2.0)*reveal*frame*envelope;
        shapeEdge=max(shapeEdge,gradient*0.16);
        charge=0.035*wake*reveal*envelope;
        vec2 inset=abs(xy-center)-(sourceRect.zw*0.5-vec2(2));
        float bracket=ink(abs(max(inset.x,inset.y))-0.6)*step(-16.0,min(inset.x,inset.y));
        shapeEdge=max(shapeEdge,bracket*0.48*smoothstep(0.76,0.84,p)*(1.0-smoothstep(0.90,1.0,p)));
    } else {
        content=0.0; // Close is composed from two independent source halves below.
    }
    vec2 uv=(xy-pos)/max(size,vec2(1.0))+0.5;
    float inside=step(0.0,uv.x)*step(uv.x,1.0)*step(0.0,uv.y)*step(uv.y,1.0);
    vec2 sampleUV=vec2(uv.x,1.0-uv.y);
    vec4 color=vec4(0);
    if(mode<0.5 || mode>1.5) color=texture(sampler,clamp(sampleUV,0.0,1.0))*inside*content*reveal;
    color.rgb=mix(color.rgb,red*color.a,charge);
    vec4 sourceColor=color;
    float luminous=max(shapeEdge,max(coreAlpha,halo));
    vec3 core=mix(red,vec3(1.0,0.84,0.69),hotness*0.88);
    if(mode<0.5) core=mix(red,vec3(1.0,0.55,0.45),smoothstep(0.3,0.9,shapeEdge));
    color=vec4(core*luminous,luminous)+color*(1.0-luminous);
    if (mode>0.5 && mode<1.5) color=splitClose(xy,p);
    float clip=step(screenRect.x,xy.x)*step(xy.x,screenRect.x+screenRect.z)*step(screenRect.y,xy.y)*step(xy.y,screenRect.y+screenRect.w);
    if (mode>0.5 && mode<1.5) color*=clip;
    else if (mode>0.5) color=mix(sourceColor,color,clip);
    color=encodingToNits(color,sourceNamedTransferFunction,sourceTransferFunctionParams.x,sourceTransferFunctionParams.y);
    color.rgb=(colorimetryTransform*vec4(color.rgb,1.0)).rgb;
    color*=modulation;
    color.rgb=doTonemapping(color.rgb);
    fragColor=nitsToDestinationEncoding(color);
}
