import type { ConfigLimits } from '@/lib/limits'

/**
 * FE-35.3: GET config/limits as a default deployment serves it (typed by the generated
 * `ConfigLimits`, so a field added to or dropped from the contract fails `tsc`). The editor
 * vitests (rules, scenario editor, import wizard) take their bounds from here, never from a
 * constant of the component under test; limits.test.ts checks every value against the backend's
 * own constants, the objects BE-35.3 serves.
 */
export const SERVED_LIMITS: ConfigLimits = {
  rule: { max_buckets: 50, max_map_entries: 200, max_decimal_scale: 18, max_days: 36_600, max_bucket_code_length: 20 },
  scenario: {
    min_shocks: 1,
    max_shocks: 5,
    max_instrument_keys: 50,
    max_instrument_key_length: 200,
    fraction_scale: 4,
    max_delay_days: 3650,
    max_unavailable_days: 365,
    max_name_length: 200,
    max_description_length: 2000,
    max_change_summary_length: 1000,
    max_comment_length: 1000,
  },
  upload: {
    max_bytes: 10 * 1024 * 1024,
    max_rows: 50_000,
    max_columns: 200,
    max_cell_chars: 4_000,
    max_xlsx_uncompressed_bytes: 50 * 1024 * 1024,
    max_xlsx_ratio: 100,
    max_decoded_chars: 50 * 1024 * 1024,
  },
}

/** The served limits with some values changed, as a deployment that lowers them would serve them. */
export function servedWith(change: { rule?: Partial<ConfigLimits['rule']>; scenario?: Partial<ConfigLimits['scenario']>; upload?: Partial<ConfigLimits['upload']> }): ConfigLimits {
  return {
    rule: { ...SERVED_LIMITS.rule, ...change.rule },
    scenario: { ...SERVED_LIMITS.scenario, ...change.scenario },
    upload: { ...SERVED_LIMITS.upload, ...change.upload },
  }
}
