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

  // Presentation only — the CAD schema has no "bumper" material, but bumpers are the one part
  // of a robot with a mandated colour and drawing them grey makes the whole thing hard to read.
  const M_OF = (name, featureName) => (
    /bumper/.test(featureName || '') ? M.bumper : (M[name] || M.aluminium));
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
    spinList.push({ obj: g, axis: 'y', speed: (name && /flywheel/.test(name)) ? 9 : 3.2 });
    return g;
  }

  // ── feature dispatch ─────────────────────────────────────────────────────────
  // One builder per CAD feature type. Anything unknown falls through to a labelled box
  // rather than disappearing, so a schema addition is visible instead of silent.
  function buildFeature(f, spin) {
    const m = M_OF(f.mat, f.n);
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
        return null;
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

  function addBumperNumbers(group, number, colour, W, L) {
    const halfW = W / 2 + 0.6, halfL = L / 2 + 0.6;
    const face = 0.42;                       // just proud of the bumper so it never z-fights
    const y = 2.0;
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
    const mid = a.clone().lerp(b, 0.5); mid.y -= Math.min(1.2, a.distanceTo(b) * 0.12);
    const curve = new THREE.QuadraticBezierCurve3(a, mid, b);
    return new THREE.Mesh(new THREE.TubeGeometry(curve, 18, radius, 8, false), material);
  }

  // ── world ────────────────────────────────────────────────────────────────────
  let world = new THREE.Group(); scene.add(world);
  let assemblies = [];     // {id, group, base, explode, label}
  let spinners = [];
  let clickable = [];
  const ground = new THREE.Mesh(new THREE.PlaneGeometry(700, 700), new THREE.ShadowMaterial({ opacity: 0.2 }));
  ground.rotation.x = -Math.PI / 2; ground.position.y = -0.7; ground.receiveShadow = true; scene.add(ground);

  function label(text, sub, dot, anchor) {
    const el = document.createElement('div');
    el.className = 'lbl'; el.style.setProperty('--dot', dot || '#6fc093');
    el.innerHTML = sub ? `${text} <small>${sub}</small>` : text;
    const obj = new CSS2DObject(el); obj.position.copy(anchor); return obj;
  }

  function clearWorld() {
    // CSS2D labels live in the label renderer's DOM, not the scene graph — removing the
    // group alone leaves the previous design's labels on screen.
    world.traverse(o => {
      if (o.isCSS2DObject && o.element && o.element.parentNode) o.element.parentNode.removeChild(o.element);
      if (o.isMesh && o.geometry) o.geometry.dispose();
    });
    scene.remove(world);
    while (labelRenderer.domElement.firstChild) labelRenderer.domElement.removeChild(labelRenderer.domElement.firstChild);
    world = new THREE.Group(); scene.add(world);
    assemblies = []; spinners = []; clickable = []; origMats.clear(); selected = null;
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
      group.position.set(asm.origin[0], asm.origin[1], asm.origin[2]);
      const spin = [];
      let top = 0;

      asm.features.forEach(f => {
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
        group.add(obj);
        top = Math.max(top, f.at[1] + 1.5);
      });

      world.add(group);
      const dot = DOT[asm.id] || (asm.id.startsWith('swerve') ? DOT.drivetrain : '#8bd0a9');
      const showLabel = !asm.id.startsWith('swerve') || asm.id === 'swerve_0';
      const entry = {
        id: asm.id, group, base: group.position.clone(),
        explode: explodeVector(asm, W, L),
      };
      if (showLabel) {
        // Anchor the label on the assembly's own bounding box, so a tall tower labels at
        // its top and a flat control system labels just above the bellypan.
        const bounds = new THREE.Box3().setFromObject(group);
        const anchor = new THREE.Vector3(0, bounds.max.y - group.position.y + 1.2, 0);
        const sub = (asm.note || '').slice(0, 42);
        const lab = label(asm.name, sub, dot, anchor);
        group.add(lab); entry.label = lab;
      }
      if (asm.id === 'chassis') {
        addBumperNumbers(group, spec.team_number || 8159, bumperHex, W, L);
      }
      assemblies.push(entry);
      spin.forEach(s => spinners.push(s));
      tallest = Math.max(tallest, asm.origin[1] + top + 2);
    });

    // Fit to what was actually built, not to the frame size — a climber tower or a barrel
    // shooter reaches well outside the chassis and still has to be in shot.
    worldBounds = new THREE.Box3().setFromObject(world);
    frameView();
    setLabels(labelsOn);
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
  let labelsOn = true, explodeTarget = 0, exploded = 0, running = false, cut = 0;
  let worldBounds = new THREE.Box3(new THREE.Vector3(-14, 0, -14), new THREE.Vector3(14, 12, 14));
  function setLabels(on) { labelsOn = on; assemblies.forEach(a => { if (a.label) a.label.visible = on; }); return on; }
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
    toggleLabels: () => setLabels(!labelsOn),
    toggleExplode: () => { explodeTarget = explodeTarget ? 0 : 1; return !!explodeTarget; },
    toggleRun: () => { running = !running; return running; },
    setCut,
    select,
    onPick: (fn) => { onPick = fn; },
    // Render on demand and hand back a PNG. Reading the canvas between animation frames is
    // unreliable — the drawing buffer can be empty even while the page looks right — so this
    // draws first and reads immediately, in the same call. `scale` renders above display
    // resolution for a print- or poster-quality export, then restores the view size.
    capture(scale = 1, background = '#f6f3ea', margin = 1.12) {
      const r = canvas.parentElement.getBoundingClientRect();
      const prevBg = scene.background;
      // The canvas is alpha:true so the page background shows through on screen. An exported
      // PNG has no page behind it, so without this it is transparent — which reads as black
      // on one site and white on another. Bake the background in.
      if (background) scene.background = new THREE.Color(background);
      if (scale !== 1) renderer.setSize(r.width * scale, r.height * scale, false);
      // Pull back slightly so nothing clips at the frame edge; a tower touching the top of
      // the image looks like a mistake even when the framing is technically correct.
      const target = controls.target.clone();
      const offset = camera.position.clone().sub(target);
      camera.position.copy(target).add(offset.multiplyScalar(margin));
      camera.updateProjectionMatrix();
      renderer.render(scene, camera);
      const url = canvas.toDataURL('image/png');
      camera.position.copy(target).add(offset.divideScalar(margin));
      camera.updateProjectionMatrix();
      if (scale !== 1) renderer.setSize(r.width, r.height, false);
      scene.background = prevBg;
      return url;
    },
  };
}
