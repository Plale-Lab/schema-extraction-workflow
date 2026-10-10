import copy
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from schema_extract.acceptance import fidelity_audit
from schema_extract.native import atomic
from schema_extract.operations import ItemFailure, bind_inventory_link_repair, inventory_link_semantics_issues


class FidelityScopeTests(unittest.TestCase):
    def run_empty_attribute_audit(self, accounting_correct):
        registry = {'s': {'source_id': 's', 'source_sha256': '0' * 64,
                         'physical_page': 1, 'start_char': 0, 'end_char': 3,
                         'text': 'age', 'kind': 'header'}}
        tables = [{'handle': 't', 'name': 'sample', 'fields': [{'handle': 'f', 'name': 'age'}]}]
        decisions = {h: {'name': name, 'claims': []} for h, name in [('t', 'sample'), ('f', 'age')]}
        captured = []

        class OfflineOperations:
            def __init__(self, *args):
                pass

            def call(self, operation, instruction, payload, contract):
                captured.append(copy.deepcopy(payload))
                self_case.assertEqual(contract['properties']['objects']['properties'], {})
                return {'objects': {}, 'source_accounting_correct': accounting_correct,
                        'problem': '' if accounting_correct else 'An actual source view is missing.',
                        'optional_information_gaps': []}, {'folder': 'offline-fixture-only'}

        self_case = self
        with TemporaryDirectory() as folder:
            atomic(Path(folder) / 'source-applicability.json', {'sources': [
                {'source_id': 's', 'table_handles': ['t'], 'field_handles': [], 'all_fields_in_tables': []}]})
            with patch('schema_extract.acceptance.Operations', OfflineOperations):
                if accounting_correct:
                    self.assertTrue(fidelity_audit({'directory': folder, 'case_id': 'fixture', 'kind': 'dataset'},
                                                   None, tables, decisions, registry)['passed'])
                else:
                    with self.assertRaisesRegex(ItemFailure, 'actual source view is missing'):
                        fidelity_audit({'directory': folder, 'case_id': 'fixture', 'kind': 'dataset'},
                                       None, tables, decisions, registry)
        self.assertEqual(captured[0]['current_retained_inventory'], tables)
        self.assertEqual(captured[0]['target_source_ids'], ['s'])
        self.assertEqual(captured[0]['objects_audited_in_this_subdivision'], [])
        self.assertTrue(captured[0]['inventory_audit_scope']['mandatory_even_when_attribute_batch_empty'])

    def test_empty_attribute_batch_preserves_full_inventory_scope(self):
        self.run_empty_attribute_audit(True)

    def test_empty_attribute_batch_still_rejects_missing_source_view(self):
        self.run_empty_attribute_audit(False)

    def test_link_repair_preserves_native_candidates_and_requires_native_positions(self):
        contract = {'properties': {'tables': {}, 'elements': {}, 'source_decisions': {
            'properties': {'s': {'properties': {'data_describing': {}, 'element_indices': {}, 'reason': {}}}}}}}
        previous = {'tables': [{'name': 'native table'}], 'elements': [{'kind': 'methods', 'table_indices': [0]}],
                    'source_decisions': {'s': {'data_describing': True, 'element_indices': [6], 'reason': 'Original native decision.'}}}
        original = copy.deepcopy(previous)
        bound, applied = bind_inventory_link_repair(contract, previous, ['Source s has stale or foreign element_indices'])
        self.assertTrue(applied)
        self.assertEqual(previous, original)
        self.assertEqual(bound['properties']['tables']['const'], previous['tables'])
        self.assertEqual(bound['properties']['elements']['const'], previous['elements'])
        links = bound['properties']['source_decisions']['properties']['s']['properties']['element_indices']
        self.assertEqual(links['items']['enum'], [0])
        self.assertEqual(links['minItems'], 1)
        self.assertNotIn('const', links)

    def test_link_repair_does_not_resolve_missing_semantic_candidates(self):
        contract = {'properties': {}}
        bound, applied = bind_inventory_link_repair(contract, {'elements': [{}]},
                                                   ['Every data-describing target needs an explicit element candidate.'])
        self.assertFalse(applied)
        self.assertEqual(bound, contract)

    def test_zero_table_link_repair_preserves_original_element_semantics(self):
        contract = {'properties': {'tables': {}, 'elements': {'items': {'properties': {'table_indices': {}}}},
            'source_decisions': {'properties': {'s': {'properties': {'data_describing': {}, 'element_indices': {}}}}}}}
        previous = {'tables': [], 'elements': [{'kind': 'data_availability', 'table_indices': [0], 'ref': {'source_id': 's'}}],
                    'source_decisions': {'s': {'data_describing': True, 'element_indices': [0]}}}
        before = copy.deepcopy(previous)
        bound, applied = bind_inventory_link_repair(contract, previous,
            ['Coverage element 0 has stale or foreign table_indices [0]'])
        self.assertTrue(applied)
        self.assertEqual(bound['properties']['elements']['items']['properties']['table_indices'], {'const': []})
        self.assertEqual(previous, before)
        result = copy.deepcopy(previous)
        result['elements'][0]['table_indices'] = []  # Simulated native response, never host output repair.
        self.assertFalse(inventory_link_semantics_issues(result, previous))
        result['elements'][0]['kind'] = 'methods'
        self.assertTrue(inventory_link_semantics_issues(result, previous))


if __name__ == '__main__':
    unittest.main()
