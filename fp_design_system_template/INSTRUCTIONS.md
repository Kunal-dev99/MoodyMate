# INSTRUCTIONS — Replicating the FP Reporting look in another Claude Code project

Copy-paste the block at the bottom of this file into Claude Code in the target project to drive the work. The reference for everything below is the live `fp_reporting` codebase at `/home/webdev/fp_reporting/`.

---

## What you're applying

A complete, drop-in Next.js 14 + Tailwind 3 + shadcn/ui design system called **"Redwood Professional"**. The look is Oracle-Fusion / Workday / Stripe-flavored:

- Light by default, dark mode via `class="dark"` on `<html>` (pre-hydration script prevents FOUC)
- Deep-navy `#1B3A6B` primary, Oracle-red `#C74634` accent used sparingly
- Warm off-white canvas (`#FAFAF7`) with a 4-tier surface elevation scale
- Tight 6px radius, custom colored shadow scale, Inter + IBM Plex Mono typography
- Sticky header with a white-knockout logo on a fixed `#15171A` plate, collapsible sidebar with active-rail indicator, breadcrumb-driven page header, accent-rail page sections
- Status pills (draft/building/testing/published/failed) as one-class utilities
- Custom 10px scrollbars, accessible focus rings, `prefers-reduced-motion` respected

---

## Prerequisites in the target app

1. **Next.js 14, React 18, Tailwind 3.4, TypeScript** — versions in `package-dependencies.json`. Tailwind 4 is *not* supported here (token consumption changed).
2. **`tsconfig.json`** must have the shadcn path alias:
   ```json
   "paths": { "@/*": ["./src/*"] }
   ```
   If your app uses a different base, every import in the copied files needs the same prefix updated. Easiest: match `fp_reporting` and keep everything under `src/`.
3. **shadcn CLI is not required** at install time — all primitives are pre-copied. Keep `components.json` so future `npx shadcn add <new-component>` commands inherit the same style/baseColor.

---

## Step-by-step

### 1. Install deps
Run the `install_one_liner` from `package-dependencies.json`, plus any "optional" entries you need (e.g. `cmdk` only if you use the command palette, `react-day-picker` only if you use the date picker).

### 2. Copy files into the target app

| From this bundle | To the target app |
|---|---|
| `tailwind.config.ts` | `<root>/tailwind.config.ts` |
| `postcss.config.js` | `<root>/postcss.config.js` |
| `components.json` | `<root>/components.json` |
| `globals.css` | `src/app/globals.css` |
| `lib/utils.ts` | `src/lib/utils.ts` |
| `lib/theme.ts` | `src/lib/theme.ts` |
| `components/ui/*` | `src/components/ui/` (all 28 files) |
| `components/layout/*` | `src/components/layout/` |
| `components/common/*` | `src/components/common/` |
| `app/layout.tsx` | `src/app/layout.tsx` (overwrite — has the pre-hydration script) |
| `app/api/logo/route.ts` | `src/app/api/logo/route.ts` (optional, see step 4) |
| `assets/fp-logo-oraclepartner-l.png` | wherever your logo route reads from |

### 3. Wire up the layout

In `src/app/layout.tsx` the only edits needed are:
- The `metadata` object (title + description for your app).
- Nothing else — the pre-hydration theme script, font wiring, and `<Toaster />` mount stay verbatim.

### 4. Brand asset — pick one of two

**Option A (simpler):** put the logo at `public/brand/logo.png`, then `<Image src="/brand/logo.png" />` in `<Header>`. Delete `app/api/logo/route.ts`.

**Option B (matches fp_reporting):** keep `app/api/logo/route.ts` and place the PNG at `<repo-root>/logo/<name>.png` (one level *above* the Next.js app). Useful if multiple services share the asset. Update the `logoPath` constant in `route.ts` to match your filename.

**Hard rule (do not deviate):** the logo must always render on a `bg-[#15171A]` rounded plate. The `Header` component does this already. Don't try to "make the logo blend with the theme" — the white-knockout asset depends on a dark backdrop, and `#15171A` is the only color guaranteed to give it contrast in both light and dark mode.

### 5. App shell

In the page or layout that hosts the authenticated app shell, render:

```tsx
import { Sidebar, type NavItem } from '@/components/layout/sidebar'
import { Header } from '@/components/layout/header'
import { LayoutDashboard, FileText, Settings, Users } from 'lucide-react'

const mainNav: NavItem[] = [
  { href: '/dashboard', label: 'Dashboard', icon: LayoutDashboard },
  { href: '/items', label: 'Items', icon: FileText },
]
const adminNav: NavItem[] = [
  { href: '/users', label: 'Users', icon: Users },
  { href: '/settings', label: 'Settings', icon: Settings },
]

<Sidebar
  mainNav={mainNav}
  adminNav={adminNav}
  brandName="My Company"
  brandTagline="Tagline here"
  brandShort="MC"
  brandHref="/dashboard"
  user={{ name: currentUser.name, email: currentUser.email }}
  onLogout={handleLogout}
/>
<Header
  title="My Product"
  logoSrc="/api/logo"
  logoAlt="My Company"
  onOpenPalette={() => /* dispatch Cmd+K */}
  actions={<>{/* notifications, avatar, etc. */}</>}
/>
<main className="ml-64 transition-all duration-300">{children}</main>
```

The sidebar manages its own collapse state in `localStorage['fp_sidebar_open']`. If you want the main content to react to collapse, lift that to a store (see `fp_reporting/src/lib/store.ts` for the pattern — `sidebarOpen` + `toggleSidebar`).

### 6. Pages

Inside each page:

```tsx
import { PageHeader } from '@/components/common/PageHeader'
import { PageSection } from '@/components/common/PageSection'
import { Sparkles } from 'lucide-react'

export default function Page() {
  return (
    <>
      <PageHeader title="My page" description="One-line description." />
      <div className="px-6 pb-6 space-y-4">
        <PageSection icon={Sparkles} title="A section" accent="primary">
          {/* body */}
        </PageSection>
      </div>
    </>
  )
}
```

**One file you must edit:** `components/common/PageHeader.tsx` contains a `ROUTE_LABELS` map that maps URL segments to breadcrumb labels (`'sql-builder' → 'SQL Builder'`). Replace these with your app's routes, or delete the map and let `PageHeader` auto-title-case every segment.

### 7. Theme toggle

`<ThemeToggle />` is mounted inside the sidebar footer automatically when you pass `user`. To use it elsewhere, just import it. The store (`lib/theme.ts`) writes to `localStorage['fp_theme']` — the same key the pre-hydration script in `app/layout.tsx` reads. **Don't change either key without changing both.**

### 8. Verify the install

After running `npm run dev`:

- [ ] Page background is warm off-white (`#FAFAF7`), not neutral gray. If it's gray, `globals.css` didn't get loaded — check the `import './globals.css'` in `layout.tsx`.
- [ ] Buttons (`<Button />`) render with a navy primary, 6px radius, subtle shadow. If they're black with no shadow, the `--primary` token didn't load.
- [ ] Toggling theme from the sidebar footer instantly flips dark mode with no flash on reload. If you see a flash, the pre-hydration `<script>` is missing from `<head>`.
- [ ] Sidebar active item has a 3px navy rail on the left + lightly-tinted background.
- [ ] Status pills (`<Badge variant="success" />`) render with low-opacity colored fills, not solid blocks.
- [ ] The logo plate stays dark slate in both light and dark themes.

If any of the above is off, the most common culprit is **shadcn `baseColor`** in `components.json` being `neutral` or `zinc` — it must be `slate` (matches our tokens) or, more importantly, the `cssVariables: true` flag must be set.

---

## What this bundle does NOT include

- **Auth/session/store** — fp_reporting uses Zustand (`useAuthStore`, `useAppStore`). The shell needs *some* way to know the current user; pass it in via props. Don't import fp_reporting's store.
- **Command palette implementation** — `Header` accepts `onOpenPalette` as a callback; wire it to your own keybinding + dialog. See `frontend/src/components/common/CommandPalette.tsx` in fp_reporting for the reference implementation.
- **Workspace switcher** — domain-specific to fp_reporting (Oracle environments). Don't port it.
- **Application-specific components** — `catalogue/`, `reports/`, `sql/`, `template-designer/`, `matching/`, `security/`, `data-grid/`. Out of scope.

---

## Copy-paste prompt for Claude Code in the target project

> Below is a prompt you can paste into Claude Code in the *other* project. Replace `<DESIGN_SYSTEM_PATH>` with the absolute path to this bundle.

```
I have a design system bundle at <DESIGN_SYSTEM_PATH>. It contains everything
needed to give my app the "Redwood Professional" look (deep-navy primary,
Oracle-red accent, warm off-white canvas, sticky header + collapsible
sidebar, light/dark theming with no FOUC).

Please apply it to this project by following <DESIGN_SYSTEM_PATH>/INSTRUCTIONS.md
exactly. Specifically:

1. Read <DESIGN_SYSTEM_PATH>/README.md and <DESIGN_SYSTEM_PATH>/INSTRUCTIONS.md
   in full first.
2. Install the dependencies from <DESIGN_SYSTEM_PATH>/package-dependencies.json
   (use the `install_one_liner` field).
3. Copy every file from the bundle to the paths listed in the
   "Step-by-step → Copy files" table in INSTRUCTIONS.md, preserving directory
   structure. Overwrite existing tailwind.config.ts, postcss.config.js,
   components.json, and src/app/globals.css. Overwrite src/app/layout.tsx
   if it exists.
4. Make sure src/tsconfig.json has `"paths": { "@/*": ["./src/*"] }`.
5. Edit components/common/PageHeader.tsx ROUTE_LABELS to match this app's
   routes (or delete the map).
6. Place the logo PNG from <DESIGN_SYSTEM_PATH>/assets/ in this app
   following the "Option A" guidance in INSTRUCTIONS.md (simpler):
   put it at public/brand/logo.png and update the <Image src> in
   components/layout/header.tsx to "/brand/logo.png" — then delete
   src/app/api/logo/route.ts.
7. Wire <Sidebar> + <Header> into the authenticated layout following the
   "App shell" example in INSTRUCTIONS.md. Use this app's actual routes
   and current-user info — do NOT import any fp_reporting-specific store.
8. Run `npm run dev` and walk through the "Verify the install" checklist
   at the bottom of INSTRUCTIONS.md, fixing anything that fails.

Do not invent extra components or restyle anything beyond what is in the
bundle. The goal is a verbatim look match. When in doubt, treat the bundle
as the source of truth.
```
