import Link from "next/link";
import { ArrowRight, Box, Braces, Check, MoveRight } from "lucide-react";

const specRows = [
  ["FRAME", "27 × 27 in", "108 / 110 in perimeter"],
  ["DRIVE", "MK4i L2 · Kraken X60", "15.6 ft/s free"],
  ["SHOOTER", "4 in dual flywheel", "32.4 ft/s exit"],
  ["POWER", "REV PDH · 14 channels", "120 A main"],
  ["MASS", "94.6 lb counted", "+10.4 lb margin"],
  ["SOURCE", "189 named parts", "0 flattened bodies"],
] as const;

export default function Home() {
  return (
    <>
      <section className="blueprint-grid border-b border-border">
        <div className="mx-auto grid min-h-[760px] w-full max-w-[1440px] lg:grid-cols-[minmax(0,1fr)_520px]">
          <div className="flex flex-col justify-between px-6 py-14 sm:px-10 lg:border-r lg:border-border lg:px-16 lg:py-20">
            <div className="flex items-center gap-3 text-[11px] font-semibold uppercase tracking-[0.2em] text-muted-foreground">
              <span className="h-px w-10 bg-primary" />
              FRC design intelligence · 2026
            </div>
            <div className="my-20 max-w-4xl">
              <h1 className="font-display max-w-4xl text-[clamp(4rem,8vw,8.5rem)] font-medium leading-[0.86] tracking-[-0.065em]">
                Robots,<br />
                resolved.
              </h1>
              <p className="mt-10 max-w-2xl text-lg leading-8 text-muted-foreground sm:text-xl">
                Describe the machine. Kale Forge resolves the architecture, calculations,
                parts, interfaces, and a parametric Onshape model you can keep engineering.
              </p>
            </div>
            <div className="flex flex-wrap items-center gap-5">
              <Link href="/design" className="group inline-flex h-12 items-center gap-4 bg-foreground px-6 text-sm font-semibold text-background transition hover:bg-primary hover:text-primary-foreground">
                Enter Design Studio
                <ArrowRight className="h-4 w-4 transition-transform group-hover:translate-x-1" />
              </Link>
              <span className="text-xs leading-5 text-muted-foreground">
                Self-hosted model<br />No flattened CAD
              </span>
            </div>
          </div>

          <div className="flex flex-col bg-card/70">
            <div className="flex items-center justify-between border-b border-border px-6 py-4 text-[11px] font-semibold uppercase tracking-[0.16em] text-muted-foreground">
              <span>Design record / KF-26-0142</span>
              <span className="text-primary">Resolved</span>
            </div>
            <div className="relative grid min-h-[300px] flex-1 place-items-center overflow-hidden border-b border-border p-8">
              <div className="cad-orbit" aria-hidden>
                <span className="cad-block cad-block-a" />
                <span className="cad-block cad-block-b" />
                <span className="cad-block cad-block-c" />
                <span className="cad-axis cad-axis-x" />
                <span className="cad-axis cad-axis-y" />
              </div>
              <div className="absolute bottom-5 left-6 right-6 flex justify-between font-mono text-[10px] text-muted-foreground">
                <span>ISO / PART TREE 189</span>
                <span>UNITS / IN</span>
              </div>
            </div>
            <dl>
              {specRows.map(([label, value, note]) => (
                <div key={label} className="grid grid-cols-[82px_1fr] border-b border-border px-6 py-4 last:border-b-0">
                  <dt className="font-mono text-[10px] text-muted-foreground">{label}</dt>
                  <dd className="flex items-baseline justify-between gap-4 text-sm">
                    <span className="font-medium">{value}</span>
                    <span className="font-mono text-[10px] text-muted-foreground">{note}</span>
                  </dd>
                </div>
              ))}
            </dl>
          </div>
        </div>
      </section>

      <section className="border-b border-border">
        <div className="mx-auto grid w-full max-w-[1440px] lg:grid-cols-[360px_1fr]">
          <div className="border-b border-border px-6 py-14 sm:px-10 lg:border-b-0 lg:border-r lg:px-16 lg:py-24">
            <p className="font-mono text-[11px] uppercase tracking-[0.16em] text-primary">01 / Method</p>
            <h2 className="font-display mt-5 text-4xl font-medium leading-none tracking-[-0.04em]">
              Evidence before confidence.
            </h2>
          </div>
          <div className="grid md:grid-cols-3">
            <Method n="01" title="Constrain" text="Season rules, your stated dimensions, named hardware, build resources, and acceptance criteria become hard inputs." />
            <Method n="02" title="Resolve" text="The model proposes architecture while deterministic math closes ratios, power, geometry, travel, and mass." />
            <Method n="03" title="Prove" text="Every result carries the part tree, derivations, binder rationale, risks, and tests needed before fabrication." />
          </div>
        </div>
      </section>

      <section className="bg-foreground text-background">
        <div className="mx-auto w-full max-w-[1440px] px-6 py-20 sm:px-10 lg:px-16 lg:py-28">
          <div className="grid gap-16 lg:grid-cols-[1fr_1.1fr]">
            <div>
              <p className="font-mono text-[11px] uppercase tracking-[0.16em] text-primary">02 / Editable by construction</p>
              <h2 className="font-display mt-6 max-w-2xl text-5xl font-medium leading-[0.98] tracking-[-0.05em] sm:text-6xl">
                Nothing arrives flattened.
              </h2>
              <p className="mt-7 max-w-xl text-base leading-7 text-background/65">
                Kale Forge publishes FeatureScript source, not triangles. Each body is generated
                from a named call. Dimensions remain named variables. Derived measures remain
                formulas. Revisions update the same source tab instead of creating CAD ambiguity.
              </p>
            </div>
            <div className="border border-background/20 bg-black/20">
              <div className="flex items-center justify-between border-b border-background/20 px-5 py-3 font-mono text-[10px] text-background/50">
                <span>KaleRobot.fs</span><span>PARAMETRIC SOURCE</span>
              </div>
              <pre className="overflow-x-auto p-6 font-mono text-[12px] leading-7 text-background/85">
                <span className="block text-primary">{"// drive spur · 40T @ 20DP"}</span>
                <span className="block">{"var drive_spur_teeth = 40;"}</span>
                <span className="block">{"var drive_spur_dp = 20;"}</span>
                <span className="block">{"var drive_spur_pd ="}</span>
                <span className="block pl-6">{"drive_spur_teeth / drive_spur_dp;"}</span>
                <span className="mt-3 block text-primary">{"// frame rail · editable stock section"}</span>
                <span className="block">{"var rail_wall = 0.1;"}</span>
                <span className="block">{"var rail_len = frameLength - 1.5;"}</span>
                <span className="block">{"kaleTube(context, id + \"rail\", ...);"}</span>
              </pre>
              <div className="grid grid-cols-3 border-t border-background/20 text-center font-mono text-[10px]">
                <Metric value="228" label="named parts" />
                <Metric value="1,413" label="editable measures" />
                <Metric value="0" label="mesh bodies" />
              </div>
            </div>
          </div>
        </div>
      </section>

      <section className="border-b border-border">
        <div className="mx-auto grid w-full max-w-[1440px] lg:grid-cols-2">
          <Capability icon={<Box />} index="03" title="CAD models teach geometry" text="Training targets come from the same deterministic part tree used by the viewer, cut list, and Onshape exporter. The model learns part-level structure, not screenshots." />
          <Capability icon={<Braces />} index="04" title="Binders teach judgment" text="Requirements, alternatives, calculations, interfaces, validation evidence, and open risks are trained as one traceable engineering argument." />
        </div>
      </section>

      <section>
        <div className="mx-auto flex w-full max-w-[1440px] flex-col justify-between gap-10 px-6 py-16 sm:px-10 lg:flex-row lg:items-center lg:px-16 lg:py-20">
          <div>
            <p className="font-mono text-[11px] uppercase tracking-[0.16em] text-primary">Build from a requirement</p>
            <h2 className="font-display mt-3 text-4xl font-medium tracking-[-0.04em]">Open the studio. Keep the source.</h2>
          </div>
          <Link href="/design" className="group inline-flex items-center gap-8 border-b border-foreground pb-2 text-sm font-semibold">
            Start a design <MoveRight className="h-5 w-5 transition-transform group-hover:translate-x-2" />
          </Link>
        </div>
      </section>
    </>
  );
}

function Method({ n, title, text }: { n: string; title: string; text: string }) {
  return (
    <article className="border-b border-border px-7 py-12 last:border-b-0 md:border-b-0 md:border-r md:last:border-r-0 lg:px-10 lg:py-24">
      <span className="font-mono text-[10px] text-muted-foreground">{n}</span>
      <h3 className="mt-10 text-xl font-semibold">{title}</h3>
      <p className="mt-4 text-sm leading-7 text-muted-foreground">{text}</p>
    </article>
  );
}

function Metric({ value, label }: { value: string; label: string }) {
  return <div className="border-r border-background/20 px-3 py-4 last:border-r-0"><strong className="block text-lg text-background">{value}</strong><span className="mt-1 block text-background/45">{label}</span></div>;
}

function Capability({ icon, index, title, text }: { icon: React.ReactNode; index: string; title: string; text: string }) {
  return (
    <article className="group border-b border-border px-6 py-14 last:border-b-0 sm:px-10 lg:border-b-0 lg:border-r lg:px-16 lg:py-24 lg:last:border-r-0">
      <div className="flex items-center justify-between text-muted-foreground">
        <span className="grid h-11 w-11 place-items-center border border-border [&_svg]:h-5 [&_svg]:w-5">{icon}</span>
        <span className="font-mono text-[10px]">{index}</span>
      </div>
      <h3 className="font-display mt-12 text-4xl font-medium tracking-[-0.04em]">{title}</h3>
      <p className="mt-5 max-w-xl leading-7 text-muted-foreground">{text}</p>
      <div className="mt-10 flex items-center gap-2 text-xs font-semibold text-primary"><Check className="h-4 w-4" /> Grounded and inspectable</div>
    </article>
  );
}
