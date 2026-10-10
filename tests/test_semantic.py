from pathlib import Path
import unittest
from schema_extract.compiler import strict_json
from schema_extract.semantic import claim_issues, review_view, review_issues
from schema_extract.guards.description_scope_guard import issues


class SemanticTests(unittest.TestCase):
    def test_synthetic_header_cannot_establish_provenance_or_aggregation(self):
        text = "reading_mean,reading_max\n"
        header = {"source_id": "synthetic-header", "kind": "header", "text": text}
        claim = {"attribute": "description", "status": "observed",
                 "value": "Values are derived from Fictional dataset and aggregated using statistical operations.",
                 "refs": [{"source_id": "synthetic-header"}]}
        result = issues({"description": {"claim": claim}},
                        {"synthetic-header": header},
                        {"description": {"original_source_name": ""}})
        self.assertTrue(result)
        self.assertTrue(any("provenance" in message for message in result["description"]))
        self.assertTrue(any("aggregation" in message for message in result["description"]))

    def test_review_reads_exact_assertion_without_generation_rationale(self):
        decision = {'claims': [{'attribute': 'description', 'value': 'Original assertion', 'status': 'inferred',
                                'refs': [{'source_id': 'original'}], 'reason': 'Persuasive generation rationale'}]}
        view = review_view(decision)
        self.assertEqual(view['0']['value'], 'Original assertion')
        self.assertNotIn('reason', view['0'])

    def test_positive_native_review_does_not_override_known_source_failure(self):
        decision = {'keep': True, 'name': 'x', 'claims': [{'attribute': 'unique', 'value': True,
                                                        'status': 'observed', 'refs': [{'source_id': 'row'}]}]}
        failures = claim_issues(decision, {'row': {'kind': 'data', 'text': '1'}})
        self.assertTrue(any('population constraint' in failure for failure in failures))

    def test_unknown_description_must_recover_available_supported_content(self):
        decision = {'claims': [{'attribute': 'description', 'status': 'unknown', 'value': None, 'refs': []}]}
        review = {'claims': {'0': {'supported': True, 'problem': ''}}, 'description_available': True,
                  'availability_reason': 'Exact original header states the field naming and layout.'}
        self.assertTrue(review_issues(decision, review))


if __name__ == '__main__': unittest.main()
