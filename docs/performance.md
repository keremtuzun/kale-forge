# Making Kale Forge better, in order of leverage

Written 12 August 2026. Everything here is measured against `services/analysis/app/tests/bench.py`.

## The instrument

```bash
cd services/analysis && python -m app.tests.bench
```

55 prompts, graded with partial credit, so an improvement that fixes half a problem is
visible instead of invisible. Per case: 0.35 for the design type, 0.20 for the part type,
0.15 for season relevance, 0.30 for dimensions inside 0.02 in. Refusal cases score 1.0 for
refusing and 0.0 for building something, because a confidently wrong part is worse than no
part at all.

**Baseline, 12 Aug 2026: 1.000 across 55 cases, 55 perfect, 0 zero. 1 ms median, 2.8 s worst.**

History, so a regression is obvious:

| Date | Score | Cases | What changed |
| --- | --- | --- | --- |
| 12 Aug, first run | 0.975 | 44 | the instrument existed for the first time |
| 12 Aug, after fixes | 0.979 | 52 | four defects fixed, hard cases added and tightened |
| 12 Aug, gaps closed | 0.993 | 53 | spelled-out numbers, quantities, called-out holes, plus routing |
| 12 Aug, rip-up | 1.000 | 55 | rip-up and reroute; the crowded board routes, two more added |

A score of 1.000 means the benchmark has stopped being an instrument. Add harder cases.

The suite already answers "is anything broken". This answers "is it getting better", which is
the question that matters once the obvious bugs are gone. It found four real defects within a
minute of first running, two of which the pass/fail suite could not see:

1. `two retaining ring grooves` raised ValueError and returned a 500. `dict.get()` evaluates
   its default eagerly, so `int("two")` ran before the word lookup that would have handled it.
2. `5 x 5 in plate 1/4 in thick` produced a plate five inches thick. The role window ran 28
   characters past each number without stopping, so the "thick" belonging to the 1/4 was close
   enough to claim the 5 as well.
3. `a 20 mm bore bearing, 47 mm OD` sized the block for the 20 mm. Both "bore" and "bearing"
   scored equally and the tie broke on table order.
4. `a dead axle block` and `a mount that holds two bearings` both came back as a plain length
   of hex shaft.

All four are fixed. Run the benchmark before and after every change.

## Known gaps, named rather than hidden

Every case passes as of 12 Aug 2026, which is a statement about the benchmark rather than
about the tool. What is still not done is listed under `MANUFACTURING_READY` below, and the
next honest gaps will come from prompts nobody here thought to write.

Limits worth knowing, all of them stated in the output rather than buried here:

- Pads are modelled at 0.4 mm grid resolution, so escape routing around fine-pitch packages
  has not been checked against real pad drawings.
- Package envelopes and pad maps are IPC nominal figures for the generic package, not vendor
  drawings.
- Clearance and drill limits are ordinary cheap-fab numbers, not a quote from a board house.

## Ranked work

### 1. Resolve requirements to real parts (biggest credibility gain)

Right now every active component on a generated board is a requirement string, because the
rule against inventing MPNs is absolute and there is no parts database. A distributor API
(Octopart, Digi-Key, Mouser) turns `5 V switching regulator, >= 3 A out, >= 18 V input` into
three real orderable parts with stock and price, marked VERIFIED because they were looked up
rather than guessed. This is the difference between a plan and a BOM you can buy.

Same move on the mechanical side: WCP, REV, AndyMark and ThriftyBot part numbers against the
catalog entries that are currently marked ASSUMED, especially the motor face patterns.

### 2. Onshape round trip

FeatureScript export is currently copy and paste. The Onshape API can create the Feature
Studio directly, and Kale Forge already emits valid parametric source with dialog parameters.
For an FRC team this is the difference between a demo and a tool they use on a Tuesday.

Route: <https://www.onshape.com/partners/apply>, then the App Store launch checklist at
<https://onshape-public.github.io/docs/app-store/>. OAuth2 is required to publish.

This is also distribution: the Onshape App Store puts the tool in front of every Onshape user
rather than every person who finds the website.

### 3. Gerbers, and only then MANUFACTURING_READY

Placement, routing and rule check are all true per board, decided by checks. Rip-up and
reroute landed on 12 Aug 2026 and the crowded board that motivated it now routes in about two
seconds.

What actually made it converge was not the rip-up. It was congestion history. Tearing up
whoever is in the way solved three of the four stranded nets immediately, and then CANH and
CANL sat swapping the same corridor for ever, each evicting the other, because neither had any
memory of the fight. One byte per cell counting how often copper there had been torn up, added
to the path cost, ended it in four evictions. That is the PathFinder idea and it is worth
knowing before writing any router: rip-up without history is a coin toss repeated.

Two things the router found by failing, which is the argument for building it at all:

- Every sensor port was wired to all three supply rails at once. A dead short between 12 V,
  5 V and 3.3 V, invisible to a connectivity check that only asks whether each net has two
  pins. There is an ERC rule for it now.
- A regulator's EN and FB pins are copper whether or not a design connects them. The router
  could not see unnetted pads at all and ran traces straight across them.

Left on this ladder: `MANUFACTURING_READY`, which needs gerbers, a drill file, a
pick-and-place, and ideally a fab's own DRC agreeing. Do not flip that flag for anything less.

### 4. Model in the loop, as a tie-breaker only

The intent classifier is deterministic keyword scoring. That is a feature: it is 0 ms,
debuggable, and it cannot hallucinate. But it is brittle at the edges, and every gap the
benchmark has found so far has been at one of them.

The right shape is not to replace it. It is to call the self-hosted model only when the
deterministic margin is low, validate its answer against the same closed vocabulary, and fall
back to the deterministic result when validation fails. Coverage goes up, determinism stays,
and the failure mode stays bounded.

### 5. Latency where the user actually feels it

The pipeline is fast: 1 ms median, and the worst case is now a dense board routing in
under three seconds rather than a robot compiling in 300 ms. That is not the number
a user experiences. Vercel cold start on a serverless Python function is, and it is not
measured yet. Measure it, then decide whether it is worth keeping a warm path.

Nothing caches. An identical prompt chain rebuilds the whole robot on every load.

### 6. Keep feeding the benchmark

The three parser gaps are closed, and closing them took under an hour each because the
benchmark said exactly what was wrong. The next three will come from users, not from me.
Every refusal and every wrong part becomes a case before it becomes a fix.

### 7. More generators, driven by what the benchmark shows

Do not add generators speculatively. Add benchmark cases for prompts people actually send,
watch the refusals, and build what shows up. The refusal path is honest and it is also a
free record of demand.
