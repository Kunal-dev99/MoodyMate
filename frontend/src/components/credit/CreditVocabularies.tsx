import { BookOpen } from 'lucide-react'
import { PageSection } from '@/components/common/PageSection'
import { ProvisionalBadge } from '@/components/domain/InstrumentCatalogue'
import { Badge } from '@/components/ui/badge'
import { groupVocabularies, vocabularyTitle, type VocabularyEntry } from './credit'

/**
 * FE-19.1: the seeded credit vocabularies (GET /credit-vocabularies, D-37 §5), each value
 * labelled "Provisional / illustrative" when it is. Grade codes are neutral (G01…G08, D): no
 * rating-agency symbol and no agency parity is implied.
 */
export function CreditVocabularies({ items }: { items: VocabularyEntry[] }) {
  const anyProvisional = items.some((i) => i.provisional)
  return (
    <PageSection
      icon={BookOpen}
      title="Credit vocabularies"
      description="Seeded by the platform until the client defines its own. Values are kept as given on projection; a value outside a vocabulary is still projected, and modules exclude or warn on it with a reason (D-37 §3)."
      headerRight={anyProvisional ? <ProvisionalBadge /> : undefined}
    >
      {items.length === 0 ? (
        <p className="text-sm text-muted-foreground" data-testid="credit-vocabularies-empty">
          No credit vocabulary is seeded for your tenant.
        </p>
      ) : (
        <div className="grid gap-4 lg:grid-cols-2" data-testid="credit-vocabularies">
          {groupVocabularies(items).map(([kind, entries]) => (
            <div key={kind} data-testid="credit-vocabulary" data-kind={kind}>
              <h3 className="mb-2 text-sm font-medium">{vocabularyTitle(kind)}</h3>
              {entries.length === 0 ? (
                <p className="text-xs text-muted-foreground">None seeded.</p>
              ) : (
                <ul className="flex flex-wrap gap-2">
                  {entries.map((e) => (
                    <li
                      key={e.code}
                      className="flex items-center gap-2 rounded-md border border-border bg-surface-2 px-2 py-1 text-sm"
                      data-testid="credit-vocabulary-entry"
                      data-code={e.code}
                      data-provisional={e.provisional}
                    >
                      <span className="font-mono">{e.code}</span>
                      <span className="text-muted-foreground">{e.name}</span>
                      {e.rank !== null && <span className="font-mono text-xs text-muted-foreground">rank {e.rank}</span>}
                      {e.is_default_grade && (
                        <Badge variant="destructive" data-testid="default-grade">
                          Default grade
                        </Badge>
                      )}
                      {e.provisional && <ProvisionalBadge />}
                    </li>
                  ))}
                </ul>
              )}
            </div>
          ))}
        </div>
      )}
    </PageSection>
  )
}
