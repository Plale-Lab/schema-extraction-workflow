import copy
import unittest

from schema_extract.semantic import name_present, printed_name_choices
from schema_extract.inventory import retained_name_issues


class PrintedNameChoicesTests(unittest.TestCase):
    def test_original_spelling_is_offered_without_relaxing_source_guard(self):
        sources = [{'source_id': 'original', 'text': 'Sensors measure fan speed and CPU clock.'}]
        original = copy.deepcopy(sources)
        self.assertFalse(name_present('Fan speed', sources[0]['text']))
        self.assertEqual(printed_name_choices('Fan speed', sources),
                         [{'name': 'fan speed', 'source_id': 'original'}])
        candidate = {'action': 'keep', 'name': 'Fan speed', 'ref': {'source_id': 'original'}}
        self.assertTrue(retained_name_issues([candidate], {'original': sources[0]}, table=False))
        corrected = {**candidate, 'name': 'fan speed'}
        self.assertFalse(retained_name_issues([corrected], {'original': sources[0]}, table=False))
        self.assertEqual(sources, original)

    def test_wrapped_spelling_preserves_case_and_does_not_invent_a_phrase(self):
        source = {'source_id': 'original', 'text': 'Measurements include memory\naccesses (bytes written and read).'}
        choices = printed_name_choices('Memory accesses (bytes written and read)', [source])
        self.assertEqual(choices, [{'name': 'memory accesses (bytes written and read)', 'source_id': 'original'}])
        self.assertTrue(name_present(choices[0]['name'], source['text']))
        self.assertFalse(printed_name_choices('Memory access rate', [source]))

    def test_substrings_and_already_supported_names_add_no_choices(self):
        source = {'source_id': 'original', 'text': 'fan speedometer and CPU clock; fan speedometer.'}
        self.assertFalse(printed_name_choices('Fan speed', [source]))
        self.assertFalse(printed_name_choices('CPU clock', [source]))


if __name__ == '__main__':
    unittest.main()
