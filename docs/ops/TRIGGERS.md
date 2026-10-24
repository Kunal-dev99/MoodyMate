# Database triggers, by migration

Every trigger a migration's `upgrade()` creates, by the migration that first creates it
(BE-34.7). The backend suite checks this list against the parsed `CREATE TRIGGER` set
(`check_triggers_doc` in `backend/app/tests/observability_kit.py`, run by
`test_committed_openapi_and_samples_are_current`). A migration that adds a trigger
adds its row here in the same change.

Operational notes:

- The triggers enforce append-only and immutability rules in the database itself (D-01,
  D-02: lifecycle actions set status or validity fields, never delete rows). The app
  never disables them. Only the table owner can, inside one transaction, as the seed
  downgrades and the test cleanup do.
- A restore (see `BACKUP_RESTORE.md`) restores the triggers with the schema. Check that
  they are enabled afterwards:
  `SELECT tgrelid::regclass, tgname FROM pg_trigger WHERE NOT tgisinternal AND tgenabled = 'D';`
  should return no rows.
- Retention enforcement is described in `../RETENTION.md` (PROVISIONAL).

177 triggers in 17 migrations.

| Migration | Trigger | Table | Fires | Function |
| --- | --- | --- | --- | --- |
| `0004_audit_and_classification_tables` | `audit_event_no_truncate` | audit_event | BEFORE TRUNCATE | audit_event_append_only() |
| `0004_audit_and_classification_tables` | `audit_event_no_update_delete` | audit_event | BEFORE UPDATE OR DELETE | audit_event_append_only() |
| `0006_schema_registry_tables` | `schema_definition_no_truncate` | schema_definition | BEFORE TRUNCATE | schema_registry_no_truncate() |
| `0006_schema_registry_tables` | `schema_dependency_no_truncate` | schema_dependency | BEFORE TRUNCATE | schema_registry_no_truncate() |
| `0006_schema_registry_tables` | `schema_dependency_same_tenant` | schema_dependency | BEFORE INSERT OR UPDATE | schema_dependency_same_tenant() |
| `0006_schema_registry_tables` | `schema_field_immutable` | schema_field | BEFORE INSERT OR UPDATE OR DELETE | schema_field_guard() |
| `0006_schema_registry_tables` | `schema_field_no_truncate` | schema_field | BEFORE TRUNCATE | schema_registry_no_truncate() |
| `0006_schema_registry_tables` | `schema_version_immutable` | schema_version | BEFORE UPDATE OR DELETE | schema_version_guard() |
| `0006_schema_registry_tables` | `schema_version_no_truncate` | schema_version | BEFORE TRUNCATE | schema_registry_no_truncate() |
| `0008_schema_change_proposals` | `schema_change_proposal_immutable` | schema_change_proposal | BEFORE UPDATE OR DELETE | schema_change_proposal_guard() |
| `0008_schema_change_proposals` | `schema_change_proposal_no_truncate` | schema_change_proposal | BEFORE TRUNCATE | schema_change_proposal_guard() |
| `0009_source_registration_tables` | `source_contract_immutable` | source_contract | BEFORE UPDATE OR DELETE | source_contract_guard() |
| `0009_source_registration_tables` | `source_contract_no_truncate` | source_contract | BEFORE TRUNCATE | source_contract_guard() |
| `0009_source_registration_tables` | `source_contract_same_tenant` | source_contract | BEFORE INSERT OR UPDATE | source_contract_same_tenant() |
| `0009_source_registration_tables` | `source_profile_no_delete` | source_profile | BEFORE DELETE | source_registration_no_delete() |
| `0009_source_registration_tables` | `source_profile_no_move` | source_profile | BEFORE UPDATE | source_profile_no_move() |
| `0009_source_registration_tables` | `source_profile_no_truncate` | source_profile | BEFORE TRUNCATE | source_registration_no_delete() |
| `0009_source_registration_tables` | `source_system_no_delete` | source_system | BEFORE DELETE | source_registration_no_delete() |
| `0009_source_registration_tables` | `source_system_no_move` | source_system | BEFORE UPDATE | source_system_no_move() |
| `0009_source_registration_tables` | `source_system_no_truncate` | source_system | BEFORE TRUNCATE | source_registration_no_delete() |
| `0012_import_tables` | `import_artifact_append_only` | import_artifact | BEFORE UPDATE OR DELETE | import_append_only() |
| `0012_import_tables` | `import_artifact_no_truncate` | import_artifact | BEFORE TRUNCATE | import_append_only() |
| `0012_import_tables` | `import_batch_append_only` | import_batch | BEFORE UPDATE OR DELETE | import_append_only() |
| `0012_import_tables` | `import_batch_no_truncate` | import_batch | BEFORE TRUNCATE | import_append_only() |
| `0012_import_tables` | `import_job_immutable` | import_job | BEFORE UPDATE OR DELETE | import_job_guard() |
| `0012_import_tables` | `import_job_no_truncate` | import_job | BEFORE TRUNCATE | import_job_guard() |
| `0012_import_tables` | `import_job_same_tenant` | import_job | BEFORE INSERT OR UPDATE | import_job_same_tenant() |
| `0012_import_tables` | `source_record_append_only` | source_record | BEFORE UPDATE OR DELETE | import_append_only() |
| `0012_import_tables` | `source_record_no_truncate` | source_record | BEFORE TRUNCATE | import_append_only() |
| `0013_data_profile_snapshot` | `data_profile_snapshot_append_only` | data_profile_snapshot | BEFORE UPDATE OR DELETE | data_profile_append_only() |
| `0013_data_profile_snapshot` | `data_profile_snapshot_no_truncate` | data_profile_snapshot | BEFORE TRUNCATE | data_profile_append_only() |
| `0014_mapping_tables` | `field_mapping_immutable` | field_mapping | BEFORE UPDATE OR DELETE | field_mapping_guard() |
| `0014_mapping_tables` | `field_mapping_no_truncate` | field_mapping | BEFORE TRUNCATE | field_mapping_guard() |
| `0014_mapping_tables` | `field_mapping_same_tenant` | field_mapping | BEFORE INSERT | field_mapping_same_tenant() |
| `0014_mapping_tables` | `import_job_mapping_pin` | import_job | BEFORE INSERT OR UPDATE | import_job_mapping_pin() |
| `0014_mapping_tables` | `mapping_rule_immutable` | mapping_rule | BEFORE INSERT OR UPDATE OR DELETE | mapping_rule_guard() |
| `0014_mapping_tables` | `mapping_rule_no_truncate` | mapping_rule | BEFORE TRUNCATE | mapping_rule_guard() |
| `0014_mapping_tables` | `mapping_validation_append_only` | mapping_validation | BEFORE UPDATE OR DELETE | mapping_validation_append_only() |
| `0014_mapping_tables` | `mapping_validation_no_truncate` | mapping_validation | BEFORE TRUNCATE | mapping_validation_append_only() |
| `0014_mapping_tables` | `mapping_validation_same_tenant` | mapping_validation | BEFORE INSERT | mapping_validation_same_tenant() |
| `0014_mapping_tables` | `mapping_version_checks` | mapping_version | BEFORE INSERT OR UPDATE | mapping_version_checks() |
| `0014_mapping_tables` | `mapping_version_immutable` | mapping_version | BEFORE UPDATE OR DELETE | mapping_version_guard() |
| `0014_mapping_tables` | `mapping_version_no_truncate` | mapping_version | BEFORE TRUNCATE | mapping_version_guard() |
| `0015_validation_tables` | `canonical_record_append_only` | canonical_record | BEFORE UPDATE OR DELETE | validation_append_only() |
| `0015_validation_tables` | `canonical_record_checks` | canonical_record | BEFORE INSERT | canonical_record_checks() |
| `0015_validation_tables` | `canonical_record_no_truncate` | canonical_record | BEFORE TRUNCATE | validation_append_only() |
| `0015_validation_tables` | `import_manifest_append_only` | import_manifest | BEFORE UPDATE OR DELETE | validation_append_only() |
| `0015_validation_tables` | `import_manifest_checks` | import_manifest | BEFORE INSERT | import_manifest_checks() |
| `0015_validation_tables` | `import_manifest_no_truncate` | import_manifest | BEFORE TRUNCATE | validation_append_only() |
| `0015_validation_tables` | `quarantine_item_checks` | quarantine_item | BEFORE INSERT | quarantine_item_checks() |
| `0015_validation_tables` | `quarantine_item_guard` | quarantine_item | BEFORE UPDATE OR DELETE | quarantine_item_guard() |
| `0015_validation_tables` | `quarantine_item_no_truncate` | quarantine_item | BEFORE TRUNCATE | quarantine_item_guard() |
| `0015_validation_tables` | `record_lineage_append_only` | record_lineage | BEFORE UPDATE OR DELETE | validation_append_only() |
| `0015_validation_tables` | `record_lineage_checks` | record_lineage | BEFORE INSERT | record_lineage_checks() |
| `0015_validation_tables` | `record_lineage_no_truncate` | record_lineage | BEFORE TRUNCATE | validation_append_only() |
| `0015_validation_tables` | `validation_issue_append_only` | validation_issue | BEFORE UPDATE OR DELETE | validation_append_only() |
| `0015_validation_tables` | `validation_issue_checks` | validation_issue | BEFORE INSERT | validation_issue_checks() |
| `0015_validation_tables` | `validation_issue_no_truncate` | validation_issue | BEFORE TRUNCATE | validation_append_only() |
| `0015_validation_tables` | `validation_profile_append_only` | validation_profile | BEFORE UPDATE OR DELETE | validation_append_only() |
| `0015_validation_tables` | `validation_profile_no_truncate` | validation_profile | BEFORE TRUNCATE | validation_append_only() |
| `0016_reconciliation_and_drift` | `data_drift_event_checks` | data_drift_event | BEFORE INSERT | data_drift_event_checks() |
| `0016_reconciliation_and_drift` | `data_drift_event_guard` | data_drift_event | BEFORE UPDATE OR DELETE | data_drift_event_guard() |
| `0016_reconciliation_and_drift` | `data_drift_event_no_truncate` | data_drift_event | BEFORE TRUNCATE | data_drift_event_guard() |
| `0016_reconciliation_and_drift` | `reconciliation_item_append_only` | reconciliation_item | BEFORE UPDATE OR DELETE | reconciliation_append_only() |
| `0016_reconciliation_and_drift` | `reconciliation_item_checks` | reconciliation_item | BEFORE INSERT | reconciliation_item_checks() |
| `0016_reconciliation_and_drift` | `reconciliation_item_no_truncate` | reconciliation_item | BEFORE TRUNCATE | reconciliation_append_only() |
| `0016_reconciliation_and_drift` | `reconciliation_run_append_only` | reconciliation_run | BEFORE UPDATE OR DELETE | reconciliation_append_only() |
| `0016_reconciliation_and_drift` | `reconciliation_run_checks` | reconciliation_run | BEFORE INSERT | reconciliation_run_checks() |
| `0016_reconciliation_and_drift` | `reconciliation_run_no_truncate` | reconciliation_run | BEFORE TRUNCATE | reconciliation_append_only() |
| `0017_domain_projection` | `cashflow_event_append_only` | cashflow_event | BEFORE UPDATE OR DELETE | domain_append_only() |
| `0017_domain_projection` | `cashflow_event_checks` | cashflow_event | BEFORE INSERT | cashflow_event_checks() |
| `0017_domain_projection` | `cashflow_event_no_truncate` | cashflow_event | BEFORE TRUNCATE | domain_append_only() |
| `0017_domain_projection` | `domain_projection_append_only` | domain_projection | BEFORE UPDATE OR DELETE | domain_append_only() |
| `0017_domain_projection` | `domain_projection_checks` | domain_projection | BEFORE INSERT | domain_projection_checks() |
| `0017_domain_projection` | `domain_projection_no_truncate` | domain_projection | BEFORE TRUNCATE | domain_append_only() |
| `0017_domain_projection` | `instrument_append_only` | instrument | BEFORE UPDATE OR DELETE | domain_append_only() |
| `0017_domain_projection` | `instrument_attribute_definition_append_only` | instrument_attribute_definition | BEFORE UPDATE OR DELETE | domain_append_only() |
| `0017_domain_projection` | `instrument_attribute_definition_no_truncate` | instrument_attribute_definition | BEFORE TRUNCATE | domain_append_only() |
| `0017_domain_projection` | `instrument_attribute_value_append_only` | instrument_attribute_value | BEFORE UPDATE OR DELETE | domain_append_only() |
| `0017_domain_projection` | `instrument_attribute_value_checks` | instrument_attribute_value | BEFORE INSERT | instrument_attribute_value_checks() |
| `0017_domain_projection` | `instrument_attribute_value_no_truncate` | instrument_attribute_value | BEFORE TRUNCATE | domain_append_only() |
| `0017_domain_projection` | `instrument_checks` | instrument | BEFORE INSERT | domain_identity_checks() |
| `0017_domain_projection` | `instrument_no_truncate` | instrument | BEFORE TRUNCATE | domain_append_only() |
| `0017_domain_projection` | `instrument_type_append_only` | instrument_type | BEFORE UPDATE OR DELETE | domain_append_only() |
| `0017_domain_projection` | `instrument_type_no_truncate` | instrument_type | BEFORE TRUNCATE | domain_append_only() |
| `0017_domain_projection` | `instrument_version_append_only` | instrument_version | BEFORE UPDATE OR DELETE | domain_append_only() |
| `0017_domain_projection` | `instrument_version_checks` | instrument_version | BEFORE INSERT | instrument_version_checks() |
| `0017_domain_projection` | `instrument_version_no_truncate` | instrument_version | BEFORE TRUNCATE | domain_append_only() |
| `0017_domain_projection` | `position_append_only` | position | BEFORE UPDATE OR DELETE | domain_append_only() |
| `0017_domain_projection` | `position_checks` | position | BEFORE INSERT | domain_identity_checks() |
| `0017_domain_projection` | `position_no_truncate` | position | BEFORE TRUNCATE | domain_append_only() |
| `0017_domain_projection` | `position_version_append_only` | position_version | BEFORE UPDATE OR DELETE | domain_append_only() |
| `0017_domain_projection` | `position_version_checks` | position_version | BEFORE INSERT | position_version_checks() |
| `0017_domain_projection` | `position_version_no_truncate` | position_version | BEFORE TRUNCATE | domain_append_only() |
| `0018_calculations` | `assumption_set_lifecycle` | assumption_set | BEFORE INSERT OR UPDATE OR DELETE | calc_lifecycle_guard() |
| `0018_calculations` | `assumption_set_no_truncate` | assumption_set | BEFORE TRUNCATE | calc_lifecycle_guard() |
| `0018_calculations` | `calculation_input_ref_guard` | calculation_input_ref | BEFORE INSERT OR UPDATE OR DELETE | calc_output_guard() |
| `0018_calculations` | `calculation_input_ref_no_truncate` | calculation_input_ref | BEFORE TRUNCATE | calc_output_guard() |
| `0018_calculations` | `calculation_module_append_only` | calculation_module | BEFORE UPDATE OR DELETE | calc_append_only() |
| `0018_calculations` | `calculation_module_no_truncate` | calculation_module | BEFORE TRUNCATE | calc_append_only() |
| `0018_calculations` | `calculation_run_guard` | calculation_run | BEFORE INSERT OR UPDATE OR DELETE | calculation_run_guard() |
| `0018_calculations` | `calculation_run_no_truncate` | calculation_run | BEFORE TRUNCATE | calculation_run_guard() |
| `0018_calculations` | `calculation_run_same_tenant` | calculation_run | BEFORE INSERT | calc_same_tenant() |
| `0018_calculations` | `calculation_warning_guard` | calculation_warning | BEFORE INSERT OR UPDATE OR DELETE | calc_output_guard() |
| `0018_calculations` | `calculation_warning_no_truncate` | calculation_warning | BEFORE TRUNCATE | calc_output_guard() |
| `0018_calculations` | `dataset_snapshot_append_only` | dataset_snapshot | BEFORE UPDATE OR DELETE | calc_append_only() |
| `0018_calculations` | `dataset_snapshot_member_append_only` | dataset_snapshot_member | BEFORE UPDATE OR DELETE | calc_append_only() |
| `0018_calculations` | `dataset_snapshot_member_checks` | dataset_snapshot_member | BEFORE INSERT | dataset_snapshot_member_checks() |
| `0018_calculations` | `dataset_snapshot_member_no_truncate` | dataset_snapshot_member | BEFORE TRUNCATE | calc_append_only() |
| `0018_calculations` | `dataset_snapshot_no_truncate` | dataset_snapshot | BEFORE TRUNCATE | calc_append_only() |
| `0018_calculations` | `intermediate_value_checks` | intermediate_value | BEFORE INSERT | intermediate_value_checks() |
| `0018_calculations` | `intermediate_value_guard` | intermediate_value | BEFORE INSERT OR UPDATE OR DELETE | calc_output_guard() |
| `0018_calculations` | `intermediate_value_no_truncate` | intermediate_value | BEFORE TRUNCATE | calc_output_guard() |
| `0018_calculations` | `liquidity_gap_result_checks` | liquidity_gap_result | BEFORE INSERT | liquidity_gap_result_checks() |
| `0018_calculations` | `liquidity_gap_result_guard` | liquidity_gap_result | BEFORE INSERT OR UPDATE OR DELETE | calc_output_guard() |
| `0018_calculations` | `liquidity_gap_result_no_truncate` | liquidity_gap_result | BEFORE TRUNCATE | calc_output_guard() |
| `0018_calculations` | `maturity_bucket_definition_append_only` | maturity_bucket_definition | BEFORE UPDATE OR DELETE | calc_append_only() |
| `0018_calculations` | `maturity_bucket_definition_checks` | maturity_bucket_definition | BEFORE INSERT | maturity_bucket_definition_checks() |
| `0018_calculations` | `maturity_bucket_definition_no_truncate` | maturity_bucket_definition | BEFORE TRUNCATE | calc_append_only() |
| `0018_calculations` | `module_version_append_only` | module_version | BEFORE UPDATE OR DELETE | calc_append_only() |
| `0018_calculations` | `module_version_no_truncate` | module_version | BEFORE TRUNCATE | calc_append_only() |
| `0018_calculations` | `module_version_same_tenant` | module_version | BEFORE INSERT | calc_same_tenant() |
| `0018_calculations` | `rule_definition_append_only` | rule_definition | BEFORE UPDATE OR DELETE | calc_append_only() |
| `0018_calculations` | `rule_definition_no_truncate` | rule_definition | BEFORE TRUNCATE | calc_append_only() |
| `0018_calculations` | `rule_definition_same_tenant` | rule_definition | BEFORE INSERT | calc_same_tenant() |
| `0018_calculations` | `rule_set_append_only` | rule_set | BEFORE UPDATE OR DELETE | calc_append_only() |
| `0018_calculations` | `rule_set_member_append_only` | rule_set_member | BEFORE UPDATE OR DELETE | calc_append_only() |
| `0018_calculations` | `rule_set_member_checks` | rule_set_member | BEFORE INSERT | rule_set_member_checks() |
| `0018_calculations` | `rule_set_member_no_truncate` | rule_set_member | BEFORE TRUNCATE | calc_append_only() |
| `0018_calculations` | `rule_set_no_truncate` | rule_set | BEFORE TRUNCATE | calc_append_only() |
| `0018_calculations` | `rule_version_lifecycle` | rule_version | BEFORE INSERT OR UPDATE OR DELETE | calc_lifecycle_guard() |
| `0018_calculations` | `rule_version_no_truncate` | rule_version | BEFORE TRUNCATE | calc_lifecycle_guard() |
| `0018_calculations` | `rule_version_same_tenant` | rule_version | BEFORE INSERT | calc_same_tenant() |
| `0020_lcr_results` | `hqla_asset_result_checks` | hqla_asset_result | BEFORE INSERT | hqla_asset_result_checks() |
| `0020_lcr_results` | `hqla_asset_result_guard` | hqla_asset_result | BEFORE INSERT OR UPDATE OR DELETE | calc_output_guard() |
| `0020_lcr_results` | `hqla_asset_result_no_truncate` | hqla_asset_result | BEFORE TRUNCATE | calc_output_guard() |
| `0020_lcr_results` | `lcr_result_guard` | lcr_result | BEFORE INSERT OR UPDATE OR DELETE | calc_output_guard() |
| `0020_lcr_results` | `lcr_result_no_truncate` | lcr_result | BEFORE TRUNCATE | calc_output_guard() |
| `0022_credit_domain` | `counterparty_append_only` | counterparty | BEFORE UPDATE OR DELETE | domain_append_only() |
| `0022_credit_domain` | `counterparty_checks` | counterparty | BEFORE INSERT | domain_identity_checks() |
| `0022_credit_domain` | `counterparty_no_truncate` | counterparty | BEFORE TRUNCATE | domain_append_only() |
| `0022_credit_domain` | `counterparty_version_append_only` | counterparty_version | BEFORE UPDATE OR DELETE | domain_append_only() |
| `0022_credit_domain` | `counterparty_version_checks` | counterparty_version | BEFORE INSERT | counterparty_version_checks() |
| `0022_credit_domain` | `counterparty_version_no_truncate` | counterparty_version | BEFORE TRUNCATE | domain_append_only() |
| `0022_credit_domain` | `credit_exposure_append_only` | credit_exposure | BEFORE UPDATE OR DELETE | domain_append_only() |
| `0022_credit_domain` | `credit_exposure_checks` | credit_exposure | BEFORE INSERT | domain_identity_checks() |
| `0022_credit_domain` | `credit_exposure_no_truncate` | credit_exposure | BEFORE TRUNCATE | domain_append_only() |
| `0022_credit_domain` | `credit_exposure_version_append_only` | credit_exposure_version | BEFORE UPDATE OR DELETE | domain_append_only() |
| `0022_credit_domain` | `credit_exposure_version_checks` | credit_exposure_version | BEFORE INSERT | credit_exposure_version_checks() |
| `0022_credit_domain` | `credit_exposure_version_no_truncate` | credit_exposure_version | BEFORE TRUNCATE | domain_append_only() |
| `0022_credit_domain` | `credit_vocabulary_append_only` | credit_vocabulary | BEFORE UPDATE OR DELETE | domain_append_only() |
| `0022_credit_domain` | `credit_vocabulary_no_truncate` | credit_vocabulary | BEFORE TRUNCATE | domain_append_only() |
| `0023_credit_permissions` | `obligor_rating_lifecycle` | obligor_rating | BEFORE INSERT OR UPDATE OR DELETE | obligor_rating_guard() |
| `0023_credit_permissions` | `obligor_rating_no_truncate` | obligor_rating | BEFORE TRUNCATE | obligor_rating_guard() |
| `0026_scenarios` | `scenario_change_guard` | scenario_change | BEFORE INSERT OR UPDATE OR DELETE | scenario_change_guard() |
| `0026_scenarios` | `scenario_change_no_truncate` | scenario_change | BEFORE TRUNCATE | scenario_change_guard() |
| `0026_scenarios` | `scenario_guard` | scenario | BEFORE INSERT OR UPDATE OR DELETE | scenario_guard() |
| `0026_scenarios` | `scenario_no_truncate` | scenario | BEFORE TRUNCATE | scenario_guard() |
| `0026_scenarios` | `scenario_version_guard` | scenario_version | BEFORE INSERT OR UPDATE OR DELETE | scenario_version_guard() |
| `0026_scenarios` | `scenario_version_no_truncate` | scenario_version | BEFORE TRUNCATE | scenario_version_guard() |
| `0027_scenario_runs` | `calculation_scenario_pin_checks` | calculation_scenario_pin | BEFORE INSERT | calculation_scenario_pin_checks() |
| `0027_scenario_runs` | `calculation_scenario_pin_guard` | calculation_scenario_pin | BEFORE INSERT OR UPDATE OR DELETE | calc_output_guard() |
| `0027_scenario_runs` | `calculation_scenario_pin_no_truncate` | calculation_scenario_pin | BEFORE TRUNCATE | calc_output_guard() |
| `0027_scenario_runs` | `scenario_contribution_checks` | scenario_contribution | BEFORE INSERT | scenario_contribution_checks() |
| `0027_scenario_runs` | `scenario_contribution_guard` | scenario_contribution | BEFORE INSERT OR UPDATE OR DELETE | calc_output_guard() |
| `0027_scenario_runs` | `scenario_contribution_no_truncate` | scenario_contribution | BEFORE TRUNCATE | calc_output_guard() |
| `0027_scenario_runs` | `scenario_run_guard` | scenario_run | BEFORE INSERT OR UPDATE OR DELETE | scenario_run_guard() |
| `0027_scenario_runs` | `scenario_run_no_truncate` | scenario_run | BEFORE TRUNCATE | scenario_run_guard() |
| `0027_scenario_runs` | `scenario_shock_application_checks` | scenario_shock_application | BEFORE INSERT | scenario_shock_application_checks() |
| `0027_scenario_runs` | `scenario_shock_application_guard` | scenario_shock_application | BEFORE INSERT OR UPDATE OR DELETE | calc_output_guard() |
| `0027_scenario_runs` | `scenario_shock_application_no_truncate` | scenario_shock_application | BEFORE TRUNCATE | calc_output_guard() |
| `0030_identity_links` | `domain_identity_link_append_only` | domain_identity_link | BEFORE UPDATE OR DELETE | domain_append_only() |
| `0030_identity_links` | `domain_identity_link_checks` | domain_identity_link | BEFORE INSERT | domain_identity_link_checks() |
| `0030_identity_links` | `domain_identity_link_no_truncate` | domain_identity_link | BEFORE TRUNCATE | domain_append_only() |
| `0030_identity_links` | `legal_entity_append_only` | legal_entity | BEFORE UPDATE OR DELETE | domain_append_only() |
| `0030_identity_links` | `legal_entity_checks` | legal_entity | BEFORE INSERT | domain_identity_checks() |
| `0030_identity_links` | `legal_entity_no_truncate` | legal_entity | BEFORE TRUNCATE | domain_append_only() |
