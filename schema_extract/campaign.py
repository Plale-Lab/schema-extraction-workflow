"""Sequential native campaign operations, durable logging and ongoing probing."""
from pathlib import Path
import argparse
import copy
import json
import os
import threading
import time
import traceback
import sys
import subprocess

from .compiler import classification_plan, validate_decisions, strict_json, identity, settings, canonical, ROOT, classification_response, keyed_classification_contract
from .native import Native, NativeError, BudgetExceeded, IncompleteGeneration, atomic, atomic_bytes, utc, sha
from datetime import datetime
from .preparation import prepare_campaign, replay_preparation


class Ledger:
    def __init__(self, output):
        self.output = Path(output)
        self.lock = threading.RLock()
        self.state = {'pid': os.getpid(), 'status': 'running', 'started_utc': utc(),
                      'operation': 'classification', 'model_generation_calls': 0,
                      'metadata_requests': 0, 'prompt_tokens': 0, 'completion_tokens': 0,
                      'accepted_artifacts': 0, 'required_artifacts': 20,
                      'automatic_success': False, 'cases': {}, 'runtime_manual_enabled': False}
        self.started = time.monotonic()
        previous = self.output / 'events.jsonl'
        self.head = '0' * 64
        self.number = 0
        self.first_started = None
        if previous.exists():
            for line in previous.read_text(encoding='utf-8').splitlines():
                entry = strict_json(line)
                if entry['previous_sha256'] != self.head or identity({k:v for k,v in entry.items() if k != 'sha256'}) != entry['sha256']:
                    raise ValueError('Existing event chain differs')
                self.head = entry['sha256']
                self.number = entry['sequence']
                if self.first_started is None:
                    self.first_started = datetime.fromisoformat(entry['observed_utc']).timestamp()
                    self.state['first_started_utc'] = entry['observed_utc']
                self.account(entry['event'])

    def account(self, event):
        if event.get('status') == 'dispatched':
            key = 'model_generation_calls' if event.get('model_generation') else 'metadata_requests'
            self.state[key] += 1
        if event.get('status') == 'received' and event.get('model_generation'):
            try:
                envelope = strict_json((Path(event['folder']) / 'response.raw').read_bytes().decode('utf-8'))
                usage = envelope.get('usage') or {}
            except (ValueError, OSError, TypeError):
                # A durable parsed receipt preserves a measurement even when the
                # raw body later fails integrity. It cannot certify extraction.
                parsed = Path(event['folder']) / 'generation.json'
                usage = strict_json(parsed.read_text(encoding='utf-8')).get('usage') or {} if parsed.exists() else {}
                if usage:
                    self.state['usage_from_durable_parse_receipt'] = self.state.get('usage_from_durable_parse_receipt', 0) + 1
            self.state['prompt_tokens'] += usage.get('prompt_tokens', 0)
            self.state['completion_tokens'] += usage.get('completion_tokens', 0)
            if not usage:
                self.state['missing_generation_usage'] = self.state.get('missing_generation_usage', 0) + 1
        if event.get('model_generation') and 'seconds' in event:
            self.state['generation_observation_seconds'] = self.state.get('generation_observation_seconds', 0) + event['seconds']

    def save(self):
        with self.lock:
            if self.first_started is None:
                self.first_started = time.time()
                self.state['first_started_utc'] = utc()
            self.state.update(updated_utc=utc(), elapsed_seconds=time.monotonic() - self.started,
                              campaign_elapsed_seconds=time.time()-self.first_started,
                              event_chain_sha256=self.head, event_count=self.number)
            atomic(self.output / 'controller.json', self.state)

    def record(self, event):
        with self.lock:
            self.number += 1
            entry = {'sequence': self.number, 'observed_utc': utc(), 'previous_sha256': self.head,
                     'event': event}
            entry['sha256'] = identity(entry)
            with (self.output / 'events.jsonl').open('a', encoding='utf-8', newline='\n') as stream:
                stream.write(canonical(entry) + '\n')
                stream.flush()
                os.fsync(stream.fileno())
            self.head = entry['sha256']
            self.account(event)
            self.state['last_event'] = event
            self.save()


def runtime_identity():
    files = [p for directory in ['schema_extract', 'prompts', 'templates', 'contracts', 'config']
             for p in (ROOT / directory).rglob('*') if p.is_file() and p.suffix != '.pyc' and '__pycache__' not in p.parts]
    hashes = {str(p.relative_to(ROOT)).replace('\\', '/'): sha(p) for p in files}
    return {'files': hashes, 'sha256': identity(hashes)}


def live_controller(pid, campaign_path):
    """Inspect the actual process and its command, without optional psutil."""
    if type(pid) is not int or pid <= 0:
        return False
    if os.name == 'nt':
        code = "Get-CimInstance Win32_Process -Filter 'ProcessId=" + str(pid) + "' | Select-Object ProcessId,CommandLine | ConvertTo-Json -Compress"
        result = subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive', '-Command', code],
                                capture_output=True, text=True, timeout=15,
                                creationflags=subprocess.CREATE_NO_WINDOW)
        if result.returncode:
            raise ValueError('Unable to inspect existing controller process; no duplicate dispatch')
        if not result.stdout.strip():
            return False
        process = strict_json(result.stdout.strip())
        command = process.get('CommandLine') or ''
        return 'schema_extract.campaign' in command and str(campaign_path).lower() in command.lower()
    try:
        command = Path('/proc/' + str(pid) + '/cmdline').read_bytes().replace(b'\0', b' ').decode()
    except FileNotFoundError:
        return False
    return 'schema_extract.campaign' in command and str(campaign_path) in command


def replay_checkpoint(checkpoint, prepared):
    saved = strict_json(Path(checkpoint).read_text(encoding='utf-8'))
    if saved['request_sha256'] != identity(prepared['wire']):
        raise ValueError('Existing request checkpoint identity differs')
    transport = Path(saved['native_receipt']['folder'])
    receipt = strict_json((transport / 'receipt.json').read_text(encoding='utf-8'))
    if receipt.get('status') != 'received':
        raise NativeError('Cached request is not a received terminal response; inspect before any retry', transport)
    if sha(transport / 'response.raw') != receipt['response_sha256']:
        raise ValueError('Raw response bytes differ from their received receipt hash')
    envelope = strict_json((transport / 'response.raw').read_bytes().decode('utf-8'))
    if envelope['choices'][0]['finish_reason'] != 'stop':
        raise ValueError('Cached classification response did not complete')
    actual = strict_json(envelope['choices'][0]['message']['content'])
    ids = prepared.get('input_ids') or [unit['source_id'] for unit in saved['result']['units']]
    if classification_response(actual, ids) != saved['result']:
        raise ValueError('Classification checkpoint differs from original native response')
    return saved['result'], saved['native_receipt']


def classify(campaign_path, provider_path, endpoint):
    output = Path(campaign_path)
    campaign = strict_json((output / 'campaign.json').read_text(encoding='utf-8'))
    if campaign.get('arrow_dependency_path'):
        sys.path.insert(0, campaign['arrow_dependency_path'])
    old = output / 'controller.json'
    if old.exists():
        state = strict_json(old.read_text(encoding='utf-8'))
        pid = state.get('pid')
        if pid and pid != os.getpid():
            if live_controller(pid, output):
                raise ValueError('Existing campaign controller is live; do not duplicate dispatch')
        atomic(output / ('controller-before-resume-' + str(time.time_ns()) + '.json'), state)
    ledger = Ledger(output)
    runtime = runtime_identity()
    atomic(output / ('classification-runtime-' + runtime['sha256'][:12] + '.json'), runtime)
    ledger.state.update(runtime_sha256=runtime['sha256'], campaign_sha256=sha(output / 'campaign.json'),
                        native_authorization='Explicit campaign case scope; Mercury Qwen',
                        authorized_case_ids=[c['case_id'] for c in campaign['cases']])
    ledger.record({'status': 'controller_started', 'pid': os.getpid(), 'runtime_sha256': runtime['sha256']})
    native = Native(provider_path, output / 'transport', event=ledger.record, endpoint=endpoint)
    halt = threading.Event()

    def monitor():
        while not halt.wait(30):
            try:
                ledger.record({'status': 'probe_passed', 'probe': native.probe()})
            except Exception as exc:
                ledger.record({'status': 'probe_observation_failed', 'error_type': type(exc).__name__,
                               'disposition': 'Do not infer generation termination or restart from this probe'})

    thread = threading.Thread(target=monitor, daemon=True)
    try:
        ledger.record({'status': 'probe_passed', 'probe': native.probe()})
        thread.start()
        for case in campaign['cases']:
            cid = case['case_id']
            native.case_id = cid
            out = Path(case['directory'])
            registry = replay_preparation(case)
            plan = classification_plan(registry, case['kind'])
            atomic(out / 'classification-plan.json', plan)
            decisions = []
            case_state = {'status': 'running', 'packets_completed': 0, 'packets_total': len(plan['requests']),
                          'started_utc': utc()}
            ledger.state['cases'][cid] = case_state
            ledger.state.update(current_case=cid, phase='classification')
            ledger.save()
            tick = time.monotonic()
            try:
                for index, prepared in enumerate(plan['requests']):
                    checkpoint = out / 'classification' / (str(index).zfill(5) + '.json')
                    checkpoint.parent.mkdir(exist_ok=True)
                    key = identity(prepared['wire'])
                    cached = False
                    if checkpoint.exists():
                        try:
                            result, receipt = replay_checkpoint(checkpoint, prepared)
                            cached = True
                        except NativeError:
                            raise
                        except (ValueError, KeyError, OSError) as exc:
                            # Keep the old checkpoint and all raw files; a fresh native
                            # response is required. Never reconstruct an HTTP envelope
                            # from a parsed object and claim it is original evidence.
                            preserved = out / 'classification' / 'quarantined' / (checkpoint.stem + '-' + str(time.time_ns()) + '.json')
                            atomic_bytes(preserved, checkpoint.read_bytes())
                            ledger.record({'status': 'checkpoint_replay_failed', 'case_id': cid,
                                           'packet': index, 'error': str(exc),
                                           'preserved_checkpoint': str(preserved),
                                           'disposition': 'Fresh native response needed; original raw evidence retained'})
                    if not cached:
                        request = copy.deepcopy(prepared)
                        failure = None
                        for attempt in range(settings()['max_repairs_per_item'] + 1):
                            try:
                                result, receipt = native.complete(request)
                                result = classification_response(result, prepared['input_ids'])
                                packet_registry = {sid: registry[sid] for sid in prepared['input_ids']}
                                packet_decisions = result['units']
                                # This path currently has no citation-only alternative entries.
                                validate_decisions(packet_registry, packet_decisions)
                                break
                            except IncompleteGeneration as exc:
                                ledger.record({'status': 'classification_terminal_length', 'case_id': cid,
                                               'packet': index, 'attempt': attempt, 'native_folder': str(exc.folder)})
                                if attempt == settings()['max_repairs_per_item']: raise
                                request = copy.deepcopy(prepared)
                                payload = strict_json(request['wire']['messages'][1]['content'])
                                payload['same_contract_repair'] = {'error': 'Previous request ended at its output limit while repeating whitespace.',
                                                                   'instruction': 'Return compact JSON. Include source_id, label, quote_ids and reason for every original unit. Do not emit blank lines.'}
                                request['wire']['messages'][1]['content'] = canonical(payload)
                            except NativeError:
                                # A lost response can leave a live remote request; inspection
                                # is required before retrying the same work item.
                                raise
                            except (ValueError, KeyError, TypeError) as exc:
                                failure = str(exc)
                                ledger.record({'status': 'classification_contract_failed', 'case_id': cid,
                                               'packet': index, 'attempt': attempt, 'error': failure})
                                if attempt == settings()['max_repairs_per_item']: raise
                                request = copy.deepcopy(prepared)
                                payload = strict_json(request['wire']['messages'][1]['content'])
                                payload['same_contract_repair'] = {'error': failure, 'previous_response': result,
                                                                   'instruction': 'Return unit_results keyed by every original target source ID. Each required key has label, quote_ids and reason. No source ID may be omitted or repeated.'}
                                request['wire']['response_contract'] = keyed_classification_contract(prepared['input_ids'])
                                request['wire']['messages'][1]['content'] = canonical(payload)
                        atomic(checkpoint, {'request_sha256': key, 'result': result, 'native_receipt': receipt,
                                            'registry_sha256': identity(registry), 'completed_utc': utc()})
                    decisions.extend(result['units'])
                    case_state['packets_completed'] = index + 1
                    ledger.save()
                validate_decisions(registry, decisions)
                atomic(out / 'classification-decisions.json', decisions)
                from .navigation import integrate_navigation
                integrate_navigation(case)
                case_state.update(status='classification_completed', relevant_units=sum(d['label'] == 'relevant' for d in decisions),
                                  total_units=len(decisions), seconds=time.monotonic()-tick, finished_utc=utc(),
                                  extraction_pending=True, automatic_success=False)
                ledger.record({'status': 'case_classification_completed', 'case_id': cid, 'case': case_state})
            except Exception as exc:
                case_state.update(status='needs_workflow_iteration', error_type=type(exc).__name__, error=str(exc),
                                  seconds=time.monotonic()-tick, finished_utc=utc())
                atomic(out / 'classification-error.json', {'case': case_state, 'traceback': traceback.format_exc()})
                ledger.record({'status': 'case_classification_failed', 'case_id': cid, 'case': case_state})
                if isinstance(exc, NativeError) and not isinstance(exc, BudgetExceeded):
                    raise
        ledger.state['status'] = 'classification_completed_extraction_pending' if all(c['status'] == 'classification_completed' for c in ledger.state['cases'].values()) else 'needs_workflow_iteration'
    except Exception as exc:
        ledger.state.update(status='needs_transport_inspection' if isinstance(exc, NativeError) else 'needs_workflow_iteration',
                            error_type=type(exc).__name__, error=str(exc))
        raise
    finally:
        halt.set()
        if thread.is_alive(): thread.join(timeout=1)
        ledger.state.update(pid=None, finished_utc=utc())
        ledger.record({'status': 'controller_stopped', 'automatic_success': False})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    prepare = commands.add_parser('prepare')
    for name in ['papers', 'samples', 'supporting', 'out']:
        prepare.add_argument('--' + name, required=True)
    prepare.add_argument('--arrow-dependencies')
    for command in ['classify', 'extract', 'run']:
        run = commands.add_parser(command)
        run.add_argument('--campaign', required=True)
        run.add_argument('--provider', required=True)
        run.add_argument('--endpoint', required=True)
        run.add_argument('--run-policy', required=True)
        if command == 'extract':
            run.add_argument('--case', action='append')
            run.add_argument('--follow', action='store_true')
            run.add_argument('--defer-unresolved-optional', action='store_true')
    args = parser.parse_args()
    if args.command == 'prepare':
        if args.arrow_dependencies:
            sys.path.insert(0, args.arrow_dependencies)
        result = prepare_campaign(args.papers, args.samples, args.supporting, args.out)
        print(canonical({'cases': len(result['cases']), 'units': {c['case_id']: c['base_units'] for c in result['cases']}}))
    else:
        from .run_policy import policy_scope
        with policy_scope(args.run_policy, args.campaign):
                if args.command == 'classify':
                    classify(args.campaign, args.provider, args.endpoint)
                else:
                    from .runner import extract
                    if args.command == 'run':
                        classify(args.campaign, args.provider, args.endpoint)
                        extract(args.campaign, args.provider, args.endpoint)
                    else:
                        extract(args.campaign, args.provider, args.endpoint, args.case, args.follow, args.defer_unresolved_optional)


if __name__ == '__main__':
    main()
