import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock

from schema_extract.compiler import settings
from schema_extract.native import Native, NativeError, BudgetExceeded
from schema_extract.run_policy import Admission, policy_scope, read_policy, validate_consumption


class RunPolicyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.clock = [100.0]
        self.value = {'version': 1, 'case_ids': ['paper', 'dataset'],
                      'limits': {'generation_requests': 3, 'case_generation_requests': 2,
                                 'wall_seconds': 20, 'case_wall_seconds': 10},
                      'workflow_overrides': {'completion_profile': 'full_supported',
                                             'compact_attribute_batches_enabled': False}}
        self.path = self.root / 'policy.json'
        self.path.write_text(json.dumps(self.value))
        self.policy = read_policy(self.path)
        self.admission = Admission(self.root, self.policy, lambda: self.clock[0])

    def terminal(self, transaction, status='received'):
        self.admission.finish(transaction, {'status': status})

    def test_case_aggregate_and_restart_share_admissions(self):
        self.admission.admit('paper', 'one'); self.terminal('one')
        self.admission = Admission(self.root, self.policy, lambda: self.clock[0])
        self.admission.admit('paper', 'two'); self.terminal('two')
        with self.assertRaises(NativeError): self.admission.admit('paper', 'three')
        self.admission.admit('dataset', 'three'); self.terminal('three')
        with self.assertRaises(NativeError): self.admission.admit('dataset', 'four')
        self.assertEqual(self.admission.state()['requests'], 3)

    def test_unknown_and_pending_are_never_resent(self):
        self.admission.admit('paper', 'one')
        with self.assertRaises(NativeError): self.admission.admit('dataset', 'two')
        self.terminal('one', 'observation_failed')
        with self.assertRaises(NativeError): self.admission.admit('dataset', 'two')

    def test_wall_deadline_survives_process_restart(self):
        self.admission.admit('paper', 'one'); self.terminal('one')
        self.clock[0] = 110
        with self.assertRaises(NativeError): self.admission.admit('paper', 'two')
        self.admission.admit('dataset', 'two'); self.terminal('two')
        self.clock[0] = 120
        with self.assertRaises(NativeError): self.admission.admit('dataset', 'three')

    def test_changed_policy_cannot_reset_ledger(self):
        self.admission.admit('paper', 'one'); self.terminal('one')
        self.value['limits']['generation_requests'] = 99
        self.path.write_text(json.dumps(self.value))
        altered = Admission(self.root, read_policy(self.path))
        with self.assertRaises(ValueError): altered.state()

    def test_scope_routing_is_restored_and_lock_prevents_duplicate_owner(self):
        (self.root / 'campaign.json').write_text(json.dumps({'cases': [{'case_id': c} for c in self.value['case_ids']]}))
        before = settings()
        with policy_scope(self.path, self.root):
            self.assertEqual(settings()['completion_profile'], 'full_supported')
            self.assertFalse(settings()['compact_attribute_batches_enabled'])
            with self.assertRaises(FileExistsError):
                with policy_scope(self.path, self.root): pass
        self.assertEqual(settings(), before)
        self.assertFalse((self.root / 'bounded-run.lock').exists())

    def test_invalid_policy_and_foreign_case_are_rejected(self):
        with self.assertRaises(NativeError): self.admission.admit('other', 'one')
        self.value['limits']['wall_seconds'] = None
        self.path.write_text(json.dumps(self.value))
        with self.assertRaises(ValueError): read_policy(self.path)

    def test_full_profile_cannot_use_partial_compact_path(self):
        self.value['workflow_overrides']['compact_attribute_batches_enabled'] = True
        self.path.write_text(json.dumps(self.value))
        with self.assertRaises(ValueError): read_policy(self.path)

    def test_acceptance_cannot_promote_missing_or_deferred_source_consumption(self):
        tables = [{'handle': 'table', 'fields': [{'handle': 'f0'}]}]
        traces = {'table': {'complete_source_consumption': True},
                  'f0': {'complete_source_consumption': False, 'deferred_optional_source_ids': ['unread']}}
        with self.assertRaises(ValueError): validate_consumption(tables, traces)
        traces['f0'] = {'complete_source_consumption': True, 'deferred_optional_source_ids': []}
        self.assertEqual(validate_consumption(tables, traces), {'complete': True, 'items': 2})

    def test_real_transport_boundary_cannot_dispatch_past_case_cap(self):
        (self.root / 'campaign.json').write_text(json.dumps({'cases': [{'case_id': c} for c in self.value['case_ids']]}))
        provider = self.root / 'offline-provider.json'
        provider.write_text(json.dumps({'model': 'offline', 'api_key_env': 'OFFLINE_FIXTURE_KEY'}))
        def response(raw):
            result = MagicMock()
            result.__enter__.return_value.status = 200
            result.__enter__.return_value.read.return_value = raw
            return result
        metrics = b'vllm:num_requests_running 0\nvllm:num_requests_waiting 0\n'
        with policy_scope(self.path, self.root):
            native = Native(provider, self.root / 'transport', endpoint='http://127.0.0.1:1/v1')
            native.case_id = 'paper'
            native._secret = lambda: 'offline-fixture'
            native.opener = MagicMock()
            native.opener.open.side_effect = [response(metrics), response(b'{}'), response(metrics), response(b'{}'), response(metrics)]
            for _ in range(2): native.transact('generation', native.base + '/chat/completions', {'model': 'offline'})
            with self.assertRaises(BudgetExceeded): native.transact('generation', native.base + '/chat/completions', {'model': 'offline'})
            calls = native.opener.open.call_args_list
            generations = [c for c in calls if c.args[0].full_url.endswith('/chat/completions')]
            self.assertEqual(len(generations), 2)
            receipts = [json.loads(p.read_text()) for p in (self.root / 'transport').glob('*/receipt.json')]
            self.assertEqual(sum(r['status'] == 'rejected_before_generation' for r in receipts), 1)
