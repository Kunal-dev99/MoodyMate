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
import type { NavItem } from '@/components/layout/sidebar'

/**
 * Platform workspaces (Architecture §14). `planned` workspaces render a clear
 * "planned" state until the delivery-plan phase that builds them; they are never
 * presented as working features.
 */
export interface Workspace extends NavItem {
  icon: LucideIcon
  slug: string
  description: string
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
    description: 'Schema catalogue, versions, compare and change proposals.',
    planned: { phase: 'Phases 4–5', workstream: 'WS1 DG-01, DG-05' },
  },
  {
    slug: 'ingestion',
    href: '/ingestion',
    label: 'Ingestion Workbench',
    icon: Database,
    description: 'Imports, mapping grid, profiling, validation issues and quarantine.',
    planned: { phase: 'Feature 02', workstream: 'WS2' },
  },
  {
    slug: 'domain',
    href: '/domain',
    label: 'Financial Domain',
    icon: Landmark,
    description: 'Instrument types and records, assumptions and the rule registry.',
    planned: { phase: 'Feature 03', workstream: 'WS3' },
  },
  {
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
    planned: { phase: 'Feature 04', workstream: 'WS4' },
  },
  {
    slug: 'reports',
    href: '/reports',
    label: 'Reports',
    icon: FileBarChart,
    description: 'Run-linked reports and controlled exports.',
    planned: { phase: 'Feature 04', workstream: 'WS4' },
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
    planned: { phase: 'Phase 3', workstream: 'WS1 DG-04' },
  },
  {
    slug: 'access',
    href: '/access',
    label: 'Access & Roles',
    icon: ShieldCheck,
    description: 'Your identity, roles and effective permissions.',
    planned: { phase: 'Phase 2', workstream: 'WS1 DG-02' },
  },
]

export function findWorkspace(slug: string): Workspace | undefined {
  return [...mainWorkspaces, ...adminWorkspaces].find((w) => w.slug === slug)
}
