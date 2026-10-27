#version 460 core
#include <flutter/runtime_effect.glsl>

// Orb bloom, soft radial light and an edge glow with a chromatic split.
// One fullscreen quad, no textures; rendered at buffer_scale of the screen.
uniform vec2 uSize;
uniform float uTime;
uniform float uGlow;
uniform float uEdge;
uniform float uEnergy;
uniform float uLight;
uniform vec3 uAcc;
uniform vec3 uHot;
uniform vec3 uOrb;   // centre x, y, radius in buffer pixels

out vec4 fragColor;

void main() {
  vec2 p = FlutterFragCoord().xy;
  vec2 q = p - uOrb.xy;
  float d = length(q) / uOrb.z;
  float ang = atan(q.y, q.x);
  float breathe = 1.0 + 0.06 * sin(uTime * 1.3) * uEnergy;
  float core = exp(-d * d * 2.2);
  float halo = exp(-max(d - 0.9, 0.0) * 1.7) * smoothstep(0.0, 0.9, d);
  float shafts = 0.5 + 0.5 * sin(ang * 5.0 + uTime * 0.18 + sin(ang * 3.0 - uTime * 0.11) * 1.4);
  float vol = shafts * exp(-d * 0.75) * 0.55;
  float orb = (core * 0.55 + halo * 0.32 + vol * 0.2) * uEnergy * breathe;

  // screen-edge glow with a chromatic split (r and b offset by uEdge)
  float sd = min(min(p.x, uSize.x - p.x), min(p.y, uSize.y - p.y));
  float split = 1.0 + 2.5 * uEdge;
  vec3 ed = vec3(exp(-(sd + split) * 0.12), exp(-sd * 0.12), exp(-(sd - split * 0.5 + 1.5) * 0.12)) * 0.09;

  vec3 col = mix(uAcc, uHot, clamp(core * 1.4, 0.0, 1.0)) * orb + uAcc * ed * (0.5 + uEnergy * 0.5);
  col *= uGlow;
  float a = clamp(max(col.r, max(col.g, col.b)), 0.0, 1.0);
  float k = mix(0.28, 1.0, uLight);
  fragColor = vec4(col * mix(1.0, 0.55, uLight), a * k);
}
