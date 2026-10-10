import tempfile
from pathlib import Path
import hashlib
import unittest

from schema_extract.preparation import add_text, prepare_dataset, replay_preparation
from schema_extract.compiler import identity
from schema_extract.native import atomic, sha


class PreparationTests(unittest.TestCase):
    def test_complete_text_tail_and_repeated_field_handles_survive_replay(self):
        raw = b'name,name,value\n001,002,"a,b"\n003,004,\n'
        with tempfile.TemporaryDirectory() as directory:
            out = Path(directory)
            (out / 'inputs').mkdir()
            original = out / 'inputs' / 'sample.csv'
            original.write_bytes(raw)
            sample = {'source_format': '.csv', 'sampled_rows': 2, 'columns': ['name', 'name', 'value']}
            registry, texts, inputs = prepare_dataset(sample, raw, [], out)
            atomic(out / 'source-registry.json', registry)
            atomic(out / 'source-texts.json', texts)
            atomic(out / 'sample-provenance.json', sample)
            inputs.insert(0, {'path': str(original), 'sha256': sha(original), 'source_format': '.csv', 'representation': 'original_sample'})
            case = {'directory': str(out), 'kind': 'dataset', 'registry_sha256': identity(registry), 'input_manifest': inputs}
            replay_preparation(case)
            inventory = __import__('json').loads((out / 'dataset-inventory.json').read_text())
            self.assertNotEqual(inventory[0]['field_id'], inventory[1]['field_id'])
            self.assertEqual([f['name'] for f in inventory], ['name', 'name', 'value'])
            (out / 'inputs' / 'sample-readout.csv').write_bytes(raw[:-1])
            with self.assertRaisesRegex(ValueError, 'byte identity'): replay_preparation(case)

    def test_same_text_views_keep_separate_source_identities(self):
        text = 'complete original text\nwith trailing spaces  '
        digest = hashlib.sha256(text.encode()).hexdigest()
        registry, texts = {}, {}
        first = add_text(registry, texts, text, digest, 1, 'first.txt', 'supporting', target=9)
        second = add_text(registry, texts, text, digest, 1, 'second.txt', 'supporting', target=9)
        self.assertFalse(set(first) & set(second))
        for ids in [first, second]: self.assertEqual(''.join(registry[sid]['text'] for sid in ids), text)


if __name__ == '__main__': unittest.main()
