# Domain identity links: how historical text keys are reconciled (BE-31.5, D-58 §5)

**Status: PROVISIONAL.** Legal entities and links are identities only. Their attributes and the
source-of-truth rule (which source wins when two disagree) are the client's to set (Data Model
§11). Until then, nothing here corrects or chooses between sources.

## What is linked

Domain rows are append-only (D-29 §6), so no foreign-key column is added to them (D-58 §1).
Instead, migration 0030 adds two append-only tables. UPDATE, DELETE and TRUNCATE are refused by
triggers.

- `legal_entity`: a stable UUID per (tenant, source system, code). The code is a position's
  `legal_entity_ref`. `provisional` is always true.
- `domain_identity_link`: one row per (source kind, source row, target kind), `UNIQUE`. It
  carries ids only (D-58 §7). The target's own route applies its classification on read.

| Source row (`source_kind`) | Text key | Target (`target_kind`) |
|---|---|---|
| `position_version` (POSITION_VERSION) | `instrument_key` | INSTRUMENT (`instrument.business_key`) |
| `position_version` (POSITION_VERSION) | `legal_entity_ref` | LEGAL_ENTITY (`legal_entity.code`) |
| `cashflow_event` (CASHFLOW_EVENT) | `instrument_key` | INSTRUMENT (`instrument.business_key`) |
| `credit_exposure_version` (CREDIT_EXPOSURE) | `counterparty_key` | COUNTERPARTY (`counterparty.business_key`) |

## The resolver (`resolver_version` 1, method `BUSINESS_KEY_UNIQUE`)

Resolution is deterministic and never guesses (D-58 §3). For each source row and target kind:

- **Exactly one** identity of the tenant has the row's key: one link is written.
- **None** has it: `LINK_UNRESOLVED`. It is counted and no link is written.
- **Several** have it, for example the same instrument id from two source systems:
  `LINK_AMBIGUOUS`. It is counted and no link is written.
- The key is empty: counted as `no_key`, and no link is written.

Each row lands in exactly one bucket: `rows = linked + unresolved + ambiguous + no_key`. The
counts are value-free. They name no key.

Legal entities are registered before linking. Every (position source, `legal_entity_ref`) with
no entity yet gets one.

## When links are written

1. **At projection (D-58 §4).** Projecting positions, cash flows or credit exposures registers
   the legal entities and links the projection's own rows, in the same transaction. The audit
   event `import.project` carries the counts (`links_written`, `links_unresolved`,
   `links_ambiguous`, `legal_entities_new`). The projection summary is unchanged.
2. **Historical rows: `POST /api/v1/identity-links/reconcile`** (`domain:project`, audited
   `identity_link.reconcile`). It runs the same resolver over every position, cash-flow and
   credit-exposure row of the caller's tenant (D-04). It returns one count entry per pair, in a
   fixed order. Run it once per tenant after Phase 31 is deployed. Rows projected before 0030,
   or while the switch was off, are linked then.

## Idempotence and keys that become ambiguous later (D-58 §5)

- An existing link is never changed or removed. A repeat reconcile writes nothing
  (`links_written` 0) and returns the same counts.
- A key that was unique when linked can become ambiguous later, for example when a second
  source delivers the same instrument id. The link is kept and reported as
  `linked_now_ambiguous` (a subset of `linked`). Rows projected after that point are counted
  `LINK_AMBIGUOUS` and are not linked.
- Correction and precedence (re-pointing or retiring a link) wait for the client's
  source-of-truth rule. They would be a new `resolver_version`, never an edit.

## Runs do not read links (D-58 §6)

Calculation runs resolve cross-references by business key per snapshot (D-30 §4), as before. A
run's `result_sha256` is the same before and after a reconcile. Adopting links in a run needs a
new engine and manifest version (deferred).

## The switch: `IDENTITY_LINKS_ENABLED` (default true, D-02)

When it is off:

- projections write no legal entity and no link;
- the reconcile answers 409 `identity_links_disabled`, audited DENIED, and writes nothing;
- existing entities and links stay readable through `GET /legal-entities`,
  `GET /legal-entities/{id}` and `GET /identity-links` (`domain:read`).

Switching it back on and running the reconcile links whatever was projected in between.

## Reversibility

Both tables are append-only and kept under D-02. The 0030 downgrade refuses while either table
has a row. Removing identities or links is the owner-level procedure in `docs/RETENTION.md`.
