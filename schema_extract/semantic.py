"""Source-bound claim gates for extraction and same-contract repairs.

These gates reject known unsupported assertions. Passing them alone never
establishes semantic acceptance; an independent native review is also required.
"""
import copy
from pathlib import Path
import re

from .compiler import identity, strict_json, ROOT
from .guards.description_scope_guard import issues as description_issues


def name_present(name, text):
    """Match a source name; quote replay still checks the untouched raw span."""
    from .compiler import canonical
    if canonical(name) in text or re.search(r'(?<!\w)' + re.escape(name) + r'(?!\w)', text): return True
    # Printed wrapped names can contain whitespace between their fragments.
    # This lookup does not alter any evidence quote or byte representation.
    normalized_name = re.sub(r'\s+', ' ', name).strip()
    return bool(re.search(r'(?<!\w)' + re.escape(normalized_name) + r'(?!\w)', re.sub(r'\s+', ' ', text)))


def printed_name_choices(name, sources):
    """Offer exact source spellings for case drift; never rename a candidate."""
    words = name.split()
    if not words:
        return []
    pattern = re.compile(r'(?<!\w)' + r'\s+'.join(re.escape(word) for word in words) + r'(?!\w)', re.IGNORECASE)
    choices, seen = [], set()
    for unit in sources:
        text = unit['text']
        if name_present(name, text):
            continue
        for match in pattern.finditer(text):
            printed = re.sub(r'\s+', ' ', match.group()).strip()
            key = (unit['source_id'], printed)
            if key not in seen:
                seen.add(key)
                choices.append({'name': printed, 'source_id': unit['source_id']})
    return choices


def source_spans(units, registry):
    """Offer exact shorter spans while retaining all complete source units."""
    result = []
    for key in units:
        unit = registry[key]
        text = unit['text']
        boundaries = {0, len(text)}
        for match in re.finditer(r'(?<=[.!?;])\s+(?=[A-Z0-9])|\n', text):
            boundaries.add(match.start())
        points = sorted(boundaries)
        spans = []
        for a, b in zip(points, points[1:]):
            while a < b and text[a].isspace(): a += 1
            while b > a and text[b-1].isspace(): b -= 1
            if a == b: continue
            sid = 'quote-' + identity([key, a, b])[:24]
            span = {**unit, 'source_id': sid, 'start_char': unit['start_char'] + a,
                    'end_char': unit['start_char'] + b, 'text': text[a:b],
                    'base_source_id': key, 'citation_only': True}
            registry[sid] = span
            spans.append({'source_id': sid, 'text': text[a:b]})
        result.append({**unit, 'exact_citation_spans': spans})
    return result


def bind_contract(table, source_ids):
    schema = strict_json((ROOT / 'contracts' / ('table-stage.json' if table else 'field-stage.json')).read_text(encoding='utf-8'))
    def visit(value):
        if isinstance(value, dict):
            if set(value.get('properties', {})) == {'source_id'}:
                value['properties']['source_id'] = {'enum': list(source_ids)}
            for child in value.values(): visit(child)
        elif isinstance(value, list):
            for child in value: visit(child)
    visit(schema)
    return schema


def claim_issues(decision, registry, *, table=False, expected_name=None, dataset_fact=None, allow_optional_unknown=False):
    contract = strict_json((ROOT / 'contracts' / 'evidence-contract.json').read_text(encoding='utf-8'))
    expected = contract['table_attributes' if table else 'field_attributes']
    failures = []
    claims = decision.get('claims', [])
    attributes = [claim.get('attribute') for claim in claims]
    if set(attributes) != set(expected):
        failures.append('Every current table/field attribute requires a claim or explicit unknown decision. Preserve distinct conflicting source claims.')
    signatures = [identity({key: claim.get(key) for key in ['attribute', 'value', 'status', 'refs']}) for claim in claims]
    if len(signatures) != len(set(signatures)):
        failures.append('Repeated identical claim candidates do not add source information.')
    if not decision.get('keep'):
        failures.append('An established physical table/field cannot be dropped for unknown attributes.')
    if expected_name is not None and decision.get('name') != expected_name:
        failures.append('Retain the exact original field name: ' + expected_name)
    if not any(claim.get('attribute') == 'name' and claim.get('status') != 'unknown'
               and claim.get('value') == decision.get('name') for claim in claims):
        failures.append('The retained object needs a source-bound name claim matching its native decision name.')
    if dataset_fact:
        witnesses = dataset_fact.get('negative_constraint_witnesses', {})
        for attribute, pointers in [('required', [witnesses['empty_cell']] if witnesses.get('empty_cell') else []),
                                    ('unique', witnesses.get('repeated_values', []))]:
            if not pointers: continue
            if allow_optional_unknown and all(claim.get('status') == 'unknown' for claim in claims if claim.get('attribute') == attribute):
                continue
            needed = {pointer['source_id'] for pointer in pointers}
            demonstrated = False
            for claim in claims:
                if claim.get('attribute') != attribute or claim.get('status') != 'observed' or claim.get('value') is not False: continue
                cited = {ref['source_id'] for ref in claim.get('refs', [])}
                cited |= {registry[sid].get('base_source_id') for sid in list(cited) if sid in registry}
                if needed <= cited: demonstrated = True
            if not demonstrated:
                failures.append(attribute + ': preserve observed false with all supplied exact negative sample-witness IDs, alongside any declared conflict claim.')
    for claim in claims:
        attribute, status, value = claim.get('attribute'), claim.get('status'), claim.get('value')
        refs = claim.get('refs', [])
        if status == 'unknown':
            if refs: failures.append(attribute + ': unknown must not invent evidence references.')
            continue
        if not refs or any(ref.get('source_id') not in registry for ref in refs):
            failures.append(str(attribute) + ': nonunknown claims need exact supplied source IDs.')
            continue
        own = [registry[ref['source_id']] for ref in refs]
        if attribute in {'conformsTo','value.unit.system','value.enum.valueSet'}:
            from .compiler import canonical
            identifiers = value if isinstance(value,list) else [value]
            if any(isinstance(identifier,str) and not any(identifier in unit['text'] or canonical(identifier) in unit['text'] for unit in own) for identifier in identifiers):
                failures.append(attribute+': a URI identifier must be supplied in its own original source; a conventional namespace from outside knowledge is not an extracted value. Return unknown if unresolved.')
        if attribute=='role' and value=='target' and not any(re.search(r'\b(?:target|label|prediction|response)\b',unit['text'].replace('_',' ').replace('-',' '),re.I) for unit in own):
            failures.append('role: a measured metric does not establish a prediction target; cite original target/label evidence or return unknown.')
        if attribute == 'conformsTo':
            scholarly = any(re.search(r'\b(?:paper|publication|article|cite|citation)\b',unit['text'],re.I) for unit in own)
            qualification = any(re.search(r'\b(?:standard|specification|conform\w*|schema|metadata|vocabulary)\b',unit['text'],re.I) for unit in own)
            if scholarly and not qualification:
                failures.append('conformsTo: a cited paper describing the dataset does not declare conformance to a metadata or data-encoding standard. Cite an actual applicable standard declaration or explicitly leave this optional attribute unknown.')
        if attribute == 'name' and not table and not any(name_present(value, unit['text']) for unit in own):
            matches = sorted([key for key, unit in registry.items() if name_present(value, unit['text'])], key=lambda key: len(registry[key]['text']))
            failures.append('name: its own cited source must carry the actual field name, not just a table caption or unrelated neighboring field. Exact name-bearing supplied source IDs: ' + str(matches[:3]))
        if expected_name is not None and attribute == 'name' and value != expected_name:
            failures.append('Name claim changed an original physical field name.')
        if attribute in {'required', 'unique'} and value is True:
            supported = any(unit.get('kind') in {'supporting', 'paper'} for unit in own)
            if attribute == 'required' and dataset_fact:
                metadata = dataset_fact.get('embedded_metadata') or {}
                supported = supported or (status == 'declared' and metadata.get('nullable') is False and any(u.get('column_index') == dataset_fact['column_index'] for u in own))
            if status != 'declared' or not supported:
                failures.append(attribute + ': sample nonemptiness/distinctness does not establish a population constraint.')
        if attribute in {'value.enum.values', 'value.enum.valueSet'} and not (status == 'declared' and any(u.get('kind') in {'supporting', 'paper'} for u in own)):
            failures.append(attribute + ': sample values are not legal-value enums or code lists.')
        if attribute == 'value.datatype' and dataset_fact:
            metadata = dataset_fact.get('embedded_metadata')
            if metadata and metadata.get('template_datatype') is None:
                failures.append('value.datatype: unsupported native compound/vector datatype must remain unknown.')
            if status == 'declared' and not any(u.get('kind') == 'supporting' for u in own):
                if not metadata or metadata.get('template_datatype') != value or not any(u.get('column_index') == dataset_fact['column_index'] for u in own):
                    failures.append('value.datatype: declared datatype requires its own native embedded type or applicable supporting definition.')
        if attribute == 'name' and dataset_fact and dataset_fact.get('name_origin') == 'positional_handle_without_original_name' and status != 'inferred':
            failures.append('A positional handle for a headerless file is an inferred identification label, not an original declared/observed column name.')
        if attribute == 'encoding.header' and any(unit.get('original_format') == '.parquet' for unit in own) and not any(unit.get('kind') == 'supporting' for unit in own):
            failures.append('encoding.header: a generated CSV readout header does not establish a physical Parquet header; record unknown unless original applicable documentation establishes it.')
        if attribute == 'description':
            local = {}
            for ref in refs:
                key = ref['source_id']
                unit = copy.deepcopy(registry[key])
                if unit.get('kind') in {'data', 'native_field_readout', 'native_value'}:
                    unit['kind'] = 'row'
                elif unit.get('kind') == 'native_table_readout':
                    unit.update(kind='embedded_metadata', declared_attribute='name')
                local[key] = unit
            failures.extend('description: '+problem for problems in description_issues({'description': {'claim': claim}}, local,
                                                                       {'description': {'original_source_name': expected_name or ''}}).values() for problem in problems)
    return failures


def review_view(decision):
    """Audit exact current assertions; never inject extraction rationales."""
    return {str(index): {'attribute': claim['attribute'], 'value': claim['value'],
                         'status': claim['status'], 'refs': copy.deepcopy(claim['refs'])}
            for index, claim in enumerate(decision['claims'])}


def review_contract(decision):
    ids = list(review_view(decision))
    verdict = {'type': 'object', 'additionalProperties': False, 'required': ['supported', 'problem'],
               'properties': {'supported': {'type': 'boolean'}, 'problem': {'type': 'string'}}}
    return {'type': 'object', 'additionalProperties': False, 'required': ['claims', 'description_available', 'availability_reason'],
            'properties': {'claims': {'type': 'object', 'additionalProperties': False, 'required': ids,
                                      'properties': {key: copy.deepcopy(verdict) for key in ids}},
                           'description_available': {'type': 'boolean'}, 'availability_reason': {'type': 'string'}}}


def review_issues(decision, review, *, allow_optional_unknown=False):
    if set(review.get('claims', {})) != set(review_view(decision)):
        return ['Independent semantic review did not account for every exact current claim.']
    problems = [decision['claims'][int(index)]['attribute'] + ': ' + item['problem']
                for index, item in review['claims'].items() if not item['supported']
                and not (allow_optional_unknown and decision['claims'][int(index)]['status'] == 'unknown'
                         and decision['claims'][int(index)]['attribute'] != 'name')]
    description = next((claim for claim in decision['claims'] if claim['attribute'] == 'description'), None)
    if description and description['status'] == 'unknown' and review.get('description_available') and not allow_optional_unknown:
        problems.append('Supported partial description is available: ' + review['availability_reason'])
    return problems
