# Kale Forge Complete 2026

This folder is a desktop snapshot of the complete Kale Forge workspace after the work performed across the v15 and v18 training, CAD reliability, Onshape, website, and Design Studio tasks.

## Source state

- GitHub repository: `keremtuzun/kale-forge`
- Production branch: `main`
- Main merge commit: `3bad70de623f8ff94bd3de467b8f6ace77381232`
- Merged pull request: `#2 Add v18 training and refined Kale Forge UI`
- Snapshot includes the local v18 adapter, datasets, training configurations, logs, scorecards, and the two intentionally untracked evaluation artifacts.
- Disposable caches, Vercel build output, Git internals, virtual environments, and local `.env` secrets are excluded.

## Model decision

The locked v15 scorecard was:

- Intent: 7/9 valid, 6/9 enum clean, 7 distinct designs
- CAD: 5/9 valid, 0/9 schema clean, 4.4 mean features
- Held out: 5/7 valid, 0/7 schema clean, 7.3 mean features

The strongest accepted v18 checkpoint is `models/adapters/kale-design-qwen3-4b-v18-cad-repair-50`:

- Intent: 9/9 valid, 9/9 clean, 9 distinct designs
- CAD: 6/9 valid, 5/9 schema clean, 17.6 mean features
- Held out: 7/7 valid, 4/7 schema clean, 27.4 mean features

Repair2 and Repair3 regressed and were rejected. They were preserved for auditability. v18 improved substantially over v15, but it did not meet every promotion gate, so the project does not claim perfect CAD generation.

## Reliability architecture

The model now produces a compact parametric design specification. The application then:

1. Validates the specification against a strict schema.
2. Rejects malformed JSON, duplicate parts, illegal features, invalid dimensions, and repeated loops.
3. Compiles validated specifications deterministically into named parts, features, mates, measurements, and editable Onshape source.
4. Uses constrained JSON decoding in the Transformers inference path.
5. Preserves rejected candidates, corrected failure pairs, and preference training tools for future improvement.

## Website and Design Studio

- Only the welcome page and Design Studio are presented as the primary website experience.
- The welcome page uses the supplied Kale Forge mecanum chassis engineering artwork.
- The Design Studio uses the same beige drafting paper theme as the welcome page.
- Visible em dashes were removed from the updated site copy.
- Rendered designs include a dedicated prompt editing dock.
- Prompt revisions preserve the prior parametric design and rebuild every editable part.
- Quick edits include frame width, turret removal, elevator stages, and drive ratio.
- A direct revision test changed a 27 inch turreted elevator design to 25 inches wide, removed the turret and elevator, and retained 183 editable parts.

## Verification

- The targeted robot design test suite passed: 20 tests.
- The Design Studio Python function compiled successfully.
- The Vercel build completed locally.
- GitHub branch and PR changes were merged into `main`.

## Deployment status at snapshot time

`kaleai.vercel.app` remained on the last healthy Vercel deployment because the newer Vercel production deployments stayed in provider status `UNKNOWN`. The unverified deployment was not promoted over the healthy live site. The updated source is present on GitHub `main` and in this folder.
