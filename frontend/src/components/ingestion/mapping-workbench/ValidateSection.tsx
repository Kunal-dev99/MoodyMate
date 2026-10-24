'use client'

import { FlaskConical } from 'lucide-react'
import { PageSection } from '@/components/common/PageSection'
import type { MappingVersion } from '@/components/ingestion/mappings'
import { Button } from '@/components/ui/button'
import type { MappingWorkbenchState } from './useMappingWorkbench'
import { ValidationResult } from './ValidationResult'

/** The dry run of the open version against the selected import (BE-10.4): nothing is written to the import. */
export function ValidateSection({ wb, version, onValidate }: { wb: MappingWorkbenchState; version: MappingVersion; onValidate: () => void }) {
  const { mayPropose, importId, dirty, busy, validation } = wb
  return (
    <PageSection
      icon={FlaskConical}
      title="Validate against this import"
      description="A dry run of the open version over every row of the import: counts and issues only, no values. Nothing is written to the import."
      headerRight={
        mayPropose ? (
          <Button
            size="sm"
            onClick={onValidate}
            disabled={!importId || dirty || busy !== null}
            title={dirty ? 'Save the draft first: a dry run checks a saved version.' : !importId ? 'Choose an import first.' : undefined}
            data-testid="mapping-validate"
          >
            {busy === 'validate' ? 'Validating…' : `Validate v${version.version} against this import`}
          </Button>
        ) : undefined
      }
    >
      {!mayPropose && <p className="text-sm text-muted-foreground">A dry run needs the mapping:propose permission.</p>}
      {mayPropose && dirty && <p className="mb-2 text-sm text-muted-foreground">Save the draft as a new version first: a dry run checks a saved version.</p>}
      {validation ? (
        <ValidationResult v={validation} />
      ) : (
        mayPropose && (
          <p className="text-sm text-muted-foreground" data-testid="mapping-validation-none">
            {version.last_validation
              ? `The latest dry run of v${version.version} was ${version.last_validation.outcome} (import ${version.last_validation.import_id.slice(0, 8)}). Run it again to see the details.`
              : `v${version.version} has no dry run yet.`}
          </p>
        )
      )}
    </PageSection>
  )
}
