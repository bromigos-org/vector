"""GLSL for the hologram renderer. All light is additive, premultiplied, in linear HDR
(RGBA16F), then bloomed and tone-mapped in one composite pass."""

MAXP = 24  # parts per model (u_pmat / u_pcol / u_pw arrays)

# ------------------------------------------------------------------ part-indexed geometry
PART_VS = """
layout(location=0) in vec3 a_pos;
layout(location=1) in vec3 a_nrm;
layout(location=2) in float a_part;
uniform mat4 u_vp, u_model;
uniform mat4 u_pmat[%d];
uniform vec4 u_pcol[%d];
uniform float u_pw[%d];
out vec3 g_w; out vec3 g_n; out vec4 g_col; out float g_ly; out float g_pw;
void main() {
    int p = int(a_part + 0.5);
    vec4 w = u_model * (u_pmat[p] * vec4(a_pos, 1.0));
    g_w = w.xyz;
    g_n = mat3(u_model) * mat3(u_pmat[p]) * a_nrm;
    g_col = u_pcol[p];
    g_ly = a_pos.y;
    g_pw = u_pw[p];
    gl_Position = u_vp * w;
}
""" % (MAXP, MAXP, MAXP)

# free geometry: per-vertex colour, no parts (table rings, leader lines, HUD)
FREE_VS = """
layout(location=0) in vec3 a_pos;
layout(location=1) in vec4 a_col;
uniform mat4 u_vp, u_model;
out vec3 g_w; out vec3 g_n; out vec4 g_col; out float g_ly; out float g_pw;
void main() {
    vec4 w = u_model * vec4(a_pos, 1.0);
    g_w = w.xyz; g_n = vec3(0.0, 1.0, 0.0); g_col = a_col; g_ly = -10.0; g_pw = 1.0;
    gl_Position = u_vp * w;
}
"""

# fat anti-aliased lines: each segment becomes a screen-space quad
LINE_GS = """
layout(lines) in;
layout(triangle_strip, max_vertices = 4) out;
in vec3 g_w[]; in vec3 g_n[]; in vec4 g_col[]; in float g_ly[]; in float g_pw[];
uniform vec2 u_px;        // viewport size in pixels
uniform float u_width;    // base width in pixels
out vec4 f_col; out float f_d; out float f_ly; out float f_hw;
void main() {
    vec4 a = gl_in[0].gl_Position, b = gl_in[1].gl_Position;
    if (a.w < 0.01 || b.w < 0.01) return;
    if (g_col[0].a < 0.003) return;
    vec2 sa = a.xy / a.w * u_px * 0.5, sb = b.xy / b.w * u_px * 0.5;
    vec2 d = sb - sa;
    float L = length(d);
    vec2 dir = L > 1e-4 ? d / L : vec2(1.0, 0.0);
    vec2 n = vec2(-dir.y, dir.x);
    float hw = u_width * g_pw[0] * 0.5 + 1.0;
    vec2 off = n * hw / (u_px * 0.5);
    vec2 ext = dir * 0.5 / (u_px * 0.5);
    f_hw = hw - 1.0;
    f_col = g_col[0]; f_ly = g_ly[0];
    f_d = hw;  gl_Position = vec4((sa.xy / (u_px * 0.5) - ext + off) * a.w, a.z, a.w); EmitVertex();
    f_d = -hw; gl_Position = vec4((sa.xy / (u_px * 0.5) - ext - off) * a.w, a.z, a.w); EmitVertex();
    f_col = g_col[1]; f_ly = g_ly[1];
    f_d = hw;  gl_Position = vec4((sb.xy / (u_px * 0.5) + ext + off) * b.w, b.z, b.w); EmitVertex();
    f_d = -hw; gl_Position = vec4((sb.xy / (u_px * 0.5) + ext - off) * b.w, b.z, b.w); EmitVertex();
    EndPrimitive();
}
"""

LINE_FS = """
in vec4 f_col; in float f_d; in float f_ly; in float f_hw;
uniform vec4 u_scan;      // x: local y of the scan plane, y: half-width, z: strength
uniform float u_gain;     // overall brightness (hidden-line pass uses less)
out vec4 o;
void main() {
    float cov = clamp(f_hw + 0.5 - abs(f_d), 0.0, 1.0);
    float core = exp(-abs(f_d) / max(f_hw, 0.5));      // brighter spine
    float q = (f_ly - u_scan.x) / u_scan.y;
    float s = u_scan.z * exp(-q * q);
    float k = cov * f_col.a * u_gain * (0.75 + 0.5 * core + 2.5 * s);
    o = vec4(f_col.rgb * k, k);
}
"""

# surface: depth + a faint fresnel shell, topographic slices, the scan band
FILL_VS = """
layout(location=0) in vec3 a_pos;
layout(location=1) in vec3 a_nrm;
layout(location=2) in float a_part;
uniform mat4 u_vp, u_model;
uniform mat4 u_pmat[%d];
uniform vec4 u_pcol[%d];
out vec3 v_w; out vec3 v_n; out vec4 v_col; out float v_ly;
void main() {
    int p = int(a_part + 0.5);
    vec4 w = u_model * (u_pmat[p] * vec4(a_pos, 1.0));
    v_w = w.xyz;
    v_n = mat3(u_model) * mat3(u_pmat[p]) * a_nrm;
    v_col = u_pcol[p];
    v_ly = a_pos.y;
    gl_Position = u_vp * w;
}
""" % (MAXP, MAXP)

FILL_FS = """
in vec3 v_w; in vec3 v_n; in vec4 v_col; in float v_ly;
uniform vec3 u_eye;
uniform vec4 u_scan;
uniform float u_fill;     // shell strength
uniform float u_slices;   // topographic slices per unit height
out vec4 o;
void main() {
    vec3 V = normalize(u_eye - v_w);
    vec3 N = v_n / max(length(v_n), 1e-5);
    float fres = pow(clamp(1.0 - abs(dot(N, V)), 0.0, 1.0), 2.2);
    float q = (v_ly - u_scan.x) / u_scan.y;
    float s = u_scan.z * exp(-q * q);
    float x = v_ly * u_slices;
    float f = fract(x), w = max(fwidth(x), 1e-4);
    float slice = u_slices > 0.5 ? 1.0 - smoothstep(0.0, w * 1.3, min(f, 1.0 - f)) : 0.0;
    float k = v_col.a * (u_fill * (0.035 + 0.5 * fres) + 0.05 * slice * u_fill + 1.1 * s);
    o = vec4(v_col.rgb * k, k * 0.6);
}
"""

# ------------------------------------------------------------------ the projection table
DISC_VS = """
layout(location=0) in vec3 a_pos;
uniform mat4 u_vp, u_model;
out vec2 v_xy;
void main() { v_xy = a_pos.xz; gl_Position = u_vp * u_model * vec4(a_pos, 1.0); }
"""
DISC_FS = """
in vec2 v_xy;
uniform vec3 u_col;
uniform float u_time, u_r, u_power;
uniform float u_calm;     // reduced motion: no sweeping beam (the rings still turn, slower)
out vec4 o;
const float PI = 3.14159265;
float ring(float r, float at, float w) { float q = (r - at) / w; return exp(-q * q); }
void main() {
    float r = length(v_xy) / u_r;
    if (r > 1.25) discard;
    float a = atan(v_xy.y, v_xy.x);
    float glow = exp(-r * r * 3.0) * 0.55;                       // emitter bed
    float rings = ring(r, 1.0, 0.012) * 1.4 + ring(r, 0.82, 0.008) * 0.8 + ring(r, 0.45, 0.01) * 0.5;
    float seg = step(0.5, fract((a + u_time * 0.21) * 12.0 / PI));  // rotating segmented band
    float band = seg * smoothstep(0.88, 0.9, r) * (1.0 - smoothstep(0.95, 0.97, r)) * 0.55;
    float ticks = step(0.92, fract(a * 60.0 / PI)) * smoothstep(1.03, 1.04, r) * (1.0 - smoothstep(1.1, 1.11, r)) * 0.7;
    float sweep = pow(max(0.0, cos(a - u_time * 0.9)), 24.0) * smoothstep(1.0, 0.1, r) * 0.35 * (1.0 - u_calm);
    float k = (glow + rings + band + ticks + sweep) * u_power;
    o = vec4(u_col * k, k * 0.5);
}
"""
CONE_VS = """
layout(location=0) in vec3 a_pos;     // x, z on the unit circle; y = 0 (base) or 1 (top)
uniform mat4 u_vp, u_model;
uniform float u_r0, u_r1, u_h;
out float v_t; out float v_a; out vec3 v_w;
void main() {
    float r = mix(u_r0, u_r1, a_pos.y);
    vec3 p = vec3(a_pos.x * r, a_pos.y * u_h, a_pos.z * r);
    v_t = a_pos.y; v_a = atan(a_pos.z, a_pos.x);
    vec4 w = u_model * vec4(p, 1.0);
    v_w = w.xyz;
    gl_Position = u_vp * w;
}
"""
CONE_FS = """
in float v_t; in float v_a; in vec3 v_w;
uniform vec3 u_col; uniform float u_time, u_power;
uniform float u_calm;     // reduced motion: the light holds steady (no flicker)
out vec4 o;
void main() {
    float fall = pow(clamp(1.0 - v_t, 0.0, 1.0), 1.7);
    float streak = pow(clamp(0.5 + 0.5 * sin(v_a * 28.0 + u_time * 0.5), 0.0, 1.0), 6.0);
    float flick = 0.85 + 0.15 * sin(u_time * 7.0 + v_a * 3.0) * (1.0 - u_calm);
    float k = fall * (0.035 + 0.11 * streak) * flick * u_power;
    o = vec4(u_col * k, k * 0.4);
}
"""

# ------------------------------------------------------------------ 2D: textured quads (text)
QUAD_VS = """
layout(location=0) in vec2 a_pos;
layout(location=1) in vec2 a_uv;
uniform mat4 u_vp;
out vec2 v_uv;
void main() { v_uv = a_uv; gl_Position = u_vp * vec4(a_pos, 0.0, 1.0); }
"""
QUAD_FS = """
in vec2 v_uv;
uniform sampler2D u_tex;
uniform vec4 u_col;       // rgb tint, a opacity
uniform float u_mode;     // 0: alpha mask tinted, 1: solid fill
out vec4 o;
void main() {
    float a = u_mode > 0.5 ? 1.0 : texture(u_tex, v_uv).a;
    a *= u_col.a;
    o = vec4(u_col.rgb * a, a);
}
"""

# ------------------------------------------------------------------ post: bloom + composite
POST_VS = """
out vec2 v_uv;
void main() {
    vec2 p = vec2((gl_VertexID << 1) & 2, gl_VertexID & 2);
    v_uv = p; gl_Position = vec4(p * 2.0 - 1.0, 0.0, 1.0);
}
"""
DOWN_FS = """
in vec2 v_uv; uniform sampler2D u_tex; uniform vec2 u_texel; out vec4 o;
void main() {
    vec4 c = texture(u_tex, v_uv + u_texel * vec2(-1, -1)) + texture(u_tex, v_uv + u_texel * vec2(1, -1))
           + texture(u_tex, v_uv + u_texel * vec2(-1, 1)) + texture(u_tex, v_uv + u_texel * vec2(1, 1));
    o = c * 0.25;
}
"""
BLUR_FS = """
in vec2 v_uv; uniform sampler2D u_tex; uniform vec2 u_dir; out vec4 o;
void main() {
    float w[5] = float[](0.227027, 0.1945946, 0.1216216, 0.054054, 0.016216);
    vec4 c = texture(u_tex, v_uv) * w[0];
    for (int i = 1; i < 5; i++) {
        c += texture(u_tex, v_uv + u_dir * float(i) * 1.6) * w[i];
        c += texture(u_tex, v_uv - u_dir * float(i) * 1.6) * w[i];
    }
    o = c;
}
"""
COMP_FS = """
in vec2 v_uv;
uniform sampler2D u_scene, u_bloom;
uniform float u_bloom_k;
uniform vec4 u_bg;        // backdrop rgb + alpha
uniform float u_fade;
out vec4 o;
void main() {
    vec3 c = texture(u_scene, v_uv).rgb + texture(u_bloom, v_uv).rgb * u_bloom_k;
    c = max(c, vec3(0.0));
    if (any(isnan(c))) c = vec3(0.0);
    c = 1.0 - exp(-c * 1.15);                 // soft shoulder, never clips to flat white
    c *= u_fade;
    float lum = max(c.r, max(c.g, c.b));
    float a = clamp(u_bg.a * u_fade + lum, 0.0, 1.0);
    o = vec4(u_bg.rgb * u_bg.a * u_fade + c, a);
}
"""
