import unittest

from schema_extract.assembly import select_claims, replay_anchors, bind


class AssemblyTests(unittest.TestCase):
    def setUp(self):
        self.registry = {'one': {'source_id': 'one', 'source_sha256': 'a' * 64,
                                  'physical_page': 1, 'start_char': 0, 'end_char': 10,
                                  'text': 'alpha\nbeta', 'source_path': 'original.txt'},
                         'two': {'source_id': 'two', 'source_sha256': 'b' * 64,
                                  'physical_page': 1, 'start_char': 0, 'end_char': 8,
                                  'text': 'contrary', 'source_path': 'other.txt'}}

    def claim(self, value, status, sid):
        return {'attribute': 'value.datatype', 'value': value, 'status': status,
                'refs': [{'source_id': sid}], 'reason': 'Native original-source assertion'}

    def test_equal_priority_disagreement_keeps_both_claims_and_no_selected_value(self):
        evidence_registry = {}
        values, evidence, conflicts = select_claims([self.claim('integer', 'declared', 'one'),
                                                     self.claim('decimal', 'declared', 'two')],
                                                    ['value.datatype'], self.registry, evidence_registry)
        self.assertNotIn('value.datatype', values)
        self.assertEqual(evidence['value.datatype']['status'], 'unknown')
        self.assertIsNone(conflicts[0]['selected'])
        self.assertEqual(len(conflicts[0]['claims']), 2)
        self.assertEqual(len(evidence_registry), 2)

    def test_precedence_keeps_lower_priority_disagreement_in_evidence(self):
        values, evidence, conflicts = select_claims([self.claim('integer', 'declared', 'one'),
                                                     self.claim('decimal', 'inferred', 'two')],
                                                    ['value.datatype'], self.registry, {})
        self.assertEqual(values['value.datatype'], 'integer')
        self.assertEqual(len(conflicts[0]['claims']), 2)
        self.assertEqual(conflicts[0]['selected'], 'integer')

    def test_altered_quote_whitespace_cannot_pass_original_source_replay(self):
        anchors = {}
        bind({'source_id': 'one'}, self.registry, anchors)
        replay_anchors(anchors, {'a' * 64: {'1': 'alpha\nbeta'}})
        with self.assertRaisesRegex(ValueError, 'quote replay differs'):
            replay_anchors(anchors, {'a' * 64: {'1': 'alpha beta'}})

    def test_missing_attribute_decision_cannot_silently_become_unknown(self):
        with self.assertRaisesRegex(ValueError, 'Missing complete attribute'):
            select_claims([], ['description'], self.registry, {})


if __name__ == '__main__': unittest.main()
