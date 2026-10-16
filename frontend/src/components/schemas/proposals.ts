import type { Schemas } from '@/api/client'

export type Proposal = Schemas['ProposalOut']
export type ProposalStatus = Schemas['ProposalStatus']
export type FieldIn = Schemas['SchemaFieldIn']

/** Proposal lifecycle (D-06) on the design system's status styles. */
export const PROPOSAL_STATUS_STYLE: Record<ProposalStatus, { label: string; className: string }> = {
  DRAFT: { label: 'Draft', className: 'status-draft' },
  SUBMITTED: { label: 'Awaiting approval', className: 'status-testing' },
  IN_REVIEW: { label: 'In review', className: 'status-testing' },
  APPROVED: { label: 'Approved', className: 'status-building' },
  APPLIED: { label: 'Applied', className: 'status-published' },
  REJECTED: { label: 'Rejected', className: 'status-failed' },
  SUPERSEDED: { label: 'Superseded', className: 'status-draft' },
}

/** Submit is offered only once an impact analysis is recorded (FE-5.1); the API agrees. */
export function canSubmit(p: Proposal, userId: string): boolean {
  return p.status === 'DRAFT' && p.impact !== null && p.proposer_user_id === userId
}

/** The approver decides only on someone else's submitted proposal (maker-checker). */
export function canDecide(p: Proposal, userId: string, mayApprove: boolean): boolean {
  return mayApprove && p.status === 'SUBMITTED' && p.proposer_user_id !== userId
}

export function emptyField(): FieldIn {
  return {
    name: '',
    data_type: 'STRING',
    required: false,
    unit: null,
    precision: null,
    scale: null,
    classification: 'internal',
    extension_eligible: false,
    description: null,
  }
}

const NAME = /^[a-z][a-z0-9_]{0,99}$/

/** Client-side checks mirroring the API, so problems show before the request. */
export function draftProblems(fields: FieldIn[]): string[] {
  const problems: string[] = []
  if (fields.length === 0) problems.push('Add at least one field.')
  const seen = new Set<string>()
  for (const f of fields) {
    const label = f.name || '(unnamed)'
    if (!NAME.test(f.name)) problems.push(`${label}: use lower_snake_case, starting with a letter.`)
    if (seen.has(f.name)) problems.push(`${label}: duplicate field name.`)
    seen.add(f.name)
    if (Number.isNaN(f.precision) || Number.isNaN(f.scale)) problems.push(`${label}: precision and scale must be whole numbers.`)
    if (f.precision != null && (f.precision < 1 || f.precision > 38)) problems.push(`${label}: precision must be 1–38.`)
    if (f.scale != null && (f.scale < 0 || f.scale > 38)) problems.push(`${label}: scale must be 0–38.`)
    if (f.data_type === 'DECIMAL' && (f.precision == null || f.scale == null))
      problems.push(`${label}: a DECIMAL needs both precision and scale.`)
    if (f.scale != null && f.precision != null && f.scale > f.precision)
      problems.push(`${label}: scale cannot exceed precision.`)
  }
  return problems
}
