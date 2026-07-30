"""Kale Forge Design Studio — self-contained Vercel Python function.

GET /                             → the Design Studio page (prompt in, engineered design out).
GET /?json=1&prompt=...&season=…  → the design spec as JSON (deterministic synthesis).
GET /?seasons=1                   → the seasons the studio can build for.

The synthesis is the repo's own stdlib modules (frc_parts / frc_season /
frc_robot_knowledge / robot_spec), bundled under ./app so this deploys as one function. It
always runs the deterministic path (use_model=False): no external AI, no network. Every
generated design carries its own 3D drivetrain + power-system viewer, built from that
design's real spec.

`season` picks the game the robot is designed for and is the highest-authority input after
the prompt's explicit facts: it sets the gamepiece, the goal height, the climb reach and the
perimeter budget, so the same prompt yields a genuinely different robot in a different year.
Omit it and the season is read out of the prompt, falling back to the current one.
"""
from __future__ import annotations

import json
import os
import sys
from http.server import BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

# make the bundled `app` package importable
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

from app.services.robot_spec import build_robot_spec  # noqa: E402
from app.services.frc_parts import MOTORS  # noqa: E402
from app.services.frc_season import SELECTABLE, season_options  # noqa: E402
from app.services.frc_featurescript import build_featurescript  # noqa: E402

# the per-design 3D viewer, served statically at /studio-viewer.js
try:
    with open(os.path.join(_ROOT, "studio-viewer.js"), encoding="utf-8") as _fh:
        VIEWER_JS = _fh.read()
except OSError:
    VIEWER_JS = "export function buildScene(){throw new Error('viewer.js missing')}"


def _viewer_model(spec: dict) -> dict:
    """Resolve the few catalog dimensions the 3D viewer needs but the spec doesn't inline."""
    dt = spec.get("drivetrain") or {}
    mk = dt.get("motor_key", "kraken_x60")
    sk = dt.get("steer_motor_key", mk)
    m = MOTORS.get(mk, MOTORS["kraken_x60"])
    s = MOTORS.get(sk, m)
    return {
        "motor": {"key": mk, "name": m["name"], "dia_in": m["diameter_in"], "len_in": m["length_in"]},
        "steer_motor": {"key": sk, "name": s["name"], "dia_in": s["diameter_in"], "len_in": s["length_in"]},
    }


def _name(spec: dict) -> str:
    dt = spec.get("drivetrain") or {}
    module = dt.get("module") or (dt.get("type", "swerve").title() + " drivetrain")
    season = (spec.get("season") or {}).get("label") or (spec.get("profile") or {}).get("season", "")
    subs = spec.get("subsystems") or []
    lead = subs[0].title() if subs else "Drivebase"
    return f"{module} {lead} robot" + (f" — {season}" if season else "")


def make_spec(prompt: str, season: str = "") -> dict:
    spec = build_robot_spec(prompt, use_model=False, season=season)
    spec["viewer"] = _viewer_model(spec)
    spec["name"] = _name(spec)
    return spec


class handler(BaseHTTPRequestHandler):
    def _send(self, code: int, body: bytes, content_type: str,
              extra: dict[str, str] | None = None) -> None:
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        for key, value in (extra or {}).items():
            self.send_header(key, value)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        qs = parse_qs(parsed.query)
        if parsed.path in ("/favicon.ico", "/robots.txt"):
            self._send(200, b"", "text/plain")
            return
        if parsed.path == "/viewer.js" or parsed.path == "/studio-viewer.js":
            # Re-read from disk when running locally so edits show up without a restart;
            # on Vercel the import-time copy is used (read-only filesystem, cold start).
            body = VIEWER_JS
            if os.environ.get("KALE_DEV"):
                try:
                    with open(os.path.join(_ROOT, "studio-viewer.js"), encoding="utf-8") as fh:
                        body = fh.read()
                except OSError:
                    pass
            self._send(200, body.encode(), "application/javascript; charset=utf-8")
            return
        if qs.get("fs", ["0"])[0] == "1":
            # The design as editable FeatureScript. Served with an open CORS header on purpose:
            # the point of this endpoint is that Onshape (or anything else) can pull the source
            # straight in rather than round-tripping it through a download folder. It is
            # generated output from a public prompt, so there is nothing here to protect.
            prompt = (qs.get("prompt", [""])[0] or "").strip()
            if len(prompt) < 4:
                self._send(400, b"// prompt too short", "text/plain; charset=utf-8")
                return
            season = (qs.get("season", [""])[0] or "").strip()
            if season not in SELECTABLE:
                season = ""
            try:
                spec = make_spec(prompt[:5000], season)
                source = build_featurescript(spec, spec.get("name", "Kale FRC Robot"))
                self._send(200, source.encode(), "text/plain; charset=utf-8",
                           extra={"Access-Control-Allow-Origin": "*"})
            except Exception as exc:
                self._send(500, f"// generation failed: {exc}".encode(),
                           "text/plain; charset=utf-8")
            return
        if qs.get("seasons", ["0"])[0] == "1":
            self._send(200, json.dumps({"seasons": season_options(),
                                        "default": SELECTABLE[0] if SELECTABLE else ""}).encode(),
                       "application/json")
            return
        if qs.get("json", ["0"])[0] == "1":
            prompt = (qs.get("prompt", [""])[0] or "").strip()
            if len(prompt) < 4:
                self._send(400, json.dumps({"error": "prompt too short"}).encode(), "application/json")
                return
            # An unrecognised season is ignored rather than rejected: it degrades to inferring
            # the season from the prompt, which is a working design, not an error page.
            season = (qs.get("season", [""])[0] or "").strip()
            if season not in SELECTABLE:
                season = ""
            try:
                spec = make_spec(prompt[:5000], season)
                self._send(200, json.dumps(spec).encode(), "application/json")
            except Exception as exc:  # keep the demo resilient
                self._send(500, json.dumps({"error": str(exc)}).encode(), "application/json")
            return
        self._send(200, PAGE.encode(), "text/html; charset=utf-8")


# ─────────────────────────────────────────────────────────────────────────────
# The page. Prompt in; the engineered design and its own 3D drivetrain/power viewer out.
# ─────────────────────────────────────────────────────────────────────────────
PAGE = r"""<!doctype html>
<html lang="en" data-theme="dark">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Design Studio · Kale Forge</title>
<meta name="description" content="Describe an FRC robot; get an engineered design with its own one-to-one 3D drivetrain and power-system viewer.">
<style>
  :root{
    --bg:#111612;--surface:#171d18;--surface-2:#202821;--ink:#eef3ef;--muted:#a6b1a8;
    --line:#303a32;--line-strong:#465248;--brand:#6fc093;--brand-hover:#8bd0a9;--brand-soft:#203b2b;
    --danger:#ef8b81;--warning:#e3bb62;--sans:Inter,ui-sans-serif,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;
    --mono:ui-monospace,"SFMono-Regular",Menlo,Consolas,monospace;
  }
  *{box-sizing:border-box}
  html,body{height:100%;margin:0}
  body{background:var(--bg);color:var(--ink);font-family:var(--sans);-webkit-font-smoothing:antialiased;overflow:hidden}
  a{color:inherit;text-decoration:none}
  .app{position:fixed;inset:0;display:grid;grid-template-rows:auto 1fr}
  .bar{display:flex;align-items:center;gap:14px;padding:0 18px;min-height:56px;border-bottom:1px solid var(--line);background:color-mix(in srgb,var(--bg) 92%,transparent);backdrop-filter:blur(10px);z-index:8}
  .brand{font-size:17px;font-weight:720;letter-spacing:-.03em}
  .brand b{color:var(--brand);font-weight:720}
  .prompt{flex:1;display:flex;gap:8px;max-width:900px}
  .prompt input{flex:1;background:var(--surface);border:1px solid var(--line-strong);border-radius:9px;color:var(--ink);padding:9px 12px;font:500 14px var(--sans)}
  .prompt input:focus{outline:none;border-color:var(--brand)}
  .prompt select{background:var(--surface);border:1px solid var(--line-strong);border-radius:9px;color:var(--ink);padding:9px 10px;font:600 13px var(--sans);cursor:pointer}
  .prompt select:focus{outline:none;border-color:var(--brand)}
  .btn{appearance:none;border:1px solid var(--line-strong);background:transparent;color:var(--ink);font:680 13px var(--sans);padding:9px 14px;border-radius:9px;cursor:pointer;white-space:nowrap;transition:.14s}
  .btn:hover{border-color:var(--brand);color:var(--brand)}
  .btn.primary{background:var(--brand);color:#06170e;border-color:var(--brand)}
  .btn.primary:hover{background:var(--brand-hover)}
  .btn.on{background:var(--brand);color:#06170e;border-color:var(--brand)}
  .stage{position:relative;min-height:0;display:grid;grid-template-columns:340px 1fr}
  .dossier{border-right:1px solid var(--line);overflow:auto;padding:16px;background:var(--surface)}
  .dossier h2{font-size:12px;letter-spacing:.09em;text-transform:uppercase;color:var(--muted);margin:18px 0 8px}
  .dossier h2:first-child{margin-top:0}
  .name{font-size:19px;font-weight:740;letter-spacing:-.02em;margin:0}
  .sub{color:var(--muted);font-size:12px;margin:3px 0 6px}
  .kv{display:grid;grid-template-columns:auto 1fr;gap:5px 12px;font-size:12.5px}
  .kv dt{color:var(--muted)} .kv dd{margin:0;text-align:right;font-variant-numeric:tabular-nums}
  .chips{display:flex;flex-wrap:wrap;gap:6px;margin-top:4px}
  .chip{font-size:11px;padding:4px 8px;border-radius:999px;border:1px solid var(--line-strong);color:var(--muted);cursor:pointer}
  .chip:hover{border-color:var(--brand);color:var(--brand)}
  .sslist{margin:0;padding:0;list-style:none}
  .sslist li{border-top:1px solid var(--line);padding:9px 0}
  .sslist li[data-asm]:hover b{color:var(--brand)}
  .sslist li.sel{box-shadow:inset 3px 0 0 var(--brand);padding-left:9px}
  .sslist b{font-size:13px} .sslist span{display:block;color:var(--muted);font-size:12px;margin-top:2px}
  .note{color:var(--muted);font-size:12px;line-height:1.5;margin:6px 0 0}
  .verify{color:var(--warning);font-size:11px;border-top:1px solid var(--line);padding-top:8px;margin-top:12px;line-height:1.45}
  .view{position:relative;min-height:0}
  #scene{position:absolute;inset:0;display:block}
  .ctrls{position:absolute;left:14px;top:14px;z-index:5;display:flex;flex-direction:column;gap:8px;width:190px}
  .panel{border:1px solid var(--line-strong);border-radius:11px;background:color-mix(in srgb,var(--surface) 88%,transparent);backdrop-filter:blur(12px);box-shadow:0 18px 40px rgba(0,0,0,.34);padding:10px}
  .panel .row{display:flex;gap:7px}.panel .row .btn{flex:1;text-align:center;padding:7px 6px}
  .panel .btn{width:100%;margin-top:7px;text-align:center}
  .panel .btn:first-of-type{margin-top:0}
  .slab{font-size:11px;color:var(--muted);display:flex;justify-content:space-between;margin:9px 0 3px}
  input[type=range]{width:100%;accent-color:var(--brand)}
  .lbl{font:600 11px/1.2 var(--sans);color:var(--ink);white-space:nowrap;cursor:pointer;padding:3px 7px;border-radius:7px;border:1px solid var(--line-strong);background:color-mix(in srgb,var(--surface) 82%,transparent);backdrop-filter:blur(6px);transform:translate(-50%,-50%);pointer-events:auto;box-shadow:0 6px 16px rgba(0,0,0,.3)}
  .lbl::before{content:"";display:inline-block;width:7px;height:7px;border-radius:50%;background:var(--dot,var(--brand));margin-right:6px;vertical-align:-1px}
  .lbl small{color:var(--muted);font-weight:500}
  .empty{position:absolute;inset:0;display:grid;place-items:center;color:var(--muted);font-size:14px;text-align:center;padding:20px}
  .spin{width:32px;height:32px;border:3px solid var(--line-strong);border-top-color:var(--brand);border-radius:50%;animation:sp 1s linear infinite;margin:0 auto 12px}
  @keyframes sp{to{transform:rotate(360deg)}}
  .hint{position:absolute;left:50%;bottom:12px;transform:translateX(-50%);z-index:4;color:var(--muted);font-size:12px;background:color-mix(in srgb,var(--surface) 80%,transparent);border:1px solid var(--line);padding:5px 11px;border-radius:999px}
  @media (max-width:820px){.stage{grid-template-columns:1fr;grid-template-rows:44% 1fr}.dossier{border-right:0;border-bottom:1px solid var(--line)}.ctrls{width:150px}}
</style>
</head>
<body>
<div class="app">
  <div class="bar">
    <a class="brand" href="https://kaleai.vercel.app">Kale <b>Forge</b></a>
    <form class="prompt" id="form">
      <select id="season" title="The game this robot is designed for — it sets the gamepiece, the goal height, the climb reach and the frame perimeter budget"></select>
      <input id="q" autocomplete="off" placeholder="Describe a robot — e.g. 'MK5i swerve on Krakens at R2 with a fast over-bumper intake and a climber'">
      <button class="btn primary" type="submit">Generate</button>
    </form>
  </div>
  <div class="stage">
    <div class="dossier" id="dossier">
      <div class="empty" id="dossier-empty">Describe a robot and press Generate.<br>Each design comes with its own 3D drivetrain and power system.</div>
    </div>
    <div class="view">
      <canvas id="scene"></canvas>
      <div class="ctrls" id="ctrls" style="display:none">
        <div class="panel">
          <div class="row"><button class="btn" data-view="iso">Iso</button><button class="btn" data-view="top">Top</button><button class="btn" data-view="front">Front</button><button class="btn" data-view="side">Side</button></div>
          <button class="btn on" id="b-labels">Labels: on</button>
          <button class="btn" id="b-explode">Exploded</button>
          <button class="btn" id="b-run">Run mechanisms</button>
          <div class="slab"><span>Section cut</span><span id="cutv">off</span></div>
          <input type="range" id="cut" min="0" max="100" value="0">
          <div class="slab" id="picked" style="display:none"></div>
        </div>
      </div>
      <div class="empty" id="view-empty"><div><div class="spin" style="display:none" id="spin"></div>The 3D drivetrain for your design appears here.</div></div>
      <div class="hint" id="hint" style="display:none">drag to orbit · scroll to zoom · click a part</div>
    </div>
  </div>
</div>

<script type="importmap">
{"imports":{"three":"https://cdn.jsdelivr.net/npm/three@0.160.0/build/three.module.js","three/addons/":"https://cdn.jsdelivr.net/npm/three@0.160.0/examples/jsm/"}}
</script>
<script type="module">
import * as THREE from 'three';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';
import { CSS2DRenderer, CSS2DObject } from 'three/addons/renderers/CSS2DRenderer.js';
import { RoomEnvironment } from 'three/addons/environments/RoomEnvironment.js';
import { buildScene } from '/studio-viewer.js';

const $ = s => document.querySelector(s);
const form = $('#form'), q = $('#q'), seasonSel = $('#season');

// One starting prompt per season. The same words describe a different robot in a different
// game, so handing over last year's example with this year's season selected is worse than
// no example at all.
const SEASON_EXAMPLES = {
  '2026-rebuilt': "27 inch REBUILT robot on MK4i swerve — dual-roller over-bumper intake into a spindexer, turreted hooded shooter, telescoping climber to L3",
  '2025-reefscape': "28 inch REEFSCAPE robot, three-stage cascade elevator to L4, coaxial slapdown intake, wristed carriage arm, deep-cage climb",
  'offseason': "MK5i swerve on Krakens at R2 with a fast dual-roller over-bumper intake and a deep-cage climber",
};
const FALLBACK = SEASON_EXAMPLES['2026-rebuilt'];
q.value = FALLBACK;

let seasonInfo = {};
function applySeasonExample(){
  const next = SEASON_EXAMPLES[seasonSel.value];
  // Only replace an untouched example, so a prompt the user typed always survives.
  if (next && Object.values(SEASON_EXAMPLES).concat([FALLBACK]).includes(q.value)) q.value = next;
  const s = seasonInfo[seasonSel.value];
  if (s) q.placeholder = `Describe a ${s.label} robot — gamepiece ${s.gamepiece}, ${s.perimeter_in} in frame perimeter budget`;
}
seasonSel.addEventListener('change', applySeasonExample);

// The season list is served by the same function; a failure here leaves the selector empty
// and the season is read out of the prompt instead, which still produces a design.
fetch('/?seasons=1').then(r => r.json()).then(data => {
  seasonSel.innerHTML = data.seasons.map(s =>
    `<option value="${s.key}">${s.year ? s.year + ' ' + s.game : s.game}</option>`).join('');
  data.seasons.forEach(s => { seasonInfo[s.key] = s; });
  seasonSel.value = data.default || '';
  applySeasonExample();
}).catch(() => { seasonSel.style.display = 'none'; });

let scene3d = null;
form.addEventListener('submit', async (e) => {
  e.preventDefault();
  const prompt = q.value.trim(); if (prompt.length < 4) return;
  $('#dossier-empty') && ($('#dossier-empty').style.display='none');
  $('#view-empty').style.display='grid'; $('#spin').style.display='block';
  try {
    const res = await fetch('/?json=1&prompt=' + encodeURIComponent(prompt)
                            + '&season=' + encodeURIComponent(seasonSel.value || ''));
    const spec = await res.json();
    if (spec.error) throw new Error(spec.error);
    renderDossier(spec);
    if (!scene3d) {
      scene3d = buildScene({ THREE, OrbitControls, CSS2DRenderer, CSS2DObject, RoomEnvironment, canvas: $('#scene') });
      // Exposed so the viewer can be driven from the console or a screenshot script, and so
      // scene3d.capture() can export a PNG of the current design.
      window.scene3d = scene3d;
    }
    scene3d.load(spec);
    $('#ctrls').style.display='flex'; $('#hint').style.display='block';
    $('#view-empty').style.display='none';
    wireControls();
    // The assembly list in the dossier and the 3D view are two views of the same tree,
    // so picking in one selects in the other.
    document.querySelectorAll('[data-asm]').forEach(li=>li.addEventListener('click',()=>{
      document.querySelectorAll('[data-asm]').forEach(o=>o.classList.remove('sel'));
      li.classList.add('sel'); scene3d.select(li.dataset.asm);
    }));
  } catch (err) {
    $('#view-empty').style.display='grid'; $('#spin').style.display='none';
    $('#view-empty').querySelector('div').innerHTML = 'Could not generate: ' + err.message;
  }
});

function row(dt, dd){ return `<dt>${dt}</dt><dd>${dd}</dd>`; }
function renderDossier(spec){
  const d = spec.drivetrain||{}, e = spec.electrical||{}, f = spec.frame||{};
  const inc = (spec.subsystems||[]);
  const partHtml = [];
  const season = spec.season||{};
  partHtml.push(`<p class="name">${spec.name||'Robot'}</p><p class="sub">${spec.team_number?'Team '+spec.team_number+' · ':''}${season.label||(spec.profile&&spec.profile.label)||''}${season.selected_by?' · '+season.selected_by:''}</p>`);

  // What the season asks for, before what this robot does about it. Every row states the
  // number, where it came from and what it forces — the derivation is the useful part.
  if (season.design_targets && season.design_targets.length){
    partHtml.push(`<h2>What ${season.label} asks for</h2><p class="note">${season.summary||''}</p><dl class="kv">
      ${season.design_targets.map(t=>row(t.target, t.value+' — '+t.from)).join('')}
    </dl>`);
    partHtml.push(`<p class="note">${season.design_targets.map(t=>t.means).slice(0,3).join(' ')}</p>`);
    if (season.verify) partHtml.push(`<p class="note">${season.verify}</p>`);
  }

  // The rule check is reported, never enforced: a design that trips one is shown with the
  // check attached so the team decides what to do about it.
  const checks = spec.rule_check||[];
  if (checks.length){
    const bad = checks.filter(c=>!c.ok);
    partHtml.push(`<h2>Construction rule check</h2><dl class="kv">
      ${checks.map(c=>row(c.check+' ('+c.rule+')', (c.ok?'pass':'FAILS')+' — '+c.detail)).join('')}
    </dl>`);
    bad.forEach(c=>partHtml.push(`<p class="note"><b>${c.check} fails ${c.rule}.</b> ${c.fix}</p>`));
    partHtml.push(`<p class="note">These are the four things a synthesised robot can actually get wrong. Passing them means nothing was caught here — it is not an inspection.</p>`);
  }

  partHtml.push(`<h2>Drivetrain</h2><dl class="kv">
     ${row('Type', d.type||'—')}
     ${row('Module', d.module||'—')}
     ${row('Drive motor', d.motor||'—')}
     ${d.drive_ratio?row('Drive ratio', (d.drive_ratio_label?d.drive_ratio_label+' — ':'')+d.drive_ratio+':1'):''}
     ${d.steer_ratio?row('Azimuth', d.steer_ratio+':1'):''}
     ${d.free_speed_fps?row('Free speed', d.free_speed_fps+' ft/s'):''}
     ${row('Modules', (d.module_count||0)+(d.modules_included===false?' (reserved)':''))}
   </dl>`);
  if (spec.intake && spec.intake.included){
    const i = spec.intake;
    partHtml.push(`<h2>Intake (detailed)</h2><dl class="kv">
      ${row('Type', i.type)}
      ${row('Rollers', (i.roller_count||1)+' × Ø'+i.roller_diameter_in+' in')}
      ${i.roller_center_distance_in?row('Center distance', i.roller_center_distance_in+' in'):''}
      ${i.compliant_wheel?row('Wheels', i.compliant_wheel):''}
      ${row('Compression', (i.compression_in||0.5)+' in')}
      ${i.gear_reduction?row('Reduction', i.gear_reduction):''}
      ${i.roller_surface_speed_fps?row('Surface speed', i.roller_surface_speed_fps+' ft/s'):''}
      ${i.deploy?row('Deploy', i.deploy):''}
      ${i.motor?row('Motor', (i.motor_count||1)+' × '+i.motor):''}
    </dl>`);
    if (i.roller_surface_speed_fps) partHtml.push(`<p class="note">Surface speed is geared above the approach speed so the roller pulls the gamepiece in rather than pushing it away; compression comes from the roller center distance, not from feel.</p>`);
  }
  const hp = spec.hopper;
  if (hp && hp.included){
    partHtml.push(`<h2>Hopper / indexer</h2><dl class="kv">
      ${row('Type', hp.type)}
      ${row('Floor', hp.floor_width_in+' × '+hp.floor_depth_in+' in, '+hp.wall_height_in+' in walls')}
      ${row('Capacity', '~'+hp.capacity_estimate+' '+((spec.season&&spec.season.gamepiece)||'pieces'))}
      ${row('Feed rate', '~'+hp.feed_rate_per_s+' per second')}
      ${row('Exit lane', hp.lanes+' × '+hp.exit_lane_width_in+' in')}
      ${row('Index wheels', hp.wheel_count+' × Ø'+hp.wheel_diameter_in+' in, '+hp.wheel_proud_in+' in proud of the floor')}
      ${row('Reduction', hp.gear_reduction)}
      ${row('Motors', (hp.motor_count||1)+' × '+hp.motor)}
    </dl>
    <p class="note">${hp.sensor}. Index wheels stand proud of the floor because a flush wheel lets the piece ride the floor and slip, and a high one lets it climb over.</p>
    <p class="note">${hp.caveat}</p>`);
  }
  const sh = spec.shooter;
  if (sh && sh.included){
    partHtml.push(`<h2>Shooter</h2><dl class="kv">
      ${row('Type', sh.type)}
      ${sh.flywheel_stages?row('Flywheel stages', sh.flywheel_stages):''}
      ${row('Flywheel', 'Ø'+sh.flywheel_diameter_in+' in')}
      ${sh.barrel_length_in?row('Barrel', sh.barrel_length_in+' in'):''}
      ${row('Compression', (sh.compression_in||0.5)+' in')}
      ${sh.hood_angle_deg?row('Hood / pivot', sh.hood_angle_deg[0]+'–'+sh.hood_angle_deg[1]+'°'):''}
      ${sh.turreted?row('Turret','yes'):''}
      ${sh.gear_reduction?row('Reduction', sh.gear_reduction):''}
      ${sh.flywheel_surface_speed_fps?row('Surface speed', sh.flywheel_surface_speed_fps+' ft/s'):''}
      ${sh.exit_velocity_fps?row('Exit velocity', sh.exit_velocity_fps+' ft/s'):''}
      ${sh.spinup_time_s?row('Recovery', '~'+sh.spinup_time_s+' s'):''}
      ${row('Motors', (sh.motor_count||2)+' × '+sh.motor)}
    </dl>${sh.feed_path?`<p class="note">Feed path: ${sh.feed_path}.</p>`:''}
    ${sh.exit_velocity_fps&&!sh.stacked?`<p class="note">A gamepiece squeezed between one wheel and a stationary hood leaves at about half the wheel's surface speed — size the range on the exit velocity, not the surface speed.</p>`:''}`);
    // The shot the season actually asks for: required velocity worked back from the goal
    // geometry, and what this design does against it.
    const st = sh.shot;
    if (st){
      partHtml.push(`<h2>The shot</h2><dl class="kv">
        ${row('Target', st.target+' opening at '+st.target_height_in+' in')}
        ${row('Release height', st.release_height_in+' in')}
        ${row('Cheapest angle', st.optimal_angle_deg+'° at '+st.design_range_ft+' ft')}
        ${row('Required exit', st.required_exit_fps+' ft/s ('+st.required_surface_speed_fps+' ft/s surface)')}
        ${row('Required at '+st.long_range_ft+' ft', st.required_exit_fps_long+' ft/s')}
        ${row('This design', st.achieved_exit_fps+' ft/s → '+st.achieved_range_ft+' ft, '+st.entry_angle_deg+'° entry')}
        ${row('Apex', st.apex_in+' in')}
      </dl>
      <p class="note">${st.makes_design_range?'Clears the design range.':'<b>Short of the design range</b> — raise the surface speed or lower the reduction.'} The cheapest angle is 45° + ½·atan(Δh/d), which is the shot needing the least flywheel energy.</p>
      <p class="note">${st.caveat}</p>`);
    }
  }
  const el2 = spec.elevator;
  if (el2 && el2.included){
    partHtml.push(`<h2>Elevator</h2><dl class="kv">
      ${row('Architecture', el2.architecture)}
      ${row('Stages', el2.stages)}
      ${row('Max height', el2.max_height_in+' in')}
      ${el2.rigging?row('Rigging', el2.rigging):''}
      ${el2.travel_in?row('Carriage travel', el2.travel_in+' in ('+el2.stage_travel_in+' in per stage)'):''}
      ${el2.carriage_speed_multiple>1?row('Carriage speed', el2.carriage_speed_multiple+'× drum payout'):''}
      ${el2.stage_overlap_in?row('Overlap at full ext.', el2.stage_overlap_in+' in'):''}
      ${el2.upright_span_in?row('Upright span', el2.upright_span_in+' in'):''}
      ${row('Rail', el2.rail)} ${row('Reduction', el2.reduction)}
      ${row('Motors', (el2.motor_count||2)+' × '+el2.motor)}
    </dl>${el2.bearing_blocks?`<p class="note">Bearing blocks: ${el2.bearing_blocks}.</p>`:''}
    ${el2.stage_overlap_in?`<p class="note">That remaining overlap, with a bearing block at each end of it, is the only thing resisting the tip moment at full extension.</p>`:''}`);
  }
  const am = spec.manipulator;
  if (am && am.included){
    partHtml.push(`<h2>Arm</h2><dl class="kv">
      ${row('Type', am.type)}
      ${row('Reach', am.reach_in+' in')}
      ${am.segments?row('Segments', am.segments+(am.segment_lengths_in?' ('+am.segment_lengths_in.join(' + ')+' in)':'')):''}
      ${am.shoulder_pivot_deg?row('Shoulder', am.shoulder_pivot_deg[0]+'–'+am.shoulder_pivot_deg[1]+'°'):''}
      ${am.wrist?row('Wrist', (am.wrist_range_deg||[]).join('–')+'°'):''}
      ${am.end_effector?row('End effector', am.end_effector):''}
      ${am.reduction?row('Reduction', am.reduction):''}
      ${am.holding_torque_nm?row('Holding torque', am.holding_torque_nm+' N·m'):''}
      ${am.shoulder_height_in?row('Shoulder height', am.shoulder_height_in+' in'):''}
      ${row('Shaft', am.shaft)}
    </dl>${am.gravity_compensation?`<p class="note">Reduction sized on the worst case — holding horizontal at full extension — with ${am.gravity_compensation}.</p>`:''}`);
  }
  const cl = spec.climber;
  if (cl && cl.included){
    partHtml.push(`<h2>Climber</h2><dl class="kv">
      ${row('Type', cl.type)}
      ${cl.stages?row('Stages', cl.stages):''}
      ${row('Stowed / extended', cl.stowed_height_in+' → '+cl.extended_height_in+' in')}
      ${cl.winch_drum_diameter_in?row('Winch drum', 'Ø'+cl.winch_drum_diameter_in+' in'):''}
      ${cl.rope?row('Rope', cl.rope):''}
      ${cl.hook?row('Hook', cl.hook):''}
      ${cl.travel_in?row('Travel', cl.travel_in+' in'):''}
      ${cl.rope_tension_lbf?row('Rope tension', cl.rope_tension_lbf+' lbf over '+cl.load_paths+' path'+(cl.load_paths>1?'s':'')):''}
      ${cl.drum_torque_nm?row('Drum torque', cl.drum_torque_nm+' N·m'):''}
      ${cl.reduction?row('Reduction', cl.reduction):''}
      ${row('Motors', (cl.motor_count||2)+' × '+cl.motor)}
    </dl><p class="note">${cl.ratchet}. ${cl.engagement||''}</p>
    ${cl.drum_torque_nm?`<p class="note">The drum radius is a lever working against you: a bigger drum takes more rope but costs you reduction.</p>`:''}`);
  }
  partHtml.push(`<h2>Power</h2><dl class="kv">
     ${row('Distributor', (e.distributor_key||'pdh').toUpperCase())}
     ${row('Channels used', (e.budget&&e.budget.channels_used)||'—')}
     ${row('Main breaker', ((e.budget&&e.budget.main_breaker_a)||120)+' A')}
     ${row('Frame', (f.width_in||28)+' × '+(f.length_in||28)+' in')}
   </dl>`);
  const cad = spec.cad;
  if (cad && cad.assemblies){
    const c = cad.feature_counts||{};
    const order = ['tube','plate','gusset','shaft','bearing','gear','pulley','sprocket','belt','wheel','motor','gearbox','bolts'];
    const chips = order.filter(k=>c[k]).map(k=>`${c[k]} ${k}`).concat(
      Object.keys(c).filter(k=>!order.includes(k)).map(k=>`${c[k]} ${k}`));
    partHtml.push(`<h2>CAD model</h2><dl class="kv">
      ${row('Schema', cad.version)}
      ${row('Assemblies', cad.assemblies.length)}
      ${row('Modelled features', cad.feature_total)}
      ${row('Envelope', cad.envelope_in.join(' × ')+' in')}
    </dl>
    <ul class="sslist">`+cad.assemblies.map(a=>
      `<li data-asm="${a.id}" style="cursor:pointer"><b>${a.name}</b><span>${a.features.length} features${a.note?' · '+a.note:''}</span>
       ${(a.mates||[]).length?`<span style="margin-top:4px">${a.mates.join(' · ')}</span>`:''}</li>`).join('')+
    `</ul><p class="note">${chips.join(' · ')}.</p>`);
    const cuts = spec.cut_list||[];
    if (cuts.length){
      const sec = r => `${+r.section_in[0].toFixed(2)}×${+r.section_in[1].toFixed(2)}×${r.wall_in} in`;
      partHtml.push(`<h2>Tube cut list</h2><dl class="kv">`+
        cuts.map(r=>row(sec(r), `${r.qty} × ${r.length_in} in — ${r.used_in.join(', ')}`)).join('')+`</dl>
        <p class="note">Every member is a catalog stock section, including the telescoping stages, so the list is orderable as written. Add your own kerf and squaring allowance.</p>`);
    }
    partHtml.push(`<p class="verify">${cad.caveat}</p>`);
  }
  const me = spec.mass_estimate;
  if (me){
    const rows = Object.entries(me).filter(([k,v])=>typeof v === 'number');
    if (rows.length) partHtml.push(`<h2>Mass estimate</h2><dl class="kv">`+
      rows.map(([k,v])=>row(k.replace(/_lb$/,'').replace(/_/g,' '), v+' lb')).join('')+`</dl>`);
  }
  if ((spec.design_notes||[]).length){
    partHtml.push(`<h2>Design notes</h2><ul class="sslist">`+
      spec.design_notes.slice(0,5).map(n=>`<li><span>${n}</span></li>`).join('')+`</ul>`);
  }
  if ((spec.risks||[]).length){
    partHtml.push(`<h2>Risks</h2><ul class="sslist">`+
      spec.risks.slice(0,4).map(n=>`<li><span>${n}</span></li>`).join('')+`</ul>`);
  }
  const tq = spec.techniques||[];
  if (tq.length){
    partHtml.push(`<h2>Build techniques (${tq.length})</h2><ul class="sslist">`+
      tq.slice(0,8).map(t=>`<li><b>${t.name}</b><span>${t.why||''}</span>
        ${t.how?`<span style="margin-top:4px"><b style="font-weight:600">How:</b> ${t.how}</span>`:''}
        ${t.pitfall?`<span style="margin-top:4px;color:var(--warning)"><b style="font-weight:600">Avoid:</b> ${t.pitfall}</span>`:''}</li>`).join('')+
      `</ul>${tq.length>8?`<p class="note">+ ${tq.length-8} more in the full package.</p>`:''}`);
  }
  partHtml.push(`<div class="verify">Nominal envelopes and published figures for packaging and first-order sizing — verify every part against the vendor drawing and the current game manual before fabrication. Deterministic synthesis; concept geometry, not native parametric CAD.</div>`);
  $('#dossier').innerHTML = partHtml.join('');
}

function wireControls(){
  const s = scene3d; if (!s || s._wired) return; s._wired = true;
  document.querySelectorAll('[data-view]').forEach(b=>b.addEventListener('click',()=>s.setView(b.dataset.view)));
  const bl=$('#b-labels'); bl.addEventListener('click',()=>{const on=s.toggleLabels();bl.classList.toggle('on',on);bl.textContent='Labels: '+(on?'on':'off');});
  const be=$('#b-explode'); be.addEventListener('click',()=>{const on=s.toggleExplode();be.classList.toggle('on',on);});
  const br=$('#b-run'); br.addEventListener('click',()=>{const on=s.toggleRun();br.classList.toggle('on',on);br.textContent=on?'Stop mechanisms':'Run mechanisms';});
  const cut=$('#cut'); cut.addEventListener('input',()=>{s.setCut(cut.value/100);$('#cutv').textContent=cut.value>0?cut.value+'%':'off';});
  // Clicking a part reports the feature the CAD tree actually holds for it.
  const picked=$('#picked');
  s.onPick((id,f)=>{
    document.querySelectorAll('[data-asm]').forEach(o=>o.classList.toggle('sel', o.dataset.asm===id));
    if(!f){picked.style.display='none';return;}
    const dims=[];
    if(f.sec) dims.push(f.sec[0]+'×'+f.sec[1]+' in, '+f.len.toFixed(2)+' in long');
    else if(f.size) dims.push(f.size.map(v=>+v.toFixed(2)).join(' × ')+' in');
    else if(f.dia) dims.push('Ø'+f.dia+' in'+(f.len?' × '+f.len+' in':''));
    if(f.teeth) dims.push(f.teeth+'T, PD '+f.pd+' in');
    if(f.bore) dims.push(f.bore+' in bore');
    picked.style.display='block';
    picked.innerHTML='<span>'+f.n+'</span><span>'+(dims.join(' · ')||f.t)+'</span>';
  });
}
</script>
</body>
</html>
"""
