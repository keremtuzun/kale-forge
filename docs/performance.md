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

**Baseline, 12 Aug 2026: 0.993 across 53 cases, 52 perfect, 0 zero. 1 ms median, 2.6 s worst.**

History, so a regression is obvious:

| Date | Score | Cases | What changed |
| --- | --- | --- | --- |
| 12 Aug, first run | 0.975 | 44 | the instrument existed for the first time |
| 12 Aug, after fixes | 0.979 | 52 | four defects fixed, hard cases added and tightened |
| 12 Aug, gaps closed | 0.993 | 53 | spelled-out numbers, quantities, called-out holes, plus routing |

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

The three parser gaps are closed. "Three quarters of an inch" parses, "three 0.5 in spacers"
makes three spacers, and "with a 25 mm hole in the middle" cuts the hole (or refuses out loud
when the hole would not leave any material).

One case scores below 1.0 on purpose, and it is the honest edge of the tool.

| Case | Score | What is missing |
| --- | --- | --- |
| `pcb-crowded` | 0.60 | 37 parts and 8 nets on one board: two rails find no path. The board stops at `PLACEMENT_COMPLETE` and says which nets failed. |

That case is the routing frontier. Fixing it means real rip-up-and-reroute rather than the
two-ordering retry that is there now, and it should be fixed by making the router better, not
by removing the case.

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

### 3. Rip-up and reroute, then gerbers

`PLACEMENT_COMPLETE`, `ROUTING_COMPLETE` and `RULE_CHECK_COMPLETE` all became true on
12 Aug 2026, and all three are decided per board by checks rather than declared. Six of the
seven benchmark boards reach the top rung in under 200 ms.

Two things left on this ladder:

- **Rip-up and reroute.** The router tries two net orderings and keeps the better one. A
  crowded board (37 parts, 8 nets) still strands two rails. Proper rip-up would take it.
- **`MANUFACTURING_READY`.** That needs gerbers, a drill file and a pick-and-place, and
  ideally a fab's own DRC agreeing. Do not flip that flag for anything less.

Note what the DRC caught when it was first switched on: the router was reserving corridors
too narrow for its own clearance rule, and using a circular mask that left the diagonal
neighbour open. Both were invisible to the router, which believed it had obeyed itself. Keep
the DRC independent for exactly that reason.

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

### 6. Keep feeding the benchmark

The three parser gaps are closed, and closing them took under an hour each because the
benchmark said exactly what was wrong. The next three will come from users, not from me.
Every refusal and every wrong part becomes a case before it becomes a fix.

### 7. More generators, driven by what the benchmark shows

Do not add generators speculatively. Add benchmark cases for prompts people actually send,
watch the refusals, and build what shows up. The refusal path is honest and it is also a
free record of demand.
