# Who to show Kale Forge to

Written 12 August 2026. Nothing here has been sent. These are public communities and public
figures whose work is public, not private contacts, and none of them has been contacted.

## What you are actually selling

Be specific about the wedge, because "AI CAD" is a crowded phrase in 2026 and three funded
companies already own the generic version of it.

> Kale Forge writes parametric source you can keep editing, and labels every number with
> where it came from.

Two things nobody else in the category does:

1. **The output is editable.** FeatureScript with named driving dimensions and a feature
   dialog. Change the bearing OD in Onshape and the mounting holes move. Most text-to-CAD
   ships a mesh or an opaque solid, which is a dead end the moment you need to change it.
2. **Every number is labelled.** Verified, user provided, calculated, inferred, assumed, or
   unresolved. No manufacturer part number is ever invented. The PCB says which stage it
   actually reached instead of implying a finished board.

Third differentiator, narrower but sharper: it knows FRC. Rules, catalog parts, season
constraints, real bumper geometry.

## Tier 1: people who will use it this month

These are FRC teams and mentors. They have a real need, a short decision loop, and they talk
to each other constantly.

| Where | Why them | The ask |
| --- | --- | --- |
| [Chief Delphi](https://www.chiefdelphi.com/) Technical forum | The central FRC forum. Every tool a team adopts gets posted here first. | Post the bearing block demo, not the robot demo. A part in 3 seconds is believable; a whole robot invites scepticism. |
| [2026 FRC Open Alliance directory](https://www.chiefdelphi.com/t/2026-frc-open-alliance-information-and-directory/508112) | Teams that already publish their CAD openly. Highest concentration of people who will try a tool and write about it. | Offer to generate the small parts on their published build threads and post the comparison. |
| [Spectrum 3847](https://spectrum3847.org/) | Runs weekly public Discord design training, Tuesdays 8pm, and publishes a large resource library. Switched to Onshape specifically for openness. | Ask to demo for 10 minutes in a Tuesday design session. |
| FRC 1678 Citrus Circuits fall workshops | The most complete published FRC design curriculum. Their Onshape CAD was featured in PTC Education's CADvent. | A tool that generates the exact parts their curriculum teaches is a natural companion, not a competitor. Offer it as a teaching aid. |
| [OnShape4FRC.com](https://onshape4frc.com/) (Max Westwater, Dave Powers, Jack Tervay, FRC 6328) | Built the Onshape onboarding path for FRC. Kale Forge exports FeatureScript, so it lands exactly where their users already are. | Ask whether a generated part fits their onboarding flow. |
| FRC 6328 Mechanical Advantage | Ship AdvantageKit and AdvantageScope. They know how to make FRC software get adopted, and their audience trusts their taste. | Ask for a critique, not a promotion. It is more likely to be answered and more useful. |
| [r/FRC](https://www.reddit.com/r/FRC/) | Broader, younger, less technical than Chief Delphi. Good for the 3D viewer screenshot. | Same bearing block clip. |

Order matters. Post to Chief Delphi last, after two or three teams have actually used it, so
the thread has evidence in it rather than a claim.

## Tier 2: the category

Not customers. Peers, benchmarks, and possibly the people who hire you or fund you.

| Who | What they are | Why contact them |
| --- | --- | --- |
| [Zoo](https://zoo.dev/) (formerly KittyCAD) | The pioneer. Own geometric kernel, developer-first, Zookeeper conversational CAD agent. | They publish research and care about the parametric problem. A student who shipped provenance labelling on generated CAD is interesting to them. |
| AdamCAD | YC W25, raised 4.1M. Text to parametric 3D with dimension sliders. | Closest to your surface area. Worth knowing exactly where you differ before anyone asks. |
| [Leo AI](https://www.getleo.ai/) | Generative AI for mechanical CAD, geometry-aware search over PDM vaults, SOC-2. | Enterprise angle. Their blog benchmarks the whole category, which is free competitive research. |
| Spectral Labs (SGS-1) | Geometry foundation model. | The model-side approach to the same problem. Useful contrast to your deterministic pipeline. |
| [Onshape Partner program](https://www.onshape.com/partners/apply) | Distribution, not competition. | The single highest-leverage contact on this page. See performance.md item 2. |

## Tier 3: money and mentorship for a student founder

You are at school. Several of these exist specifically for that and several have no lower age
limit.

| Programme | Terms | Note |
| --- | --- | --- |
| Emergent Ventures (Tyler Cowen, Mercatus) | Grants, applications from age 13, worldwide, fast decisions | Lowest friction on this list. A short application about a working tool is exactly its shape. |
| [Neo Scholars](https://neo.com/scholars) | Mentorship, network, funding, path to Neo Residency with a 40k grant | Applications open as of the last check. Verify the current deadline before writing. |
| [Z Fellows](https://www.zfellows.com/) | 10k, one week, high schoolers explicitly welcome | Fast, low commitment, strong alumni network. |
| Conrad Challenge | STEM innovation competition, ages 13 to 18, cycle runs August to April | The 2026 to 2027 cycle is opening now. Kale Forge fits the aerospace and engineering track. |
| 1517 Fund, Thiel Fellowship | Larger cheques for people building instead of finishing school | Later. Both want traction first. |

Verify every deadline yourself before applying. These change and my information is a search
result, not a confirmation.

## Tier 4: launch surfaces

Only after the FRC teams have used it and something is quotable.

- **Show HN.** The honest completion ladder is the story: a generator that refuses to claim a
  board is finished is genuinely unusual and Hacker News notices that kind of thing.
- **Product Hunt.** Lower value for a developer tool with a narrow initial audience. Later.

## Drafts

Short on purpose. Long messages from strangers do not get read.

### Chief Delphi post

> **Kale Forge: type a part, get editable Onshape source**
>
> I have been building a design tool for the last few months and it is at the point where it
> is useful for small parts.
>
> Type "a bearing block for a 1/2 in hex shaft" and you get a block dimensioned outward from
> the bearing: pocket cut to a press fit, a shoulder for the bearing to seat on, a shaft
> relief through the back, four mounting holes inset far enough to clear the pocket. It comes
> out as FeatureScript with the driving dimensions in the feature dialog, so widening the
> block in Onshape moves the holes with it. STEP export too.
>
> The part I care about most: every number says where it came from. The 1.125 in bearing OD
> is marked verified because it is a catalog part. The 0.315 in wall is marked assumed,
> because I picked it, and it tells you why (0.28 times the OD, floored at 0.25 in, thinner
> walls split on the press). It will not invent a critical dimension quietly.
>
> Ten mechanical generators so far: bearing block and housing, shaft, spacer, plate, gusset,
> motor plate, gearbox plate, roller, pulley, L bracket. Ask it for a four bar linkage and it
> tells you it cannot make one, which I would rather do than fake it.
>
> It also does whole robots and schematic-level PCBs, but the parts are the bit I would like
> feedback on.
>
> Free, no account needed for the studio: kaleai.vercel.app
>
> What would you want it to make that it does not?

### Spectrum 3847, asking for 10 minutes

> Hi, I run an FRC design tool called Kale Forge and I would like a hard critique of it from
> people who actually teach this.
>
> It generates individual parts from a description and exports them as parametric
> FeatureScript, so a bearing block arrives in Onshape with its dimensions still editable
> rather than as a dead solid. It shows where every number came from, and it will refuse
> rather than guess a critical one.
>
> Would you be open to 10 minutes in one of the Tuesday design sessions? I would rather be
> told what is wrong with it in front of students than find out later. Happy to just send a
> link if that is easier.

### Onshape partner enquiry

> I have built a tool that generates parametric FeatureScript from a written requirement:
> "a bearing block for a 1/2 in hex shaft" becomes a Feature Studio with named driving
> dimensions and a working feature dialog, not an imported mesh.
>
> It is live at kaleai.vercel.app and the export is copy and paste today. I would like to
> create the Feature Studio through the API instead, and eventually list it on the App Store.
>
> Is the partner programme the right route, and is there anything about generated
> FeatureScript that would need review first?

### Emergent Ventures, opening paragraph

> I am a secondary school student in Istanbul. I built Kale Forge, an engineering design tool
> that turns a written requirement into editable parametric CAD and labels every dimension
> with whether it was verified, calculated, or assumed. It generates individual machined
> parts, competition robot assemblies, and schematic-level circuit boards, and it refuses to
> claim a stage it has not actually reached. It is live and it is used by robotics teams.
>
> The grant would go toward [parts database licensing / compute / the specific thing you
> need]. Fill this in with a real number and a real reason.

## One rule

Do not describe it as finished. The most credible thing about this project is that it says
what it cannot do, and that is unusual enough to be the pitch.
