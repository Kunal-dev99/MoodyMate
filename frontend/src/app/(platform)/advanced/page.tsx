import { redirect } from 'next/navigation'

/** FE-24.1: the "Advanced" breadcrumb lands on the section's only workspace. */
export default function AdvancedPage() {
  redirect('/advanced/stress-explorer')
}
