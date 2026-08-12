# Making Kale Forge better, in order of leverage

Written 12 August 2026. Everything here is measured against `services/analysis/app/tests/bench.py`.

## The instrument

```bash
cd services/analysis && python -m app.tests.bench
```

52 prompts, graded with partial credit, so an improvement that fixes half a problem is
visible instead of invisible. Per case: 0.35 for the design type, 0.20 for the part type,
0.15 for season relevance, 0.30 for dimensions inside 0.02 in. Refusal cases score 1.0 for
refusing and 0.0 for building something, because a confidently wrong part is worse than no
part at all.

**Baseline, 12 Aug 2026: 0.979 across 52 cases, 49 perfect, 0 zero. 0 ms median, 310 ms worst.**

The suite already answers "is anything broken". This answers "is it getting better", which is
the question that matters once the obvious bugs are gone. It found four real defects within a
minute of first running, two of which the 470-case pass/fail suite could not see:

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

Three benchmark cases score below 1.0 on purpose. They are the honest edge of the tool.

| Case | Score | What is missing |
| --- | --- | --- |
| `hard-spelled-number` | 0.70 | "three quarters of an inch" is not parsed. Only digits and fractions are. |
| `hard-count` | 0.60 | "three 0.5 in spacers" makes one spacer. Counts are ignored for mechanical parts. |
| `hard-metric-plate` | 0.60 | "with a 25 mm hole in the middle" is ignored. Only pattern holes are generated. |

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

### 3. PCB routing, the next honest rung

`PLACEMENT_COMPLETE` became true on 12 Aug 2026 and it is true per board, decided by geometric
checks rather than declared. `ROUTING_COMPLETE` is the next one. Even a two-layer
power-and-ground pour plus manhattan routing on the signal nets would move it, and the checks
already exist to verify it. Do not flip the flag until they pass.

### 4. Model in the loop, as a tie-breaker only

The intent classifier is deterministic keyword scoring. That is a feature: it is 0 ms,
debuggable, and it cannot hallucinate. But it is brittle at the edges, which is what the three
known gaps are.

The right shape is not to replace it. It is to call the self-hosted model only when the
deterministic margin is low, validate its answer against the same closed vocabulary, and fall
back to the deterministic result when validation fails. Coverage goes up, determinism stays,
and the failure mode stays bounded.

### 5. Latency where the user actually feels it

The pipeline is fast: 0 ms median, 310 ms worst for a 339-part robot. That is not the number
a user experiences. Vercel cold start on a serverless Python function is, and it is not
measured yet. Measure it, then decide whether it is worth keeping a warm path.

Nothing caches. An identical prompt chain rebuilds the whole robot on every load.

### 6. The three named gaps above

Cheap, and each one is a prompt a real person will type. Word numbers and fractions first,
then counts, then explicit feature requests on a plate.

### 7. More generators, driven by what the benchmark shows

Do not add generators speculatively. Add benchmark cases for prompts people actually send,
watch the refusals, and build what shows up. The refusal path is honest and it is also a
free record of demand.
