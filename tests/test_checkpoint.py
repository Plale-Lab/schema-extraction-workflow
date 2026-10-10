import tempfile
from pathlib import Path
import unittest
import copy

from schema_extract.campaign import replay_checkpoint
from schema_extract.acceptance import request_source_replay
from schema_extract.compiler import identity, canonical
from schema_extract.native import atomic, atomic_bytes, sha


class CheckpointTests(unittest.TestCase):
    def test_historical_context_replays_exact_preserved_parent_without_mutating_it(self):
        parent = {'source_id': 'p', 'source_sha256': 'a'*64, 'physical_page': 1,
                  'start_char': 20, 'end_char': 30, 'text': 'alpha beta', 'kind': 'supporting'}
        registry = {'p': parent}
        original = copy.deepcopy(registry)
        unit = {**parent, 'source_id': 'part-'+identity(['p', 2, 7])[:24],
                'start_char': 22, 'end_char': 27, 'text': 'pha b',
                'base_source_id': 'p', 'citation_only': True,
                'linked_subdivision': {'original_source_id': 'p', 'part_index': 0, 'parts_total': 2}}
        request_source_replay(unit, registry)
        self.assertEqual(registry, original)
        for key, bad in [('text', 'pha x'), ('source_sha256', 'b'*64), ('physical_page', 2)]:
            with self.subTest(key=key), self.assertRaisesRegex(ValueError, 'source binding differs'):
                request_source_replay({**unit, key: bad}, registry)
        with self.assertRaisesRegex(ValueError, 'identity/interval'):
            request_source_replay({**unit, 'source_id': 'part-invented'}, registry)
        with self.assertRaisesRegex(ValueError, 'no preserved linked parent'):
            request_source_replay({**unit, 'linked_subdivision': {'original_source_id': 'foreign'}}, registry)

    def test_received_raw_corruption_cannot_be_hidden_by_a_valid_parsed_cache(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            wire = {'messages': [], 'max_tokens': 1}
            result = {'units': []}
            body = canonical({'model': 'qwen', 'choices': [{'finish_reason': 'stop', 'message': {'content': canonical(result)}}]}).encode()
            atomic_bytes(root / 'response.raw', body)
            atomic(root / 'receipt.json', {'status': 'received', 'response_sha256': sha(root / 'response.raw')})
            atomic(root / 'checkpoint.json', {'request_sha256': identity(wire), 'result': result,
                                              'native_receipt': {'folder': str(root)}})
            self.assertEqual(replay_checkpoint(root / 'checkpoint.json', {'wire': wire})[0], result)
            atomic_bytes(root / 'response.raw', b'\0' * len(body))
            with self.assertRaisesRegex(ValueError, 'receipt hash'):
                replay_checkpoint(root / 'checkpoint.json', {'wire': wire})
            self.assertEqual((root / 'response.raw').read_bytes(), b'\0' * len(body))


if __name__ == '__main__': unittest.main()
