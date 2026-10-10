"""Full original-source, native-transcript, coverage and delivery acceptance."""
from pathlib import Path
import copy
import hashlib
import jsonschema

from .compiler import strict_json, canonical, identity, settings, base_ids, validate_registry
from .native import atomic, sha
from .navigation import integrate_navigation
from .preparation import replay_preparation
from .semantic import claim_issues, review_view, review_issues
from .assembly import assemble, replay_anchors, location, owned_delivery, schema_projection
from .operations import Operations, ItemFailure, assertion_view
from .extraction import local_sources, dataset_sources, context_ids
from .inventory import obj, arr, STRING, BOOLEAN, split_work


def received(folder):
    folder = Path(folder)
    receipt = strict_json((folder / 'receipt.json').read_text(encoding='utf-8'))
    if receipt['status'] != 'received' or sha(folder/'response.raw') != receipt['response_sha256']:
        raise ValueError('Original native response receipt integrity differs: ' + folder.name)
    if receipt['response_bytes'] != (folder/'response.raw').stat().st_size:
        raise ValueError('Original native response byte count differs')
    if receipt.get('request_sha256') and sha(folder/'request.json') != receipt['request_sha256']:
        raise ValueError('Original native request receipt integrity differs')
    return strict_json((folder/'response.raw').read_bytes().decode('utf-8'))


def request_source_replay(unit, registry):
    """Replay historical linked context against its unchanged current parent."""
    known = registry.get(unit['source_id'])
    if known is None:
        linkage = unit.get('linked_subdivision', {})
        parent_id = linkage.get('original_source_id')
        parent = registry.get(parent_id)
        if not parent or unit.get('base_source_id') != parent_id or not unit.get('citation_only'):
            raise ValueError('Native request source is absent and has no preserved linked parent')
        a, b = unit['start_char'] - parent['start_char'], unit['end_char'] - parent['start_char']
        if not 0 <= a < b <= len(parent['text']) or unit['source_id'] != 'part-' + identity([parent_id, a, b])[:24]:
            raise ValueError('Historical linked source identity/interval differs')
        known = {**parent, 'source_id': unit['source_id'], 'start_char': parent['start_char'] + a,
                 'end_char': parent['start_char'] + b, 'text': parent['text'][a:b]}
    for key in ['source_id', 'source_sha256', 'physical_page', 'start_char', 'end_char', 'text', 'kind']:
        if known[key] != unit[key]:
            raise ValueError('Native request original source binding differs')


def replay_transcript(case, traces, registry):
    out = Path(case['directory'])
    allowed = (out.parents[1] / 'transport').resolve()
    proofs = {}
    for path in (out/'extraction-operations').glob('*.json'):
        saved = strict_json(path.read_text(encoding='utf-8'))
        if 'dispatched_prepared_request' not in saved: continue
        prepared = saved['dispatched_prepared_request']
        if identity(prepared) != saved['dispatched_prepared_request_sha256']:
            raise ValueError('Dispatched extraction preparation identity differs')
        folder = Path(saved['native_receipt']['folder']).resolve()
        if folder.parent != allowed: raise ValueError('Foreign native run receipt cannot enter acceptance')
        envelope = received(folder)
        wire = strict_json((folder/'request.json').read_text(encoding='utf-8'))
        if envelope['model'] != wire['model'] or len(envelope['choices']) != 1 or envelope['choices'][0]['finish_reason'] != 'stop':
            raise ValueError('Original extraction response identity or completion differs')
        actual = strict_json(envelope['choices'][0]['message']['content'])
        if actual != saved['result']: raise ValueError('Extraction checkpoint differs from original native response')
        jsonschema.validate(actual, prepared['response_contract'])
        if wire['structured_outputs']['json'] != prepared['response_contract']:
            raise ValueError('Native guided extraction contract differs')
        payload = strict_json(wire['messages'][1]['content'])
        original = strict_json(prepared['messages'][1]['content'])
        if payload.pop('native_response_contract') != prepared['response_contract'] or payload != original:
            raise ValueError('Native extraction payload differs from its prepared request')
        if wire['messages'][0]['content'] != prepared['messages'][0]['content'] + '\nReturn compact JSON without indentation. Include every required response key. Keep each navigation reason to one brief sentence.':
            raise ValueError('Native extraction instruction translation differs')
        if wire['temperature'] != 0 or wire['chat_template_kwargs'] != {'enable_thinking': False}:
            raise ValueError('Native extraction profile differs')
        for unit in original.get('source_units', []):
            request_source_replay(unit, registry)
        token_folder = Path(saved['native_receipt']['tokenization_folder']).resolve()
        if token_folder.parent != allowed: raise ValueError('Foreign tokenizer receipt')
        token = received(token_folder)
        token_wire = strict_json((token_folder/'request.json').read_text(encoding='utf-8'))
        if token_wire['messages'] != wire['messages'] or token_wire['model'] != wire['model'] or token_wire['chat_template_kwargs'] != wire['chat_template_kwargs']:
            raise ValueError('Actual tokenizer/chat-template input differs from generation')
        if token_wire['add_generation_prompt'] is not True or token_wire['add_special_tokens'] is not False:
            raise ValueError('Actual tokenizer special-token profile differs')
        sizing = strict_json((token_folder/'sizing.json').read_text(encoding='utf-8'))
        if type(token.get('count')) is not int or token['count'] != sizing['input_tokens']:
            raise ValueError('Exact tokenizer count replay differs')
        if token['count'] + wire['max_tokens'] + sizing['safety_reserve'] > sizing['context_tokens']:
            raise ValueError('Native tokenizer context budget exceeded')
        if sizing['wire_bytes'] != len(canonical(wire).encode('utf-8')) or sizing['wire_bytes'] > settings()['max_wire_bytes']:
            raise ValueError('Native wire byte bound replay differs')
        proofs[str(folder)] = {'value': actual, 'payload': original}
    checked = {'claim_reviews': 0, 'inventory_reviews': 0}
    def visit(value):
        if isinstance(value, list):
            for child in value: visit(child)
        elif isinstance(value, dict):
            if 'extraction_receipt' in value and 'review_receipt' in value:
                extraction = proofs[str(Path(value['extraction_receipt']['folder']).resolve())]['value']
                decision = extraction.get('table', extraction.get('field'))
                review = proofs[str(Path(value['review_receipt']['folder']).resolve())]
                audit = review['value']
                inspected = review['payload']['exact_current_claims']
                if value.get('native_projection'):
                    from .compact import REPRESENTATION, expand_decision, expand_review
                    projection = value['native_projection']
                    if projection['representation'] != REPRESENTATION: raise ValueError('Unknown native decision projection')
                    handle = projection['handle']
                    decision = expand_decision(extraction['items'][handle],table=projection.get('table',False))
                    audit = expand_review(decision,audit['items'][handle])
                    inspected = inspected[handle]
                if identity(decision) != value['decision_sha256'] or inspected != review_view(decision):
                    raise ValueError('Independent audit did not inspect the exact current native assertions')
                if 'same_contract_repair' in review['payload']:
                    # A repaired audit may see its own previous audit response;
                    # the immutable exact_current_claims must remain identical.
                    pass
                if review_issues(decision, audit, allow_optional_unknown=settings().get('unknown_optional_attributes_allowed', False)):
                    raise ValueError('Required native claim audit failed')
                unresolved = {claim['attribute'] for claim in decision['claims'] if claim['status'] == 'unknown'} - {
                    claim['attribute'] for claim in decision['claims'] if claim['status'] != 'unknown'}
                for key, verdict in review['value'].get('reconciliation', {}).items():
                    claim = review['payload']['reconciliation_claim_candidates'][key]
                    if not verdict['supported'] and not (settings().get('unknown_optional_attributes_allowed') and claim['attribute'] != 'name' and claim['attribute'] in unresolved):
                        raise ValueError('Required native partial-claim accounting audit failed')
                checked['claim_reviews'] += 1
            if 'decision_receipt' in value and 'review_receipt' in value:
                decision_proof = proofs[str(Path(value['decision_receipt']['folder']).resolve())]
                review_proof = proofs[str(Path(value['review_receipt']['folder']).resolve())]
                decision = decision_proof['value']
                review = review_proof['value']
                if identity(decision) != value['candidate_sha256'] or not review['complete'] or any(not item.get('decision_correct', item.get('supported')) for item in review['verdicts'].values()):
                    raise ValueError('Required native inventory/membership/applicability audit failed')
                if review_proof['payload']['exact_current_candidate'] != assertion_view(decision):
                    raise ValueError('Independent inventory audit did not inspect the exact current native assertions')
                if value.get('rationale_excluded_from_review') and not review_proof['payload'].get('rationale_excluded_from_review'):
                    raise ValueError('Independent inventory audit rationale exclusion differs')
                if value.get('group_name_projection'):
                    from .inventory import GROUP_NAME_PROJECTION, GROUP_ACTION_PROJECTION, project_group_assignments
                    projection = value['group_name_projection']
                    if projection['representation'] not in {GROUP_NAME_PROJECTION, GROUP_ACTION_PROJECTION}:
                        raise ValueError('Unknown native group name projection')
                    has_merge_target = all('merge_group_id' in item for item in decision['assignments'].values())
                    if has_merge_target != (projection['representation'] == GROUP_ACTION_PROJECTION):
                        raise ValueError('Native group representation does not match its original response keys')
                    projected = project_group_assignments(decision, decision_proof['payload']['existing_groups'],
                        projection['candidate_order'], table=projection['table'])
                    inspected = assertion_view(projected) if review_proof['payload'].get('rationale_excluded_from_review') else projected
                    if projected != projection['projected_assignments'] or inspected != review_proof['payload']['exact_projected_assignments']:
                        raise ValueError('Canonical group identity does not replay from the exact native keep/merge decisions and independent audit')
                    has_roles = any('accounting_role' in item for item in decision['assignments'].values())
                    if has_roles:
                        from .inventory import FIELD_ROLE_REPRESENTATION, field_role_issues
                        roles = value.get('native_field_roles', {})
                        expected_roles = {cid: item['accounting_role'] for cid, item in decision['assignments'].items()}
                        if projection['table'] or roles.get('representation') != FIELD_ROLE_REPRESENTATION or roles.get('roles') != expected_roles or field_role_issues(decision['assignments']):
                            raise ValueError('Native field source-role decisions do not replay from their original response and independent review')
                    elif value.get('native_field_roles'):
                        raise ValueError('Field source roles have no original native response')
                if value.get('membership_projection'):
                    from .inventory import membership_aliases, expand_memberships
                    projection=value['membership_projection']
                    tables=strict_json((Path(case['directory'])/'complete-candidate-inputs.json').read_text(encoding='utf-8'))['tables']
                    if projection['representation'] not in {'source-membership-alias-v1','source-membership-alias-v2'} or projection['aliases']!=membership_aliases(tables):
                        raise ValueError('Source membership aliases differ from the complete original inventory.')
                    expanded=expand_memberships(decision,projection['aliases'])
                    sources=strict_json((Path(case['directory'])/'source-applicability.json').read_text(encoding='utf-8'))['sources']
                    if identity(expanded)!=projection['projected_sources_sha256'] or any(item not in sources for item in expanded):
                        raise ValueError('Source membership does not replay from the original native aliases.')
                checked['inventory_reviews'] += 1
            for child in value.values(): visit(child)
    visit(traces)
    if not checked['claim_reviews']: raise ValueError('No independent native claim reviews')
    return {'passed': True, 'original_native_checkpoints': len(proofs), **checked,
            'raw_envelopes_reconstructed': False, 'actual_tokenizer_replay_passed': True}


def source_replay(case, registry, texts):
    original = replay_preparation(case)
    originals = strict_json((Path(case['directory'])/'source-texts.json').read_text(encoding='utf-8'))
    for digest, pages in originals.items():
        if texts.get(digest) != pages: raise ValueError('Original source text changed in extraction')
    valid_files = {item['sha256']: item for item in case['input_manifest']}
    extra_file = Path(case['directory'])/'extraction-inputs'/'native-table-identity.json'
    if case['kind'] == 'dataset':
        sample = strict_json((Path(case['directory'])/'sample-provenance.json').read_text(encoding='utf-8'))
        campaign_path = Path(case['directory']).parents[1]/'campaign.json'
        campaign = strict_json(campaign_path.read_text(encoding='utf-8'))
        manifest_path = Path(campaign['sample_archive_path']).parent/'manifest.json'
        if sha(manifest_path) != campaign['original_samples_manifest_sha256']:
            raise ValueError('Original source-collection manifest identity changed')
        manifest = strict_json(manifest_path.read_text(encoding='utf-8'))
        if sample != next(record for record in manifest['tables'] if record['pair_id'] == case['pair_id']):
            raise ValueError('Native source-collection observation differs from the original manifest entry')
        facts = strict_json((Path(case['directory'])/'dataset-facts.json').read_text(encoding='utf-8'))
        from pathlib import PurePosixPath
        expected = {'file_name': PurePosixPath(sample['source_path']).name, 'source_collection_path': sample['source_path'],
                    'original_source_format': sample['source_format'], 'sample_member': sample['output_path'],
                    'sample_sha256': sample['output_sha256'], 'sample_rows': sample['sampled_rows'],
                    'original_header_present': sample['header_present'] if sample['source_format'] in {'.csv', '.csv.gz'} else None,
                    'native_embedded_names_present': sample['source_format'] == '.parquet', 'column_count': len(facts),
                    'scope': 'Original source-collection metadata and complete native sample readout. Names/statistics do not establish production processes or data provenance.'}
        if extra_file.read_bytes() != (canonical(expected)+'\n').encode('utf-8'):
            raise ValueError('Derived source-collection identity does not replay from the original metadata')
        valid_files[sha(extra_file)] = {'path': str(extra_file), 'representation': 'native_table_readout'}
        if texts.get(sha(extra_file)) != {'1': extra_file.read_text(encoding='utf-8')}:
            raise ValueError('Derived source-collection text differs')
    validate_registry(registry)
    for key, unit in registry.items():
        digest = unit['source_sha256']
        if digest not in valid_files: raise ValueError('Foreign source bytes in extraction registry')
        text = texts[digest][str(unit['physical_page'])]
        if text[unit['start_char']:unit['end_char']] != unit['text']:
            raise ValueError('Exact extraction source interval replay differs')
        if key in original and unit != original[key]: raise ValueError('Original prepared source unit changed')
        if unit['kind'] == 'native_value':
            if case['kind'] != 'dataset': raise ValueError('Dataset observation entered Paper source registry')
            field = facts[unit['column_index']]
            if unit['raw_value'] != field['values_as_written'][unit['row_number']-1] or unit['original_binary_sha256'] != sample['output_sha256']:
                raise ValueError('Exact native value witness coordinate replay differs')
    return {'passed': True, 'source_units': len(registry), 'original_bytes_and_parsers_replayed': True}


def coverage(case, tables, registry):
    out = Path(case['directory'])
    assignments = strict_json((out/'source-applicability.json').read_text(encoding='utf-8'))['sources']
    original, navigation = integrate_navigation(case)
    by_source = {item['source_id']: item for item in assignments}
    table_map = {table['handle']: table for table in tables}
    field_tables = {field['handle']: table['handle'] for table in tables for field in table['fields']}
    entries = []
    for sid in base_ids(original):
        unit = original[sid]
        if sid in by_source:
            item = by_source[sid]
            handles = list(dict.fromkeys(item['table_handles'] + item['all_fields_in_tables'] + [field_tables[h] for h in item['field_handles']]))
            entry = {**item, 'used_for': [table_map[h]['name'] for h in handles]}
        elif case['kind'] == 'dataset' and unit['kind'] in {'data', 'native_field_readout'}:
            entry = {'source_id': sid, 'kind': unit['kind'], 'table_handles': ['table'],
                     'field_handles': [field['handle'] for field in tables[0]['fields']], 'all_fields_in_tables': [],
                     'disposition': 'used', 'used_for': [tables[0]['name']],
                     'reason': 'Complete original native sample is replayed positionally; all fields and all sampled rows are retained.'}
        else: raise ValueError('Original source unit is absent from complete coverage: ' + sid)
        entry['location'] = location(unit)
        entries.append(entry)
    if case['kind'] == 'paper':
        inventory = strict_json((out/'paper-native-inventory.json').read_text(encoding='utf-8'))
        assigned_tables = {item['candidate_id']: item for item in inventory['table_candidate_assignments']}
        for element in inventory['original_elements']:
            sid = element['ref']['source_id']
            parent = registry[sid].get('base_source_id', sid)
            matching = [entry for entry in entries if entry['source_id'] == parent]
            if not matching:
                matching = [entry for entry in entries if registry[entry['source_id']]['source_sha256'] == registry[sid]['source_sha256']
                            and registry[entry['source_id']]['physical_page'] == registry[sid]['physical_page']
                            and registry[entry['source_id']]['start_char'] <= registry[sid]['start_char'] < registry[sid]['end_char'] <= registry[entry['source_id']]['end_char']]
            if len(matching) != 1: raise ValueError('Original Paper element has no unambiguous source coverage interval')
            retained = [assigned_tables[cid]['group_id'] for cid in element['table_candidate_ids'] if assigned_tables[cid]['action'] != 'exclude']
            matching[0].setdefault('elements', []).append({**element, 'location': location(registry[sid]),
                'used_for': list(dict.fromkeys(table_map[h]['name'] for h in retained))})
    return entries


def assertion_targets(packet, applicable, decisions, registry):
    """Select assertions citing this source; global name/view accounting stays complete."""
    def intersects(ref, target):
        return (ref['source_sha256'] == target['source_sha256'] and ref['physical_page'] == target['physical_page']
                and max(ref['start_char'],target['start_char']) < min(ref['end_char'],target['end_char']))
    return [handle for handle in applicable if any(
        claim['status'] != 'unknown' and any(intersects(registry[ref['source_id']],registry[sid])
            for ref in claim['refs'] for sid in packet) for claim in decisions[handle]['claims'])]


def fidelity_audit(case, native, tables, decisions, registry):
    """Compare full original passages with exact retained attribute assertions."""
    out = Path(case['directory'])
    assignments = strict_json((out/'source-applicability.json').read_text(encoding='utf-8'))['sources']
    if not assignments: return {'passed': True, 'source_packets': 0, 'scope': 'No supplied supporting text; native sample and field gates still required.'}
    operations = Operations(native, out/'extraction-operations', case['kind'])
    from .compiler import packetize
    results = []
    table_fields = {table['handle']: [f['handle'] for f in table['fields']] for table in tables}
    names = {h: value['name'] for h, value in decisions.items()}
    compact_inventory = [{'handle': table['handle'], 'name': table['name'],
                          'fields': [{'handle': f['handle'], 'name': f['name']} for f in table['fields']]} for table in tables]
    ids = [item['source_id'] for item in assignments]
    by_id = {item['source_id']: item for item in assignments}
    def work(packet):
        applicable = list(dict.fromkeys(h for sid in packet for h in by_id[sid]['table_handles'] + by_id[sid]['field_handles']
                                       + [f for t in by_id[sid]['all_fields_in_tables'] for f in table_fields[t]]))
        targets = assertion_targets(packet,applicable,decisions,registry)
        batches = [targets[i:i+6] for i in range(0, len(targets), 6)] or [[]]
        receipts = []
        for batch in batches:
            own_refs = [ref['source_id'] for h in batch for claim in decisions[h]['claims'] if claim['status']!='unknown' for ref in claim['refs']]
            sources, local = local_sources(list(dict.fromkeys(context_ids(packet, registry)+own_refs)), registry)
            exact = {h: {'name': names[h], 'claims': review_view(decisions[h])} for h in batch}
            verdict = obj({'preserved': BOOLEAN, 'problem': STRING})
            contract = obj({'objects': obj({h: copy.deepcopy(verdict) for h in batch}), 'source_accounting_correct': BOOLEAN,
                            'problem': STRING, 'optional_information_gaps': arr(STRING)})
            contract['properties']['source_accounting_correct'] = {
                'type': 'boolean',
                'description': 'Judge target-passage name/view inventory and applicability accounting. An empty attribute-only objects map is valid and does not imply missing inventory review.'}
            payload = {'kind': case['kind'], 'job_id': case['case_id'], 'source_units': sources,
                       'target_source_ids': packet, 'current_retained_inventory': compact_inventory,
                       'exact_current_attribute_assertions': exact, 'exact_current_applicability': [by_id[sid] for sid in packet],
                       'objects_audited_in_this_subdivision': batch, 'objects_in_other_subdivisions': [h for h in targets if h not in batch]}
            payload['attribute_audit_scope'] = 'Known assertions whose own exact references intersect the target packet. All supplied current inventory names and original source views still receive the global source-completeness check.'
            payload['inventory_audit_scope'] = {
                'inventory': 'current_retained_inventory', 'passages': 'target_source_ids',
                'mandatory_even_when_attribute_batch_empty': True,
                'attribute_batch_empty': not batch,
                'empty_batch_meaning': 'No retained known attribute assertions cite this target packet. Independently judge all supplied inventory names, source views and applicability for these passages; do not require unrelated objects in the attribute-only objects map.'}
            payload['identifier_scope'] = {
                'source_ids': 'Identify passages in source_units and target_source_ids.',
                'table_handles': list(table_fields),
                'field_handles': [h for handles in table_fields.values() for h in handles],
                'inventory_handles': 'Internal object identities in current_retained_inventory and exact_current_applicability. They are not source IDs or required JSON output property names.',
                'response_objects': 'Only the source-linked attribute batch has verdict slots. A retained inventory handle is valid even when it is absent from this attribute-only response objects map.'}
            audit, receipt = operations.call('full-fidelity-' + identity([packet, batch, exact])[:20],
                'Perform the final independent source-first completeness audit. Read every complete target source unit. '
                'Verify that all dataset tables, actual field names and source views are retained in the current inventory and exact assertions. '
                'Check retained known optional assertions for source support; unresolved optional attributes may remain unknown under the explicit bounded run policy. '
                'Look for lost units, enum definitions, production methods, levels, periods, scales, row meaning, formats and codes. '
                'Only audit attribute preservation for objects_audited_in_this_subdivision; other subdivisions audit the remaining objects. '
                'There are two separate scopes: the objects map is only for source-linked known attribute assertions; source_accounting_correct is the mandatory target-passage inventory and applicability audit. '
                'If the attribute batch is empty, return objects={} and still perform the inventory audit using current_retained_inventory and every target source unit. '
                'An empty attribute batch is valid even when the global inventory is nonempty; it is not evidence that inventory names were omitted or unaudited. '
                'Distinguish source IDs from inventory table/field handles and response keys. Resolve applicability handles against current_retained_inventory, not against source_units or the attribute-only decoder objects map. '
                'Still check target-wide inventory and applicability completeness. Current assertions are exact, with no generation rationale. '
                'source_accounting_correct judges the current accounting decisions for these target passages. Correctly excluded citations, general background and passages about unprovided Dataset files have source_accounting_correct=true. '
                'A target passage need not mention every name in the retained inventory. Missing optional datatype or description information in this passage is not a missing name or source view. '
                'For Dataset, only the supplied physical data file and its complete native column inventory are schema targets; supporting documentation about other unprovided files is accounted for without creating new data-file schemas. '
                'Record available but unresolved optional details in optional_information_gaps without failing the case solely for those gaps. '
                'Unknown means not resolved in this bounded extraction, and must not be interpreted as proof that the source is silent.',
                payload, contract)
            errors = [h+': '+verdict['problem'] for h, verdict in audit['objects'].items() if not verdict['preserved']]
            if not audit['source_accounting_correct']: errors.append('Original source accounting incorrect: '+audit['problem'])
            atomic(out/('fidelity-audit-'+identity([packet,batch])[:20]+'.json'), {'audit': audit, 'receipt': receipt, 'errors': errors})
            if errors: raise ItemFailure('Full fidelity source audit requires native repair: ' + canonical(errors))
            receipts.append(receipt)
            registry.update(local)
        return receipts
    for packet in packetize(ids, registry, target=3000):
        for receipts in split_work(packet, work): results.extend(receipts)
    report = {'passed': True, 'native_audit_receipts': results, 'source_packets': len(results), 'independent_source_first_completeness_checked': True}
    atomic(out/'full-fidelity-audit.json', report)
    return report


def accept(case, native, tables, decisions, registry, texts, traces):
    out = Path(case['directory'])
    from .run_policy import active, validate_consumption
    policy = active()
    consumption = validate_consumption(tables, traces) if settings().get('completion_profile') == 'full_supported' else None
    current_coverage = coverage(case, tables, registry)
    candidate, evidence, retention = assemble(case['kind'], case['case_id'], tables, decisions, registry, current_coverage)
    # Preserve a complete candidate before an expensive final native gate; this
    # projection only copies model decisions and is explicitly not acceptance.
    candidate_schema, candidate_evidence = schema_projection(candidate)
    atomic(out/'candidate-full-schema.json', candidate_schema)
    atomic(out/'candidate-evidence.json', candidate_evidence)
    atomic(out/'candidate-status.json', {'status': 'UNVALIDATED', 'accepted': False, 'schema_sha256': sha(out/'candidate-full-schema.json'), 'source': 'deterministic projection of native decisions'})
    fidelity = fidelity_audit(case, native, tables, decisions, registry)
    entries = candidate['tables'] if case['kind'] == 'paper' else [candidate]
    for table, entry in zip(tables, entries):
        accounting = {}
        for handle in [table['handle']] + [field['handle'] for field in table['fields']]:
            trace = traces[handle]
            accounting[handle] = {'deferred_optional_source_ids': trace.get('deferred_optional_source_ids', []),
                                  'unresolved_source_claim_candidates': trace['final'].get('unresolved_partial_claims', {}),
                                  'earlier_verified_partial_claims': trace['final'].get('previous_verified_partial_claims', {}),
                                  'duplicate_native_claim_variants': trace['final'].get('duplicate_native_claim_variants', []),
                                  'optional_native_review_diagnostics': trace['final'].get('optional_native_review_diagnostics', {}),
                                  'native_claim_accounting': trace['final'].get('claim_accounting', [])}
            partials={**accounting[handle]['unresolved_source_claim_candidates'],**accounting[handle]['earlier_verified_partial_claims']}
            accounting[handle]['partial_claim_source_support']={ref['source_id']:{'location':location(registry[ref['source_id']]),'text':registry[ref['source_id']]['text']}
                for claim in partials.values() for ref in claim['refs']}
        entry['evidence']['optional_resolution'] = accounting
    atomic(out/'extraction-source-registry.json', registry)
    atomic(out/'final-retention-ledger.json', retention)
    atomic(out/'final-evidence-registry.json', evidence)
    atomic(out/'complete-candidate-inputs.json', {'tables': tables, 'decisions': decisions, 'coverage': current_coverage, 'traces': traces})
    def validate(snapshot):
        if snapshot != candidate: raise ValueError('Checked snapshot differs from complete assembled candidate')
        source = source_replay(case, registry, texts)
        anchors = replay_anchors(evidence, texts)
        transcript = replay_transcript(case, traces, registry)
        for item in fidelity.get('native_audit_receipts', []):
            folder = Path(item['folder'])
            original_audit = received(folder)
            audit_value = strict_json(original_audit['choices'][0]['message']['content'])
            if not audit_value.get('source_accounting_correct',audit_value.get('source_complete',False)) or any(not verdict['preserved'] for verdict in audit_value['objects'].values()):
                raise ValueError('Original final name/inventory fidelity audit did not pass')
            audit_payload = strict_json(strict_json((folder/'request.json').read_text(encoding='utf-8'))['messages'][1]['content'])
            for handle, assertion in audit_payload['exact_current_attribute_assertions'].items():
                if assertion != {'name': decisions[handle]['name'], 'claims': review_view(decisions[handle])}:
                    raise ValueError('Final fidelity audit did not inspect the exact accepted assertions')
        if case['kind'] == 'dataset':
            facts = strict_json((out/'dataset-facts.json').read_text(encoding='utf-8'))
            if [f['name'] for f in tables[0]['fields']] != [f['name'] for f in facts] or [f['handle'] for f in tables[0]['fields']] != [f['field_id'] for f in facts]:
                raise ValueError('Final Dataset original ordered field coverage differs')
        for table in tables:
            errors = claim_issues(decisions[table['handle']], registry, table=True, expected_name=table['name'],
                                  allow_optional_unknown=settings().get('unknown_optional_attributes_allowed', False))
            for field in table['fields']:
                fact = next((f for f in facts if f['field_id'] == field['handle']), None) if case['kind'] == 'dataset' else None
                errors += claim_issues(decisions[field['handle']], registry, expected_name=field['name'], dataset_fact=fact,
                                       allow_optional_unknown=settings().get('unknown_optional_attributes_allowed', False))
            if errors: raise ValueError('Complete assembled source/semantic guards failed: '+canonical(errors))
        return {'passed': True, 'case_id': case['case_id'], 'source_replay': source, 'exact_quote_replay': anchors,
                'original_native_transcript': transcript, 'full_fidelity': fidelity,
                'table_count': len(tables), 'field_count': len(retention), 'coverage_units': len(current_coverage),
                'schema_sha256': identity(snapshot), 'runtime_manual_injection': False,
                'completion_profile': settings().get('completion_profile'),
                'run_policy_sha256': policy['sha256'] if policy else None,
                'full_supported_source_consumption': consumption,
                'unknown_attribute_decisions': sum(claim['status'] == 'unknown' for decision in decisions.values() for claim in decision['claims']),
                'optional_attribute_perfection_required': False}
    pure_schema, separate_evidence = schema_projection(candidate)
    receipt = owned_delivery(candidate, out/'accepted-artifact', validate,
                             {'full-schema.json': pure_schema, 'evidence.json': separate_evidence})
    result = {'status': 'accepted', 'automatic_success': True, 'case_id': case['case_id'],
              'delivery': receipt, 'full_validation_sha256': sha(out/'accepted-artifact'/'snapshot-validation.json')}
    atomic(out/'acceptance.json', result)
    return result
