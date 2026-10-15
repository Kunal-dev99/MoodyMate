# FP Design System — "Redwood Professional"

A drop-in Next.js 14 + Tailwind 3 + shadcn/ui design system extracted verbatim from the [`fp_reporting`](https://fpsql.ajvaz.com) Oracle Fusion AI Reporting Platform.

This bundle is **read-only source files** — copy them into your target project. There is no runtime, no npm package, no build step. You install the same deps, you copy the same files, you get the same look.

## What's in here

```
fp_design_system/
├── README.md                  # You are here
├── INSTRUCTIONS.md            # Step-by-step + copy-paste Claude Code prompt
├── tokens.md                  # Human-readable color/spacing/typography reference
├── package-dependencies.json  # Exact versions + one-line install command
│
├── tailwind.config.ts         # Tailwind config (extends default with our tokens)
├── postcss.config.js          # PostCSS plugin chain
├── components.json            # shadcn CLI config (slate baseColor, CSS vars on)
├── globals.css                # Design tokens (CSS vars) + base + components layers
│
├── lib/
│   ├── utils.ts               # cn() helper (clsx + tailwind-merge)
│   └── theme.ts               # Zustand theme store (light/dark/system)
│
├── components/
│   ├── ui/                    # 27 shadcn primitives, verbatim from fp_reporting
│   ├── layout/
│   │   ├── header.tsx         # Sticky top bar (generic, takes props)
│   │   ├── sidebar.tsx        # Collapsible sidebar (generic, takes nav items)
│   │   └── ThemeToggle.tsx    # Sun/Moon/Monitor dropdown
│   └── common/
│       ├── PageHeader.tsx     # Page title + auto-breadcrumbs
│       └── PageSection.tsx    # Card with optional accent rail + icon tile
│
├── app/
│   ├── layout.tsx             # Root layout — fonts + pre-hydration theme script
│   └── api/logo/route.ts      # Logo route (optional, see INSTRUCTIONS)
│
└── assets/
    └── fp-logo-oraclepartner-l.png  # Brand logo (white knockout, 4.7KB)
```

## Quick start

```bash
# 1. cd into your target Next.js 14 project
cd /path/to/other/app

# 2. Install dependencies (full command in package-dependencies.json)
npm i next@^14.2.33 react@^18.2.0 react-dom@^18.2.0 \
      tailwindcss@^3.4.1 tailwindcss-animate@^1.0.7 \
      class-variance-authority@^0.7.0 clsx@^2.1.0 tailwind-merge@^2.2.1 \
      zustand@^4.5.0 lucide-react@^0.330.0 \
      @radix-ui/react-{accordion,alert-dialog,checkbox,collapsible,dialog,dropdown-menu,label,popover,progress,scroll-area,select,separator,slot,tabs,toast,tooltip}

# 3. Copy files following the table in INSTRUCTIONS.md
cp -r /home/webdev/fp_design_system/lib/* src/lib/
cp -r /home/webdev/fp_design_system/components/* src/components/
cp /home/webdev/fp_design_system/globals.css src/app/globals.css
cp /home/webdev/fp_design_system/tailwind.config.ts ./tailwind.config.ts
cp /home/webdev/fp_design_system/postcss.config.js ./postcss.config.js
cp /home/webdev/fp_design_system/components.json ./components.json
cp /home/webdev/fp_design_system/app/layout.tsx src/app/layout.tsx

# 4. Drop the logo
mkdir -p public/brand && cp /home/webdev/fp_design_system/assets/fp-logo-oraclepartner-l.png public/brand/logo.png

# 5. Wire <Header> + <Sidebar> into your authenticated layout
#    (see "App shell" example in INSTRUCTIONS.md)

# 6. Run + verify
npm run dev
```

## Provenance

Every artefact is sourced from a real file in fp_reporting:

| Bundle file | Source in fp_reporting |
|---|---|
| `tailwind.config.ts` | `frontend/tailwind.config.ts` (verbatim) |
| `globals.css` | `frontend/src/app/globals.css` (verbatim) |
| `components.json` | `frontend/components.json` (verbatim) |
| `lib/utils.ts` | `frontend/src/lib/utils.ts` (verbatim) |
| `lib/theme.ts` | `frontend/src/lib/theme.ts` (verbatim) |
| `components/ui/*` | `frontend/src/components/ui/*` (verbatim) |
| `components/layout/ThemeToggle.tsx` | `frontend/src/components/layout/ThemeToggle.tsx` (verbatim) |
| `components/layout/header.tsx` | `frontend/src/components/layout/header.tsx` (generalized — same visuals, props instead of fp_reporting auth store) |
| `components/layout/sidebar.tsx` | `frontend/src/components/layout/sidebar.tsx` (generalized — same visuals, nav as props, local-storage collapse state) |
| `components/common/*` | `frontend/src/components/common/PageHeader.tsx`, `PageSection.tsx` (verbatim) |
| `app/layout.tsx` | `frontend/src/app/layout.tsx` (metadata.title placeholder is the only edit) |
| `app/api/logo/route.ts` | `frontend/src/app/api/logo/route.ts` (verbatim — optional in the target app) |
| `assets/fp-logo-oraclepartner-l.png` | `logo/fp-logo-oraclepartner-l.png` (verbatim) |

Nothing in this bundle was invented. The two "generalized" files (`header.tsx`, `sidebar.tsx`) preserve every class, dimension, color, hover state, and pixel from the originals — only the data plumbing (auth store, workspace switcher, notification bell) was lifted out so they accept props instead. The original notification bell + workspace switcher + user dropdown still belong in the `actions` prop of `<Header>` in the target app, wired to the target app's own data.

## Reading order

1. **`INSTRUCTIONS.md`** — what to do, step by step. Includes a copy-paste prompt for Claude Code in the target project.
2. **`tokens.md`** — every color, radius, shadow, and typography value with HSL + hex + role.
3. **`package-dependencies.json`** — exact versions and the one-line install command.
4. **`globals.css`** and **`tailwind.config.ts`** — the actual source of truth for everything.

## Branding note

The "Fusion Practices — Oracle Partner" branding (logo, name, tagline) is baked into the *content* of the original sidebar and header but **not** into the design system. The bundle's generalized `<Sidebar>` and `<Header>` take `brandName`, `brandTagline`, `brandShort`, `logoSrc`, `logoAlt`, `title` as props. The logo PNG in `assets/` is provided as a working default — replace it with your own asset and pass a new `logoSrc`. The visual treatment (white-knockout-on-`#15171A`-plate) is what the design system mandates, not the specific image.
