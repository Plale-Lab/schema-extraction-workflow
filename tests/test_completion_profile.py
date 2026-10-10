from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import copy
import json

from schema_extract.assembly import schema_projection
from schema_extract.compiler import ROOT, strict_json
from schema_extract.semantic import claim_issues, review_issues, name_present
from schema_extract.compiler import classification_response
from schema_extract.extraction import extract_paper


class CompletionProfileTests(unittest.TestCase):
    def test_paper_optional_deferral_preserves_reviewed_work_and_table_contract(self):
        registry = {'s': {'source_id': 's', 'source_sha256': 'a'*64, 'physical_page': 1,
                         'start_char': 0, 'end_char': 15, 'text': 'age temperature', 'kind': 'paper'}}
        fields = {h: {'candidate_id': h, 'table_candidate_id': 't'} for h in ['old', 'new']}
        native_table = {'handle': 't', 'name': 'observations', 'source_ids': ['s'], 'row_entity': 'observation'}
        retained = [{'handle': 'old', 'name': 'age', 'source_ids': ['s']},
                    {'handle': 'new', 'name': 'temperature', 'source_ids': ['s']}]
        captured = []
        def grouping(_, __, ___, *, table, parent=None):
            if table:
                return [copy.deepcopy(native_table)], [{'candidate_id': 't', 'group_id': 't', 'action': 'keep'}], []
            return copy.deepcopy(retained), [], []
        def extract_batch(case, operations, source_registry, jobs, progress):
            captured.extend(copy.deepcopy(jobs))
            progress({job['handle']: {'name': job['name']} for job in jobs},
                     {job['handle']: {'final': {}} for job in jobs})
        for enabled in [False, True]:
            with self.subTest(enabled=enabled), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                (root/'source-texts.json').write_text(json.dumps({'a'*64: {'1': registry['s']['text']}}), encoding='utf-8')
                (root/'paper-extraction-traces.json').write_text(json.dumps({'old': {'final': {
                    'native_projection': {'representation': 'offline-fixture'}, 'independent_review_passed': True}}}), encoding='utf-8')
                captured.clear()
                with patch('schema_extract.extraction.integrate_navigation', return_value=(copy.deepcopy(registry), [])), \
                     patch('schema_extract.extraction.settings', return_value={'compact_attribute_batches_enabled': True}), \
                     patch('schema_extract.inventory.discover_paper', return_value=({'t': {}}, fields, [], [])), \
                     patch('schema_extract.inventory.reconcile', side_effect=grouping), \
                     patch('schema_extract.inventory.attribution', return_value=([], [])), \
                     patch('schema_extract.inventory.applicable_ids', return_value=[]), \
                     patch('schema_extract.compact.extract_fields', side_effect=extract_batch):
                    extract_paper({'directory': str(root), 'case_id': 'offline-paper', 'defer_unresolved_optional': enabled}, object())
                flags = {job['handle']: job['defer_optional'] for job in captured}
                self.assertEqual(flags, {'t': False, 'old': False, 'new': enabled})

    def test_unknown_datatype_and_available_description_do_not_block_names_first_completion(self):
        attrs = strict_json((ROOT/'contracts'/'evidence-contract.json').read_text())['field_attributes']
        claims = [{'attribute': attr, 'value': None, 'status': 'unknown', 'refs': [], 'reason': 'Unresolved in bounded extraction.'} for attr in attrs]
        claims[0] = {'attribute': 'name', 'value': 'original_name', 'status': 'observed', 'refs': [{'source_id': 'header'}], 'reason': 'Original header.'}
        decision = {'name': 'original_name', 'keep': True, 'claims': claims}
        registry = {'header': {'kind': 'header', 'text': 'original_name'}}
        self.assertFalse(claim_issues(decision, registry, expected_name='original_name', allow_optional_unknown=True))
        review = {'claims': {str(i): {'supported': c['attribute'] == 'name', 'problem': 'Optional value unresolved.'} for i, c in enumerate(claims)},
                  'description_available': True, 'availability_reason': 'The source has optional description detail.'}
        self.assertFalse(review_issues(decision, review, allow_optional_unknown=True))
        self.assertTrue(review_issues(decision, review))

    def test_names_first_profile_still_rejects_unsupported_known_constraint(self):
        decision = {'name': 'x', 'keep': True, 'claims': [{'attribute': 'name', 'value': 'x', 'status': 'observed', 'refs': [{'source_id': 'row'}]},
                    {'attribute': 'unique', 'value': True, 'status': 'observed', 'refs': [{'source_id': 'row'}]}]}
        errors = claim_issues(decision, {'row': {'kind': 'data', 'text': '1'}}, expected_name='x', allow_optional_unknown=True)
        self.assertTrue(any('population constraint' in message for message in errors))

    def test_unknown_name_is_never_an_optional_completion(self):
        decision = {'name': 'x', 'keep': True, 'claims': [{'attribute': 'name', 'value': None, 'status': 'unknown', 'refs': []}]}
        errors = claim_issues(decision, {}, expected_name='x', allow_optional_unknown=True)
        self.assertTrue(any('source-bound name' in message for message in errors))

    def test_positive_audit_cannot_make_a_caption_support_an_absent_field_name(self):
        decision = {'name': 'snow_depth_water_equivalent', 'keep': True,
                    'claims': [{'attribute': 'name', 'value': 'snow_depth_water_equivalent', 'status': 'declared', 'refs': [{'source_id': 'caption'}]}]}
        errors = claim_issues(decision, {'caption': {'kind': 'paper', 'text': 'Table 3. First set of catchment attributes derived from HydroATLAS.'}},
                              expected_name=decision['name'], allow_optional_unknown=True)
        self.assertTrue(any('own cited source' in message for message in errors))
        self.assertTrue(name_present(decision['name'], 'Snow water equivalent (snow_depth_water_equivalent) Daily min/max and mean mm'))
        self.assertFalse(name_present('age', 'storage'))

    def test_classification_order_projection_cannot_invent_missing_or_repeated_decisions(self):
        a = {'source_id': 'a', 'label': 'none', 'quote_ids': [], 'reason': 'Original a'}
        b = {'source_id': 'b', 'label': 'relevant', 'quote_ids': ['b'], 'reason': 'Original b'}
        self.assertEqual(classification_response({'units': [b, a]}, ['a', 'b']), {'units': [a, b]})
        with self.assertRaises(ValueError): classification_response({'units': [a, a]}, ['a', 'b'])
        with self.assertRaises(ValueError): classification_response({'units': [a]}, ['a', 'b'])

    def test_paper_pure_schema_keeps_every_table_without_evidence_leakage(self):
        first = {'table': {'name': 'stations', 'structure': {'fields': []}, 'encoding': {'fileType': 'unknown'}}}
        second = {'table': {'name': 'observations', 'structure': {'fields': []}, 'encoding': {'fileType': 'unknown'}}}
        bundle = {'tables': [{'schema': first, 'evidence': {'table': 'first-proof'}},
                             {'schema': second, 'evidence': {'table': 'second-proof'}}], 'coverage': [{'source_id': 'original'}]}
        schema, evidence = schema_projection(bundle)
        self.assertEqual(schema, {'tables': [first, second]})
        self.assertNotIn('coverage', schema)
        self.assertEqual(evidence['coverage'], bundle['coverage'])
        self.assertEqual(len(evidence['tables']), 2)

    def test_dataset_projection_keeps_unknown_defaults_and_separate_evidence(self):
        body = {'table': {'name': 'source.csv', 'structure': {'fields': [{'name': 'x', 'required': False, 'unique': False, 'value': {}}]}, 'encoding': {'fileType': 'unknown'}}}
        schema, evidence = schema_projection({'schema': body, 'evidence': {'x': {'value.datatype': {'status': 'unknown'}}}})
        self.assertEqual(schema, body)
        self.assertEqual(evidence['x']['value.datatype']['status'], 'unknown')


if __name__ == '__main__': unittest.main()
