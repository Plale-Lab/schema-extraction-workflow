"""Explicit, immutable campaign policy and durable admission for bounded runs.

This does not alter global workflow configuration or supply extraction answers.
Classification, extraction and every review/repair share one admission ledger.
"""
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path
import hashlib
import json
import os
import time

_active = ContextVar('schema_run_policy', default=None)
OVERRIDES = {'completion_profile', 'compact_attribute_batches_enabled'}


def active():
    return _active.get()


def read_policy(path):
    from .compiler import strict_json
    path = Path(path).resolve()
    raw = path.read_bytes()
    value = strict_json(raw.decode('utf-8'))
    if set(value) != {'version', 'case_ids', 'limits', 'workflow_overrides'} or value['version'] != 1:
        raise ValueError('Run policy contract differs')
    cases = value['case_ids']
    if not isinstance(cases, list) or not cases or len(set(cases)) != len(cases) or any(not isinstance(c, str) for c in cases):
        raise ValueError('Explicit unique case scope required')
    limits = value['limits']
    if set(limits) != {'generation_requests', 'wall_seconds', 'case_generation_requests', 'case_wall_seconds'}:
        raise ValueError('Finite aggregate and case limits required')
    if any(type(v) is not int or v <= 0 for v in limits.values()):
        raise ValueError('Limits must be positive finite integers')
    overrides = value['workflow_overrides']
    if not isinstance(overrides, dict) or not set(overrides) <= OVERRIDES:
        raise ValueError('Only completion routing may be overridden')
    if overrides.get('completion_profile', 'full_supported') not in {'full_supported', 'names_first'}:
        raise ValueError('Unknown completion profile')
    if 'compact_attribute_batches_enabled' in overrides and type(overrides['compact_attribute_batches_enabled']) is not bool:
        raise ValueError('Compact routing must be Boolean')
    if overrides.get('completion_profile') == 'full_supported' and overrides.get('compact_attribute_batches_enabled', False):
        raise ValueError('Names-first compact path cannot claim full-supported coverage')
    return {'path': str(path), 'sha256': hashlib.sha256(raw).hexdigest(), 'value': value}


@contextmanager
def policy_scope(path, campaign):
    from .compiler import strict_json
    from .native import atomic
    policy = read_policy(path)
    root = Path(campaign).resolve()
    cases = strict_json((root / 'campaign.json').read_text(encoding='utf-8'))['cases']
    if set(policy['value']['case_ids']) != {c['case_id'] for c in cases}:
        raise ValueError('Policy scope must equal the fresh campaign scope')
    lock = root / 'bounded-run.lock'
    fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    os.write(fd, json.dumps({'pid': os.getpid(), 'policy_sha256': policy['sha256']}).encode())
    os.fsync(fd)
    os.close(fd)
    token = _active.set(policy)
    try:
        atomic(root / 'run-policy-binding.json', policy)
        yield policy
    finally:
        _active.reset(token)
        lock.unlink()


class Admission:
    def __init__(self, root, policy, clock=time.time):
        self.root = Path(root)
        self.policy = policy
        self.clock = clock
        self.path = self.root / 'generation-budget.json'

    def state(self):
        from .compiler import strict_json
        if self.path.exists():
            state = strict_json(self.path.read_text(encoding='utf-8'))
            if state['policy_sha256'] != self.policy['sha256']:
                raise ValueError('Budget policy changed; no reset or cap increase')
            return state
        return {'policy_sha256': self.policy['sha256'], 'started_epoch': None,
                'requests': 0, 'cases': {}, 'pending': None, 'halted': False}

    def admit(self, case_id, transaction):
        from .native import atomic, NativeError, BudgetExceeded
        state = self.state()
        limits = self.policy['value']['limits']
        if case_id not in self.policy['value']['case_ids']:
            raise NativeError('Generation outside authorized case scope')
        if state['pending'] or state['halted']:
            raise NativeError('Unknown or live dispatched outcome; inspect before any new generation')
        now = self.clock()
        start = state['started_epoch'] if state['started_epoch'] is not None else now
        case = state['cases'].get(case_id, {'requests': 0, 'started_epoch': now})
        remaining = min(limits['wall_seconds'] - (now-start), limits['case_wall_seconds'] - (now-case['started_epoch']))
        if remaining <= 0 or state['requests'] >= limits['generation_requests'] or case['requests'] >= limits['case_generation_requests']:
            raise BudgetExceeded('Finite run/case admission budget exhausted')
        state['started_epoch'] = start
        state['requests'] += 1
        case['requests'] += 1
        state['cases'][case_id] = case
        state['pending'] = str(transaction)
        atomic(self.path, state)
        return remaining

    def finish(self, transaction, receipt):
        from .native import atomic
        state = self.state()
        if state['pending'] != str(transaction):
            raise ValueError('Budget terminal transaction differs')
        state['last_terminal'] = receipt
        state['pending'] = None
        # Only a durably received HTTP terminal response permits further work.
        state['halted'] = receipt['status'] not in {'received', 'failed'}
        atomic(self.path, state)


def validate_consumption(tables, traces):
    """Full-supported routing cannot accept names-first deferred source packets."""
    expected = [t['handle'] for t in tables] + [f['handle'] for t in tables for f in t['fields']]
    for handle in expected:
        trace = traces.get(handle, {})
        if not trace.get('complete_source_consumption') or trace.get('deferred_optional_source_ids'):
            raise ValueError('Full-supported source consumption incomplete: ' + handle)
    return {'complete': True, 'items': len(expected)}
