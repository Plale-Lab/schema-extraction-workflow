import unittest
import tempfile
import copy

from schema_extract.inventory import discovery_context, provisional_name_diagnostics, retained_name_issues, discover_paper, project_group_assignments, discovery_link_issues, field_identity_collisions, membership_sources, reconcile
from schema_extract.compiler import RequestTooLarge


class DiscoveryContextTests(unittest.TestCase):
    def registry(self):
        return {str(i): {'source_id': str(i), 'source_sha256': 'original',
                         'physical_page': 1, 'start_char': i * 10,
                         'end_char': i * 10 + 10, 'text': 'x' * 10}
                for i in range(5)}

    def test_dictionary_target_gets_whole_page_context(self):
        registry = self.registry()
        ids, scope = discovery_context(['2'], registry)
        self.assertEqual(set(ids), set(registry))
        self.assertEqual(scope['complete_pages'][0]['source_ids'], list(registry))

    def test_context_does_not_expand_another_document_or_page(self):
        registry = self.registry()
        registry['3']['physical_page'] = 2
        registry['4']['source_sha256'] = 'different'
        _, scope = discovery_context(['1'], registry)
        self.assertEqual(scope['complete_pages'][0]['source_ids'], ['0', '1', '2'])

    def test_oversized_page_retains_targets_and_records_context_bound(self):
        registry = self.registry()
        before = {sid: unit['text'] for sid, unit in registry.items()}
        ids, scope = discovery_context(['2'], registry, page_limit=20)
        self.assertIn('2', ids)
        self.assertFalse(scope['complete_pages'])
        self.assertEqual(scope['pages_using_adjacent_context'][0]['source_ids'], list(registry))
        self.assertEqual(before, {sid: unit['text'] for sid, unit in registry.items()})

    def test_unsupported_discovery_candidate_can_only_be_excluded(self):
        local = {'caption': {'text': 'Streamflow records are available.'}}
        candidate = {'name': 'Median length of flow time series', 'ref': {'source_id': 'caption'}}
        tables = [{'fields': [candidate]}]
        diagnostics = provisional_name_diagnostics(tables, local)
        self.assertEqual(diagnostics[0]['name'], candidate['name'])
        self.assertEqual(tables[0]['fields'], [candidate])
        assignment = {**candidate, 'action': 'exclude'}
        self.assertEqual(retained_name_issues([assignment], local, table=False), [])
        for action in ['keep', 'merge']:
            self.assertTrue(retained_name_issues([{**assignment, 'action': action}], local, table=False))

    def test_positive_membership_cannot_use_another_fields_source(self):
        local = {'wind': {'text': 'wind_speed'}, 'temperature': {'text': 'temperature_2m'}}
        assignment = {'action': 'keep', 'name': 'temperature_2m', 'ref': {'source_id': 'wind'}}
        self.assertTrue(retained_name_issues([assignment], local, table=False))
        assignment['ref']['source_id'] = 'temperature'
        self.assertEqual(retained_name_issues([assignment], local, table=False), [])

    def test_native_source_correction_does_not_rewrite_discovered_candidate(self):
        local = {'dictionary': {'text': 'Temperature (temperature_2m)'}}
        original = {'name': 'Air temperature measurement', 'ref': {'source_id': 'dictionary'}}
        corrected = {'action': 'keep', 'name': 'temperature_2m', 'ref': {'source_id': 'dictionary'}}
        self.assertTrue(provisional_name_diagnostics([{'fields': [original]}], local))
        self.assertFalse(retained_name_issues([corrected], local, table=False))
        self.assertEqual(original['name'], 'Air temperature measurement')

    def test_auxiliary_context_size_fallback_never_loses_original_targets(self):
        registry = self.registry()
        for index, unit in enumerate(registry.values()):
            unit.update(text='x' * 1500, start_char=index * 1500,
                        end_char=(index + 1) * 1500, kind='paper')
        original_targets = list(registry)
        calls = []

        class BoundStop:
            def checked(self, operation, instruction, payload, contract, targets, validate):
                calls.append(payload)
                if payload['dictionary_page_context']['complete_pages']:
                    raise RequestTooLarge('Unsent wire size bound')
                value = {'tables': [], 'elements': [], 'no_data_reason': 'Offline fixture.',
                         'source_decisions': {sid: {'data_describing': False, 'element_indices': [],
                                                   'reason': 'Offline fixture.'} for sid in targets}}
                if validate(value):
                    raise AssertionError('Offline source-accounting fixture is invalid.')
                return value, {}

        with tempfile.TemporaryDirectory() as directory:
            discover_paper({'directory': directory, 'case_id': 'offline'}, BoundStop(), registry)
        successful = [payload for payload in calls if not payload['dictionary_page_context']['complete_pages']]
        self.assertEqual([sid for payload in successful for sid in payload['target_source_ids']], original_targets)
        for payload in successful:
            supplied = {unit['source_id']: unit['text'] for unit in payload['source_units']}
            for sid in payload['target_source_ids']:
                self.assertEqual(supplied[sid], registry[sid]['text'])
            self.assertIn('bounded_context_reason', payload['dictionary_page_context'])

    def test_alias_merge_keeps_established_canonical_name_support(self):
        local = {'alias': {'text': 'Air temperature refers to the Temperature measurement defined above.'}}
        registry = {'definition': {'text': 'Temperature field identifier is temperature_2m.'}}
        anchors = {'g0': {'name': 'temperature_2m', 'source_ids': ['definition']}}
        assignment = {'action': 'merge', 'group_id': 'g0', 'name': 'temperature_2m',
                      'ref': {'source_id': 'alias'}}
        self.assertFalse(retained_name_issues([assignment], local, table=False, anchors=anchors, registry=registry))
        registry['definition']['text'] = 'A caption without any printed field identifier.'
        self.assertTrue(retained_name_issues([assignment], local, table=False, anchors=anchors, registry=registry))

    def test_merge_inherits_native_anchor_without_rewriting_raw_response(self):
        value = {'assignments': {'candidate': {'action': 'merge', 'group_id': 'anchor',
                 'new_name': 'Drifted echo', 'new_row_entity': 'Drifted entity',
                 'ref': {'source_id': 'alias'}, 'reason': 'Native membership choice.'}}}
        original = copy.deepcopy(value)
        existing = {'anchor': {'name': 'Time series', 'row_entity': 'Catchment-day'}}
        projected = project_group_assignments(value, existing, ['candidate'], table=True)
        self.assertEqual(projected[0]['name'], 'Time series')
        self.assertEqual(projected[0]['row_entity'], 'Catchment-day')
        self.assertEqual(projected[0]['ref'], {'source_id': 'alias'})
        self.assertEqual(value, original)

    def test_same_batch_native_keep_supplies_canonical_field_name(self):
        def item(action, name):
            return {'action': action, 'group_id': 'keep', 'new_name': name,
                    'ref': {'source_id': 'original'}, 'reason': 'Native source decision.'}
        value = {'assignments': {'alias': item('merge', 'Display label'),
                                 'keep': item('keep', 'temperature_2m')}}
        projected = project_group_assignments(value, {}, ['alias', 'keep'], table=False)
        self.assertEqual([p['name'] for p in projected], ['temperature_2m', 'temperature_2m'])
        self.assertEqual([p['candidate_id'] for p in projected], ['alias', 'keep'])

    def test_v2_native_action_anchors_keep_and_native_merge_selects_group(self):
        def item(action, target, name):
            return {'action': action, 'merge_group_id': target, 'new_name': name,
                    'ref': {'source_id': 'original'}, 'reason': 'Offline native representation fixture.'}
        value = {'assignments': {'alias': item('merge', 'keep', 'Display label'),
                                 'keep': item('keep', '', 'temperature_2m'),
                                 'excluded': item('exclude', '', 'Heading')}}
        original = copy.deepcopy(value)
        projected = project_group_assignments(value, {}, list(value['assignments']), table=False)
        self.assertEqual([p['group_id'] for p in projected], ['keep', 'keep', ''])
        self.assertEqual([p['name'] for p in projected[:2]], ['temperature_2m', 'temperature_2m'])
        self.assertEqual(value, original)

    def test_v2_unsupported_retained_field_still_fails_source_guard(self):
        value = {'assignments': {'c': {'action': 'keep', 'merge_group_id': '',
                 'new_name': 'Invented variable', 'ref': {'source_id': 'original'}, 'reason': ''}}}
        projected = project_group_assignments(value, {}, ['c'], table=False)
        self.assertTrue(retained_name_issues(projected, {'original': {'text': 'temperature_2m'}}, table=False))

    def test_same_printed_field_names_require_native_reconciliation(self):
        existing = {'g': {'name': 'moisture_index'}}
        records = [{'action': 'keep', 'group_id': 'new', 'name': 'moisture_index'}]
        before = copy.deepcopy(records)
        self.assertTrue(field_identity_collisions(records, existing))
        self.assertEqual(records, before)
        records[0].update(action='merge', group_id='g')
        self.assertFalse(field_identity_collisions(records, existing))
        records[0].update(action='keep', group_id='new', name='mi_annual')
        self.assertFalse(field_identity_collisions(records, existing))

    def test_membership_size_fallback_preserves_complete_text_and_exact_candidate_ref(self):
        registry = self.registry()
        registry['quote'] = {**registry['2'], 'source_id': 'quote', 'text': 'x',
                             'start_char': 21, 'end_char': 22, 'base_source_id': '2', 'citation_only': True}
        before = copy.deepcopy(registry)
        candidates = {'c': {'candidate_id': 'c', 'name': 'Records', 'row_entity': 'record',
                            'ref': {'source_id': 'quote'}, 'fields': []}}
        calls = []
        class Fixture:
            def checked(self, operation, instruction, payload, contract, targets, validate, audit_context=None):
                calls.append(payload)
                if not payload.get('bounded_citation_options'):
                    raise RequestTooLarge('Offline translated-wire bound.')
                result = {'assignments': {'c': {'action': 'keep', 'merge_group_id': '', 'new_name': 'Records',
                    'new_row_entity': 'record', 'ref': {'source_id': 'quote'}, 'reason': 'Offline fixture only.'}}}
                if validate(result): raise AssertionError(validate(result))
                audit = audit_context(result)
                self_case.assertIn('quote', {u['source_id'] for u in audit['source_units']})
                return result, {'offline_fixture_only': True}
        self_case = self
        groups, assignments, traces = reconcile(candidates, Fixture(), registry, table=True)
        self.assertEqual(len(calls), 2)
        supplied = {u['source_id']: u for u in calls[1]['source_units']}
        for sid in ['1', '2', '3', 'quote']:
            self.assertEqual(supplied[sid]['text'], before[sid]['text'])
            self.assertEqual(supplied[sid]['start_char'], before[sid]['start_char'])
            self.assertNotIn('exact_citation_spans', supplied[sid])
        self.assertEqual(groups[0]['source_ids'], ['quote'])
        self.assertEqual(traces[0]['group_name_projection']['candidate_order'], ['c'])

    def test_membership_transport_failure_does_not_activate_size_fallback(self):
        from schema_extract.native import NativeError
        calls = []
        class Fixture:
            def checked(self, *args, **kwargs):
                calls.append(args)
                raise NativeError('Offline lost-observation fixture.')
        with self.assertRaises(NativeError):
            reconcile({'c': {'candidate_id': 'c', 'name': 'Records', 'row_entity': 'record',
                       'ref': {'source_id': '2'}, 'fields': []}}, Fixture(), self.registry(), table=True)
        self.assertEqual(len(calls), 1)

    def test_unsupported_kept_name_still_fails_after_projection(self):
        value = {'assignments': {'c': {'action': 'keep', 'group_id': 'c',
                 'new_name': 'Invented variable', 'ref': {'source_id': 'original'}, 'reason': ''}}}
        projected = project_group_assignments(value, {}, ['c'], table=False)
        self.assertTrue(retained_name_issues(projected, {'original': {'text': 'temperature_2m'}}, table=False))

    def test_discovery_repair_exposes_current_index_range_without_reindexing_native_links(self):
        value = {'tables': [{}], 'elements': [{'table_indices': [1, 2]}],
                 'source_decisions': {'s': {'data_describing': True, 'element_indices': [0]}}}
        original = copy.deepcopy(value)
        errors = discovery_link_issues(value, ['s'])
        self.assertIn('valid zero-based positions [0]', errors[0])
        self.assertEqual(value, original)
        value['elements'][0]['table_indices'] = [0]
        self.assertFalse(discovery_link_issues(value, ['s']))
        value['source_decisions']['s']['element_indices'] = [1]
        self.assertTrue(discovery_link_issues(value, ['s']))
        value['source_decisions']['s']['element_indices'] = []
        self.assertTrue(discovery_link_issues(value, ['s']))
        value['source_decisions']['s'] = {'data_describing': False, 'element_indices': []}
        value['tables'] = []; value['elements'] = []
        self.assertFalse(discovery_link_issues(value, ['s']))


if __name__ == '__main__':
    unittest.main()
