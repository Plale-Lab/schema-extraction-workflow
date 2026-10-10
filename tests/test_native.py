"""Transport regression gates: preserve bytes and never retry lost observations."""
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
from http.server import BaseHTTPRequestHandler, HTTPServer

from schema_extract.native import Native, NativeError
from schema_extract.compiler import canonical, RequestTooLarge
from schema_extract.compiler import settings


class NativeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.calls = []
        self.count = 10
        self.server_failure = False
        config = settings()
        config['native_generation_enabled'] = True
        self.config_patch = patch('schema_extract.native.settings', return_value=config)
        self.config_patch.start()
        self.addCleanup(self.config_patch.stop)
        self.body = b'{"model":"test-qwen","choices":[{"message":{"content":"{\\\"text\\\":\\\"original\\\\nbytes\\\"}"},"finish_reason":"stop"}],"usage":{"prompt_tokens":10,"completion_tokens":8}}'
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args): pass
            def do_POST(self):
                raw = self.rfile.read(int(self.headers['Content-Length']))
                owner.calls.append((self.path, json.loads(raw), self.headers.get('Authorization')))
                if self.path == '/tokenize':
                    self.send_response(200)
                    self.end_headers()
                    self.wfile.write(canonical({'count': owner.count}).encode())
                else:
                    self.send_response(503 if owner.server_failure else 200)
                    self.end_headers()
                    self.wfile.write(b'transient service error' if owner.server_failure else owner.body)

        self.server = HTTPServer(('127.0.0.1', 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        (self.root / 'secret.txt').write_text('unit-test-credential', encoding='utf-8')
        (self.root / 'binding.local.json').write_text(canonical({'base_url': 'http://127.0.0.1:' + str(self.server.server_port) + '/v1',
                                                               'model': 'test-qwen', 'api_key_file': 'secret.txt'}), encoding='utf-8')
        self.native = Native(self.root / 'binding.local.json', self.root / 'transactions')
        self.request = {'messages': [{'role': 'system', 'content': 'extract'}, {'role': 'user', 'content': 'source'}],
                        'max_tokens': 32}

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self.temp.cleanup()

    def test_exact_tokenizer_precedes_generation_and_raw_bytes_survive(self):
        result, receipt = self.native.complete(self.request)
        self.assertEqual(result, {'text': 'original\nbytes'})
        self.assertEqual([call[0] for call in self.calls], ['/tokenize', '/v1/chat/completions'])
        self.assertEqual((Path(receipt['folder']) / 'response.raw').read_bytes(), self.body)
        self.assertEqual(self.calls[1][1]['chat_template_kwargs'], {'enable_thinking': False})
        for path in (self.root / 'transactions').rglob('*'):
            if path.is_file(): self.assertNotIn(b'unit-test-credential', path.read_bytes())

    def test_tokenizer_context_overrun_prevents_generation(self):
        self.count = 32768
        with self.assertRaises(RequestTooLarge): self.native.complete(self.request)
        self.assertEqual([call[0] for call in self.calls], ['/tokenize'])

    def test_service_error_has_one_dispatch_and_original_error_body(self):
        self.server_failure = True
        with self.assertRaises(NativeError) as failure: self.native.complete(self.request)
        self.assertEqual(sum(call[0].endswith('completions') for call in self.calls), 1)
        self.assertEqual((failure.exception.folder / 'response.raw').read_bytes(), b'transient service error')

    def test_invalid_tokenizer_count_prevents_generation(self):
        self.count = True
        with self.assertRaises(NativeError): self.native.complete(self.request)
        self.assertEqual([call[0] for call in self.calls], ['/tokenize'])

    def test_guidance_translation_keeps_contract(self):
        contract = {'type': 'object', 'additionalProperties': False, 'required': ['text'],
                    'properties': {'text': {'type': 'string'}}}
        self.request['response_contract'] = contract
        self.native.complete(self.request)
        wire = self.calls[1][1]
        self.assertEqual(wire['structured_outputs']['json'], contract)
        self.assertNotIn('thinking', wire)
        self.assertNotIn('response_contract', wire)
        payload = json.loads(wire['messages'][1]['content'])
        self.assertEqual(payload['native_response_contract'], contract)
        self.assertIn('compact JSON', wire['messages'][0]['content'])


if __name__ == '__main__': unittest.main()
