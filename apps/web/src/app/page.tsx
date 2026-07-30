import Link from "next/link";
import { ArrowRight, ArrowUpRight } from "lucide-react";

export default function Home() {
  return <>
    {/* ── Hero ─────────────────────────────────────────────────────────── */}
    <section className="border-b border-border">
      <div className="mx-auto grid w-full max-w-6xl items-center gap-14 px-6 py-24 lg:grid-cols-[1.05fr_.95fr]">
        <div>
          <p className="text-xs font-semibold uppercase tracking-[0.18em] text-primary">AI robot design, engineered</p>
          <h1 className="mt-5 text-5xl font-semibold leading-[1.05] tracking-[-0.03em] md:text-6xl">
            Describe the robot.<br />Get an engineered design.
          </h1>
          <p className="mt-6 max-w-xl text-lg leading-8 text-muted-foreground">
            Kale Forge turns a written requirement into an original, fully editable robot —
            chassis, drivetrain, mechanisms, electronics — with the math worked out on real
            parts and every dimension left open to change.
          </p>
          <div className="mt-9 flex flex-wrap items-center gap-4">
            <Link href="/design" className="inline-flex items-center gap-2 rounded-md bg-primary px-5 py-3 font-semibold text-primary-foreground transition hover:opacity-90">
              Open the Design Studio <ArrowRight className="h-4 w-4" />
            </Link>
            <a href="#method" className="inline-flex items-center gap-1.5 px-1 py-3 font-medium text-muted-foreground transition hover:text-foreground">
              How it works
            </a>
          </div>
          <p className="mt-6 text-xs text-muted-foreground">
            Runs on Kale&apos;s own fine-tuned model. No external AI APIs, ever.
          </p>
        </div>
        <DesignRecord />
      </div>
    </section>

    {/* ── Three quiet facts ────────────────────────────────────────────── */}
    <section className="border-b border-border">
      <div className="mx-auto grid w-full max-w-6xl divide-y divide-border px-6 md:grid-cols-3 md:divide-x md:divide-y-0">
        <Fact title="Original synthesis" text="Each design is derived from your constraints and the season's rules — not retrieved from a library of robots." />
        <Fact title="Fully parametric" text="Roughly 200 dimensioned parts per robot, every measure a named variable. Change one number and the model rebuilds." />
        <Fact title="Self-hosted model" text="An open-weight base fine-tuned on Kale's engineering corpus, served on infrastructure Kale controls." />
      </div>
    </section>

    {/* ── Method ───────────────────────────────────────────────────────── */}
    <section id="method" className="border-b border-border">
      <div className="mx-auto w-full max-w-6xl px-6 py-24">
        <p className="text-xs font-semibold uppercase tracking-[0.18em] text-primary">Method</p>
        <h2 className="mt-4 max-w-2xl text-4xl font-semibold tracking-tight">From one sentence to a design you can defend.</h2>
        <div className="mt-14 grid gap-px overflow-hidden rounded-lg border border-border bg-border md:grid-cols-2 lg:grid-cols-4">
          <Step n="01" title="Describe" text="State what you know — frame size, season, named hardware, the job the robot has to do. Explicit facts are treated as specification, never as suggestion." />
          <Step n="02" title="Synthesize" text="The model resolves everything you left open, constrained to a real parts catalog and the season's rule budget, so no answer can exceed what would pass inspection." />
          <Step n="03" title="Inspect" text="Every design ships with its evidence: a part-level CAD tree, a channel-by-channel power budget, a mass roll-up, a cut list, and a dossier explaining each choice." />
          <Step n="04" title="Publish" text="Send the design to your Onshape account as parametric source — real features with named dimensions, ready to edit, not a frozen mesh." />
        </div>
      </div>
    </section>

    {/* ── Onshape: nothing arrives flattened ───────────────────────────── */}
    <section className="border-b border-border">
      <div className="mx-auto grid w-full max-w-6xl gap-14 px-6 py-24 lg:grid-cols-2 lg:items-center">
        <div>
          <p className="text-xs font-semibold uppercase tracking-[0.18em] text-primary">Onshape publishing</p>
          <h2 className="mt-4 text-4xl font-semibold tracking-tight">Nothing arrives flattened.</h2>
          <p className="mt-5 leading-7 text-muted-foreground">
            A mesh export has vertices, not dimensions — once a robot is triangles, there is
            nothing left to edit. Kale Forge publishes generated parametric source instead:
            every part is a real feature, every measure is a named variable beside the call
            that uses it, and a gear&apos;s pitch diameter is written as its derivation from the
            tooth count, so editing the count moves the geometry.
          </p>
          <ul className="mt-8 space-y-4 text-sm leading-6 text-muted-foreground">
            <ListItem strong="Every part is its own feature" text="— tubes with real wall thickness, shafts with real hex sections, bearings with real bores." />
            <ListItem strong="Every measure is editable" text="— over a thousand named variables on a full robot, one per dimension, in one obvious place." />
            <ListItem strong="The frame drives the layout" text="— change the frame parameters in the feature dialog and positions and frame members rebuild with it." />
          </ul>
        </div>
        <SourceExcerpt />
      </div>
    </section>

    {/* ── Honesty ──────────────────────────────────────────────────────── */}
    <section className="border-b border-border">
      <div className="mx-auto grid w-full max-w-6xl gap-12 px-6 py-24 lg:grid-cols-[.9fr_1.1fr]">
        <div>
          <p className="text-xs font-semibold uppercase tracking-[0.18em] text-primary">What this is not</p>
          <h2 className="mt-4 text-4xl font-semibold tracking-tight">Concept geometry, stated plainly.</h2>
        </div>
        <div className="text-muted-foreground">
          <p className="leading-7">
            Kale Forge produces dimensioned concept geometry: the numbers are consistent with
            each other and with the parts catalog, and none of them has been checked against a
            vendor drawing, a stress case, or the current game manual. The rule check on every
            design is reported, not enforced — passing means nothing was caught, not that the
            robot is legal.
          </p>
          <div className="mt-8 grid gap-px overflow-hidden rounded-lg border border-border bg-border sm:grid-cols-3">
            {[
              ["Verify parts", "against vendor drawings before machining"],
              ["Verify rules", "against the current manual and team updates"],
              ["Verify loads", "with real masses, prototypes, and proof tests"],
            ].map(([strong, rest]) => (
              <div key={strong} className="bg-background p-5 text-sm">
                <span className="font-semibold text-foreground">{strong}</span>
                <span className="mt-1 block leading-6">{rest}</span>
              </div>
            ))}
          </div>
        </div>
      </div>
    </section>

    {/* ── Closing CTA ──────────────────────────────────────────────────── */}
    <section>
      <div className="mx-auto flex w-full max-w-6xl flex-col items-start justify-between gap-8 px-6 py-20 md:flex-row md:items-center">
        <div>
          <h2 className="text-3xl font-semibold tracking-tight">Start from a requirement.</h2>
          <p className="mt-2 text-muted-foreground">Pick a season, describe the robot, and read the evidence it comes back with.</p>
        </div>
        <Link href="/design" className="inline-flex shrink-0 items-center gap-2 rounded-md bg-primary px-5 py-3 font-semibold text-primary-foreground transition hover:opacity-90">
          Open the Design Studio <ArrowRight className="h-4 w-4" />
        </Link>
      </div>
    </section>
  </>;
}

/* A design dossier excerpt, set like the spec sheet the studio actually produces. */
function DesignRecord() {
  return <div className="overflow-hidden rounded-lg border border-border bg-card shadow-sm">
    <div className="flex items-center justify-between border-b border-border px-5 py-3.5">
      <span className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">Design record</span>
      <span className="mono text-xs text-muted-foreground">r3 · 2026 REBUILT</span>
    </div>
    <div className="px-5 py-4">
      <h2 className="font-semibold">Turreted fuel cycler</h2>
      <p className="mono mt-0.5 text-xs text-muted-foreground">&ldquo;27 in swerve, over-bumper intake, spindexer, turreted hooded shooter, L3 climb&rdquo;</p>
    </div>
    <dl className="divide-y divide-border border-t border-border text-sm">
      <RecordRow k="Frame" v="27 × 27 in · 108 in perimeter of a 110 in budget" />
      <RecordRow k="Drivetrain" v="4× MK4i L2 on Kraken X60 · 15.6 ft/s free" />
      <RecordRow k="Shooter" v="4 in dual flywheel · exit 32.4 ft/s · hood 52–78°" />
      <RecordRow k="Power" v="PDH · 14 channels assigned · 120 A main" />
      <RecordRow k="Mass" v="94.6 lb counted vs 105 lb target" />
      <RecordRow k="CAD" v="189 parts · 12 assemblies · every measure editable" />
    </dl>
    <div className="flex items-center justify-between border-t border-border bg-muted/40 px-5 py-3 text-xs text-muted-foreground">
      <span>Rule check: nothing caught — verify against the current manual</span>
      <ArrowUpRight className="h-3.5 w-3.5 shrink-0" />
    </div>
  </div>;
}

function RecordRow({ k, v }: { k: string; v: string }) {
  return <div className="grid grid-cols-[92px_1fr] gap-4 px-5 py-3">
    <dt className="text-muted-foreground">{k}</dt>
    <dd className="mono text-[13px] leading-6">{v}</dd>
  </div>;
}

/* Real shape of the published FeatureScript — the artifact the Onshape claim rests on. */
function SourceExcerpt() {
  const lines = [
    ["cmt", "// drive spur — 40T @ 20 DP → PD 2.000 in"],
    ["src", "var gear_spur_teeth = 40; var gear_spur_dp = 20;"],
    ["src", "var gear_spur_x = -11.25; var gear_spur_y = 2.05;"],
    ["src", "kaleDisc(context, id + \"gear_spur\","],
    ["src", "    gear_spur_teeth / gear_spur_dp, …);"],
    ["cmt", "// frame rail — 2x1x0.1 in stock, 25.5 in long"],
    ["src", "var rail_len = 25.5; var rail_wall = 0.1;"],
    ["src", "kaleTube(context, id + \"rail\", 2, 1,"],
    ["src", "    rail_len * scaleZ, rail_wall, …);"],
  ] as const;
  return <div className="overflow-hidden rounded-lg border border-border bg-card shadow-sm">
    <div className="flex items-center justify-between border-b border-border px-5 py-3.5 text-xs">
      <span className="font-semibold uppercase tracking-wider text-muted-foreground">Published source</span>
      <span className="mono text-muted-foreground">KaleRobot.fs · Feature Studio</span>
    </div>
    <pre className="mono overflow-x-auto p-5 text-[13px] leading-7">
      {lines.map(([kind, text], index) => (
        <span key={index} className={kind === "cmt" ? "block text-muted-foreground" : "block"}>{text}</span>
      ))}
    </pre>
    <p className="border-t border-border bg-muted/40 px-5 py-3 text-xs leading-5 text-muted-foreground">
      Edit the tooth count and the pitch diameter follows; edit the frame and the rail follows.
      That is the difference between publishing source and publishing a picture.
    </p>
  </div>;
}

function Fact({ title, text }: { title: string; text: string }) {
  return <div className="py-8 md:px-8 first:md:pl-0 last:md:pr-0">
    <h3 className="font-semibold">{title}</h3>
    <p className="mt-2 text-sm leading-6 text-muted-foreground">{text}</p>
  </div>;
}

function Step({ n, title, text }: { n: string; title: string; text: string }) {
  return <div className="bg-background p-7">
    <span className="mono text-xs text-primary">{n}</span>
    <h3 className="mt-3 text-lg font-semibold">{title}</h3>
    <p className="mt-2 text-sm leading-6 text-muted-foreground">{text}</p>
  </div>;
}

function ListItem({ strong, text }: { strong: string; text: string }) {
  return <li className="flex gap-3">
    <span className="mt-2.5 h-1 w-1 shrink-0 rounded-full bg-primary" />
    <span><span className="font-semibold text-foreground">{strong}</span> {text}</span>
  </li>;
}
