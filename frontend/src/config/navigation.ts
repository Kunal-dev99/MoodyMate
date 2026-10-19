import {
  Bot,
  Calculator,
  Database,
  FileBarChart,
  FileStack,
  GitCompare,
  Landmark,
  LayoutDashboard,
  ScrollText,
  ShieldCheck,
  type LucideIcon,
} from 'lucide-react'
import type { Schemas } from '@/api/client'
import type { NavItem } from '@/components/layout/sidebar'
import { CALCULATIONS_UI_ENABLED, CREDIT_DOMAIN_UI_ENABLED, DOMAIN_UI_ENABLED, RATINGS_UI_ENABLED, RULES_UI_ENABLED } from '@/config/features'

/**
 * Platform workspaces (Architecture §14). `planned` workspaces render a clear
 * "planned" state until the delivery-plan phase that builds them; they are never
 * presented as working features.
 */
export interface Workspace extends NavItem {
  icon: LucideIcon
  slug: string
  description: string
  /** Hidden from the nav, and shown as 403, without this permission (cosmetic; D-04). */
  permission?: Schemas['Permission']
  planned?: { phase: string; workstream: string }
}

export const mainWorkspaces: Workspace[] = [
  {
    slug: 'dashboard',
    href: '/dashboard',
    label: 'Overview',
    icon: LayoutDashboard,
    description: 'Platform status and environment.',
  },
  {
    slug: 'schemas',
    href: '/schemas',
    label: 'Source & Schemas',
    icon: FileStack,
    description: 'Schema catalogue, versions, compare and controlled change proposals.',
    permission: 'schema:read',
  },
  {
    slug: 'ingestion',
    href: '/ingestion',
    label: 'Ingestion Workbench',
    icon: Database,
    description: 'Imports, mapping grid, profiling, validation issues and quarantine.',
    permission: 'source:read',
  },
  // FE-14.1: live behind DOMAIN_UI_ENABLED; off, the Phase 13 "planned" entry exactly (D-02).
  DOMAIN_UI_ENABLED
    ? {
        slug: 'domain',
        href: '/domain',
        label: 'Financial Domain',
        icon: Landmark,
        // FE-16.1: the registry is named only while it is live (NEXT_PUBLIC_RULES_UI); off, the Phase 15 text.
        // FE-19.1: the counterparties are named only while they are live (NEXT_PUBLIC_CREDIT_DOMAIN_UI); off, the Phase 18 text.
        description:
          (RULES_UI_ENABLED
            ? 'Instrument catalogue with its lineage to imports, and the registry: methodology rules and their versions, calculation modules and assumption sets.'
            : 'Instrument catalogue: projected versions, attributes, cash flows and their lineage to imports.') +
          (CREDIT_DOMAIN_UI_ENABLED ? ' Counterparties and credit exposures with their versions and lineage.' : '') +
          // FE-20.1: the ratings are named only while they are live (NEXT_PUBLIC_RATINGS_UI); off, the Phase 19 text.
          (CREDIT_DOMAIN_UI_ENABLED && RATINGS_UI_ENABLED ? ' Obligor ratings with maker-checker approval.' : ''),
        permission: 'domain:read',
      }
    : {
        slug: 'domain',
        href: '/domain',
        label: 'Financial Domain',
        icon: Landmark,
        description: 'Instrument types and records, assumptions and the rule registry.',
        planned: { phase: 'Feature 03', workstream: 'WS3' },
      },
  // FE-15.1: live behind CALCULATIONS_UI_ENABLED; off, the Phase 14 "planned" entry exactly (D-02).
  CALCULATIONS_UI_ENABLED
    ? {
        slug: 'calculations',
        href: '/calculations',
        label: 'Calculations',
        icon: Calculator,
        description:
          'Pinned dataset snapshots, run submission, run history, manifests, warnings and the illustrative maturity ladder with its lineage.',
        permission: 'calc:read',
      }
    : {
        slug: 'calculations',
        href: '/calculations',
        label: 'Calculations',
        icon: Calculator,
        description: 'Module selection, run submission, run history, warnings and results.',
        planned: { phase: 'Feature 03–04', workstream: 'WS3 / WS4' },
      },
  {
    slug: 'scenarios',
    href: '/scenarios',
    label: 'Reconciliation & Scenarios',
    icon: GitCompare,
    description: 'Baseline versus scenario comparison, breaks and lineage drill-down.',
    // FE-19.1: Feature 04 is credit risk; this workspace stays planned under WS4.
    // PlannedWorkspace renders "Scheduled for <phase> (<workstream>)": "Scheduled for WS4 (planned)".
    planned: { phase: 'WS4', workstream: 'planned' },
  },
  {
    slug: 'reports',
    href: '/reports',
    label: 'Reports',
    icon: FileBarChart,
    description: 'Run-linked reports and controlled exports.',
    // FE-19.1: Feature 04 is credit risk; this workspace stays planned under WS4.
    // PlannedWorkspace renders "Scheduled for <phase> (<workstream>)": "Scheduled for WS4 (planned)".
    planned: { phase: 'WS4', workstream: 'planned' },
  },
  {
    slug: 'assistant',
    href: '/assistant',
    label: 'AI Assistant',
    icon: Bot,
    description: 'Evidence-backed explanations and proposals. Optional; can be disabled.',
    planned: { phase: 'Feature 05', workstream: 'WS5' },
  },
]

export const adminWorkspaces: Workspace[] = [
  {
    slug: 'audit',
    href: '/audit',
    label: 'Audit',
    icon: ScrollText,
    description: 'Immutable audit timeline of privileged actions.',
    permission: 'audit:read',
  },
  {
    slug: 'access',
    href: '/access',
    label: 'Access & Roles',
    icon: ShieldCheck,
    description: 'Your identity, roles and effective permissions; users for administrators.',
  },
]

/** A page of a workspace, shown as a tab strip at the top of its routes. */
export interface WorkspaceSection {
  slug: string
  href: string
  label: string
  /** Hidden without this permission (cosmetic; D-04: the API refuses with 403). */
  permission: Schemas['Permission']
}

/**
 * FE-20.1/20.2: the Ratings tab (the review queue at /domain/ratings, read with domain:read; its
 * actions need rating:approve), only while NEXT_PUBLIC_RATINGS_UI and NEXT_PUBLIC_CREDIT_DOMAIN_UI
 * are on. Off, the Phase 19 tab strip exactly (D-02).
 */
const ratingSections: WorkspaceSection[] = RATINGS_UI_ENABLED
  ? [{ slug: 'ratings', href: '/domain/ratings', label: 'Ratings', permission: 'domain:read' }]
  : []

/** FE-19.1: the Counterparties tab (domain:read), only while NEXT_PUBLIC_CREDIT_DOMAIN_UI is on. */
const creditSections: WorkspaceSection[] = CREDIT_DOMAIN_UI_ENABLED
  ? [{ slug: 'counterparties', href: '/domain/counterparties', label: 'Counterparties', permission: 'domain:read' }, ...ratingSections]
  : []

/** FE-16.1: the read-only registry tabs, only while NEXT_PUBLIC_RULES_UI is on. */
const registrySections: WorkspaceSection[] = RULES_UI_ENABLED
  ? [
      { slug: 'rules', href: '/domain/rules', label: 'Rules', permission: 'rule:read' },
      { slug: 'modules', href: '/domain/modules', label: 'Modules', permission: 'rule:read' },
      { slug: 'assumption-sets', href: '/domain/assumption-sets', label: 'Assumption sets', permission: 'rule:read' },
    ]
  : []

/**
 * FE-16.1: the Financial Domain's pages: the Phase 14 instrument catalogue (at /domain, as
 * before), FE-19.1's counterparties, FE-20's ratings queue and the read-only registry of rules, modules and assumption
 * sets. Empty while both NEXT_PUBLIC_RULES_UI and NEXT_PUBLIC_CREDIT_DOMAIN_UI are off or the
 * workspace is planned, so /domain shows no tab strip, as in Phase 15 (D-02). With
 * NEXT_PUBLIC_CREDIT_DOMAIN_UI=off alone the list is the Phase 18 one exactly.
 */
export const domainSections: WorkspaceSection[] =
  DOMAIN_UI_ENABLED && creditSections.length + registrySections.length > 0
    ? [{ slug: 'instruments', href: '/domain', label: 'Instruments', permission: 'domain:read' }, ...creditSections, ...registrySections]
    : []

export function findWorkspace(slug: string): Workspace | undefined {
  return [...mainWorkspaces, ...adminWorkspaces].find((w) => w.slug === slug)
}
