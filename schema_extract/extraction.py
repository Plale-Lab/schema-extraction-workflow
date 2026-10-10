"""Source-context preparation and complete physical Dataset extraction work."""
from pathlib import Path, PurePosixPath
import copy
import csv
import io
import json

from .compiler import identity, canonical, strict_json, base_ids, packetize, RequestTooLarge, settings
from .native import atomic, sha
from .navigation import integrate_navigation
from .preparation import add_text, replay_preparation
from .semantic import source_spans, review_view
from .operations import Operations, ItemFailure


def context_ids(ids, registry):
    keys = base_ids(registry)
    result = []
    for sid in ids:
        unit = registry[sid]
        parent = unit.get('base_source_id', sid)
        if parent not in keys:
            parents = [key for key in keys if registry[key]['source_sha256'] == unit['source_sha256']
                       and registry[key]['physical_page'] == unit['physical_page']
                       and registry[key]['start_char'] <= unit['start_char'] < unit['end_char'] <= registry[key]['end_char']]
            parent = parents[0] if parents else sid
        candidates = [parent]
        if parent in keys:
            index = keys.index(parent)
            candidates = [key for key in keys[max(0, index-1):index+2]
                          if registry[key]['source_sha256'] == registry[parent]['source_sha256']]
        for key in candidates:
            if key not in result: result.append(key)
    return result


def local_sources(ids, registry, citation_spans=True):
    local = {key: copy.deepcopy(registry[key]) for key in ids}
    units = source_spans(list(local), local) if citation_spans else list(local.values())
    return units, local


def dataset_sources(case, registry):
    """Add replayable source-collection metadata and exact native sample witnesses."""
    out = Path(case['directory'])
    texts = strict_json((out / 'source-texts.json').read_text(encoding='utf-8'))
    registry = copy.deepcopy(registry)
    facts = strict_json((out / 'dataset-facts.json').read_text(encoding='utf-8'))
    sample = strict_json((out / 'sample-provenance.json').read_text(encoding='utf-8'))
    identity_file = out / 'extraction-inputs' / 'native-table-identity.json'
    identity_file.parent.mkdir(exist_ok=True)
    observation = {'file_name': PurePosixPath(sample['source_path']).name,
                   'source_collection_path': sample['source_path'],
                   'original_source_format': sample['source_format'],
                   'sample_member': sample['output_path'], 'sample_sha256': sample['output_sha256'],
                   'sample_rows': sample['sampled_rows'],
                   'original_header_present': sample['header_present'] if sample['source_format'] in {'.csv', '.csv.gz'} else None,
                   'native_embedded_names_present': sample['source_format'] == '.parquet',
                   'column_count': len(facts),
                   'scope': 'Original source-collection metadata and complete native sample readout. Names/statistics do not establish production processes or data provenance.'}
    atomic(identity_file, observation)
    table_ids = add_text(registry, texts, identity_file.read_text(encoding='utf-8'), sha(identity_file), 1,
                         identity_file.name, 'native_table_readout', citation_only=True, machine_readout=True,
                         original_binary_sha256=sample['output_sha256'], original_format=sample['source_format'])
    readout = out / 'inputs' / 'sample-readout.csv'
    if sample['source_format'] != '.csv.gz':
        text = readout.read_text(encoding='utf-8')
        lines = text.splitlines(keepends=True)
        reader = csv.reader(lines)
        next(reader)
        end = sum(len(line) for line in lines[:reader.line_num])
        sid = 'header-' + identity([sha(readout), 0, end])[:24]
        registry[sid] = {'source_id': sid, 'source_sha256': sha(readout), 'physical_page': 1,
                         'start_char': 0, 'end_char': end, 'text': text[:end], 'kind': 'header',
                         'source_path': readout.name, 'section_context': readout.name + '#complete-header',
                         'citation_only': True, 'machine_readout': sample['source_format'] != '.csv',
                         'original_binary_sha256': sample['output_sha256'],
                         'physical_header_present': True if sample['source_format'] == '.csv' else None,
                         'original_format': sample['source_format']}
        table_ids.append(sid)
    # Value witnesses refer to exact intervals of a replayed complete field
    # readout. Row/column coordinates are retained; no sample prefix is selected.
    for field in facts:
        path = out / 'inputs' / ('field-' + str(field['column_index']).zfill(4) + '.json')
        text = path.read_text(encoding='utf-8')
        marker = '"values_as_written":['
        start = text.index(marker) + len(marker)
        positions = []
        for value in field['values_as_written']:
            token = canonical(value)
            if text[start:start+len(token)] != token:
                raise ValueError('Native field value interval replay differs')
            positions.append((start, start+len(token)))
            start += len(token) + 1
        values = field['values_as_written']
        indices = [0, len(values)-1]
        empty = next((index for index, value in enumerate(values) if value == ''), None)
        if empty is not None: indices.append(empty)
        seen, duplicates = {}, None
        for index, value in enumerate(values):
            if value and value in seen:
                duplicates = [seen[value], index]
                indices.extend(duplicates)
                break
            if value: seen[value] = index
        witnesses = {}
        for index in dict.fromkeys(indices):
            a, b = positions[index]
            sid = 'value-' + identity([sha(path), field['column_index'], index, a, b])[:24]
            registry[sid] = {'source_id': sid, 'source_sha256': sha(path), 'physical_page': 1,
                             'start_char': a, 'end_char': b, 'text': text[a:b],
                             'source_path': path.name, 'section_context': path.name + '#row-' + str(index+1),
                             'kind': 'native_value', 'machine_readout': True, 'citation_only': True,
                             'original_binary_sha256': sample['output_sha256'], 'column_index': field['column_index'],
                             'row_number': index+1, 'raw_value': values[index]}
            witnesses[index] = sid
        field['witness_source_ids'] = list(witnesses.values())
        field['negative_constraint_witnesses'] = {
            'empty_cell': {'source_id': witnesses[empty], 'row_number': empty+1} if empty is not None else None,
            'repeated_values': [{'source_id': witnesses[index], 'row_number': index+1} for index in duplicates] if duplicates else []}
    atomic(out / 'extraction-source-registry.json', registry)
    atomic(out / 'extraction-source-texts.json', texts)
    atomic(out / 'extraction-source-extension.json', {
        'source_collection_metadata': observation, 'metadata_file_sha256': sha(identity_file),
        'sample_provenance_sha256': sha(out / 'sample-provenance.json'),
        'registry_sha256': identity(registry), 'original_registry_sha256': case['registry_sha256'],
        'fields': [{'field_id': f['field_id'], 'column_index': f['column_index'],
                    'witness_source_ids': f['witness_source_ids'],
                    'negative_constraint_witnesses': f['negative_constraint_witnesses']} for f in facts],
        'source_preparation_only': True, 'model_calls': 0, 'automatic_success': False})
    return registry, texts, facts, table_ids


def linked_source_ids(ids, registry, target=1500):
    """Expose explicit exact subdivisions of an oversized continuous source."""
    result = []
    for key in ids:
        unit = registry[key]
        if len(unit['text']) <= target:
            result.append(key); continue
        for a in range(0, len(unit['text']), target):
            b = min(len(unit['text']), a+target)
            sid = 'part-' + identity([key, a, b])[:24]
            registry[sid] = {**unit, 'source_id': sid, 'text': unit['text'][a:b],
                             'start_char': unit['start_char']+a, 'end_char': unit['start_char']+b,
                             'base_source_id': key, 'citation_only': True,
                             'linked_subdivision': {'original_source_id': key, 'part_index': a//target,
                                                    'parts_total': (len(unit['text'])+target-1)//target}}
            result.append(sid)
    return list(dict.fromkeys(result))


def extract_item(case, operations, registry, handle, primary, contextual, *, table=False, expected_name=None, fact=None):
    """Complete source consumption followed by source-bound native reconciliation."""
    from .inventory import split_work
    primary = list(dict.fromkeys(primary))
    all_ids = linked_source_ids(list(dict.fromkeys(primary + contextual)), registry)
    # Keep source identity and exact negative counterexamples in every local task.
    anchors = linked_source_ids(primary[:1] + (fact.get('witness_source_ids', []) if fact else []), registry)
    packets = packetize(all_ids, registry, target=4500)
    deferred = []
    limit = settings().get('max_optional_source_packets_per_item') if settings().get('completion_profile') == 'names_first' else None
    if limit is not None and len(packets) > limit:
        deferred = [sid for packet in packets[limit:] for sid in packet]
        packets = packets[:limit]
    partials = []
    def base_payload(ids, scope):
        sources, local = local_sources(ids, registry)
        payload = {'kind': case['kind'], 'job_id': case['case_id'], 'handle': handle, 'source_units': sources,
                   'source_scope': scope, 'original_object': {'name': expected_name, 'handle': handle},
                   'original_field': {key: fact[key] for key in ['field_id', 'name', 'name_origin', 'column_index', 'negative_constraint_witnesses']} if fact else None}
        return payload, local
    def work(packet):
        ids = list(dict.fromkeys(anchors + packet))
        scope = {'prepared_source_units_total': len(all_ids), 'current_source_packet_ids': packet,
                 'full_source_consumption_before_reconciliation': True, 'sample_only': fact is not None,
                 'sample_rows': fact['sample_rows'] if fact else None,
                 'instructions': 'This is an explicit source subdivision. Preserve supported partial facts and unknown decisions; later original-source packets are reconciled. Sample values do not prove legal enums or positive population constraints.'}
        scope['deferred_optional_source_ids'] = deferred
        scope['optional_deferral_reason'] = 'The user prioritizes completion of source-derived names across all cases. Additional optional-attribute context remains preserved and replayable; do not claim it was inspected here or assert that its information is absent.' if deferred else None
        scope['full_source_consumption_before_reconciliation'] = not deferred
        payload, local = base_payload(ids, scope)
        value, trace = operations.extract_reviewed(handle + '-source-' + identity(packet)[:16], payload, local,
                                                   table=table, expected_name=expected_name, dataset_fact=fact)
        registry.update(local)
        return {'decision': value, 'trace': trace, 'source_ids': ids, 'consumed_source_ids': packet}
    for packet in packets:
        partials.extend(split_work(packet, work))
    if not partials: raise ItemFailure('An established object has no original source work.')
    current = partials[0]
    reconciliation = []
    # Pairwise reconciliation has finite source operands. It is not a host
    # description rewrite or an implicit unbounded retry of the same task.
    for index, other in enumerate(partials[1:], 1):
        operands = [current['decision'], other['decision']]
        claims = {str(i)+'-'+str(j): claim for i, decision in enumerate(operands)
                  for j, claim in review_view(decision).items() if claim['status'] != 'unknown'}
        cited = list(dict.fromkeys(ref['source_id'] for claim in claims.values() for ref in claim['refs']))
        payload, local = base_payload(list(dict.fromkeys(anchors + cited)),
                                      {'source_bound_reconciliation': True, 'sample_only': fact is not None,
                                       'sample_rows': fact['sample_rows'] if fact else None})
        payload.update(reconciliation_claim_candidates=claims,
                       reconciliation_rule='Combine compatible description facts using original wording and at most four exact locations. Retain genuine source conflicts. Apply declared > observed > inferred; equally ranked disagreement remains unresolved. Account for every supplied known partial claim. Exclude only a claim actually superseded or shown unsupported by the complete original sources, with a specific independently reviewed reason.')
        value, trace = operations.extract_reviewed(handle + '-reconciliation-' + str(index), payload, local,
                                                   table=table, expected_name=expected_name, dataset_fact=fact)
        registry.update(local)
        current = {'decision': value, 'trace': trace}
        reconciliation.append(current)
    return current['decision'], {'final': current['trace'], 'partials': partials, 'reconciliation': reconciliation,
                                 'consumed_source_ids': [sid for packet in packets for sid in packet],
                                 'deferred_optional_source_ids': deferred,
                                 'complete_source_consumption': not deferred, 'completion_profile': settings().get('completion_profile')}


def extract_dataset(case, native):
    """Produce reviewed complete decisions; final coverage/delivery still follow."""
    out = Path(case['directory'])
    registry, decisions = integrate_navigation(case)
    registry, texts, facts, table_ids = dataset_sources(case, registry)
    operations = Operations(native, out / 'extraction-operations', 'dataset')
    from .inventory import attribution, applicable_ids
    sample = strict_json((out / 'sample-provenance.json').read_text(encoding='utf-8'))
    table_name = PurePosixPath(sample['source_path']).name
    table_records = [{'handle': 'table', 'name': table_name, 'source_ids': table_ids,
                      'reason': 'One physical original sample file with its complete native ordered inventory.',
                      'fields': [{'handle': field['field_id'], 'name': field['name'], 'source_ids': field['source_ids']} for field in facts]}]
    assignments, attribution_traces = attribution(case, operations, registry, table_records, dataset=True)
    results, traces, table_records = {}, {}, []
    work = [('table', None, table_ids)] + [(field['field_id'], field, field['source_ids'] + field['witness_source_ids']) for field in facts]
    compact_jobs = []
    previous_path = out/'dataset-extraction-traces.json'
    previous = strict_json(previous_path.read_text(encoding='utf-8')) if previous_path.exists() else {}
    for handle, fact, primary in work:
        contextual = context_ids(applicable_ids(handle, 'table', assignments), registry)
        old_projection = previous.get(handle,{}).get('final',{}).get('native_projection')
        if settings().get('compact_attribute_batches_enabled') and (handle not in previous or old_projection):
            compact_jobs.append({'handle':handle,'name':fact['name'] if fact else table_name,'primary':primary,'contextual':contextual,'fact':fact,'table':fact is None,
                                 'defer_optional':bool(fact and case.get('defer_unresolved_optional') and not previous.get(handle,{}).get('final',{}).get('independent_review_passed'))})
            continue
        value, trace = extract_item(case, operations, registry, handle, primary, contextual,
                                   table=fact is None, expected_name=fact['name'] if fact else table_name, fact=fact)
        results[handle] = value
        traces[handle] = trace
        atomic(out / 'dataset-reviewed-decisions.json', results)
        atomic(out / 'dataset-extraction-traces.json', traces)
        atomic(out / 'extraction-source-registry.json', registry)
        atomic(out / 'dataset-extraction-progress.json', {'status': 'running', 'completed_items': len(results),
                                                         'total_items': len(work), 'current_handle': handle,
                                                         'automatic_success': False})
    if compact_jobs:
        from .compact import extract_fields
        def progress(values, proofs):
            results.update(values); traces.update(proofs)
            atomic(out/'dataset-reviewed-decisions.json',results)
            atomic(out/'dataset-extraction-traces.json',traces)
            atomic(out/'extraction-source-registry.json',registry)
            atomic(out/'dataset-extraction-progress.json',{'status':'running','completed_items':len(results),
                   'total_items':len(work),'current_handle':list(values)[-1],'automatic_success':False})
        extract_fields(case,operations,registry,compact_jobs,progress)
    table_records = [{'handle': 'table', 'name': results['table']['name'], 'source_ids': table_ids,
                      'reason': results['table']['reason'],
                      'fields': [{'handle': field['field_id'], 'name': field['name'],
                                  'source_ids': field['source_ids']} for field in facts]}]
    atomic(out / 'dataset-extraction-progress.json', {'status': 'reviewed_decisions_complete_final_gates_pending',
                                                     'completed_items': len(results), 'total_items': len(work),
                                                     'automatic_success': False})
    traces['_source_attribution'] = attribution_traces
    atomic(out / 'dataset-extraction-traces.json', traces)
    return table_records, results, registry, texts, traces


def extract_paper(case, native):
    from .inventory import discover_paper, reconcile, attribution, applicable_ids
    out = Path(case['directory'])
    registry, navigation = integrate_navigation(case)
    texts = strict_json((out / 'source-texts.json').read_text(encoding='utf-8'))
    operations = Operations(native, out / 'extraction-operations', 'paper')
    candidates, fields, elements, discovery_traces = discover_paper(case, operations, registry)
    tables, table_assignments, grouping_traces = reconcile(candidates, operations, registry, table=True)
    if not tables: raise ItemFailure('No retained dataset table; full Paper acceptance is not established.')
    by_candidate = {item['candidate_id']: item for item in table_assignments}
    field_assignments, field_traces = [], []
    for table in tables:
        selected = {key: field for key, field in fields.items()
                    if by_candidate[field['table_candidate_id']]['group_id'] == table['handle']}
        retained, assigned, reviewed = reconcile(selected, operations, registry, table=False, parent=table)
        table['fields'] = retained
        field_assignments.extend(assigned); field_traces.extend(reviewed)
    accounting = {'tables': tables, 'table_candidate_assignments': table_assignments,
                  'field_candidate_assignments': field_assignments,
                  'excluded_table_field_candidates': [key for key, field in fields.items()
                                                       if by_candidate[field['table_candidate_id']]['action'] == 'exclude'],
                  'original_elements': elements}
    atomic(out / 'paper-native-inventory.json', accounting)
    assignments, attribution_traces = attribution(case, operations, registry, tables)
    results, item_traces = {}, {}
    previous_path = out/'paper-extraction-traces.json'
    previous = strict_json(previous_path.read_text(encoding='utf-8')) if previous_path.exists() else {}
    for table in tables:
        work = [(table, True)] + [(field, False) for field in table['fields']]
        compact_jobs = []
        for item, is_table in work:
            contextual = context_ids(applicable_ids(item['handle'], table['handle'], assignments), registry)
            old_projection = previous.get(item['handle'],{}).get('final',{}).get('native_projection')
            if settings().get('compact_attribute_batches_enabled') and (item['handle'] not in previous or old_projection):
                compact_jobs.append({'handle':item['handle'],'name':item['name'],'primary':item['source_ids'],'contextual':contextual,'table':is_table,
                                     'defer_optional':bool(not is_table and case.get('defer_unresolved_optional') and not previous.get(item['handle'],{}).get('final',{}).get('independent_review_passed'))})
                continue
            value, trace = extract_item(case, operations, registry, item['handle'], item['source_ids'], contextual,
                                       table=is_table, expected_name=item['name'])
            results[item['handle']] = value; item_traces[item['handle']] = trace
            atomic(out / 'paper-reviewed-decisions.json', results)
            atomic(out / 'paper-extraction-traces.json', item_traces)
            atomic(out / 'extraction-source-registry.json', registry)
            atomic(out / 'paper-extraction-progress.json', {'status': 'running', 'completed_items': len(results),
                                                           'current_handle': item['handle'], 'automatic_success': False})
        if compact_jobs:
            from .compact import extract_fields
            def progress(values, proofs):
                results.update(values); item_traces.update(proofs)
                atomic(out/'paper-reviewed-decisions.json',results)
                atomic(out/'paper-extraction-traces.json',item_traces)
                atomic(out/'extraction-source-registry.json',registry)
                atomic(out/'paper-extraction-progress.json',{'status':'running','completed_items':len(results),
                       'current_handle':list(values)[-1],'automatic_success':False})
            extract_fields(case,operations,registry,compact_jobs,progress)
    traces = {**item_traces, '_discovery': discovery_traces, '_table_grouping': grouping_traces,
              '_field_grouping': field_traces, '_source_attribution': attribution_traces}
    atomic(out / 'paper-extraction-traces.json', traces)
    atomic(out / 'extraction-source-texts.json', texts)
    return tables, results, registry, texts, traces
