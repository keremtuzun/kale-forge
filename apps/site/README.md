# Kale Forge — product website

A self-contained, zero-build static site for Kale Forge (marketing / product overview). No
framework, no dependencies, no backend — just `index.html`, `styles.css`, `app.js`.

```
apps/site/
  index.html    full single-page site (hero, pipeline, 42 rules, AI, architecture, examples, security, get-started)
  styles.css    design system (dark default + light toggle, responsive)
  app.js         theme toggle, rule-category tabs, copy buttons, mobile menu
  vercel.json    static config (clean URLs + security headers)
  robots.txt
```

## Preview locally

```bash
cd apps/site
python3 -m http.server 8899      # then open http://localhost:8899
```

## Deploy to Vercel

Any of these work with your normal Vercel account:

**A. Vercel CLI (recommended)**
```bash
npm i -g vercel
cd apps/site
vercel --prod          # first run links/creates the project, then deploys
```

**B. Dashboard drag-and-drop**
Go to vercel.com → Add New → Project → deploy the `apps/site` folder. No build settings
needed (Framework Preset: *Other*, Output Directory: `.`).

**C. Git import**
Push the repo, import it in Vercel, set the project's **Root Directory** to `apps/site`,
Framework Preset **Other**, and deploy.

There is no build step — Vercel just serves the static files.
