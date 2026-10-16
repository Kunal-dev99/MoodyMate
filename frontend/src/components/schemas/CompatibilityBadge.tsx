import { Badge } from '@/components/ui/badge'
import { COMPATIBILITY_STYLE, type Compatibility } from './registry'

/** How a schema change affects consumers (FE-4.2). */
export function CompatibilityBadge({ value }: { value: Compatibility }) {
  const style = COMPATIBILITY_STYLE[value]
  return (
    <Badge variant={style.variant} title={style.help} data-testid="compatibility-badge" data-compatibility={value}>
      {style.label}
    </Badge>
  )
}
