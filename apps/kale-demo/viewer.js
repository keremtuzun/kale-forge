// Kale Forge — CAD-tree viewer.
//
// This renders `spec.cad` and nothing else. The spec's CAD tree is a list of assemblies,
// each a list of dimensioned features — tubes with a real section and wall, plates with
// pockets, hex shafts, flanged bearings, HTD pulleys, gears whose pitch diameter follows
// from the tooth count, belts, wheels, motors from the catalog envelope. Every feature
// carries its own centre and rotation in one coordinate system, so the picture you get is
// the geometry the BOM and the cut list describe, not an illustration of it.
//
// Coordinates match frc_cad.py: inches, +X robot right, +Y up, -Z toward the front,
// origin at the frame centre on the top face of the bellypan.
export function buildScene({ THREE, OrbitControls, CSS2DRenderer, CSS2DObject, RoomEnvironment, canvas }) {
  // preserveDrawingBuffer keeps the frame readable after the draw call, so the canvas can be
  // saved or captured as a PNG. It costs a little performance and buys the thing people
  // actually want from a design tool: a picture of their robot they can send someone.
  const renderer = new THREE.WebGLRenderer({ canvas, antialias: true, alpha: true,
                                             preserveDrawingBuffer: true });
  renderer.setPixelRatio(Math.min(devicePixelRatio, 2));
  renderer.shadowMap.enabled = true; renderer.shadowMap.type = THREE.PCFSoftShadowMap;
  renderer.toneMapping = THREE.ACESFilmicToneMapping; renderer.toneMappingExposure = 1.2;
  renderer.localClippingEnabled = true;

  const scene = new THREE.Scene();
  const pmrem = new THREE.PMREMGenerator(renderer);
  scene.environment = pmrem.fromScene(new RoomEnvironment(), 0.04).texture;

  const camera = new THREE.PerspectiveCamera(40, 1, 0.5, 6000);
  camera.position.set(30, 26, 38);

  const labelRenderer = new CSS2DRenderer();
  Object.assign(labelRenderer.domElement.style, { position: 'absolute', inset: '0', pointerEvents: 'none' });
  canvas.parentElement.appendChild(labelRenderer.domElement);

  const controls = new OrbitControls(camera, canvas);
  controls.enableDamping = true; controls.dampingFactor = 0.08;
  controls.minDistance = 6; controls.maxDistance = 400; controls.maxPolarAngle = Math.PI * 0.54;

  scene.add(new THREE.HemisphereLight(0xffffff, 0x2b3630, 0.66));
  const key = new THREE.DirectionalLight(0xffffff, 2.6);
  key.position.set(44, 70, 34); key.castShadow = true;
  key.shadow.mapSize.set(2048, 2048); key.shadow.bias = -0.0004;
  Object.assign(key.shadow.camera, { near: 10, far: 320, left: -90, right: 90, top: 90, bottom: -90 });
  scene.add(key);
  const rim = new THREE.DirectionalLight(0x9fd8bb, 0.75); rim.position.set(-34, 26, -38); scene.add(rim);
  const fill = new THREE.DirectionalLight(0xffe9c4, 0.35); fill.position.set(20, 10, -40); scene.add(fill);

  // A cutaway is a real section plane, not a fade: the slider sweeps it in from the right.
  const sectionPlane = new THREE.Plane(new THREE.Vector3(-1, 0, 0), 999);

  // ── materials ────────────────────────────────────────────────────────────────
  const mat = (o) => new THREE.MeshStandardMaterial({ clippingPlanes: [sectionPlane], ...o });
  const M = {
    aluminium: mat({ color: 0xb7bec4, metalness: 0.92, roughness: 0.33 }),
    'aluminium-dark': mat({ color: 0x8b9298, metalness: 0.9, roughness: 0.42 }),
    anodised: mat({ color: 0xcbb46a, metalness: 0.96, roughness: 0.28 }),
    steel: mat({ color: 0xc8ced4, metalness: 0.96, roughness: 0.22 }),
    black: mat({ color: 0x1a1c1e, metalness: 0.55, roughness: 0.5 }),
    'plastic-black': mat({ color: 0x232528, metalness: 0.15, roughness: 0.72 }),
    delrin: mat({ color: 0xe6e4dd, metalness: 0.05, roughness: 0.6 }),
    nylon: mat({ color: 0x30353a, metalness: 0.1, roughness: 0.7 }),
    urethane: mat({ color: 0x55595e, metalness: 0.1, roughness: 0.62 }),
    compliant: mat({ color: 0x2f9d5b, metalness: 0.08, roughness: 0.72 }),
    rubber: mat({ color: 0x161719, metalness: 0.18, roughness: 0.88 }),
    copper: mat({ color: 0xc47a3a, metalness: 0.9, roughness: 0.4 }),
    bolt: mat({ color: 0x6d747b, metalness: 0.95, roughness: 0.3 }),
    hub: mat({ color: 0x9aa0a6, metalness: 0.9, roughness: 0.35 }),
    pocket: mat({ color: 0x363b3a, metalness: 0.6, roughness: 0.55 }),
    orange: mat({ color: 0xff6a13, metalness: 0.4, roughness: 0.45, emissive: 0x3a1500 }),
    redWire: mat({ color: 0xd23b30, metalness: 0.1, roughness: 0.6 }),
    blackWire: mat({ color: 0x0f1011, metalness: 0.1, roughness: 0.6 }),
    rope: mat({ color: 0xd8d4c6, metalness: 0.05, roughness: 0.85 }),
    led: mat({ color: 0x35d67a, emissive: 0x1c8f4c, emissiveIntensity: 1.3, roughness: 0.4 }),
    polycarb: mat({ color: 0xbfd8cc, metalness: 0.08, roughness: 0.16, transparent: true, opacity: 0.28 }),
    // Bumper colour comes from the design (spec.bumper_color), red unless the prompt says
    // otherwise. The fabric reads matte.
    bumper: mat({ color: 0xc0392b, metalness: 0.02, roughness: 0.92 }),
    plywood: mat({ color: 0xc9a876, metalness: 0.0, roughness: 0.85 }),
    foam: mat({ color: 0xe4e0d2, metalness: 0.0, roughness: 0.95 }),
    envelope: new THREE.MeshStandardMaterial({ color: 0x6fc093, wireframe: true, transparent: true, opacity: 0.5 }),
  };
  // Electrical components keep their real-world colours so the layout is readable at a glance.
  const ELEC = {
    pdh: mat({ color: 0x14171b, metalness: 0.5, roughness: 0.5 }),
    pdp: mat({ color: 0x14171b, metalness: 0.5, roughness: 0.5 }),
    battery: mat({ color: 0x26292d, metalness: 0.3, roughness: 0.6 }),
    main_breaker: mat({ color: 0xc0392b, metalness: 0.25, roughness: 0.55 }),
    sb50: mat({ color: 0xd8332a, metalness: 0.3, roughness: 0.5 }),
    rio: mat({ color: 0x1d2024, metalness: 0.45, roughness: 0.5 }),
  };
  const DOT = {
    chassis: '#b8bec2', drivetrain: '#ff6a13', intake: '#e3bb62', shooter: '#8bd0a9',
    elevator: '#6fc093', manipulator: '#a9d0ff', climber: '#e08a5b', electrical: '#2aa0ff',
  };

  // Material routing. A declared material always wins (plywood must stay plywood even when
  // the part is named "front bumper plywood"); the /bumper/ name fallback only catches parts
  // with no material of their own, so legacy fabric-less bumpers still read as bumpers.
  const M_OF = (name, featureName) => (
    (name && M[name]) || (/bumper/.test(featureName || '') ? M.bumper : M.aluminium));
  const box = (w, h, d, m) => new THREE.Mesh(new THREE.BoxGeometry(w, h, d), m);
  const cyl = (rt, rb, h, m, s = 32) => new THREE.Mesh(new THREE.CylinderGeometry(rt, rb, h, s), m);
  const D2R = Math.PI / 180;

  // ── reusable part shapes ─────────────────────────────────────────────────────
  // A plate is extruded from a rounded outline with real pocket holes cut through it —
  // the pockets are geometry, not a painted-on texture.
  function makePlate(w, t, d, m, pockets) {
    const r = Math.min(0.35, w * 0.12, d * 0.12);
    const shape = new THREE.Shape();
    const hw = w / 2, hd = d / 2;
    shape.moveTo(-hw + r, -hd);
    shape.lineTo(hw - r, -hd); shape.quadraticCurveTo(hw, -hd, hw, -hd + r);
    shape.lineTo(hw, hd - r); shape.quadraticCurveTo(hw, hd, hw - r, hd);
    shape.lineTo(-hw + r, hd); shape.quadraticCurveTo(-hw, hd, -hw, hd - r);
    shape.lineTo(-hw, -hd + r); shape.quadraticCurveTo(-hw, -hd, -hw + r, -hd);
    if (pockets > 0 && w > 3.2 && d > 3.2 && pockets >= 6) {
      // A CNC-routed pocket grid, the way a real bellypan is machined: rounded-square
      // pockets in a regular grid with uniform webs between them and a solid margin at the
      // rails — not a scatter of circles. The web and margin are fixed machining numbers;
      // the pocket size falls out of the plate.
      const margin = Math.min(1.3, Math.max(0.7, Math.min(w, d) * 0.06));
      const web = 0.55;
      const availW = w - margin * 2, availD = d - margin * 2;
      const target = Math.max(1.7, Math.sqrt((availW * availD) / pockets));
      const cols = Math.max(1, Math.round((availW + web) / (target + web)));
      const rows = Math.max(1, Math.round((availD + web) / (target + web)));
      const pw = (availW - (cols - 1) * web) / cols;
      const pd = (availD - (rows - 1) * web) / rows;
      if (pw > 0.8 && pd > 0.8) {
        const cr = Math.min(0.45, pw / 3, pd / 3);   // the router's corner radius
        for (let i = 0; i < cols; i++) {
          for (let j = 0; j < rows; j++) {
            const cx = -availW / 2 + i * (pw + web) + pw / 2;
            const cz = -availD / 2 + j * (pd + web) + pd / 2;
            const x0 = cx - pw / 2, x1 = cx + pw / 2, z0 = cz - pd / 2, z1 = cz + pd / 2;
            const hole = new THREE.Path();
            hole.moveTo(x0 + cr, z0);
            hole.lineTo(x1 - cr, z0); hole.absarc(x1 - cr, z0 + cr, cr, -Math.PI / 2, 0, false);
            hole.lineTo(x1, z1 - cr); hole.absarc(x1 - cr, z1 - cr, cr, 0, Math.PI / 2, false);
            hole.lineTo(x0 + cr, z1); hole.absarc(x0 + cr, z1 - cr, cr, Math.PI / 2, Math.PI, false);
            hole.lineTo(x0, z0 + cr); hole.absarc(x0 + cr, z0 + cr, cr, Math.PI, Math.PI * 1.5, false);
            shape.holes.push(hole);
          }
        }
      }
    } else if (pockets > 0) {
      // Small plates (module plates, mounts) keep a modest bolt-circle of round lightening
      // holes — at this scale that is what real plates use.
      const cols = Math.max(1, Math.round(Math.sqrt(pockets * w / Math.max(d, 0.01))));
      const rows = Math.max(1, Math.round(pockets / cols));
      const cw = w / cols, cd = d / rows;
      const pr = Math.max(0.16, Math.min(cw, cd) * 0.26);
      for (let i = 0; i < cols; i++) {
        for (let j = 0; j < rows; j++) {
          const cx = -hw + (i + 0.5) * cw, cz = -hd + (j + 0.5) * cd;
          if (Math.abs(cx) > hw - pr - 0.3 || Math.abs(cz) > hd - pr - 0.3) continue;
          const hole = new THREE.Path();
          hole.absarc(cx, cz, pr, 0, Math.PI * 2, true);
          shape.holes.push(hole);
        }
      }
    }
    const geo = new THREE.ExtrudeGeometry(shape, { depth: t, bevelEnabled: false, steps: 1 });
    geo.rotateX(-Math.PI / 2); geo.translate(0, t / 2, 0); geo.center();
    return new THREE.Mesh(geo, m);
  }

  // A tube is drawn as a real section: four walls, so a cutaway shows it is hollow.
  function makeTube(w, h, len, wall, m, boltPitch, pockets) {
    const g = new THREE.Group();
    const t = Math.max(wall, 0.04);
    const top = box(w, t, len, m); top.position.y = h / 2 - t / 2;
    const bot = box(w, t, len, m); bot.position.y = -h / 2 + t / 2;
    const left = box(t, h - t * 2, len, m); left.position.x = -w / 2 + t / 2;
    const right = box(t, h - t * 2, len, m); right.position.x = w / 2 - t / 2;
    g.add(top, bot, left, right);
    // End caps close the section so the tube reads as stock, not as an open channel.
    for (const sz of [-1, 1]) {
      const cap = box(w, h, t * 0.6, M['aluminium-dark']);
      cap.position.z = sz * (len / 2 - t * 0.3); g.add(cap);
    }
    // The bolt pattern and the lightening pockets are the two hole rows that make a piece of
    // FRC tube recognisable: fastener holes on the pitch, big pockets between them.
    const holeRow = (pitch, radius, inset) => {
      if (!(pitch > 0) || len < pitch + inset * 2) return;
      const n = Math.floor((len - inset * 2) / pitch);
      for (let i = 0; i <= n; i++) {
        const hole = cyl(radius, radius, w + 0.03, M.pocket, radius > 0.2 ? 16 : 10);
        hole.rotation.z = Math.PI / 2;
        hole.position.z = -(n * pitch) / 2 + i * pitch;
        g.add(hole);
      }
    };
    holeRow(boltPitch, 0.10, 1.0);
    if (pockets) holeRow(2.6, Math.min(h * 0.30, 0.55), 1.6);
    return g;
  }

  function makeGear(teeth, pitchR, thickness, m, boreR = 0.28, pockets = 0) {
    const shape = new THREE.Shape();
    const add = pitchR * 0.085, ded = pitchR * 0.11, ro = pitchR + add, rp = pitchR, rr = pitchR - ded;
    const step = (Math.PI * 2) / teeth, tTip = step * 0.17, tPit = step * 0.26, tRoot = step * 0.30;
    const p = (a, r) => [Math.cos(a) * r, Math.sin(a) * r];
    let first = true;
    for (let i = 0; i < teeth; i++) {
      const a = i * step;
      for (const pt of [p(a - tRoot, rr), p(a - tPit, rp), p(a - tTip, ro), p(a + tTip, ro),
                        p(a + tPit, rp), p(a + tRoot, rr), p(a + step * 0.5 - tRoot, rr)]) {
        if (first) { shape.moveTo(pt[0], pt[1]); first = false; } else shape.lineTo(pt[0], pt[1]);
      }
    }
    shape.closePath();
    const bore = new THREE.Path(); bore.absarc(0, 0, Math.max(boreR, 0.05), 0, Math.PI * 2, true);
    shape.holes.push(bore);
    if (pockets > 0) {
      const pr = (boreR + ro) * 0.46, hr = (ro - boreR) * 0.30;
      for (let i = 0; i < pockets; i++) {
        const a = (i / pockets) * Math.PI * 2 + step * 0.5;
        const h = new THREE.Path(); h.absarc(Math.cos(a) * pr, Math.sin(a) * pr, hr, 0, Math.PI * 2, true);
        shape.holes.push(h);
      }
    }
    const geo = new THREE.ExtrudeGeometry(shape, { depth: thickness, bevelEnabled: true, bevelThickness: 0.012, bevelSize: 0.016, bevelSegments: 2, steps: 1 });
    geo.center(); geo.rotateX(Math.PI / 2);
    const g = new THREE.Group(); g.add(new THREE.Mesh(geo, m));
    if (boreR < pitchR * 0.45) {
      const hr = boreR + Math.min(0.22, pitchR * 0.18);
      const hub = cyl(hr, hr, thickness * 1.9, M.hub, 20); g.add(hub);
    }
    return g;
  }

  // HTD pulley: a flanged body with real tooth grooves round the pitch diameter.
  function makePulley(teeth, pd, width, boreR) {
    const g = new THREE.Group();
    const r = pd / 2;
    g.add(cyl(r, r, width, M['aluminium-dark'], 40));
    for (let i = 0; i < teeth; i++) {
      const a = (i / teeth) * Math.PI * 2;
      const groove = box(0.055, width * 0.94, 0.055, M.pocket);
      groove.position.set(Math.cos(a) * r, 0, Math.sin(a) * r);
      groove.rotation.y = -a; g.add(groove);
    }
    for (const sy of [-1, 1]) {
      const fl = cyl(r + 0.09, r + 0.09, 0.05, M.hub, 40);
      fl.position.y = sy * width / 2; g.add(fl);
    }
    const bore = cyl(boreR * 0.72, boreR * 0.72, width * 1.4, M.pocket, 6);
    g.add(bore);
    return g;
  }

  // A flanged bearing: outer race, inner race, ball track and the flange that locates it.
  function makeBearing(bore, od, width, flanged) {
    const g = new THREE.Group();
    const outer = cyl(od / 2, od / 2, width, M.steel, 30);
    const inner = cyl(bore / 2 + 0.03, bore / 2 + 0.03, width * 1.06, M.pocket, 24);
    const race = cyl(od / 2 - 0.07, od / 2 - 0.07, width * 0.55, M['aluminium-dark'], 28);
    g.add(outer, inner, race);
    if (flanged) {
      const fl = cyl(od / 2 + 0.11, od / 2 + 0.11, width * 0.22, M.steel, 30);
      fl.position.y = width / 2; g.add(fl);
    }
    return g;
  }

  // Hex shafts are hexagonal. It reads instantly as FRC stock and it is what the spec says.
  function makeShaft(dia, len, form) {
    const sides = form === 'hex' ? 6 : (form === 'spline' ? 20 : 24);
    const m = cyl(dia / 2, dia / 2, len, M.steel, sides);
    if (form === 'spline') {
      const g = new THREE.Group(); g.add(m);
      for (let i = 0; i < 12; i++) {
        const a = (i / 12) * Math.PI * 2;
        const s = box(0.028, len * 0.98, 0.028, M.steel);
        s.position.set(Math.cos(a) * dia / 2, 0, Math.sin(a) * dia / 2); s.rotation.y = -a; g.add(s);
      }
      return g;
    }
    return m;
  }

  // Kraken/NEO-class motor from the catalog envelope, centred on its own origin so the
  // CAD position is the motor's centre like every other feature.
  function makeMotor(dia, len) {
    const g = new THREE.Group();
    const R = dia / 2, bodyH = len - 0.75;
    const can = cyl(R, R, bodyH, M.black, 44); g.add(can);
    for (let i = 0; i < 26; i++) {
      const a = (i / 26) * Math.PI * 2;
      const f = box(0.05, bodyH * 0.9, 0.045, M['plastic-black']);
      f.position.set(Math.cos(a) * R, 0, Math.sin(a) * R); f.rotation.y = -a; g.add(f);
    }
    const band = cyl(R + 0.05, R + 0.05, 0.3, M.orange, 44);
    band.position.y = -bodyH / 2 + 0.42; g.add(band);
    const flange = cyl(R, R, 0.15, M['aluminium-dark'], 44); flange.position.y = -bodyH / 2 - 0.07; g.add(flange);
    const boss = cyl(0.38, 0.38, 0.16, M.hub, 24); boss.position.y = -bodyH / 2 - 0.2; g.add(boss);
    const shaft = cyl(0.175, 0.175, 0.55, M.steel, 12); shaft.position.y = -bodyH / 2 - 0.5; g.add(shaft);
    for (let i = 0; i < 5; i++) {
      const fin = cyl(R * 0.8, R * 0.8, 0.045, M['aluminium-dark'], 36);
      fin.position.y = bodyH / 2 + 0.1 + i * 0.085; g.add(fin);
    }
    const conn = box(0.46, 0.26, 0.32, M['plastic-black']);
    conn.position.set(R * 0.55, bodyH / 2 + 0.2, 0); g.add(conn);
    const led = new THREE.Mesh(new THREE.SphereGeometry(0.065, 10, 10), M.led);
    led.position.set(-R * 0.5, bodyH / 2 + 0.26, 0); g.add(led);
    return g;
  }

  function makeGearbox(w, h, d, stages) {
    const g = new THREE.Group();
    g.add(box(w, h, d, M['aluminium-dark']));
    for (const sz of [-1, 1]) {
      const plate = makePlate(w * 1.02, 0.12, h * 1.02, M.aluminium, 0);
      plate.rotation.x = Math.PI / 2; plate.position.z = sz * d / 2; g.add(plate);
    }
    for (let i = 0; i < Math.max(1, stages); i++) {
      const ring = cyl(Math.min(w, h) * 0.20, Math.min(w, h) * 0.20, d * 1.04, M.hub, 18);
      ring.rotation.x = Math.PI / 2;
      ring.position.set((i - (stages - 1) / 2) * w * 0.28, (i % 2 ? 1 : -1) * h * 0.16, 0);
      g.add(ring);
    }
    for (const sx of [-1, 1]) for (const sy of [-1, 1]) {
      const b = cyl(0.075, 0.075, d * 1.1, M.bolt, 6); b.rotation.x = Math.PI / 2;
      b.position.set(sx * (w / 2 - 0.28), sy * (h / 2 - 0.28), 0); g.add(b);
    }
    return g;
  }

  function makeWheel(dia, width, kind, spinList, name) {
    const g = new THREE.Group();
    const r = dia / 2;
    if (kind === 'compliant') {
      g.add(cyl(r, r, width, M.compliant, 30));
      const hub = cyl(r * 0.36, r * 0.36, width * 1.05, M.hub, 16); g.add(hub);
      for (let i = 0; i < 8; i++) {          // the moulded relief slots of a compliant wheel
        const a = (i / 8) * Math.PI * 2;
        const slot = box(r * 0.5, width * 1.02, 0.10, M.pocket);
        slot.position.set(Math.cos(a) * r * 0.62, 0, Math.sin(a) * r * 0.62);
        slot.rotation.y = -a + 0.6; g.add(slot);
      }
    } else if (kind === 'urethane') {
      g.add(cyl(r, r, width, M.urethane, 30));
      g.add(cyl(r * 0.42, r * 0.42, width * 1.06, M.anodised, 18));
    } else {
      g.add(cyl(r, r, width, M.rubber, 36));
      for (let i = 0; i < 20; i++) {         // tread bars
        const a = (i / 20) * Math.PI * 2;
        const bar = box(0.075, width * 0.98, 0.12, M.black);
        bar.position.set(Math.cos(a) * (r - 0.02), 0, Math.sin(a) * (r - 0.02));
        bar.rotation.y = -a; g.add(bar);
      }
      const hub = makePlate(r * 1.0, width * 1.02, r * 1.0, M.hub, 4);
      hub.rotation.x = Math.PI / 2; g.add(hub);
    }
    // Compliant (green) rollers do not free-spin 360° in the animation — their motion is the
    // intake's limited deploy arc toward the front, driven by the assembly's articulation.
    // Flywheels and treaded drive wheels genuinely spin, so they keep their spinners.
    if (kind !== 'compliant') {
      spinList.push({ obj: g, axis: 'y', speed: (name && /flywheel/.test(name)) ? 9 : 3.2 });
    }
    return g;
  }

  // ── feature dispatch ─────────────────────────────────────────────────────────
  // One builder per CAD feature type. Anything unknown falls through to a labelled box
  // rather than disappearing, so a schema addition is visible instead of silent.
  // The same policy covers malformed features: one bad part in a 200-feature robot is a
  // defect to look at, never a TypeError that blanks the whole viewer.
  function unmodelledBox(f, m) {
    const ok = v => Array.isArray(v) && v.length === 3 && v.every(Number.isFinite);
    const s = ok(f.size) ? f.size : (f.dia ? [f.dia, f.len || f.dia, f.dia] : [1, 1, 1]);
    return box(Math.max(s[0], 0.2), Math.max(s[1], 0.2), Math.max(s[2], 0.2), m);
  }
  function buildFeature(f, spin) {
    const m = M_OF(f.mat, f.n);
    try {
      return buildFeatureOf(f, spin, m);
    } catch (err) {
      console.warn('kale viewer: could not build', f && f.t, f && f.n, err);
      return unmodelledBox(f, m);
    }
  }
  function buildFeatureOf(f, spin, m) {
    switch (f.t) {
      case 'tube': {
        const g = makeTube(f.sec[1], f.sec[0], f.len, f.wall || 0.1, m, f.bolt_pitch || 0, f.pockets);
        return g;
      }
      case 'plate':
        return makePlate(f.size[0], f.size[1], f.size[2], m, f.pockets || 0);
      case 'gusset': {
        const g = new THREE.Group();
        const tri = new THREE.Shape();
        tri.moveTo(0, 0); tri.lineTo(f.size[0], 0); tri.lineTo(0, f.size[1]); tri.closePath();
        const geo = new THREE.ExtrudeGeometry(tri, { depth: f.th || 0.09, bevelEnabled: false });
        geo.rotateX(-Math.PI / 2); geo.center();
        g.add(new THREE.Mesh(geo, M.aluminium));
        return g;
      }
      case 'shaft':
        return makeShaft(f.dia, f.len, f.form);
      case 'bearing':
        return makeBearing(f.bore, f.od, f.w, f.flanged);
      case 'pulley':
        return makePulley(f.teeth, f.pd, f.w, f.bore);
      case 'sprocket':
        return makeGear(f.teeth, f.pd / 2, f.w, M.steel, Math.max(f.pd / 2 - 0.5, 0.12), 4);
      case 'gear': {
        const g = makeGear(f.teeth, f.pd / 2, f.face, M_OF(f.mat === 'steel' ? 'steel' : 'anodised'),
                           f.bore / 2, f.pockets || 0);
        spin.push({ obj: g, axis: 'y', speed: 24 / Math.max(f.teeth, 6) });
        return g;
      }
      case 'bevel': {
        const g = new THREE.Group();
        g.add(cyl(f.pd * 0.28, f.pd * 0.5, f.pd * 0.34, M.anodised, 30));
        const ring = makeGear(f.teeth, f.pd / 2, f.face, M.anodised, f.pd * 0.22);
        ring.scale.set(1, 0.62, 1); ring.position.y = f.pd * 0.2; g.add(ring);
        spin.push({ obj: g, axis: 'y', speed: 3 });
        return g;
      }
      case 'wheel':
        return makeWheel(f.dia, f.w, f.kind, spin, f.n);
      case 'noodle': {
        // A pool noodle: foam cylinder along local Y (the shaft convention — the feature's
        // rot lays it along its segment), with the moulded centre bore visible at the ends.
        const g = new THREE.Group();
        g.add(cyl(f.dia / 2, f.dia / 2, f.len, M.foam, 26));
        g.add(cyl(f.dia * 0.14, f.dia * 0.14, f.len * 1.004, M.pocket, 12));
        return g;
      }
      case 'fabric': {
        // The bumper wrap: an outer skin with top and bottom returns — a U-channel of cloth
        // closing over the noodle stack. size = [length, height, wrapped depth]; the skin
        // faces local +Z and the segment's rot turns it outward.
        const g = new THREE.Group();
        const [len, h, d] = f.size, skin = 0.05;
        const face = box(len, h, skin, M.bumper); face.position.z = d / 2 - skin / 2; g.add(face);
        for (const sy of [-1, 1]) {
          const ret = box(len, skin, d, M.bumper); ret.position.y = sy * (h / 2 - skin / 2); g.add(ret);
        }
        for (const sx of [-1, 1]) {          // end closures so a corner never shows raw foam
          const end = box(skin, h, d, M.bumper); end.position.x = sx * (len / 2 - skin / 2); g.add(end);
        }
        return g;
      }
      case 'motor':
        return makeMotor(f.dia, f.len);
      case 'gearbox':
        return makeGearbox(f.size[0], f.size[1], f.size[2], f.stages || 2);
      case 'standoff':
        return cyl(f.dia / 2, f.dia / 2, f.len, M['aluminium-dark'], 12);
      case 'polycarb':
        return box(f.size[0], f.size[1], f.size[2], M.polycarb);
      case 'hardstop':
        return box(f.size[0], f.size[1], f.size[2], M.steel);
      case 'hook': {
        const g = new THREE.Group();
        g.add(box(f.size[0], f.size[1] * 0.4, f.size[2], M.steel));
        const barb = box(f.size[0], f.size[1], f.size[0], M.steel);
        barb.position.set(0, -f.size[1] * 0.5, f.size[2] * 0.42); g.add(barb);
        return g;
      }
      case 'drum': {
        const g = new THREE.Group();
        g.add(cyl(f.dia / 2, f.dia / 2, f.w, M.hub, 26));
        for (let i = 0; i < 9; i++) {        // the helical rope groove
          const ring = new THREE.Mesh(new THREE.TorusGeometry(f.dia / 2 + 0.01, 0.028, 6, 24), M.rope);
          ring.rotation.x = Math.PI / 2; ring.position.y = -f.w / 2 + 0.18 + i * (f.w - 0.36) / 8;
          g.add(ring);
        }
        for (const sy of [-1, 1]) {
          const fl = cyl(f.dia / 2 + 0.16, f.dia / 2 + 0.16, 0.08, M['aluminium-dark'], 26);
          fl.position.y = sy * f.w / 2; g.add(fl);
        }
        spin.push({ obj: g, axis: 'y', speed: 1.6 });
        return g;
      }
      case 'pawl':
        return box(f.size[0], f.size[1], f.size[2], M.steel);
      case 'slide':                        // a plastic wear pad instead of a bearing block
        return box(f.size[0], f.size[1], f.size[2], M.delrin);
      case 'brake': {
        const g = new THREE.Group();
        g.add(cyl(f.dia / 2, f.dia / 2, f.w, M.steel, 28));
        const caliper = box(f.dia * 0.35, f.dia * 0.5, f.w * 2.4, M['aluminium-dark']);
        caliper.position.y = f.dia * 0.42; g.add(caliper);
        return g;
      }
      case 'actuator': {
        const g = new THREE.Group();
        g.add(box(f.size[0], f.size[1], f.size[2], M['plastic-black']));
        const rod = cyl(0.08, 0.08, f.size[0] * 0.8, M.steel, 12);
        rod.rotation.z = Math.PI / 2; rod.position.x = f.size[0] * 0.8; g.add(rod);
        return g;
      }
      case 'tensioner': {
        const g = new THREE.Group();
        g.add(cyl(f.dia / 2, f.dia / 2, f.w, M.hub, 20));
        g.add(cyl(f.dia / 2 + 0.07, f.dia / 2 + 0.07, 0.05, M['aluminium-dark'], 20));
        return g;
      }
      case 'sensor': {
        // A beam-break is two small blocks facing each other across the lane. It is drawn
        // because a sensor you did not package is a sensor you zip-tie on at the event.
        const g = new THREE.Group();
        g.add(box(f.size[0], f.size[1], f.size[2], M['plastic-black']));
        const lens = cyl(f.size[0] * 0.22, f.size[0] * 0.22, f.size[2] * 0.3, M.copper, 10);
        lens.rotation.x = Math.PI / 2; lens.position.z = f.size[2] * 0.4; g.add(lens);
        return g;
      }
      case 'chain_track': {
        // Energy chain: a run of links, drawn as segments so the sweep volume is visible.
        const g = new THREE.Group();
        const links = Math.max(4, Math.round(f.size[2] / 0.5));
        for (let i = 0; i < links; i++) {
          const link = box(f.size[0], f.size[1], f.size[2] / links * 0.7, M['plastic-black']);
          link.position.z = -f.size[2] / 2 + (i + 0.5) * (f.size[2] / links);
          g.add(link);
        }
        return g;
      }
      case 'hood': {
        const geo = new THREE.CylinderGeometry(f.r, f.r, f.w, 26, 1, true, 0, Math.PI);
        const hood = new THREE.Mesh(geo, mat({ color: 0x9aa0a6, metalness: 0.86, roughness: 0.34, side: THREE.DoubleSide }));
        hood.rotation.z = Math.PI / 2;
        return hood;
      }
      case 'envelope':
        return new THREE.Mesh(new THREE.BoxGeometry(f.size[0], f.size[1], f.size[2]), M.envelope);
      case 'bolts': {
        const g = new THREE.Group();
        const n = (f.rep && f.rep.n) || 1, step = (f.rep && f.rep.step) || [0, 0, 0];
        for (let i = 0; i < n; i++) {
          const b = cyl(f.dia / 2, f.dia / 2, f.len, M.bolt, 6);
          const head = cyl(f.dia * 0.85, f.dia * 0.85, f.len * 0.22, M.bolt, 6);
          head.position.y = f.len * 0.45;
          const one = new THREE.Group(); one.add(b, head); one.rotation.x = Math.PI / 2;
          const wrap = new THREE.Group(); wrap.add(one);
          wrap.position.set((i - (n - 1) / 2) * step[0], (i - (n - 1) / 2) * step[1], (i - (n - 1) / 2) * step[2]);
          g.add(wrap);
        }
        return g;
      }
      case 'component': {
        const g = new THREE.Group();
        const material = ELEC[f.key] || M['plastic-black'];
        g.add(box(f.size[0], f.size[1], f.size[2], material));
        if (f.key === 'battery') {
          for (const sx of [-1, 1]) {
            const t = cyl(0.26, 0.26, 0.36, sx < 0 ? M.redWire : M.blackWire, 12);
            t.position.set(sx * f.size[0] * 0.28, f.size[1] / 2 + 0.18, f.size[2] * 0.26); g.add(t);
          }
        }
        if (f.key === 'main_breaker') {
          const btn = cyl(0.28, 0.28, 0.28, M.black, 16); btn.position.y = f.size[1] / 2 + 0.14; g.add(btn);
        }
        if (f.key === 'pdh' || f.key === 'pdp') {
          for (let i = 0; i < 10; i++) {          // the channel terminal strip
            const term = box(f.size[0] * 0.05, 0.09, f.size[2] * 0.3, M.copper);
            term.position.set(-f.size[0] * 0.42 + i * f.size[0] * 0.093, f.size[1] / 2 + 0.03, f.size[2] * 0.3);
            g.add(term);
          }
        }
        return g;
      }
      default:
        console.warn('kale viewer: unmodelled feature type', f.t, f.n);
        return unmodelledBox(f, m);
    }
  }

  // Team numbers go on all four bumpers. They are decals, not parts, so they are drawn here
  // from spec.team_number rather than carried in the CAD tree — putting them in the geometry
  // would change every chassis assembly the design model was trained on.
  // The plane is sized to the digits and the canvas is sized to the plane, at one pixel
  // density. Any mismatch between those two aspect ratios shows up as stretched type, which
  // is what a 2:1 canvas mapped onto a 7:1 plane was doing.
  function makeBumperNumber(number, bumperColor) {
    const text = String(number);
    const DIGIT_W = 1.30, HEIGHT = 1.95;     // inches; a tall digit on a 2.5 in bumper face
    const width = text.length * DIGIT_W;
    const PX = 128;
    const canvas = document.createElement('canvas');
    canvas.width = Math.round(width * PX);
    canvas.height = Math.round(HEIGHT * PX);
    const ctx = canvas.getContext('2d');
    ctx.fillStyle = bumperColor; ctx.fillRect(0, 0, canvas.width, canvas.height);
    // Condensed weights are what teams actually cut; it also keeps four digits inside the
    // panel without squeezing the glyphs.
    ctx.font = `bold ${Math.round(canvas.height * 0.78)}px "Helvetica Neue", Inter, Arial, sans-serif`;
    ctx.textAlign = 'center'; ctx.textBaseline = 'middle';
    ctx.fillStyle = readableOn(bumperColor);
    ctx.fillText(text, canvas.width / 2, canvas.height / 2 + canvas.height * 0.03,
                 canvas.width * 0.94);
    const texture = new THREE.CanvasTexture(canvas);
    texture.anisotropy = 8; texture.colorSpace = THREE.SRGBColorSpace;
    return new THREE.Mesh(
      new THREE.PlaneGeometry(width, HEIGHT),
      new THREE.MeshBasicMaterial({ map: texture, clippingPlanes: [sectionPlane] }));
  }

  // White on a dark bumper, near-black on a light one. A fixed white number disappears on
  // white or yellow bumpers, which teams do run.
  function readableOn(hex) {
    const c = new THREE.Color(hex);
    const luma = 0.2126 * c.r + 0.7152 * c.g + 0.0722 * c.b;
    return luma > 0.6 ? '#15181b' : '#ffffff';
  }

  // The numbers ride on the outer face of the bumper, so they follow the bumper's real
  // thickness and height rather than the constants the first 0.75 in backing plate happened
  // to have. A team number floating inside the noodle is the failure this avoids.
  function addBumperNumbers(group, number, colour, W, L, bumper) {
    const t = (bumper && bumper.thickness_in) || 3.31;
    const h = (bumper && bumper.height_in) || 5.0;
    const halfW = W / 2 + t, halfL = L / 2 + t;
    const face = 0.02;                       // just proud of the bumper so it never z-fights
    const y = h / 2;
    const places = [
      { at: [0, y, -(halfL + face)], rot: [0, Math.PI, 0] },
      { at: [0, y, halfL + face], rot: [0, 0, 0] },
      { at: [-(halfW + face), y, 0], rot: [0, -Math.PI / 2, 0] },
      { at: [halfW + face, y, 0], rot: [0, Math.PI / 2, 0] },
    ];
    for (const p of places) {
      const mesh = makeBumperNumber(number, colour);
      mesh.position.set(p.at[0], p.at[1], p.at[2]);
      mesh.rotation.set(p.rot[0], p.rot[1], p.rot[2]);
      mesh.userData.part = 'chassis';
      group.add(mesh);
    }
  }

  // Belts, cables and ropes connect two points, so they are placed by their endpoints
  // rather than by a centre and a rotation.
  function buildLink(f) {
    const a = new THREE.Vector3(f.at[0], f.at[1], f.at[2]);
    const b = new THREE.Vector3(f.to[0], f.to[1], f.to[2]);
    if (f.t === 'belt') {
      const g = new THREE.Group();
      const dir = b.clone().sub(a);
      const len = dir.length();
      if (len < 0.01) return null;
      // A belt is a loop: two runs on either side of the pulley centres, not a single bar.
      // The offset has to be perpendicular to the run, or a horizontal belt separates along
      // the wrong axis and reads as one thick bar.
      const axis = dir.clone().normalize();
      const up = Math.abs(axis.y) > 0.9 ? new THREE.Vector3(0, 0, 1) : new THREE.Vector3(0, 1, 0);
      const perp = new THREE.Vector3().crossVectors(axis, up).normalize();
      const gap = ((f.w || 0.35) * 1.6) / 2;
      const mid = a.clone().lerp(b, 0.5);
      for (const side of [-1, 1]) {
        const run = box(0.10, len, 0.05 + (f.w || 0.35), /chain/.test(f.kind || '') ? M.steel : M.black);
        run.position.copy(mid).addScaledVector(perp, side * gap);
        run.quaternion.setFromUnitVectors(new THREE.Vector3(0, 1, 0), axis);
        g.add(run);
      }
      return g;
    }
    const material = f.t === 'rope' ? M.rope : (f.polarity === '-' ? M.blackWire : M.redWire);
    const radius = f.t === 'rope' ? (f.dia || 0.125) / 2 : (f.dia || 0.26) / 2;
    // Cables lie on the bellypan; a deep sag put them below the pan and through the floor.
    const mid = a.clone().lerp(b, 0.5); mid.y -= Math.min(0.35, a.distanceTo(b) * 0.05);
    const curve = new THREE.QuadraticBezierCurve3(a, mid, b);
    return new THREE.Mesh(new THREE.TubeGeometry(curve, 18, radius, 8, false), material);
  }

  // ── world ────────────────────────────────────────────────────────────────────
  let world = new THREE.Group(); scene.add(world);
  let assemblies = [];     // {id, group, base, explode, label}
  let spinners = [];
  let artics = [];         // {group, deg:[min,max], phase} — limited-arc mechanism motion
  let clickable = [];
  const ground = new THREE.Mesh(new THREE.PlaneGeometry(700, 700), new THREE.ShadowMaterial({ opacity: 0.2 }));
  ground.rotation.x = -Math.PI / 2; ground.position.y = -0.7; ground.receiveShadow = true; scene.add(ground);

  function clearWorld() {
    // Floating assembly labels were removed; the traversal stays because a CSS2D object from
    // an older cached build would otherwise outlive the group it belonged to.
    world.traverse(o => {
      if (o.isCSS2DObject && o.element && o.element.parentNode) o.element.parentNode.removeChild(o.element);
      if (o.isMesh && o.geometry) o.geometry.dispose();
    });
    scene.remove(world);
    while (labelRenderer.domElement.firstChild) labelRenderer.domElement.removeChild(labelRenderer.domElement.firstChild);
    world = new THREE.Group(); scene.add(world);
    assemblies = []; spinners = []; artics = []; clickable = []; origMats.clear(); selected = null;
  }

  function load(spec) {
    clearWorld();
    const cad = spec && spec.cad;
    if (!cad || !cad.assemblies) { frameView(); return; }
    const frame = spec.frame || {};
    const W = frame.width_in || 28, L = frame.length_in || 28;
    const bumperHex = (spec.bumper_color && spec.bumper_color.hex) || '#c0392b';
    M.bumper.color.set(bumperHex);
    let tallest = 12;

    cad.assemblies.forEach(asm => {
      const group = new THREE.Group();
      const org = (Array.isArray(asm.origin) && asm.origin.length === 3) ? asm.origin : [0, 0, 0];
      group.position.set(org[0], org[1], org[2]);
      const spin = [];
      let top = 0;

      // A mechanism with an articulation block moves as a rigid body about a real axle over
      // a limited arc — the intake deploying toward the front, never a 360° spin. Moving
      // features go into a pivot subgroup centred on the axle; parts named in `static`
      // (towers, bearings, the dead axle itself) stay on the frame.
      const art = asm.articulation;
      let pivotGroup = null, staticNames = null;
      if (art && art.type === 'pivot') {
        pivotGroup = new THREE.Group();
        pivotGroup.position.set(art.at[0], art.at[1], art.at[2]);
        group.add(pivotGroup);
        staticNames = new Set(art.static || []);
        artics.push({ group: pivotGroup, deg: art.deg || [0, 60], phase: Math.random() * 0.4 });
      }
      const attach = (obj, f) => {
        const moving = pivotGroup && !staticNames.has(f.n) &&
                       !staticNames.has((f.n || '').replace(/ mirror$/, ''));
        if (moving) {
          obj.position.sub(new THREE.Vector3(art.at[0], art.at[1], art.at[2]));
          pivotGroup.add(obj);
        } else {
          group.add(obj);
        }
      };

      // A feature with mirror:'x'|'y'|'z' is a left/right pair authored once: the reflected
      // twin is derived here (position component negated; of the XYZ Euler angles the one
      // about the mirror axis survives, the other two negate). Same expansion the cut list
      // and FeatureScript do — the viewer must show both bodies or the picture lies.
      const MIRROR_AXIS = { x: 0, y: 1, z: 2 };
      const expanded = [];
      asm.features.forEach(f => {
        const axis = MIRROR_AXIS[f.mirror];
        if (axis === undefined) { expanded.push(f); return; }
        const base = { ...f }; delete base.mirror;
        expanded.push(base);
        const twin = JSON.parse(JSON.stringify(base));
        twin.n = (f.n || 'part') + ' mirror';
        twin.at = [...twin.at]; twin.at[axis] = -twin.at[axis];
        if (twin.to) { twin.to = [...twin.to]; twin.to[axis] = -twin.to[axis]; }
        if (twin.rot) twin.rot = twin.rot.map((v, i) => (i === axis ? v : -v));
        expanded.push(twin);
      });
      expanded.forEach(f => {
        // A feature that cannot be placed cannot be drawn; skip it rather than crash the world.
        if (!Array.isArray(f.at) || f.at.length !== 3) return;
        const obj = f.to ? buildLink(f) : buildFeature(f, spin);
        if (!obj) return;
        if (!f.to) {
          obj.position.set(f.at[0], f.at[1], f.at[2]);
          if (f.rot) obj.rotation.set(f.rot[0] * D2R, f.rot[1] * D2R, f.rot[2] * D2R);
        }
        obj.userData.part = asm.id;
        obj.userData.feature = f;
        obj.traverse(o => {
          if (!o.isMesh) return;
          o.castShadow = true; o.receiveShadow = true;
          o.userData.part = asm.id; o.userData.feature = f;
          clickable.push(o);
        });
        attach(obj, f);
        top = Math.max(top, f.at[1] + 1.5);
      });

      world.add(group);
      const dot = DOT[asm.id] || (asm.id.startsWith('swerve') ? DOT.drivetrain : '#8bd0a9');
      const entry = {
        id: asm.id, group, base: group.position.clone(),
        explode: explodeVector(asm, W, L),
      };
      if (asm.id === 'chassis') {
        addBumperNumbers(group, spec.team_number || 8159, bumperHex, W, L, spec.bumper);
      }
      assemblies.push(entry);
      spin.forEach(s => spinners.push(s));
      tallest = Math.max(tallest, org[1] + top + 2);
    });

    // Fit to what was actually built, not to the frame size — a climber tower or a barrel
    // shooter reaches well outside the chassis and still has to be in shot.
    worldBounds = new THREE.Box3().setFromObject(world);
    // The ground meets the robot's actual lowest point (the wheel patches), so the robot
    // stands ON the floor instead of hovering over it or sinking through it.
    ground.position.y = worldBounds.min.y - 0.01;
    frameView();
    setCut(cut);
  }

  // Explode along the axis the assembly is actually removed on: mechanisms lift and move
  // outboard, the bellypan drops, the control system rises off the pan.
  function explodeVector(asm, W, L) {
    // A real exploded view separates VERTICALLY, like an assembly drawing: every part stays
    // over its own footprint so nothing appears to leave the robot. Each assembly gets its
    // own stratum so the layers read top-down as the build order.
    const strata = {
      chassis: -5.5, drivetrain: -2.5, electrical: 4.5, intake: 7,
      hopper: 9.5, climber: 11.5, elevator: 13, arm: 15, shooter: 17,
    };
    if (asm.id.startsWith('swerve')) return new THREE.Vector3(0, -2.5, 0);
    return new THREE.Vector3(0, strata[asm.id] !== undefined ? strata[asm.id] : 9, 0);
  }

  // ── interaction ──────────────────────────────────────────────────────────────
  const ray = new THREE.Raycaster(), mouse = new THREE.Vector2();
  let downXY = null, selected = null, onPick = null;
  const origMats = new Map();
  function highlight(id, on) {
    world.traverse(o => {
      if (!o.isMesh || o.userData.part !== id) return;
      if (on) {
        if (!origMats.has(o)) origMats.set(o, o.material);
        o.material = o.material.clone();
        o.material.emissive = new THREE.Color(0x2a7d4f); o.material.emissiveIntensity = 0.34;
      } else if (origMats.has(o)) {
        o.material = origMats.get(o);
      }
    });
  }
  function select(id, feature) {
    if (selected) highlight(selected, false);
    highlight(id, true); selected = id;
    if (onPick) onPick(id, feature);
  }
  canvas.addEventListener('pointerdown', e => { downXY = [e.clientX, e.clientY]; });
  canvas.addEventListener('pointerup', e => {
    if (!downXY) return;
    const moved = Math.hypot(e.clientX - downXY[0], e.clientY - downXY[1]); downXY = null;
    if (moved > 6) return;
    const r = canvas.getBoundingClientRect();
    mouse.x = ((e.clientX - r.left) / r.width) * 2 - 1;
    mouse.y = -((e.clientY - r.top) / r.height) * 2 + 1;
    ray.setFromCamera(mouse, camera);
    const hit = ray.intersectObjects(clickable, false).find(h => h.object.userData.part);
    if (hit) select(hit.object.userData.part, hit.object.userData.feature);
  });

  // ── view state ───────────────────────────────────────────────────────────────
  let explodeTarget = 0, exploded = 0, running = false, cut = 0, articTime = 0;
  let worldBounds = new THREE.Box3(new THREE.Vector3(-14, 0, -14), new THREE.Vector3(14, 12, 14));
  function setCut(v) {
    cut = v;
    // 0% is the whole robot; sweeping right drives a real section plane in from +X.
    const halfW = Math.max(worldBounds.max.x, 1) + 2;
    sectionPlane.constant = v <= 0.01 ? 999 : halfW - v * (halfW - worldBounds.min.x + 2);
  }

  // Distance at which the whole bounding sphere fits both the vertical and horizontal FOV.
  function fitDistance() {
    const size = worldBounds.getSize(new THREE.Vector3());
    const radius = Math.max(size.length() / 2, 6);
    const vFov = camera.fov * D2R;
    const hFov = 2 * Math.atan(Math.tan(vFov / 2) * Math.max(camera.aspect, 0.4));
    return radius / Math.sin(Math.min(vFov, hFov) / 2) * 0.62;
  }

  let camTarget = null, ctrlTarget = null;
  function centre() {
    const c = worldBounds.getCenter(new THREE.Vector3());
    return new THREE.Vector3(0, c.y, 0);
  }
  function frameView() {
    const d = fitDistance(), c = centre();
    camera.position.set(d * 0.62, c.y + d * 0.48, d * 0.72);
    controls.target.copy(c);
    camTarget = null; ctrlTarget = null;
  }
  function setView(v) {
    const d = fitDistance(), c = centre();
    ctrlTarget = c.clone();
    if (v === 'top') { camTarget = new THREE.Vector3(0.01, c.y + d * 1.15, 0.01); ctrlTarget = new THREE.Vector3(0, 0, 0); }
    else if (v === 'front') camTarget = new THREE.Vector3(0, c.y + d * 0.16, -d);
    else if (v === 'side') camTarget = new THREE.Vector3(d, c.y + d * 0.16, 0.01);
    else camTarget = new THREE.Vector3(d * 0.62, c.y + d * 0.48, d * 0.72);
  }

  function resize() {
    const r = canvas.parentElement.getBoundingClientRect();
    // A hidden pane measures 0×0; keeping the previous size beats a NaN aspect ratio.
    if (r.width < 2 || r.height < 2) return;
    renderer.setSize(r.width, r.height, false);
    labelRenderer.setSize(r.width, r.height);
    camera.aspect = r.width / r.height; camera.updateProjectionMatrix();
  }
  addEventListener('resize', resize); resize();

  const clock = new THREE.Clock();
  (function tick() {
    const dt = clock.getDelta();
    exploded += (explodeTarget - exploded) * Math.min(1, dt * 4);
    assemblies.forEach(a => {
      const t = a.base.clone().addScaledVector(a.explode, exploded);
      a.group.position.lerp(t, Math.min(1, dt * 6));
    });
    if (running) spinners.forEach(s => { if (s.obj.parent) s.obj.rotation[s.axis] += s.speed * dt; });
    // Articulated mechanisms sweep their limited arc — deploy toward the front, pause, stow,
    // pause — instead of spinning. The eased triangle wave reads as a real pneumatic/motor
    // deploy rather than a metronome.
    articTime = running ? articTime + dt : articTime;
    artics.forEach(a => {
      const period = 3.6, t = ((articTime / period) + a.phase) % 1;
      const up = t < 0.5 ? t * 2 : (1 - t) * 2;               // 0→1→0 triangle
      const eased = up * up * (3 - 2 * up);                    // smoothstep both ways
      const ang = (a.deg[0] + (a.deg[1] - a.deg[0]) * eased) * D2R;
      a.group.rotation.x = ang;
    });
    if (camTarget) {
      camera.position.lerp(camTarget, Math.min(1, dt * 3));
      if (camera.position.distanceTo(camTarget) < 0.5) camTarget = null;
    }
    if (ctrlTarget) {
      controls.target.lerp(ctrlTarget, Math.min(1, dt * 3));
      if (controls.target.distanceTo(ctrlTarget) < 0.4) ctrlTarget = null;
    }
    controls.update();
    renderer.render(scene, camera);
    labelRenderer.render(scene, camera);
    requestAnimationFrame(tick);
  })();

  return {
    load,
    setView,
    toggleExplode: () => { explodeTarget = explodeTarget ? 0 : 1; return !!explodeTarget; },
    toggleRun: () => { running = !running; return running; },
    setCut,
    select,
    onPick: (fn) => { onPick = fn; },
    // Dolly along the current view vector. The scroll wheel already does this, but a wheel is
    // not discoverable and is awkward on a trackpad or a touchscreen, where the gesture is
    // usually taken by the page instead of the canvas.
    //
    // Clamped to the orbit controls' own limits so the buttons and the wheel cannot disagree
    // about how close is too close, and cancels any in-flight view animation — otherwise the
    // tick loop keeps lerping toward the old target and immediately undoes the zoom.
    // Read-only camera distance. Separate from zoom() because zoom() cancels an in-flight view
    // animation, so using it to *measure* would stop the thing being measured.
    distance: () => Math.round(camera.position.distanceTo(controls.target)),
    zoom(factor) {
      camTarget = null;
      const offset = camera.position.clone().sub(controls.target);
      const distance = Math.min(controls.maxDistance,
                                Math.max(controls.minDistance, offset.length() * factor));
      camera.position.copy(controls.target).add(offset.setLength(distance));
      camera.updateProjectionMatrix();
      return Math.round(distance);
    },
    // Render on demand and hand back a PNG. Reading the canvas between animation frames is
    // unreliable — the drawing buffer can be empty even while the page looks right — so this
    // draws first and reads immediately, in the same call. `scale` renders above display
    // resolution for a print- or poster-quality export, then restores the view size.
    // Assemblies and bounds, for scripted verification (nothing user-facing reads this).
    _debug: () => ({ world, bounds: worldBounds, assemblies, artics, spinners }),
    // Immediate view set for scripted capture: the animated lerp needs rAF ticks, and a
    // hidden pane never gets any.
    jumpView(v) {
      setView(v);
      if (camTarget) { camera.position.copy(camTarget); camTarget = null; }
      if (ctrlTarget) { controls.target.copy(ctrlTarget); ctrlTarget = null; }
      controls.update();
    },
    // Drive the articulation to a phase (0 = deployed, 1 = stowed) without the clock, so a
    // scripted capture can show the intake mid-arc.
    poseArtics(t) {
      artics.forEach(a => { a.group.rotation.x = (a.deg[0] + (a.deg[1] - a.deg[0]) * t) * D2R; });
    },
    capture(scale = 1, background = '#f6f3ea', margin = 1.12) {
      let r = canvas.parentElement.getBoundingClientRect();
      // A hidden pane measures 0×0; capture still has to produce a real image.
      if (r.width < 50 || r.height < 50) r = { width: 1280, height: 800 };
      const prevBg = scene.background;
      // The canvas is alpha:true so the page background shows through on screen. An exported
      // PNG has no page behind it, so without this it is transparent — which reads as black
      // on one site and white on another. Bake the background in.
      if (background) scene.background = new THREE.Color(background);
      const prevAspect = camera.aspect;
      camera.aspect = r.width / r.height;
      renderer.setSize(r.width * scale, r.height * scale, false);
      // Pull back slightly so nothing clips at the frame edge; a tower touching the top of
      // the image looks like a mistake even when the framing is technically correct.
      const target = controls.target.clone();
      const offset = camera.position.clone().sub(target);
      camera.position.copy(target).add(offset.multiplyScalar(margin));
      camera.updateProjectionMatrix();
      renderer.render(scene, camera);
      const url = canvas.toDataURL('image/png');
      camera.position.copy(target).add(offset.divideScalar(margin));
      camera.aspect = prevAspect;
      camera.updateProjectionMatrix();
      renderer.setSize(r.width, r.height, false);
      scene.background = prevBg;
      return url;
    },
  };
}
