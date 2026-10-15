# Redwood Professional — Design Tokens Reference

> Source of truth: `globals.css`. This file is a human-readable mirror — if the two ever disagree, the CSS wins.

Design philosophy (from `globals.css` source):

> Redwood Professional design tokens. Light is the default theme (matches Oracle Fusion / Workday / Stripe). Dark mode is enabled by adding `class="dark"` on `<html>`.

---

## Color tokens

All colors are stored as **bare HSL triplets** (e.g. `213 59% 26%`) and consumed as `hsl(var(--token))` so opacity modifiers (`hsl(var(--primary) / 0.1)`) just work. **Do not** wrap the values in `hsl(...)` inside `:root`.

### Light theme (default)

| Token | HSL | Hex (approx) | Role |
|---|---|---|---|
| `--surface-0` | `60 11% 98%` | `#FAFAF7` | Warm off-white canvas (page bg) |
| `--surface-1` | `0 0% 100%` | `#FFFFFF` | Cards, sidebar, header |
| `--surface-2` | `39 17% 94%` | `#F3F1EC` | Hover / secondary surfaces |
| `--surface-3` | `39 16% 89%` | `#EAE7DF` | Pressed / raised surfaces |
| `--background` | → `--surface-0` | `#FAFAF7` | |
| `--foreground` | `0 0% 10%` | `#1A1A1A` | Ink |
| `--card` | → `--surface-1` | `#FFFFFF` | |
| `--popover` | → `--surface-1` | `#FFFFFF` | |
| `--primary` | `213 59% 26%` | `#1B3A6B` | **Deep navy** — primary actions |
| `--primary-foreground` | `0 0% 100%` | `#FFFFFF` | |
| `--secondary` | `39 17% 94%` | `#F3F1EC` | Same as surface-2 |
| `--accent` | `8 60% 49%` | `#C74634` | **Oracle red** — sparingly |
| `--info` | `213 59% 36%` | `#254E8E`-ish | |
| `--success` | `124 46% 33%` | `#2E7D32` | Forest green |
| `--warning` | `36 100% 35%` | `#B26A00` | Amber-bronze |
| `--danger` / `--destructive` | `8 60% 49%` | `#C74634` | Same as accent |
| `--muted` | → `--surface-2` | `#F3F1EC` | |
| `--muted-foreground` | `0 0% 42%` | `#6B6B6B` | |
| `--border` / `--input` | `41 13% 83%` | `#D9D6CE` | |
| `--ring` | `213 59% 36%` | `#254E8E` | Focus ring — navy at higher lightness for visibility |

### Dark theme (`.dark` on `<html>`)

| Token | HSL | Hex (approx) | Notes |
|---|---|---|---|
| `--surface-0` | `220 12% 9%` | `#15171A` | |
| `--surface-1` | `220 9% 12%` | `#1B1E22` | |
| `--surface-2` | `220 8% 16%` | `#23272D` | |
| `--surface-3` | `220 8% 19%` | `#2C3036` | |
| `--foreground` | `40 16% 91%` | `#ECEAE4` | |
| `--primary` | `213 41% 55%` | `#5B83BD` | Lighter navy — readable on dark |
| `--accent` | `8 70% 62%` | `#E5675A` | Softer Oracle red |
| `--success` | `124 38% 62%` | `#7CC082` | |
| `--warning` | `32 70% 60%` | `#E2A04D` | |
| `--danger` / `--destructive` | `8 70% 62%` | `#E5675A` | |
| `--muted-foreground` | `220 5% 60%` | ~`#979A9F` | |
| `--border` / `--input` | `220 7% 23%` | `#34383E` | |
| `--ring` | `213 41% 60%` | `#6D90C5` | |

### Brand-only color (not a token)

| Use | Hex | Where |
|---|---|---|
| Logo plate background | `#15171A` | Always — both themes. Hard-coded `bg-[#15171A]` in `header.tsx` so the white-knockout logo survives both modes. |
| Oracle red (literal) | `#C74634` | Exposed as `oracle.red` in Tailwind config for direct use; identical to `--accent` in light mode. |

---

## Spacing & shape

| Token | Value | Notes |
|---|---|---|
| `--radius` | `0.375rem` (6px) | Tighter than default shadcn — "more enterprise" |
| `xl` corner | `calc(var(--radius) + 4px)` = 10px | |
| `lg` corner | `var(--radius)` = 6px | |
| `md` corner | `calc(var(--radius) - 2px)` = 4px | |
| `sm` corner | `calc(var(--radius) - 4px)` = 2px | |

## Shadow scale

Driven by `--shadow-color` (213 30% 15% light / 0 0% 0% dark) and three strength vars (sm/md/lg). The Tailwind `shadow-sm`/`shadow`/`shadow-md`/`shadow-lg` utilities are overridden to use these tokens — colored shadows that read as navy on light, true black on dark.

## Typography

| Family | Variable | Source |
|---|---|---|
| Sans | `--font-sans` | Inter (via `next/font/google`) |
| Mono | `--font-mono` | IBM Plex Mono (weights 400/500/600) |

Body sets `font-feature-settings: "rlig" 1, "calt" 1, "cv11" 1;` for proper ligatures and Inter's stylistic alternates. Headings get `font-semibold tracking-tight` globally.

Helper classes (`@layer components`):
- `.text-page-title` — `text-2xl font-semibold tracking-tight`
- `.text-section-title` — `text-base font-medium tracking-tight`
- `.text-helper` — `text-sm text-muted-foreground`
- `.text-label` — `text-xs font-medium uppercase tracking-wider text-muted-foreground`
- `.num` — `font-variant-numeric: tabular-nums` (use on table cells for aligned digits)

## Status pills

Five canonical statuses, each a class:

| Class | Bg | Border | Text |
|---|---|---|---|
| `.status-draft` | `muted` | `border` | `muted-foreground` |
| `.status-building` | `info / 0.1` | `info / 0.3` | `info` |
| `.status-testing` | `warning / 0.1` | `warning / 0.3` | `warning` |
| `.status-published` | `success / 0.1` | `success / 0.3` | `success` |
| `.status-failed` | `destructive / 0.1` | `destructive / 0.3` | `destructive` |

Pattern is generalizable: any semantic color rendered as a pill = `bg-[hsl(var(--X)/0.1)] border-[hsl(var(--X)/0.3)] text-[hsl(var(--X))]`.

## Misc utilities defined in `globals.css`

- `.card-elevated` — `bg-card border border-border rounded-md shadow-sm`
- `.card-interactive` — `card-elevated` + hover bumps `shadow-md` and `border-foreground/15`
- `.sql-editor` — mono font + `tab-size: 2`
- `.pulse-dot` — pulsing-halo attention marker (2s `pulse-ring` keyframe)
- Custom 10px webkit scrollbars with `border-radius: 8px` thumb
- `prefers-reduced-motion` blanket override that kills all animation durations
- `[data-radix-tabs-content]` — full-height flex layout (only relevant if you use Radix Tabs for full-height layouts)
