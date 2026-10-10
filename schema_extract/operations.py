"""Bounded extraction and independent review operations within model layer 2."""
from pathlib import Path
import copy
import jsonschema

from .compiler import canonical, identity, strict_json, settings, ROOT, RequestTooLarge
from .native import atomic, atomic_bytes, NativeError, IncompleteGeneration, utc, sha
import time
from .semantic import bind_contract, claim_issues, review_view, review_contract, review_issues, name_present, printed_name_choices


class ItemFailure(ValueError):
    pass


def assertion_view(value):
    """Separate assertions and original source text from generated rationale."""
    if isinstance(value, dict):
        return {key: copy.deepcopy(child) if key == 'source_units' else assertion_view(child)
                for key, child in value.items()
                if key not in {'reason', 'no_data_reason'}}
    if isinstance(value, list):
        return [assertion_view(child) for child in value]
    return value


def bind_object_names(contract, objects):
    """Constrain native responses to identities already derived from original sources."""
    bound = copy.deepcopy(contract)
    slots = bound.get('properties', {}).get('items', {}).get('properties', {})
    for handle, original in objects.items():
        if handle not in slots: continue
        slot = slots[handle]
        if '$ref' in slot:
            slot = copy.deepcopy(bound['$defs'][slot['$ref'].rsplit('/', 1)[1]])
        properties = slot.get('properties', {})
        if 'attribute_decisions' not in properties: continue
        properties['name'] = {'const': original['name']}
        records = properties['attribute_decisions']['properties']['name']
        claim = records['items']
        if '$ref' in claim:
            claim = copy.deepcopy(bound['$defs'][claim['$ref'].rsplit('/', 1)[1]])
        claim['properties']['value'] = {'const': original['name']}
        records['items'] = claim
        slots[handle] = slot
    return bound


def optional_deferrals(payload, errors):
    return {handle: attributes for handle, original in payload.get('original_objects', {}).items()
            if (attributes := [attribute for attribute in original.get('required_attribute_decisions', [])
                               if attribute != 'name' and any(handle+': '+attribute+':' in error for error in errors)])}


def bind_inventory_link_repair(contract, previous, errors):
    """Preserve native candidates while asking the model to relink stale positions."""
    link_errors = ('has stale or foreign element_indices', 'has stale or foreign table_indices')
    if (not isinstance(previous, dict) or not previous.get('elements') or not errors
            or any(not any(marker in error for marker in link_errors) for error in errors)):
        return contract, False
    bound = copy.deepcopy(contract)
    # These const arrays preserve the previous model's semantic assertions.
    # The model still selects every corrected coverage link; the host selects none.
    bound['properties']['tables'] = {'const': copy.deepcopy(previous['tables'])}
    if any('has stale or foreign table_indices' in error for error in errors):
        elements = bound['properties']['elements']
        elements['minItems'] = elements['maxItems'] = len(previous['elements'])
        table_positions = list(range(len(previous['tables'])))
        elements['items']['properties']['table_indices'] = (
            {'type': 'array', 'items': {'type': 'integer', 'enum': table_positions}}
            if table_positions else {'const': []})
    else:
        bound['properties']['elements'] = {'const': copy.deepcopy(previous['elements'])}
    positions = list(range(len(previous['elements'])))
    for sid, source in previous['source_decisions'].items():
        slot = bound['properties']['source_decisions']['properties'][sid]['properties']
        slot['data_describing'] = {'const': source['data_describing']}
        slot['element_indices'] = {'type': 'array', 'items': {'type': 'integer', 'enum': positions},
                                   'minItems': 1 if source['data_describing'] else 0}
    return bound, True


def inventory_link_semantics_issues(result, previous):
    """A link-only native repair cannot silently change its semantic inventory."""
    def metadata(elements):
        return [{key: value for key, value in element.items() if key != 'table_indices'} for element in elements]
    return ([] if result['tables'] == previous['tables'] and metadata(result['elements']) == metadata(previous['elements'])
            else ['Link-only repair changed the original native table/element semantic content. Preserve its exact original metadata and order.'])


class Operations:
    def __init__(self, native, output, kind):
        if kind not in {'paper', 'dataset'}:
            raise ValueError('Paper/Dataset lane required')
        self.native, self.output, self.kind = native, Path(output), kind
        self.output.mkdir(parents=True, exist_ok=True)

    def record_path(self, operation, suffix=''):
        return self.output / (operation[:24] + '-' + identity(operation)[:16] + suffix + '.json')

    def request(self, instruction, payload, contract):
        task = (ROOT / 'prompts' / 'extraction.txt').read_text(encoding='utf-8')
        task += '\n' + (ROOT / 'templates' / ('Paper_prompt.md' if self.kind == 'paper' else 'Dataset_prompt.md')).read_text(encoding='utf-8')
        if settings().get('completion_profile') == 'names_first':
            task += ('\nUser-authorized completion profile: complete the current table/field names and membership first. '
                     'After extraction, evaluation aligns name, then description, datatype and unit; no numerical prompt weights are added. '
                     'Retain reliable supported optional attributes. An optional attribute not resolved by this bounded workflow may remain unknown; '
                     'state the actual unresolved reason rather than pretending the source is silent. Never omit a named field because datatype or description is unresolved. '
                     'Unknown optional descriptions/datatypes do not block case completion. Known assertions still require applicable original-source support.')
        task += '\nCurrent operation within extraction: ' + instruction
        return {'messages': [{'role': 'system', 'content': task},
                             {'role': 'user', 'content': canonical(payload)}],
                'max_tokens': min(2048, settings()['output_tokens']['extraction']) if payload.get('names_only_optional_deferral') else settings()['output_tokens']['extraction'],
                'response_contract': contract}

    def call(self, operation, instruction, payload, contract, validate=lambda result: []):
        base = self.request(instruction, payload, contract)
        request_key = identity(base)
        checkpoint = self.record_path(operation, '-' + request_key[:16])
        failures, previous, invalid_cached = [], None, False
        support_errors = list(payload.get('independent_review_repair', {}).get('errors', []))
        if checkpoint.exists():
            saved = strict_json(checkpoint.read_text(encoding='utf-8'))
            if saved['prepared_request_sha256'] != request_key:
                raise ItemFailure('Current-operation checkpoint request differs')
            folder = Path(saved['native_receipt']['folder'])
            received = strict_json((folder / 'receipt.json').read_text(encoding='utf-8'))
            if received.get('status') != 'received' or sha(folder / 'response.raw') != received.get('response_sha256'):
                raise ItemFailure('Current-operation raw response does not match its original received receipt')
            envelope = strict_json((folder / 'response.raw').read_bytes().decode('utf-8'))
            wire = strict_json((folder / 'request.json').read_text(encoding='utf-8'))
            if envelope['model'] != wire['model'] or envelope['choices'][0]['finish_reason'] != 'stop':
                raise ItemFailure('Current-operation native model/termination replay differs')
            actual = strict_json(envelope['choices'][0]['message']['content'])
            if actual != saved['result']:
                raise ItemFailure('Current-operation checkpoint differs from raw native response')
            jsonschema.validate(actual, contract)
            failures = validate(actual)
            if settings().get('unknown_optional_attributes_allowed'):
                for handle, attributes in optional_deferrals(payload, support_errors).items():
                    decisions = actual.get('items', {}).get(handle, {}).get('attribute_decisions', {})
                    failures.extend(handle+': '+attribute+': cached known value has not applied the current bounded unknown repair.'
                                    for attribute in attributes if attribute in decisions and decisions[attribute] is not None)
            if failures:
                preserved = self.output/'quarantined'/(request_key[:16]+'-'+str(time.time_ns())+'.json')
                atomic_bytes(preserved, checkpoint.read_bytes())
                previous, invalid_cached = actual, True
            else:
                return actual, saved['native_receipt']
        seen = set()
        link_anchor, link_errors = None, None
        for attempt in range(1 if invalid_cached else 0, settings()['max_repairs_per_item'] + 1):
            current_payload = copy.deepcopy(payload)
            if operation.startswith('source-applicability-') and '-decision-' in operation:
                current_payload['compact_membership_instruction'] = (
                    'Do not repeat field_handles for a table already named in all_fields_in_tables; '
                    'that membership is already explicit. Keep every other applicable individual field membership. '
                    'Use all_fields_in_tables only when the original source actually applies to the entire table.')
            if attempt:
                current_payload['same_contract_repair'] = {
                    'errors': failures, 'current_round_previous_response': previous,
                    'instruction': 'Repair this work item through the same complete named contract. Retain all supported partial content and original object/field identities. Use only supplied original source IDs. Return compact JSON.'}
                if operation.startswith('inventory-'):
                    current_payload['same_contract_repair']['instruction'] = (
                        'Repair provisional discovery through the same complete named contract. '
                        'Candidate names are hypotheses, not established original field identities. '
                        'Remove a candidate field when its name has no name-bearing supplied source; '
                        'do not preserve an unsupported compound label or infer an unprinted column. '
                        'Retain supported variable names and account for every original target. '
                        'A narrative, method or caption may describe data without printing field names; '
                        'its table view can have an empty field list. Use only supplied source IDs. Return compact JSON.')
                elif operation.startswith(('table-membership-', 'field-membership-')):
                    current_payload['same_contract_repair']['instruction'] = (
                        'Repair candidate disposition through the same complete named contract. '
                        'Candidate labels remain hypotheses until source-supported native retention. '
                        'Exclude unsupported or explanatory candidates, or cite an actual source-supported name. '
                        'Preserve established native group identities when merging. '
                        'Dictionary column headings describe documentation layout and are not automatically dataset fields. '
                        'Retain every actual source-supported variable and account for every candidate. '
                        'Use only supplied source IDs. Return compact JSON.')
                anchors = {}
                spellings = {}
                def names(value):
                    if isinstance(value, dict):
                        name = value.get('new_name', value.get('name'))
                        if isinstance(name, str):
                            matches = [unit['source_id'] for unit in payload.get('source_units', [])
                                       if name_present(name, unit['text'])]
                            for unit in payload.get('source_units', []):
                                matches += [span['source_id'] for span in unit.get('exact_citation_spans', [])
                                            if name_present(name, span['text'])]
                            if matches: anchors[name] = list(dict.fromkeys(matches))
                            choices = printed_name_choices(name, payload.get('source_units', []))
                            if choices: spellings[name] = choices
                        for child in value.values(): names(child)
                    elif isinstance(value, list):
                        for child in value: names(child)
                names(previous)
                if anchors:
                    current_payload['same_contract_repair']['name_reference_choices'] = anchors
                    current_payload['same_contract_repair']['instruction'] += ' Each field must cite a name-bearing source from its name_reference_choices, including adjacent context when the actual field text is there.'
                if spellings:
                    current_payload['same_contract_repair']['original_printed_name_choices'] = spellings
                    current_payload['same_contract_repair']['instruction'] += ' Copy the original printed capitalization from original_printed_name_choices when correcting a provisional name; do not repeat a title-cased label absent from its own source. These lexical matches do not prove dataset-field membership.'
            repair_contract = copy.deepcopy(contract)
            links_only = False
            if attempt and operation.startswith('inventory-'):
                repair_contract, links_only = bind_inventory_link_repair(repair_contract, link_anchor or previous, link_errors or failures)
                if links_only:
                    if link_anchor is None:
                        link_anchor, link_errors = copy.deepcopy(previous), list(failures)
                    current_payload['native_coverage_link_repair'] = {
                        'instruction': 'Keep the exact original native tables, element metadata/order and data_describing decisions. Correct only elements.table_indices and source_decisions.element_indices using positions in these unchanged arrays. An empty tables array has no valid table links. The host has supplied no replacement links. Independent original-source review follows.',
                        'original_native_inventory': link_anchor,
                        'valid_current_table_positions': list(range(len(link_anchor['tables']))),
                        'valid_current_element_positions': list(range(len(link_anchor['elements'])))}
            deferred = {}
            support_errors = list(dict.fromkeys(support_errors + failures))
            if support_errors and settings().get('unknown_optional_attributes_allowed') and 'items' in contract.get('properties',{}):
                for handle, attributes in optional_deferrals(payload, support_errors).items():
                    slot=repair_contract['properties']['items']['properties'][handle]
                    if '$ref' in slot:slot=copy.deepcopy(repair_contract['$defs'][slot['$ref'].rsplit('/',1)[1]])
                    if 'attribute_decisions' not in slot.get('properties',{}):continue
                    for attribute in attributes:slot['properties']['attribute_decisions']['properties'][attribute]={'type':'null'}
                    repair_contract['properties']['items']['properties'][handle]=slot
                    deferred[handle]=attributes
                if deferred:
                    current_payload['bounded_unknown_repairs']={'attributes':deferred,'actual_guard_errors':support_errors,
                        'instruction':'Explicitly return null for these unresolved optional keys under the user-authorized completion profile. Keep all original names and other verified values. Explain bounded source-support failure in unknown_reason; do not claim that the source contains no information.'}
            repair_contract = bind_object_names(repair_contract, payload.get('original_objects', {}))
            prepared = self.request(instruction, current_payload, repair_contract)
            result = None
            try:
                result, receipt = self.native.complete(prepared)
                jsonschema.validate(result, repair_contract)
                failures = validate(result)
                if links_only:
                    failures += inventory_link_semantics_issues(result, link_anchor)
                if failures:
                    raise ItemFailure(canonical(failures))
                atomic(checkpoint, {'layer': 'extraction', 'operation': operation,
                                    'prepared_request_sha256': request_key,
                                    'dispatched_prepared_request_sha256': identity(prepared),
                                    'dispatched_prepared_request': prepared,
                                    'result': result, 'native_receipt': receipt,
                                    'completed_utc': utc(), 'automatic_success': False})
                return result, receipt
            except IncompleteGeneration:
                failures = ['Received terminal output-limit response; return compact complete JSON without blank-line loops.']
                if attempt == settings()['max_repairs_per_item']:
                    raise RequestTooLarge('Known terminal output limit exhausted bounded compact repairs; subdivide the current source packet')
            except RequestTooLarge:
                raise
            except NativeError:
                raise
            except ItemFailure as exc:
                if not failures: failures = [str(exc)]
            except (ValueError, KeyError, TypeError, jsonschema.ValidationError) as exc:
                failures = [str(exc)]
            previous = result
            signature = identity({'response': result, 'errors': failures})
            atomic(self.record_path(operation, '-' + request_key[:16] + '-failure-' + str(attempt)),
                   {'operation': operation, 'attempt': attempt, 'errors': failures,
                    'response': result, 'signature': signature, 'automatic_success': False})
            if signature in seen:
                if result is None and failures == ['Received terminal output-limit response; return compact complete JSON without blank-line loops.']:
                    raise RequestTooLarge('Repeated received terminal output limits; subdivide this bounded work item.')
                raise ItemFailure('Repeated identical failure without progress: ' + canonical(failures))
            seen.add(signature)
        raise ItemFailure('Bounded item repairs exhausted: ' + canonical(failures))

    def checked(self, operation, instruction, payload, contract, targets, validate=lambda result: [], audit_context=None):
        """Source-first independent accounting review of inventory and grouping."""
        verdict = {'type': 'object', 'additionalProperties': False,
                   'required': ['decision_correct', 'problem'],
                   'properties': {'decision_correct': {'type': 'boolean'}, 'problem': {'type': 'string'}}}
        audit_contract = {'type': 'object', 'additionalProperties': False,
                          'required': ['verdicts', 'complete', 'problem'],
                          'properties': {'verdicts': {'type': 'object', 'additionalProperties': False,
                                                     'required': list(targets),
                                                     'properties': {key: copy.deepcopy(verdict) for key in targets}},
                                         'complete': {'type': 'boolean'}, 'problem': {'type': 'string'}}}
        current, errors, seen = None, [], set()
        for attempt in range(settings()['max_repairs_per_item'] + 1):
            generation = copy.deepcopy(payload)
            if attempt:
                generation['same_contract_source_review_repair'] = {'errors': errors, 'previous_candidate': current}
            current, receipt = self.call(operation + '-decision-' + str(attempt), instruction, generation, contract, validate)
            audit = {key: value for key, value in payload.items() if key not in {'current_round_partial_decisions', 'previous_candidate'}}
            audit['exact_current_candidate'] = assertion_view(current)
            audit['targets_to_account_for'] = list(targets)
            if audit_context:
                audit.update(audit_context(current))
            audit = assertion_view(audit)
            audit['rationale_excluded_from_review'] = True
            provisional_scope = (
                ' This is provisional inventory discovery, before native candidate disposition. '
                'Judge original target/source-view accounting and discovery completeness. '
                'Candidate names are not known schema assertions: unsupported provisional candidates '
                'must be accounted for and excluded or source-corrected in subsequent native reconciliation. '
                'Do not treat their presence as approval to retain them. Actual printed variable rows '
                'still require discovery candidates; explanatory headings cannot replace those rows. '
                'Retained names and their exact own-source references are mandatory downstream gates.'
                if operation.startswith('inventory-') else '')
            review, review_receipt = self.call(operation + '-review-' + str(attempt),
                'Independently check the exact inventory, applicability or membership candidate against the supplied original sources. '
                'Check every target, source view, field candidate and data-describing element. Verify completeness as well as source support. '
                'A result table, explanatory heading or a metric is not automatically a separate dataset field. '
                'Identical names or quotes alone do not establish table identity. Detect omitted tables, raw variables, derived features and original views. '
                'When the source defines variables in dictionary rows, review the underlying dataset fields. '
                'Documentation headings describe that dictionary layout; a printed heading alone does not establish a dataset field. '
                'Require positive original evidence of the field role for retained headings. Candidate labels and parent-table hypotheses are not that evidence. '
                'Native accounting_role labels are hypotheses too: independently verify each role from original context before approving its disposition. '
                'Variant and population summaries describe datasets, not individual-record columns; prevalence, group frequencies and separability are not stored fields without positive original record-level evidence. '
                'Hypothetical examples do not establish fields of the actual dataset. '
                'For table membership, verify the underlying dataset-record entity and source-view identity. '
                'Procedural steps and paper summaries of variant statistics do not by themselves establish separate dataset tables. '
                'Require original evidence for a retained independent summary dataset; check repeated descriptions for duplicate logical tables. '
                'Set decision_correct=true when the exact current accounting/membership decision is correct. '
                'A non-data source correctly accounted for as data_describing=false or excluded has decision_correct=true and an empty problem. '
                'This verdict judges the decision; it is not another source relevance classification. '
                'Return a verdict for every target. The candidate is a hypothesis; its decisions and identifiers do not establish support.' + provisional_scope, audit, audit_contract)
            errors = validate(current)
            errors += [key + ': ' + verdict['problem'] for key, verdict in review['verdicts'].items() if not verdict['decision_correct']]
            if not review['complete']: errors.append('Incomplete source accounting: ' + review['problem'])
            atomic(self.record_path(operation, '-checked-' + str(attempt)),
                   {'candidate': current, 'review': review, 'errors': errors, 'decision_receipt': receipt,
                    'review_receipt': review_receipt, 'automatic_success': False})
            if not errors:
                return current, {'decision_receipt': receipt, 'review_receipt': review_receipt,
                                 'candidate_sha256': identity(current), 'independent_review_passed': True,
                                 'rationale_excluded_from_review': True,
                                 'automatic_success': False}
            signature = identity({'candidate': current, 'errors': errors})
            if signature in seen: raise ItemFailure('Repeated source-accounting failure: ' + canonical(errors))
            seen.add(signature)
        raise ItemFailure('Bounded source-accounting repairs exhausted: ' + canonical(errors))

    def extract_reviewed(self, handle, payload, local_registry, *, table=False, expected_name=None, dataset_fact=None):
        try:
            return self._extract_reviewed(handle, payload, local_registry, table=table,
                                          expected_name=expected_name, dataset_fact=dataset_fact)
        except ItemFailure as exc:
            if not settings().get('unknown_optional_attributes_allowed') or payload.get('unknown_completion_rescue'):
                raise
            rescue = copy.deepcopy(payload)
            rescue['unknown_completion_rescue'] = {
                'actual_failure': str(exc),
                'instruction': 'Complete the original named object and all required attribute decisions. Preserve the exact source-bound name and every reliable verified value. Put unresolved optional attributes in unknown with an honest bounded-workflow reason and no fabricated source references. This is authorized task completion, not proof that the source has no information.'}
            value, trace = self._extract_reviewed(handle + '-unknown-completion', rescue, local_registry,
                                                 table=table, expected_name=expected_name, dataset_fact=dataset_fact)
            trace['unknown_completion_rescue'] = {'actual_failure': str(exc), 'native_response_required': True}
            return value, trace

    def _extract_reviewed(self, handle, payload, local_registry, *, table=False, expected_name=None, dataset_fact=None):
        contract = bind_contract(table, local_registry)
        root_key = 'table' if table else 'field'
        partial_claims = payload.get('reconciliation_claim_candidates') or {}
        allow_unknown = settings().get('unknown_optional_attributes_allowed', False)
        if partial_claims:
            contract['properties']['claim_accounting'] = {'type': 'array', 'minItems': len(partial_claims), 'maxItems': len(partial_claims),
                'items': {'type': 'object', 'additionalProperties': False,
                          'required': ['candidate_id', 'disposition', 'output_claim_indices', 'reason'],
                          'properties': {'candidate_id': {'enum': list(partial_claims)},
                                         'disposition': {'enum': ['retained', 'equivalent', 'combined', 'conflict', 'excluded']},
                                         'output_claim_indices': {'type': 'array', 'items': {'type': 'integer', 'minimum': 0}},
                                         'reason': {'type': 'string'}}}}
            contract['required'].append('claim_accounting')
        def validate(result):
            errors = claim_issues(result[root_key], local_registry, table=table, expected_name=expected_name,
                                  dataset_fact=dataset_fact, allow_optional_unknown=allow_unknown)
            if partial_claims:
                records = result['claim_accounting']
                if [r['candidate_id'] for r in records] != list(partial_claims):
                    errors.append('Account for every current-round source-supported partial claim exactly once in supplied order.')
                for record in records:
                    indices = record['output_claim_indices']
                    if (record['disposition'] == 'excluded') == bool(indices):
                        errors.append('Retained/combined/conflict claims need output links; excluded claims have no output links.')
                    for index in indices:
                        if index >= len(result[root_key]['claims']) or result[root_key]['claims'][index]['attribute'] != partial_claims[record['candidate_id']]['attribute']:
                            errors.append('Reconciled partial claim links must identify the same current attribute.')
            return errors
        attrs = strict_json((ROOT / 'contracts' / 'evidence-contract.json').read_text(encoding='utf-8'))['table_attributes' if table else 'field_attributes']
        source_payload = {**payload, 'response_root_key': root_key,
                          'required_attribute_decisions': attrs,
                          'template': strict_json((ROOT / 'templates' / 'template.json').read_text(encoding='utf-8'))}
        instruction = ('Return one complete ' + root_key + ' decision using the named response contract. '
                       'Account for every required attribute, including explicit unknown decisions. Use one claim ordinarily; retain distinct conflicting source claims separately for the same attribute. '
                       'Descriptions must retain supported partial facts; headers and statistic suffixes alone do not establish provenance, production aggregation or row time-step meaning. '
                       'A physical Dataset field must remain present even when all optional attributes are unknown.')
        if payload.get('unknown_completion_rescue'):
            instruction += ' Apply the supplied unknown_completion_rescue instruction. Do not repeat an unsupported optional assertion; keep the original name.'
        errors = []
        decision = None
        review = None
        last = None
        for repair in range(settings()['max_repairs_per_item'] + 1):
            extraction_payload = copy.deepcopy(source_payload)
            if repair:
                extraction_payload['independent_review_repair'] = {
                    'exact_current_decision': decision, 'errors': errors,
                    'instruction': 'Correct unsupported assertions in their values or cite applicable original positive evidence. Changing only status to inferred is insufficient. Retain source-supported naming, layout and partial descriptions.'}
            result, receipt = self.call(handle + '-claims-' + str(repair), instruction,
                                        extraction_payload, contract,
                                        validate=validate)
            decision = result[root_key]
            audit_payload = {key: value for key, value in source_payload.items()
                             if key in {'kind', 'job_id', 'handle', 'source_units', 'source_scope', 'original_field',
                                        'original_object', 'physical_column_inventory'}}
            audit_payload['exact_current_claims'] = review_view(decision)
            audit_schema = review_contract(decision)
            if partial_claims:
                audit_payload['reconciliation_claim_candidates'] = partial_claims
                audit_payload['exact_current_claim_accounting'] = result['claim_accounting']
                verdict = {'type': 'object', 'additionalProperties': False, 'required': ['supported', 'problem'],
                           'properties': {'supported': {'type': 'boolean'}, 'problem': {'type': 'string'}}}
                audit_schema['properties']['reconciliation'] = {'type': 'object', 'additionalProperties': False,
                    'required': list(partial_claims), 'properties': {key: copy.deepcopy(verdict) for key in partial_claims}}
                audit_schema['required'].append('reconciliation')
            audit_instruction = ('Independently audit every exact current assertion against its own cited original source and applicable complete source context. '
                                 'Inspect each fact in a description separately, including provenance, process, aggregation, time step and row meaning. '
                                 'Non-contradiction and an inferred status do not establish support. Name metadata and sample cells cannot establish production methods. '
                                 'For an unknown description, check whether a useful supported partial description is available. '
                                 'When current-round partial claims are supplied, check every accounting decision against the original sources: '
                                 'retain compatible detail and genuine conflicting claims. In the user-authorized names-first profile, explicit optional deferral to unknown is allowed; '
                                 'record the unresolved reason and preserve the partial claims in the accounting ledger. Unknown does not assert source absence. '
                                 'Return a verdict for every supplied claim; do not see or rely on the extraction rationale.')
            review, audit_receipt = self.call(handle + '-independent-review-' + str(repair), audit_instruction,
                                              audit_payload, audit_schema)
            errors = claim_issues(decision, local_registry, table=table, expected_name=expected_name,
                                  dataset_fact=dataset_fact, allow_optional_unknown=allow_unknown) + review_issues(decision, review, allow_optional_unknown=allow_unknown)
            unresolved = {claim['attribute'] for claim in decision['claims'] if claim['status'] == 'unknown'} - {
                claim['attribute'] for claim in decision['claims'] if claim['status'] != 'unknown'}
            errors += [key + ': ' + verdict['problem'] for key, verdict in review.get('reconciliation', {}).items()
                       if not verdict['supported'] and not (allow_unknown and partial_claims[key]['attribute'] != 'name'
                                                           and partial_claims[key]['attribute'] in unresolved)]
            atomic(self.record_path(handle, '-review-result-' + str(repair)),
                   {'decision': decision, 'review': review, 'errors': errors,
                    'extraction_receipt': receipt, 'review_receipt': audit_receipt,
                    'source_registry_sha256': identity(local_registry), 'automatic_success': False})
            if not errors:
                return decision, {'extraction_receipt': receipt, 'review_receipt': audit_receipt,
                                  'decision_sha256': identity(decision), 'independent_review_passed': True,
                                  'claim_accounting': result.get('claim_accounting', []),
                                  'unresolved_partial_claims': {key: claim for key, claim in partial_claims.items() if claim['attribute'] in unresolved},
                                  'source_guards_passed': True, 'automatic_success': False}
            signature = identity({'decision': decision, 'errors': errors})
            if signature == last:
                raise ItemFailure('Same semantic failure repeated without progress: ' + canonical(errors))
            last = signature
        raise ItemFailure('Bounded source/semantic repairs exhausted: ' + canonical(errors))
