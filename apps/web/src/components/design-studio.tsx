"use client";

import { useEffect, useMemo, useState } from "react";
import { Box, Check, ChevronRight, CircuitBoard, Cloud, Copy, Download, ExternalLink, History,
  Loader2, PencilLine, Plus, RefreshCw, Rocket, ShieldCheck, Sparkles, Trash2, X } from "lucide-react";
import { api, type DesignRecord, type SeasonOption } from "@/lib/api";
import { Badge, Button, Card, Input, Textarea } from "@/components/ui/primitives";

const examples = {
  pcb: "Design an 80 × 55 mm, 4-layer ESP32-S3 controller board with 12V input, protected 5V regulation, status LED, USB, CAN, and mounting holes.",
  robot: "Design a full-scale 27 × 27 inch FRC robot on MK4i swerve. Add an over-the-bumper dual-roller intake, a spindexer hopper, a turreted hooded shooter, and a telescoping climber for L3.",
};

// Each season gets its own starting prompt. The same words mean different robots in different
// games, and handing a team a REEFSCAPE prompt with 2026 selected is a worse default than no
// prompt at all.
const seasonExamples: Record<string, string> = {
  "2026-rebuilt": "Design a 27 × 27 inch REBUILT robot on MK4i swerve. Over-the-bumper dual-roller intake into a spindexer, turreted hooded shooter, and a telescoping climber that reaches L3.",
  "2025-reefscape": "Design a 28 × 28 inch REEFSCAPE robot on MK4i swerve. Ground coral intake, three-stage cascade elevator to L4, wristed carriage arm, and a deep cage climb.",
  offseason: "Design a 27 × 27 inch swerve-ready training chassis without the modules installed, with a coaxial slapdown intake and a dead-axle arm for driver practice.",
};

function ArtifactIcon({ name }: { name: string }) {
  return name.endsWith(".zip") ? <Download className="h-4 w-4" /> : name.endsWith(".obj") ? <Box className="h-4 w-4" /> : <CircuitBoard className="h-4 w-4" />;
}

export function DesignStudio() {
  const [kind, setKind] = useState<"pcb" | "robot">("robot");
  const [prompt, setPrompt] = useState(examples.robot);
  const [seasons, setSeasons] = useState<SeasonOption[]>([]);
  const [season, setSeason] = useState("");
  const [designs, setDesigns] = useState<DesignRecord[]>([]);
  const [selectedId, setSelectedId] = useState<string>();
  const [editPrompt, setEditPrompt] = useState("");
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [connected, setConnected] = useState(false);
  const [connectOpen, setConnectOpen] = useState(false);
  const [accessKey, setAccessKey] = useState("");
  const [secretKey, setSecretKey] = useState("");

  const selected = useMemo(() => designs.find((d) => d.id === selectedId) || designs[0], [designs, selectedId]);
  const activeSeason = useMemo(() => seasons.find((s) => s.key === season), [seasons, season]);
  const refresh = async () => {
    try {
      const rows = await api.listDesigns(); setDesigns(rows); if (!selectedId && rows[0]) setSelectedId(rows[0].id);
      const status = await api.onshapeStatus(); setConnected(status.connected);
    } catch (e) { setError(e instanceof Error ? e.message : "Could not load designs"); }
  };
  useEffect(() => { void refresh(); }, []); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    // The season list is public and rarely changes; a failure here must not block the studio,
    // it just means the season falls back to being read out of the prompt.
    void api.listSeasons()
      .then((data) => { setSeasons(data.seasons); setSeason((current) => current || data.default); })
      .catch(() => setSeasons([]));
  }, []);

  const pickSeason = (key: string) => {
    setSeason(key);
    // Only swap the prompt if it is still an untouched example, so a typed prompt survives.
    const isExamplePrompt = prompt === examples.robot || Object.values(seasonExamples).includes(prompt);
    if (kind === "robot" && isExamplePrompt && seasonExamples[key]) setPrompt(seasonExamples[key]);
  };

  const create = async () => {
    setBusy("create"); setError("");
    try {
      const row = await api.createDesign(kind, prompt, "", kind === "robot" ? season : "");
      setDesigns((old) => [row, ...old]); setSelectedId(row.id);
    }
    catch (e) { setError(e instanceof Error ? e.message : "Generation failed"); }
    finally { setBusy(""); }
  };
  const revise = async () => {
    if (!selected || !editPrompt.trim()) return; setBusy("edit"); setError("");
    try { const row = await api.editDesign(selected.id, editPrompt); setDesigns((old) => old.map((d) => d.id === row.id ? row : d)); setEditPrompt(""); }
    catch (e) { setError(e instanceof Error ? e.message : "Revision failed"); } finally { setBusy(""); }
  };
  const remove = async (id: string) => {
    if (!window.confirm("Delete this design and all generated files?")) return;
    await api.deleteDesign(id); const rows = designs.filter((d) => d.id !== id); setDesigns(rows); setSelectedId(rows[0]?.id);
  };
  const connect = async () => {
    setBusy("connect"); setError("");
    try { await api.connectOnshape(accessKey, secretKey); setConnected(true); setConnectOpen(false); setAccessKey(""); setSecretKey(""); }
    catch (e) { setError(e instanceof Error ? e.message : "Connection failed"); } finally { setBusy(""); }
  };
  const publish = async () => {
    if (!selected) return; if (!connected) { setConnectOpen(true); return; }
    setBusy("publish"); setError("");
    try { const result = await api.publishOnshape(selected.id); setDesigns((old) => old.map((d) => d.id === selected.id ? { ...d, onshape: { ...result, published_revision: d.revision } } : d)); window.open(result.url, "_blank", "noopener,noreferrer"); }
    catch (e) { setError(e instanceof Error ? e.message : "Publish failed"); } finally { setBusy(""); }
  };
  const loadExample = async () => {
    setBusy("example"); setError("");
    try { const row = await api.loadWorkedExample(); setDesigns((old) => [row, ...old]); setSelectedId(row.id); }
    catch (e) { setError(e instanceof Error ? e.message : "Could not load the worked example"); }
    finally { setBusy(""); }
  };
  const copyExactRobot = async () => {
    if (!connected) { setConnectOpen(true); return; }
    setBusy("exact-copy"); setError("");
    try { const row = await api.copySerpentheim(); setDesigns((old) => [row, ...old]); setSelectedId(row.id); if (row.onshape?.url) window.open(row.onshape.url, "_blank", "noopener,noreferrer"); }
    catch (e) { setError(e instanceof Error ? e.message : "Exact copy failed"); } finally { setBusy(""); }
  };

  return <div className="space-y-5">
    <section className="blueprint-grid border border-border px-6 py-8 lg:px-8">
      <div className="flex flex-col justify-between gap-6 md:flex-row md:items-end">
        <div className="max-w-3xl">
          <p className="text-xs font-semibold uppercase tracking-[0.18em] text-primary">Design Studio</p>
          <h1 className="font-display mt-4 text-4xl font-medium tracking-[-0.04em] md:text-5xl">Design, resolve, revise.</h1>
          <p className="mt-3 max-w-2xl leading-7 text-muted-foreground">Generate editable PCB projects and full-scale FRC assemblies, then refine any revision with another prompt. Every revision is kept.</p>
        </div>
        <div className="flex shrink-0 items-center gap-2 rounded-md border border-border px-3.5 py-2.5 text-sm text-muted-foreground">
          <span className={`h-2 w-2 rounded-full ${connected ? "bg-primary" : "bg-border"}`} aria-hidden />
          Onshape {connected ? "connected · parametric only" : "not connected"}
        </div>
      </div>
    </section>

    {error && <div className="flex items-start justify-between rounded-lg border border-red-500/30 bg-red-500/10 px-4 py-3 text-sm text-red-600 dark:text-red-300"><span>{error}</span><button onClick={() => setError("")}><X className="h-4 w-4" /></button></div>}
    {designs.some((design) => design.is_example) && <div className="rounded-lg border border-primary/30 bg-accent px-4 py-4"><div className="flex items-start gap-3"><Check className="mt-0.5 h-5 w-5 shrink-0 text-primary"/><div><h2 className="font-semibold">Worked example included</h2><p className="mt-1 text-sm leading-6 text-muted-foreground">The design marked “Example” is provided only to show how the Studio works. You can revise or delete it, then create your own PCB or robot designs with the controls on this page.</p></div></div></div>}

    <div className="grid gap-5 lg:grid-cols-[380px_1fr]">
      <div className="space-y-4">
        <Card className="overflow-hidden">
          <div className="p-4"><div className="flex items-center justify-between"><span className="text-xs font-semibold uppercase tracking-wider text-primary">Reference model</span><span className="text-xs text-muted-foreground">44 instances · 48 mates</span></div><h2 className="mt-3 font-semibold">2025 Serpentheim robot</h2><p className="mt-1 text-xs leading-5 text-muted-foreground">Create an exact, independent and editable copy of every original Onshape tab, feature, assembly, and mate.</p><Button className="mt-4 w-full" variant="outline" onClick={copyExactRobot} disabled={busy !== ""}>{busy === "exact-copy" ? <Loader2 className="h-4 w-4 animate-spin"/> : <Copy className="h-4 w-4"/>} Copy exact robot</Button></div>
        </Card>
        <Card className="overflow-hidden">
          <div className="grid grid-cols-2 border-b border-border">
            {(["pcb", "robot"] as const).map((value) => <button key={value} onClick={() => { setKind(value); setPrompt(value === "robot" ? (seasonExamples[season] || examples.robot) : examples.pcb); }} className={`flex items-center justify-center gap-2 px-3 py-4 text-sm font-semibold transition ${kind === value ? "bg-accent text-primary" : "text-muted-foreground hover:bg-muted"}`}>
              {value === "pcb" ? <CircuitBoard className="h-4 w-4" /> : <Rocket className="h-4 w-4" />}{value === "pcb" ? "PCB" : "FRC Robot"}
            </button>)}
          </div>
          <div className="space-y-4 p-4">
            {kind === "robot" && seasons.length > 0 && <div>
              <label className="mb-2 block text-sm font-medium">Which season are you building for?</label>
              <div className="grid gap-2" style={{ gridTemplateColumns: `repeat(${Math.min(seasons.length, 3)}, minmax(0, 1fr))` }}>
                {seasons.map((option) => <button key={option.key} type="button" onClick={() => pickSeason(option.key)}
                  className={`rounded-lg border px-3 py-2 text-left transition ${season === option.key ? "border-primary bg-accent" : "border-border hover:bg-muted"}`}>
                  <span className="block text-sm font-semibold">{option.year || "Off"}</span>
                  <span className="block text-xs text-muted-foreground">{option.game}</span>
                </button>)}
              </div>
              {activeSeason && <p className="mt-2 text-xs leading-5 text-muted-foreground">
                {activeSeason.summary} Gamepiece: <strong className="font-semibold text-foreground">{activeSeason.gamepiece}</strong>.
                Frame budget <strong className="font-semibold text-foreground">{activeSeason.perimeter_in}&nbsp;in</strong> of perimeter,
                so the default frame is {activeSeason.frame_in[0]} × {activeSeason.frame_in[1]} in.
              </p>}
            </div>}
            <div><label className="mb-2 block text-sm font-medium">What should Kale Forge design?</label><Textarea rows={8} value={prompt} onChange={(e) => setPrompt(e.target.value)} placeholder="Include dimensions, inputs, mechanisms, and constraints…" /></div>
            <div className="rounded-lg bg-muted p-3 text-xs leading-5 text-muted-foreground"><ShieldCheck className="mr-1 inline h-4 w-4 text-primary" /> Prompts are grounded in a dimensioned parts catalog, and the season sets the goal heights, climb reach, and perimeter budget — an over-budget frame is resized and flagged, never quietly built. Name real hardware and it is used. Every dimension still needs checking against the current manual.</div>
            <Button className="w-full" onClick={create} disabled={busy !== "" || prompt.trim().length < 8}>{busy === "create" ? <Loader2 className="h-4 w-4 animate-spin" /> : <Sparkles className="h-4 w-4" />} Generate {kind === "pcb" ? "PCB" : "robot"}</Button>
          </div>
        </Card>

        <Card><div className="flex items-center justify-between border-b border-border p-4"><h2 className="text-sm font-semibold">Your designs</h2><button onClick={() => void refresh()} className="text-muted-foreground hover:text-foreground"><RefreshCw className="h-4 w-4" /></button></div>
          <div className="max-h-[420px] divide-y divide-border overflow-y-auto">
            {designs.length === 0 && <div className="p-6 text-center text-sm text-muted-foreground">Your generated designs will appear here.</div>}
            {designs.map((d) => <button key={d.id} onClick={() => setSelectedId(d.id)} className={`flex w-full items-center gap-3 p-3 text-left transition hover:bg-muted ${selected?.id === d.id ? "bg-accent" : ""}`}>
              <span className="grid h-9 w-9 shrink-0 place-items-center rounded-lg bg-muted text-primary">{d.kind === "pcb" ? <CircuitBoard className="h-4 w-4" /> : <Box className="h-4 w-4" />}</span>
              <span className="min-w-0 flex-1"><span className="flex items-center gap-2"><span className="block truncate text-sm font-medium">{d.name}</span>{d.is_example && <Badge className="shrink-0 bg-accent text-primary">Example</Badge>}</span><span className="text-xs text-muted-foreground">Revision {d.revision} · {d.kind === "pcb" ? "KiCad" : "Robot CAD"}</span></span><ChevronRight className="h-4 w-4 text-muted-foreground" />
            </button>)}
          </div>
        </Card>
      </div>

      <div>
        {!selected ? <Card className="grid min-h-[560px] place-items-center p-10 text-center">
          <div className="max-w-md">
            <div className="mx-auto grid h-16 w-16 place-items-center rounded-2xl bg-accent text-primary"><Plus /></div>
            <h2 className="mt-5 text-xl font-semibold">Your workspace is empty</h2>
            <p className="mt-2 text-sm leading-6 text-muted-foreground">That is on purpose — nothing is generated until you ask. Pick PCB or FRC Robot, describe what you want, and Kale Forge builds the engineering package: geometry, BOM with real part numbers, a wiring and power plan, and a dossier explaining every choice.</p>
            <div className="mt-6 grid gap-2 text-left text-xs text-muted-foreground">
              {["Name real hardware and it is used — “MK4n modules on Krakens at L2+”",
                "State dimensions and they are honoured, not reinterpreted",
                "Revise with another prompt; every revision is kept"].map((tip) =>
                <div key={tip} className="flex gap-2"><Check className="mt-0.5 h-3.5 w-3.5 shrink-0 text-primary" />{tip}</div>)}
            </div>
            <div className="mt-7 border-t border-border pt-5">
              <p className="text-xs text-muted-foreground">Prefer to see one first?</p>
              <Button variant="outline" className="mt-3" onClick={loadExample} disabled={busy !== ""}>
                {busy === "example" ? <Loader2 className="h-4 w-4 animate-spin" /> : <Sparkles className="h-4 w-4" />} Load a worked example
              </Button>
            </div>
          </div>
        </Card> :
        <Card className="overflow-hidden">
          <div className="flex flex-wrap items-center justify-between gap-3 border-b border-border p-5">
            <div><div className="flex items-center gap-2"><h2 className="text-xl font-semibold">{selected.name}</h2>{selected.is_example && <Badge className="bg-accent text-primary">Worked example</Badge>}<Badge className="bg-emerald-500/15 text-emerald-700 dark:text-emerald-300">r{selected.revision}</Badge></div><p className="mt-1 text-xs text-muted-foreground">{selected.is_example ? "Example only — revise it for practice or create your own design." : selected.kind === "pcb" ? "Editable KiCad engineering package" : "Full-scale FRC assembly concept"}</p></div>
            <div className="flex gap-2">{selected.onshape?.url && <a href={selected.onshape.url} target="_blank" rel="noreferrer" className="inline-flex h-9 items-center gap-2 border border-border px-3 text-sm font-medium hover:bg-muted"><ExternalLink className="h-4 w-4" /> Open source</a>}{selected.kind === "robot" && !selected.spec.exact_copy && <Button variant="outline" onClick={publish} disabled={busy !== ""}>{busy === "publish" ? <Loader2 className="h-4 w-4 animate-spin" /> : <Cloud className="h-4 w-4" />} {selected.onshape ? "Update parametric source" : "Publish editable source"}</Button>}<Button variant="ghost" size="icon" title="Delete" onClick={() => void remove(selected.id)}><Trash2 className="h-4 w-4" /></Button></div>
          </div>

          <div className="grid md:grid-cols-[1.25fr_.75fr]">
            <div className="border-b border-border bg-slate-950 p-5 md:border-b-0 md:border-r">
              <div className="mb-3 flex items-center justify-between text-xs text-slate-400"><span>GENERATED PREVIEW</span><span>REVISION {selected.revision}</span></div>
              <div className="grid min-h-[360px] place-items-center overflow-hidden rounded-xl border border-white/10 bg-slate-900/50 p-4">{selected.spec.exact_copy ? <div className="max-w-sm text-center"><div className="mx-auto grid h-20 w-20 place-items-center rounded-2xl bg-emerald-400/15 text-emerald-300"><Copy className="h-9 w-9"/></div><h3 className="mt-5 text-xl font-semibold text-white">Exact workspace copy</h3><p className="mt-2 text-sm leading-6 text-slate-400">The complete native model lives in your Onshape account, with all original features and assembly relationships.</p>{selected.onshape?.url&&<a href={selected.onshape.url} target="_blank" rel="noreferrer" className="mt-5 inline-flex items-center gap-2 rounded-lg bg-emerald-400 px-4 py-2 text-sm font-semibold text-emerald-950">Open full robot <ExternalLink className="h-4 w-4"/></a>}</div> : <img key={`${selected.id}-${selected.revision}`} src={api.artifactUrl(selected.id, "preview.svg")} alt={`${selected.name} generated preview`} className="max-h-[390px] w-full object-contain" />}</div>
            </div>
            <div className="space-y-5 p-5">
              <section><h3 className="mb-3 flex items-center gap-2 text-sm font-semibold"><Check className="h-4 w-4 text-primary" /> Design summary</h3><SpecSummary design={selected} /></section>
              <section><h3 className="mb-3 flex items-center gap-2 text-sm font-semibold"><Download className="h-4 w-4 text-primary" /> Engineering files</h3>{selected.artifacts.length ? <div className="space-y-2">{selected.artifacts.map((name) => <a key={name} href={api.artifactUrl(selected.id, name)} download className="flex items-center justify-between rounded-lg border border-border px-3 py-2 text-xs hover:bg-muted"><span className="flex items-center gap-2"><ArtifactIcon name={name} /><span className="max-w-[190px] truncate">{name}</span></span><Download className="h-3.5 w-3.5 text-muted-foreground" /></a>)}</div> : <div className="rounded-lg border border-border p-3 text-xs leading-5 text-muted-foreground">Native Part Studios, assemblies, and features are stored directly in the copied Onshape document.</div>}</section>
            </div>
          </div>

          {selected.spec.exact_copy ? <div className="border-t border-border bg-muted/30 p-5"><div className="flex items-start gap-3"><div className="grid h-9 w-9 shrink-0 place-items-center rounded-lg bg-accent text-primary"><PencilLine className="h-4 w-4"/></div><div><h3 className="text-sm font-semibold">Edit the native model in Onshape</h3><p className="mt-1 text-xs leading-5 text-muted-foreground">This exact copy preserves the source feature tree and mates. Open it in Onshape to edit those native features; Kale Forge prompt revisions remain available for designs generated by Kale Forge.</p></div></div></div> : <div className="border-t border-border bg-muted/30 p-5"><div className="mb-3 flex items-center justify-between"><h3 className="flex items-center gap-2 text-sm font-semibold"><PencilLine className="h-4 w-4 text-primary" /> Edit with a prompt</h3><span className="flex items-center gap-1 text-xs text-muted-foreground"><History className="h-3.5 w-3.5" /> Revisions preserved</span></div><div className="flex flex-col gap-2 sm:flex-row"><Input value={editPrompt} onChange={(e) => setEditPrompt(e.target.value)} onKeyDown={(e) => { if (e.key === "Enter") void revise(); }} placeholder={selected.kind === "pcb" ? "Move USB to the left, add CAN termination and make it 4 layers…" : "Make the elevator 72 inches, widen the intake and remove the climber…"} /><Button onClick={revise} disabled={!editPrompt.trim() || busy !== ""}>{busy === "edit" ? <Loader2 className="h-4 w-4 animate-spin" /> : <Sparkles className="h-4 w-4" />} Revise</Button></div></div>}
        </Card>}
      </div>
    </div>

    {connectOpen && <div className="fixed inset-0 z-50 grid place-items-center bg-black/70 p-4"><Card className="w-full max-w-md shadow-2xl"><div className="flex items-center justify-between border-b border-border p-4"><div><h2 className="font-semibold">Connect Onshape</h2><p className="mt-1 text-xs text-muted-foreground">Keys stay in server memory and are never saved in a design.</p></div><button onClick={() => setConnectOpen(false)}><X className="h-5 w-5" /></button></div><div className="space-y-4 p-4"><div><label htmlFor="onshape-access" className="mb-1 block text-xs font-medium">Access key</label><Input id="onshape-access" type="password" value={accessKey} onChange={(e) => setAccessKey(e.target.value)} autoComplete="off" /></div><div><label htmlFor="onshape-secret" className="mb-1 block text-xs font-medium">Secret key</label><Input id="onshape-secret" type="password" value={secretKey} onChange={(e) => setSecretKey(e.target.value)} autoComplete="off" /></div><div className="rounded-lg bg-muted p-3 text-xs text-muted-foreground">Create keys in Onshape → My account → Developer portal. Use a dedicated key and revoke it at any time.</div><Button className="w-full" onClick={connect} disabled={!accessKey || !secretKey || busy !== ""}>{busy === "connect" ? <Loader2 className="h-4 w-4 animate-spin" /> : <ExternalLink className="h-4 w-4" />} Verify and connect</Button></div></Card></div>}
  </div>;
}

function SpecSummary({ design }: { design: DesignRecord }) {
  if (design.kind === "pcb") {
    const s = design.spec as any;
    return <div className="grid grid-cols-2 gap-2 text-xs"><Stat label="Board" value={`${s.width_mm} × ${s.height_mm} mm`} /><Stat label="Stackup" value={`${s.layers} layers`} /><Stat label="Input" value={`${s.input_voltage_v} V`} /><Stat label="Parts" value={`${s.components?.length || 0}`} /></div>;
  }
  const s = design.spec as any;
  if (s.exact_copy) return <div className="grid grid-cols-2 gap-2 text-xs"><Stat label="Instances" value={`${s.instances}`} /><Stat label="Mates" value={`${s.mates}`} /><Stat label="Format" value="Native Onshape" /><Stat label="Access" value="Fully editable" /></div>;
  const dt = s.drivetrain, elec = s.electrical, mass = s.mass_estimate;
  const failed = (s.rule_check || []).filter((row: any) => !row.ok);
  const shot = s.shooter?.shot;
  return <div className="grid grid-cols-2 gap-2 text-xs">
    <Stat label="Season" value={s.season?.label || s.profile?.label || "FRC robot"} />
    <Stat label="Frame" value={`${s.frame.width_in} × ${s.frame.length_in} in`} />
    <Stat label="Modules" value={dt?.modules_included ? `${dt.module_count}× ${dt.module}` : `${s.drive.type} / bare`} />
    <Stat label="Drive" value={dt?.modules_included ? `${dt.motor} · ${dt.drive_ratio_label} (${dt.drive_ratio}:1)` : s.drive.type} />
    {dt?.modules_included && <Stat label="Free speed" value={`${dt.free_speed_fps} ft/s`} />}
    <Stat label="Systems" value={(s.subsystems || []).join(", ") || "chassis only"} />
    {elec && <Stat label="Power" value={`${elec.distributor} · ${elec.channels_used} ch · ${elec.main_breaker_a} A main`} />}
    {mass && <Stat label="Mass estimate" value={`${mass.counted_lb} lb (${mass.margin_lb >= 0 ? "+" : ""}${mass.margin_lb} vs target)`} />}
    {s.hopper?.included && <Stat label="Hopper" value={`~${s.hopper.capacity_estimate} ${s.season?.gamepiece || "pieces"} · ${s.hopper.feed_rate_per_s}/s`} />}
    {shot && <Stat label="Shot" value={shot.makes_design_range ? `${shot.achieved_range_ft} ft at ${shot.optimal_angle_deg}°` : `short of ${shot.design_range_ft} ft`} />}
    {s.rule_check?.length > 0 && <Stat label="Rule check" value={failed.length ? `${failed.length} to fix: ${failed.map((row: any) => row.rule).join(", ")}` : "nothing caught"} />}
    <Stat label="Architecture by" value={s.model?.used ? `model ${s.model.model_version || s.model.provider}` : "deterministic synthesis"} />
    <Stat label="Knowledge" value={s.profile?.knowledge_version || "legacy"} />
  </div>;
}

function Stat({ label, value }: { label: string; value: string }) {
  return <div className="rounded-lg bg-muted p-3"><div className="text-muted-foreground">{label}</div><div className="mt-1 font-semibold capitalize">{value}</div></div>;
}
