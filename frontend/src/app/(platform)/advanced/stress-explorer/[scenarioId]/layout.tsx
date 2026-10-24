import type { ReactNode } from 'react'
import { notFound } from 'next/navigation'

/** A scenario id is a UUID: GET /scenarios/{scenario_id} declares `format: uuid`. */
const SCENARIO_ID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i

/**
 * FE-35.6: a path segment that cannot be a scenario id is a 404 (status and page), not a scenario
 * page that loads nothing. So a retired explorer route is a real 404 instead of being read as a
 * scenario id. Cosmetic: the API refuses a malformed id itself.
 */
export default async function ScenarioLayout({ children, params }: { children: ReactNode; params: Promise<{ scenarioId: string }> }) {
  const { scenarioId } = await params
  if (!SCENARIO_ID.test(scenarioId)) notFound()
  return <>{children}</>
}
