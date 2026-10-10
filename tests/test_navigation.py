from pathlib import Path
import unittest

from schema_extract.navigation import ordered_registry


class NavigationTests(unittest.TestCase):
    def test_manifest_page_and_offset_restore_order_without_changing_text(self):
        registry = {
            'last': {'source_path': 'paper.pdf', 'physical_page': 2, 'start_char': 0, 'end_char': 3, 'text': 'end'},
            'second': {'source_path': 'paper.pdf', 'physical_page': 1, 'start_char': 3, 'end_char': 6, 'text': 'two'},
            'first': {'source_path': 'paper.pdf', 'physical_page': 1, 'start_char': 0, 'end_char': 3, 'text': 'one'},
        }
        case = {'input_manifest': [{'path': str(Path('inputs') / 'paper.pdf')}]}
        result = ordered_registry(case, registry)
        self.assertEqual(list(result), ['first', 'second', 'last'])
        self.assertEqual(result, registry)

    def test_unmanifested_file_cannot_be_projected_as_original_source(self):
        with self.assertRaisesRegex(ValueError, 'absent'):
            ordered_registry({'input_manifest': []}, {'foreign': {'source_path': 'foreign.pdf'}})


if __name__ == '__main__': unittest.main()
