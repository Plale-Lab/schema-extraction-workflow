"""Native source inventory, table/field reconciliation and source applicability.

All model operations belong to extraction. Program handles preserve every
current candidate; the host never supplies grouping answers or field counts.
"""
from pathlib import Path
import copy

from .compiler import ROOT, strict_json, identity, base_ids, packetize, settings, RequestTooLarge
from .native import atomic
from .operations import ItemFailure
from .extraction import local_sources, context_ids
from .semantic import name_present, printed_name_choices


def obj(properties):
    return {'type': 'object', 'additionalProperties': False, 'required': list(properties), 'properties': properties}


def arr(items):
    return {'type': 'array', 'items': items}


STRING = {'type': 'string'}
BOOLEAN = {'type': 'boolean'}
REF = {'$ref': '#/$defs/ref'}


def with_refs(schema, local):
    schema = copy.deepcopy(schema)
    schema.setdefault('$defs', {})['ref'] = obj({'source_id': {'enum': list(local)}})
    return schema


def exact_ids(items, key, expected):
    actual = [item[key] for item in items]
    return [] if actual == list(expected) else ['Account for each supplied target exactly once in supplied order.']


def split_work(packet, work, depth=0):
    """Only split on a pre-dispatch bound or a received terminal output limit."""
    try:
        return [work(packet)]
    except RequestTooLarge:
        if depth >= settings()['max_split_depth'] or len(packet) < 2: raise
        middle = len(packet) // 2
        return split_work(packet[:middle], work, depth+1) + split_work(packet[middle:], work, depth+1)


def discovery_context(packet, registry, page_limit=18000):
    """Keep bounded complete page context around split dictionary targets."""
    ids = context_ids(packet, registry)
    pages = list(dict.fromkeys((registry[sid]['source_sha256'],
                               registry[sid]['physical_page']) for sid in packet))
    expanded, deferred = [], []
    for digest, page in pages:
        page_ids = [sid for sid in base_ids(registry)
                    if registry[sid]['source_sha256'] == digest
                    and registry[sid]['physical_page'] == page]
        record = {'source_sha256': digest, 'physical_page': page,
                  'source_ids': page_ids}
        if sum(len(registry[sid]['text']) for sid in page_ids) <= page_limit:
            ids.extend(sid for sid in page_ids if sid not in ids)
            expanded.append(record)
        else:
            deferred.append(record)
    return ids, {'complete_pages': expanded, 'pages_using_adjacent_context': deferred,
                 'instruction': 'Only target_source_ids need new candidates. Other supplied units interpret targets. All original units remain separate discovery targets; no target text is clipped.'}


def membership_sources(ref_ids, registry, compact_citations=False):
    """Bound optional citation menus while preserving complete original context."""
    ids = context_ids(ref_ids, registry)
    if not compact_citations:
        return local_sources(ids, registry)
    ids = list(dict.fromkeys(ids + ref_ids))
    local = {sid: copy.deepcopy(registry[sid]) for sid in ids}
    return list(local.values()), local


def provisional_name_diagnostics(tables, local):
    """Unverified discovery names remain candidates until native disposition."""
    return [{'table_index': ti, 'field_index': fi, 'name': field['name'],
             'source_id': field['ref']['source_id'],
             'problem': 'Provisional name absent from its cited source; native reconciliation must exclude it or supply a supported retained name.'}
            for ti, table in enumerate(tables) for fi, field in enumerate(table['fields'])
            if not name_present(field['name'], local[field['ref']['source_id']]['text'])]


def retained_name_issues(records, local, *, table, anchors=None, registry=None):
    """No unsupported provisional field may become a retained group."""
    if table:
        return []  # Table identities may be explicitly inferred from context.
    errors = []
    for item in records:
        if item['action'] == 'exclude' or name_present(item['name'], local[item['ref']['source_id']]['text']):
            continue
        anchor = (anchors or {}).get(item.get('group_id')) if item['action'] == 'merge' else None
        supported_anchor = anchor and anchor['name'] == item['name'] and any(
            name_present(item['name'], (local.get(sid) or (registry or {}).get(sid) or {}).get('text', ''))
            for sid in anchor['source_ids'])
        if not supported_anchor:
            errors.append('Retained field '+item['name']+': its own name reference or its established canonical group must carry the exact printed name; exclude an unsupported provisional candidate or use a supported source name.')
    return errors


def field_identity_collisions(records, existing):
    """Require native disposition before duplicate names collide in final evidence."""
    names = {}
    for gid, group in existing.items():
        names.setdefault(group['name'], set()).add(gid)
    for item in records:
        if item['action'] != 'exclude':
            names.setdefault(item['name'], set()).add(item['group_id'])
    return ['Repeated field name '+name+': distinct groups '+str(sorted(groups))+
            ' collide in the final field/evidence namespace. Natively merge documented repetitions or use distinct original printed identifiers; do not silently rename or drop a field.'
            for name, groups in names.items() if len(groups) > 1]


GROUP_NAME_PROJECTION = 'native-group-new-name-only-v1'
GROUP_ACTION_PROJECTION = 'native-group-action-anchor-v2'
FIELD_ROLE_REPRESENTATION = 'native-field-accounting-role-v1'
FIELD_ROLES = ['dataset_field', 'feature_family', 'dictionary_heading', 'dataset_summary_property',
               'analysis_result', 'method_example', 'unresolved']


def field_role_issues(assignments):
    """Native role assessment must support a retained record field."""
    return [cid + ': a retained field requires original dataset_field or feature_family role; account for explanatory candidates by exclusion.'
            for cid, assignment in assignments.items()
            if assignment['action'] != 'exclude' and assignment.get('accounting_role') not in {'dataset_field', 'feature_family'}]


def discovery_link_issues(value, packet):
    """Only current-response array positions can link native coverage objects."""
    errors = [] if set(value['source_decisions']) == set(packet) else ['Every original target needs its keyed source decision.']
    count = len(value['tables'])
    for index, element in enumerate(value['elements']):
        if any(not 0 <= table_index < count for table_index in element['table_indices']):
            errors.append('Coverage element '+str(index)+' has stale or foreign table_indices '+str(element['table_indices'])+
                          '; the CURRENT response tables array has '+str(count)+' entries and valid zero-based positions '+
                          str(list(range(count)))+'. Rebuild these links after changing tables; paper table numbers are not response positions.')
    count = len(value['elements'])
    for sid, source in value['source_decisions'].items():
        if any(not 0 <= index < count for index in source['element_indices']):
            errors.append('Source '+sid+' has stale or foreign element_indices; valid CURRENT response zero-based element positions are '+str(list(range(count)))+'. Rebuild links after changing elements.')
        if source['data_describing'] and not source['element_indices']:
            errors.append('Every data-describing target needs an explicit element candidate.')
    return errors


def project_group_assignments(value, existing, order, *, table):
    """Native keep names establish groups; native merges select their identity."""
    def group_id(cid, item):
        if 'merge_group_id' not in item:
            return item['group_id']  # Replay immutable v1 responses.
        return cid if item['action'] == 'keep' else item['merge_group_id'] if item['action'] == 'merge' else ''
    created = {group_id(cid, item): item for cid, item in value['assignments'].items() if item['action'] == 'keep'}
    records = []
    for cid in order:
        raw = value['assignments'][cid]
        gid = group_id(cid, raw)
        anchor = existing.get(gid)
        new = created.get(gid)
        name = raw['new_name']
        entity = raw.get('new_row_entity', '')
        if raw['action'] == 'merge':
            name = anchor['name'] if anchor else new['new_name'] if new else name
            if table:
                entity = anchor['row_entity'] if anchor else new['new_row_entity'] if new else entity
        records.append({'candidate_id': cid, 'action': raw['action'], 'group_id': gid,
                        'name': name, 'ref': copy.deepcopy(raw['ref']), 'reason': raw['reason'],
                        **({'row_entity': entity} if table else {})})
    return records


def discover_paper(case, operations, registry):
    out = Path(case['directory'])
    packets, candidates, fields, elements, traces = [], {}, {}, [], []
    # Classification is navigation. Whole-source accounting also checks units
    # labelled none, so navigation cannot authorize omitting a dataset view.
    ids = base_ids(registry)
    def work(packet, adjacent_only=False, size_stop=None):
        context, context_scope = discovery_context(packet, registry)
        if adjacent_only:
            context = context_ids(packet, registry)
            context_scope = {'complete_pages': [], 'pages_using_adjacent_context': context_scope['complete_pages'] + context_scope['pages_using_adjacent_context'],
                             'bounded_context_reason': str(size_stop),
                             'instruction': 'Complete-page auxiliary context exceeded a bounded request. These exact original adjacent units interpret the unchanged targets. Every original unit remains a separate discovery target; no target text is clipped.'}
        sources, local = local_sources(context, registry, citation_spans=False)
        contract = strict_json((ROOT / 'contracts' / 'discovery.json').read_text(encoding='utf-8'))
        contract = with_refs(contract, local)
        contract['properties']['elements']['items']['properties']['table_indices']['description'] = (
            'Zero-based positions in THIS response tables array, not printed paper table numbers or earlier response indices. '
            'Rebuild after changing tables. Empty when this element has no current table candidate.')
        def replace(value):
            if isinstance(value, dict):
                if set(value.get('properties', {})) == {'source_id'}:
                    value.clear(); value.update(REF)
                else:
                    for child in value.values(): replace(child)
            elif isinstance(value, list):
                for child in value: replace(child)
        # Replace ordinary reference occurrences; preserve the bound definition.
        for child in contract['properties'].values(): replace(child)
        contract['properties']['source_decisions'] = obj({sid: obj({'data_describing': BOOLEAN,
            'element_indices': arr({'type': 'integer', 'minimum': 0,
                                   'description': 'Zero-based position in THIS response elements array; rebuild after changing elements.'}), 'reason': STRING}) for sid in packet})
        contract['required'].append('source_decisions')
        def validate(value):
            return discovery_link_issues(value, packet)
        payload = {'kind': 'paper', 'job_id': case['case_id'], 'target_source_ids': packet,
                   'source_units': sources, 'source_scope': 'Only this original paper; adjacent units are context.',
                   'discovery_request_representation': 'Complete unchanged original source units. Original unit IDs are the citation choices; no duplicated sentence-level citation menus. Larger contiguous target packets preserve dataset-level context, with native byte/token/output bounds and finite splitting still enforced.',
                   'requested_dataset_entity_scope': (
                       'Identify the underlying datasets described or used by the paper, with their original data records and fields. '
                       'A rendered paper table is a documentation view, not automatically a dataset table. '
                       'When a paper summary lists multiple datasets, its rows can describe separate underlying datasets; '
                       'do not turn the Dataset/Examples/Features/Type summary headings into columns of their original records. '
                       'Model-performance comparisons, model/algorithm catalogs, experimental configurations, population or variant summaries, '
                       'algorithm-internal summaries and hypothetical examples are not original dataset-record tables merely because they are tabular. '
                       'Retain them as separate data only with positive original evidence of a released or stored dataset with its own records or file. '
                       'They can still supply properties of an actual dataset or field, and every target remains explicitly accounted for. '
                       'Use the paper own dataset identities and actual named variables. A dataset view may have no named fields when the paper only describes its existence or properties. '
                       'Do not invent field names or convert aggregate counts into original data columns.'),
                   'dictionary_page_context': context_scope,
                   'coverage_index_convention': 'table_indices and element_indices refer only to zero-based arrays in this exact response. Rebuild links after removing or reordering candidates. Printed paper table numbers and previous response positions are not valid links.',
                   'field_definition_table_interpretation': (
                       'In a data dictionary or feature-definition table, entries in a Feature, Variable name, '
                       'Data variables or other field-definition column can be the actual dataset fields. '
                       'Read those variable rows, including wrapped names and printed codes; do not replace '
                       'them with the explanatory column headings such as Feature, Aggregation or Unit. '
                       'Use the caption and surrounding paper to determine the described dataset row entity. '
                       'A documentation view with one variable definition per row does not by itself mean '
                       'that the underlying dataset has one variable definition per row. '
                       'Apply this interpretation only to actual field-definition rows in the current target units. '
                       'Methods, processing and availability narrative do not justify inventing data-column names. '
                       'Every discovered name must occur in its own cited supplied source. '
                       'Discovery candidates are provisional: if validation says a name has no name-bearing '
                       'source ID, remove that unsupported candidate or use an actual printed variable name; '
                       'do not preserve the unsupported name as an established identity. '
                       'A table view without named fields in the current target may have an empty field list. '
                       'The independent review must check this distinction and all named variable rows.')}
        try:
            value, trace = operations.checked('inventory-' + identity(packet)[:16],
                'Enumerate every dataset table view, its actual fields or named feature families, and every data-describing element in the target units. '
                'Read complete tables, captions, footnotes and methods. Preserve paper field order and wrapped fragments. '
                'Apply requested_dataset_entity_scope: enumerate underlying dataset records, not every rendered results or summary table. '
                'Return all target source_decisions. Context helps interpret targets; do not create duplicate candidates solely from adjacent context. '
                'Do not impose expected object or field counts. In tables of feature statistics/rankings, raw variables and derived features can be fields; '
                'Units, Number of features, Mean and Rank headings are not automatically fields. A paper table of evaluation scores is not automatically a dataset table. '
                'Return zero tables where there is no dataset view and record the source element and reason. Cite only supplied original IDs.',
                payload, contract, packet, validate)
        except RequestTooLarge as exc:
            if adjacent_only or set(context) == set(context_ids(packet, registry)):
                raise
            return work(packet, adjacent_only=True, size_stop=exc)
        registry.update(local)
        return {'target_ids': packet, 'value': value, 'trace': trace,
                'provisional_name_diagnostics': provisional_name_diagnostics(value['tables'], local)}
    for packet in packetize(ids, registry, target=12000):
        packets.extend(split_work(packet, work))
        atomic(out / 'paper-inventory-packets.json', packets)
    for packet in packets:
        table_ids = []
        for index, table in enumerate(packet['value']['tables']):
            cid = 'view-' + identity([packet['target_ids'], index])[:20]
            candidates[cid] = {'candidate_id': cid, **table}
            table_ids.append(cid)
            for fi, field in enumerate(table['fields']):
                fid = 'candidate-' + identity([cid, fi])[:20]
                fields[fid] = {'candidate_id': fid, 'table_candidate_id': cid, **field}
        for index, element in enumerate(packet['value']['elements']):
            elements.append({'element_id': 'element-' + identity([packet['target_ids'], index])[:20],
                             **element, 'table_candidate_ids': [table_ids[i] for i in element['table_indices']]})
        traces.append(packet['trace'])
    atomic(out / 'paper-current-candidates.json', {'tables': candidates, 'fields': fields, 'elements': elements})
    return candidates, fields, elements, traces


def reconcile(candidates, operations, registry, *, table, parent=None):
    """Incremental native assignments retain all candidates and source views."""
    groups, assignments, traces = {}, [], []
    keys = list(candidates)
    for offset in range(0, len(keys), 6):
        packet = keys[offset:offset+6]
        def work(part, compact_citations=False, size_stop=None):
            existing = copy.deepcopy(groups)
            source_ids = list(dict.fromkeys(candidates[key]['ref']['source_id'] for key in part))
            if not table:
                possible_names = {candidates[key]['name'] for key in part}
                source_ids.extend(sid for group in existing.values() if group['name'] in possible_names
                                  for sid in group['source_ids'] if sid not in source_ids)
            sources, local = membership_sources(source_ids, registry, compact_citations)
            group_ids = list(existing) + part + ['']
            props = {'candidate_id': {'enum': part}, 'action': {'enum': ['keep', 'merge', 'exclude']},
                     'merge_group_id': {'enum': group_ids, 'description': 'Only a merge selects this target. A keep creates its own keyed candidate group automatically; exclude retains no group. Use empty string for keep or exclude.'},
                     'new_name': STRING, 'ref': REF, 'reason': STRING}
            if table: props['new_row_entity'] = STRING
            props.pop('candidate_id')
            if not table:
                props = {'accounting_role': {'enum': FIELD_ROLES,
                    'description': 'Assess the actual source role first. Dictionary column headings describe the variable-definition layout, not underlying data records.'}, **props}
            contract = with_refs(obj({'assignments': obj({cid: obj(copy.deepcopy(props)) for cid in part})}), local)
            def records(value):
                return project_group_assignments(value, existing, part, table=table)
            def validate(value):
                errors = [] if set(value['assignments']) == set(part) else ['Every original candidate needs its keyed assignment.']
                created = {item['group_id']: item for item in records(value) if item['action'] == 'keep'}
                anchors = {**existing, **{gid: {'name': item['name'], 'source_ids': [item['ref']['source_id']]}
                                        for gid, item in created.items()}}
                errors += retained_name_issues(records(value), local, table=table, anchors=anchors, registry=registry)
                if not table:
                    errors += field_identity_collisions(records(value), existing)
                    errors += field_role_issues(value['assignments'])
                for item in records(value):
                    gid = item['group_id']
                    if item['action'] == 'exclude':
                        if gid: errors.append('Excluded candidates have no retained group.')
                    elif not gid or gid not in existing and gid not in created:
                        errors.append('Retained or merged candidates need a current kept group anchor.')
                    elif gid in existing and item['name'] != existing[gid]['name']:
                        errors.append('Keep the established original group identity and source-derived name.')
                    elif gid in created and item['name'] != created[gid]['name']:
                        errors.append('Members of a group must use its one reconciled name.')
                    if item['action'] == 'keep' and gid != item['candidate_id']:
                        errors.append('A new group is anchored by its own current candidate handle.')
                return errors
            payload = {'kind': 'paper', 'source_units': sources,
                       'current_candidates': [{key: value for key, value in candidates[cid].items() if not table or key != 'fields'} for cid in part],
                       'existing_groups': existing, 'parent_table': parent, 'object_kind': 'table' if table else 'field',
                       'name_projection': 'A native keep creates the group at its own keyed candidate ID, using new_name/new_row_entity. merge_group_id applies only to merge actions, selecting an established or current kept group and inheriting that anchor name/entity. An exclude retains no group. Ignored keep/exclude merge targets do not change identity.'}
            if compact_citations:
                payload['bounded_citation_options'] = {
                    'reason': str(size_stop),
                    'instruction': 'Complete original context units and every exact candidate/anchor reference remain supplied, with unchanged text and identities. Additional generated sentence-level citation menus are omitted after a bounded request stop. This does not omit or clip original source content.'}
            if table:
                payload['dataset_table_scope_interpretation'] = (
                    'Identify the underlying dataset records, their row entity, files, granularity and identifiers. '
                    'A paper table caption, procedural workflow, feature-definition layout, variant-statistics summary '
                    'or evaluation-score table does not by itself establish a separate dataset table. '
                    'Retain a summary as its own dataset table only with original evidence that it is distinct released or stored dataset data with its own records or file, rather than a paper analysis summary. '
                    'Rows describing dataset variants or population distributions do not establish individual-record data columns. '
                    'Reconcile original documentation views of the same underlying table; distinct captions alone '
                    'are not separate dataset identities. Account for every candidate through native keep, merge or exclude.')
            else:
                payload['source_role_decision'] = (
                    'Assess accounting_role from original context before choosing keep/merge/exclude. '
                    'Actual variable entries in dictionary rows are dataset_field or feature_family; '
                    'headings describing those variable-definition rows are dictionary_heading. '
                    'Summary properties, analysis results and method examples require their original source role. '
                    'These native role labels are hypotheses requiring independent review, not supporting evidence. '
                    'Do not infer role from a candidate label or a parent-table hypothesis.')
                spelling_choices = {candidates[cid]['name']: printed_name_choices(candidates[cid]['name'], sources)
                                    for cid in part}
                spelling_choices = {name: choices for name, choices in spelling_choices.items() if choices}
                if spelling_choices:
                    payload['original_printed_name_choices'] = {
                        'choices': spelling_choices,
                        'instruction': 'These are literal spellings found in the unchanged supplied source, for provisional labels with capitalization drift. A native keep must choose an actual source spelling and its own source ID. This lookup does not establish a field role or select keep/merge/exclude; independently verify those decisions. Do not title-case a lower-case printed name.'}
                payload['candidate_role_interpretation'] = (
                    'Candidates are provisional. In a field-definition dictionary, variable rows identify '
                    'dataset fields; explanatory column headings describe the documentation layout. '
                    'Retaining a printed heading requires original evidence that it is also an actual '
                    'field of this dataset table. Otherwise natively exclude that explanatory candidate. '
                    'Population frequencies, class prevalence, separability and variant-summary properties describe a dataset or an experiment; they are not record-level columns merely because the paper summarizes them in a table. '
                    'Retain such a candidate only when the original source positively identifies it as a stored field of the underlying data records. Hypothetical examples do not establish fields of the actual dataset. '
                    'Keep actual raw variables and derived features using their supported source names.')
            def audit_context(value):
                selected = list(dict.fromkeys(sid for item in records(value) if item['group_id'] in existing
                                              for sid in existing[item['group_id']]['source_ids']))
                if not table:
                    selected.extend(sid for item in records(value) for group in existing.values()
                                    if group['name'] == item['name'] for sid in group['source_ids'] if sid not in selected)
                audit_units, audit_local = membership_sources(list(dict.fromkeys(source_ids + selected)), registry, compact_citations)
                registry.update(audit_local)
                return {'source_units': audit_units,
                        'exact_projected_assignments': records(value),
                        'selected_existing_group_sources': selected,
                        'existing_group_source_retrieval': 'Every original view of each selected existing group is included for independent membership review.'}
            try:
                value, trace = operations.checked(('table' if table else 'field') + '-membership-' + identity([parent, part, existing])[:16],
                'Reconcile every current candidate with the established source-derived groups. '
                'For tables, compare original row entity, files, granularity, identifiers and source-view relationships. '
                'Do not merge different row entities or split meteorological variables of one row entity into separate tables. '
                'Identical text or names alone do not establish table identity. For fields, preserve every distinct raw variable or derived feature; '
                'merge only documented repetitions/synonyms of the same field in this table. Use the paper own names and order. '
                'When the paper explicitly gives a variable identifier/code alongside a display label, use that original identifier for the field name and retain the label as descriptive context. Do not expand a printed family pattern into invented columns. '
                'Exclude only an out-of-scope or misidentified explanatory item supported by its original context, never because attributes are unknown. '
                'A keep creates its own keyed candidate group automatically. Only a merge selects merge_group_id, using an established or current kept anchor. Use empty merge_group_id for keep or exclude.',
                    payload, contract, part, validate, audit_context=audit_context)
            except RequestTooLarge as exc:
                if compact_citations:
                    raise
                return work(part, compact_citations=True, size_stop=exc)
            trace['group_name_projection'] = {'representation': GROUP_ACTION_PROJECTION,
                'candidate_order': part, 'table': table, 'projected_assignments': records(value)}
            if not table:
                trace['native_field_roles'] = {'representation': FIELD_ROLE_REPRESENTATION,
                    'roles': {cid: item['accounting_role'] for cid, item in value['assignments'].items()}}
            registry.update(local)
            for item in records(value):
                if item['action'] == 'keep':
                    groups[item['group_id']] = {'handle': item['group_id'], 'name': item['name'],
                                               'source_ids': [], 'candidate_ids': [], 'reason': item['reason'],
                                               **({'row_entity': item['row_entity']} if table else {})}
            for item in records(value):
                if item['action'] != 'exclude':
                    group = groups[item['group_id']]
                    group['candidate_ids'].append(item['candidate_id'])
                    group['source_ids'] = list(dict.fromkeys(group['source_ids'] + [candidates[item['candidate_id']]['ref']['source_id'], item['ref']['source_id']]))
            assignments.extend(records(value)); traces.append(trace)
            return value
        split_work(packet, work)
    return list(groups.values()), assignments, traces


def membership_aliases(tables):
    aliases = {'t'+str(index): table['handle'] for index, table in enumerate(tables)}
    fields = [field for table in tables for field in table['fields']]
    aliases.update({'f'+str(index): field['handle'] for index, field in enumerate(fields)})
    if len(set(aliases.values())) != len(aliases): raise ValueError('Membership handles must be unique.')
    return aliases


def expand_memberships(value, aliases):
    result = []
    for sid, source in value['sources'].items():
        expanded = copy.deepcopy(source)
        for key in ['table_handles','field_handles','all_fields_in_tables']:
            expanded[key] = [aliases[handle] for handle in source[key]]
        if 'disposition' not in source:
            expanded['disposition'] = 'used' if any(source[key] for key in ['table_handles','field_handles','all_fields_in_tables']) else 'excluded'
        result.append({'source_id': sid, **expanded})
    return result


def applicability_contract():
    return obj({
        'kind': {'enum': ['table', 'figure_caption', 'supplement', 'data_availability', 'methods', 'other_data', 'non_data']},
        'table_handles': arr({'$ref': '#/$defs/table_handle'}),
        'field_handles': arr({'$ref': '#/$defs/field_handle'}),
        'all_fields_in_tables': arr({'$ref': '#/$defs/table_handle'}), 'reason': STRING})


def attribution(case, operations, registry, tables, *, dataset=False):
    """Account for each complete original unit and route applicable context."""
    aliases = membership_aliases(tables)
    reverse = {handle: alias for alias, handle in aliases.items()}
    table_ids = [reverse[table['handle']] for table in tables]
    field_ids = [reverse[field['handle']] for table in tables for field in table['fields']]
    inventory = [{'handle': reverse[t['handle']], 'name': t['name'], 'row_entity': t.get('row_entity'),
                  'fields': [{'handle': reverse[f['handle']], 'name': f['name']} for f in t['fields']]} for t in tables]
    ids = [sid for sid in base_ids(registry) if not dataset or registry[sid]['kind'] == 'supporting']
    results, traces = [], []
    def work(packet):
        sources, local = local_sources(context_ids(packet, registry), registry)
        source_contract = applicability_contract()
        contract = obj({'sources': obj({sid: copy.deepcopy(source_contract) for sid in packet})})
        contract['$defs'] = {'table_handle': {'enum': table_ids}, 'field_handle': {'enum': field_ids}}
        def validate(value):
            errors = [] if set(value['sources']) == set(packet) else ['Every original target needs its keyed applicability decision.']
            for source in value['sources'].values():
                targets = source['table_handles'] + source['field_handles'] + source['all_fields_in_tables']
                if len(source['table_handles']) != len(set(source['table_handles'])) or len(source['field_handles']) != len(set(source['field_handles'])):
                    errors.append('Repeated source membership adds no evidence.')
            return errors
        payload = {'kind': case['kind'], 'job_id': case['case_id'], 'source_units': sources,
                   'target_source_ids': packet, 'current_retained_inventory': inventory,
                   'source_membership_semantics':'All three target arrays empty explicitly excludes the source for this inventory; any nonempty array explicitly uses it. The host only projects this decision to the ordinary disposition. No redundant used/excluded value is generated.'}
        value, trace = operations.checked('source-applicability-' + identity(packet)[:16],
            'Account for each target original source unit and assign its applicable table and field context to the complete retained inventory. '
            'Read definitions, methods, captions, footnotes and data availability beyond a field own naming passage. '
            'Use all_fields_in_tables only when a stated property/method actually applies to all those fields. '
            'Do not assume a README describes every physical file or column. Cite applicable current source text only; ignore linked resources. '
            'If a source reveals an omitted dataset table or field, the inventory is incomplete: the independent reviewer must reject it. '
            'For a nonapplicable target return all three arrays empty with a specific exclusion reason. For a used target return its applicable original handles. Return every target exactly once.',
            payload, contract, packet, validate)
        registry.update(local)
        expanded = expand_memberships(value, aliases)
        trace['membership_projection'] = {'representation':'source-membership-alias-v2','aliases':aliases,
                                          'projected_sources_sha256':identity(expanded)}
        return expanded, trace
    for packet in packetize(ids, registry, target=3000):
        for result, trace in split_work(packet, work):
            results.extend(result); traces.append(trace)
        atomic(Path(case['directory']) / 'source-applicability.json', {'sources': results, 'traces': traces})
    return results, traces


def applicable_ids(handle, table_handle, assignments):
    return [item['source_id'] for item in assignments if handle in item['field_handles']
            or handle in item['table_handles'] or table_handle in item['all_fields_in_tables']]
