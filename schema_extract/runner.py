"""Resume-ready extraction campaign controller with durable probes and timing."""
from pathlib import Path
import os
import shutil
import sys
import threading
import time
import traceback

from .compiler import ROOT, strict_json, identity, settings
from .native import Native, NativeError, BudgetExceeded, atomic, atomic_bytes, sha, utc
from .assembly import schema_projection
from .compiler import canonical
from .campaign import Ledger, runtime_identity, live_controller
from .extraction import extract_paper, extract_dataset
from .acceptance import accept, source_replay, replay_transcript
from .semantic import claim_issues


def freeze_runtime(output):
    runtime = runtime_identity()
    target = Path(output)/'runtime'/runtime['sha256'][:12]
    for name, digest in runtime['files'].items():
        source = ROOT/name
        destination = target/'files'/name
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists() and sha(destination) != digest: raise ValueError('Preserved extraction runtime differs')
        if not destination.exists(): atomic_bytes(destination, source.read_bytes())
        if sha(destination) != digest: raise ValueError('Extraction runtime snapshot hash differs')
    atomic(target/'identity.json', runtime)
    return runtime


def verify_accepted(case):
    out = Path(case['directory'])
    result = strict_json((out/'acceptance.json').read_text(encoding='utf-8'))
    receipt = result['delivery']
    if result['status'] != 'accepted' or not result['automatic_success'] or sha(receipt['artifact']) != receipt['sha256']:
        raise ValueError('Previously accepted delivery identity differs')
    bundle = strict_json((out/'accepted-artifact'/'assembled-candidate.json').read_text(encoding='utf-8'))
    schema, evidence = schema_projection(bundle)
    if Path(receipt['artifact']).read_bytes() != (canonical(schema)+'\n').encode('utf-8'):
        raise ValueError('Previously accepted pure schema projection/delivery bytes differ')
    sidecar = receipt['deliverables']['evidence.json']
    if sha(sidecar['artifact']) != sidecar['sha256'] or Path(sidecar['artifact']).read_bytes() != (canonical(evidence)+'\n').encode('utf-8'):
        raise ValueError('Previously accepted separate evidence delivery differs')
    if sha(out/'accepted-artifact'/'snapshot-validation.json') != result['full_validation_sha256']:
        raise ValueError('Previously accepted complete validation report differs')
    registry = strict_json((out/'extraction-source-registry.json').read_text(encoding='utf-8'))
    texts = strict_json((out/'extraction-source-texts.json').read_text(encoding='utf-8'))
    inputs = strict_json((out/'complete-candidate-inputs.json').read_text(encoding='utf-8'))
    source_replay(case, registry, texts)
    replay_transcript(case, inputs['traces'], registry)
    facts = strict_json((out/'dataset-facts.json').read_text(encoding='utf-8')) if case['kind'] == 'dataset' else []
    for table in inputs['tables']:
        errors = claim_issues(inputs['decisions'][table['handle']], registry, table=True, expected_name=table['name'],
                              allow_optional_unknown=settings().get('unknown_optional_attributes_allowed', False))
        for field in table['fields']:
            fact = next((f for f in facts if f['field_id'] == field['handle']), None)
            errors += claim_issues(inputs['decisions'][field['handle']], registry, expected_name=field['name'], dataset_fact=fact,
                                   allow_optional_unknown=settings().get('unknown_optional_attributes_allowed', False))
        if errors: raise ValueError('Previously accepted current source/semantic guards failed: '+canonical(errors))
    return result


def extract(campaign_path, provider_path, endpoint, case_ids=None, follow=False, defer_unresolved_optional=False):
    output = Path(campaign_path)
    campaign = strict_json((output/'campaign.json').read_text(encoding='utf-8'))
    if campaign.get('arrow_dependency_path'): sys.path.insert(0, campaign['arrow_dependency_path'])
    control = output/'extraction-controller'
    if case_ids:
        global_state_path = control/'controller.json'
        if global_state_path.exists():
            global_state = strict_json(global_state_path.read_text(encoding='utf-8'))
            if live_controller(global_state.get('pid'), output):
                for cid in case_ids:
                    prior = global_state.get('cases', {}).get(cid, {})
                    if not prior.get('finished_utc') or prior.get('status') != 'needs_workflow_iteration':
                        raise ValueError('Selected case is still owned by the live main extraction controller')
        control = output/('extract-scope-'+identity(sorted(case_ids))[:12])
    control.mkdir(exist_ok=True)
    old = control/'controller.json'
    if old.exists():
        state = strict_json(old.read_text(encoding='utf-8'))
        if state.get('pid') and live_controller(state['pid'], output): raise ValueError('Extraction controller is already live')
        atomic(control/('controller-before-resume-'+str(time.time_ns())+'.json'), state)
    ledger = Ledger(control)
    runtime = freeze_runtime(output)
    ledger.state.update(operation='extraction', runtime_sha256=runtime['sha256'],
                        campaign_sha256=sha(output/'campaign.json'), accepted_artifacts=0,
                        native_authorization='Explicit campaign case scope; Mercury Qwen',
                        authorized_case_ids=[c['case_id'] for c in campaign['cases']])
    ledger.record({'status': 'extraction_controller_started', 'runtime_sha256': runtime['sha256'], 'pid': os.getpid()})
    native = Native(provider_path, output/'transport', event=ledger.record, endpoint=endpoint)
    halt = threading.Event()
    def monitor():
        while not halt.wait(30):
            try: ledger.record({'status': 'probe_passed', 'probe': native.probe()})
            except Exception as exc:
                ledger.record({'status': 'probe_observation_failed', 'error_type': type(exc).__name__,
                               'disposition': 'Probe failure does not establish generation termination; no restart'})
    thread = threading.Thread(target=monitor, daemon=True)
    attempted = set()
    cases = [case for case in campaign['cases'] if not case_ids or case['case_id'] in case_ids]
    if defer_unresolved_optional:
        cases = [{**case, 'defer_unresolved_optional': True} for case in cases]
        ledger.state['defer_unresolved_optional'] = True
    if case_ids and set(case_ids) != {case['case_id'] for case in cases}: raise ValueError('Unknown campaign case requested')
    try:
        ledger.record({'status': 'probe_passed', 'probe': native.probe()}); thread.start()
        while True:
            advanced = False
            for case in cases:
                cid = case['case_id']; native.case_id = cid; out = Path(case['directory'])
                if cid in attempted: continue
                if (out/'acceptance.json').exists():
                    verify_accepted(case)
                    ledger.state['cases'][cid] = {'status': 'accepted', 'automatic_success': True, 'replayed_existing_delivery': True}
                    attempted.add(cid); advanced = True
                elif (out/'classification-decisions.json').exists():
                    attempted.add(cid); advanced = True
                    state = {'status': 'running', 'phase': 'extraction', 'started_utc': utc(), 'automatic_success': False}
                    ledger.state['cases'][cid] = state
                    ledger.state.update(current_case=cid, phase='extraction'); ledger.save()
                    tick = time.monotonic()
                    try:
                        result = (extract_paper if case['kind'] == 'paper' else extract_dataset)(case, native)
                        ledger.state['phase'] = 'acceptance'; ledger.save()
                        accepted = accept(case, native, *result)
                        state.update(status='accepted', automatic_success=True, acceptance=accepted,
                                     finished_utc=utc(), seconds=time.monotonic()-tick)
                        ledger.record({'status': 'case_accepted', 'case_id': cid, 'case': state})
                    except Exception as exc:
                        state.update(status='budget_exhausted' if isinstance(exc, BudgetExceeded) else 'needs_transport_inspection' if isinstance(exc, NativeError) else 'needs_workflow_iteration',
                                     error_type=type(exc).__name__, error=str(exc), finished_utc=utc(), seconds=time.monotonic()-tick)
                        atomic(out/'extraction-error.json', {'case': state, 'traceback': traceback.format_exc(), 'runtime_sha256': runtime['sha256']})
                        ledger.record({'status': 'case_extraction_failed', 'case_id': cid, 'case': state})
                        if isinstance(exc, NativeError) and not isinstance(exc, BudgetExceeded): raise
                ledger.state['accepted_artifacts'] = sum(s['status'] == 'accepted' for s in ledger.state['cases'].values())
                ledger.save()
            if len(attempted) == len(cases): break
            classification = strict_json((output/'controller.json').read_text(encoding='utf-8'))
            live = live_controller(classification.get('pid'), output)
            if not follow or not live: break
            if not advanced:
                ledger.state.update(phase='waiting_for_classification', current_case=None)
                ledger.save(); halt.wait(10)
        ledger.state['status'] = 'complete' if ledger.state['accepted_artifacts'] == len(cases) else 'full_case_work_remaining'
        ledger.state['automatic_success'] = ledger.state['status'] == 'complete'
    except Exception as exc:
        ledger.state.update(status='budget_exhausted' if isinstance(exc, BudgetExceeded) else 'needs_transport_inspection' if isinstance(exc, NativeError) else 'needs_workflow_iteration',
                            error_type=type(exc).__name__, error=str(exc))
        raise
    finally:
        halt.set()
        if thread.is_alive(): thread.join(timeout=1)
        ledger.state.update(pid=None, finished_utc=utc())
        ledger.record({'status': 'extraction_controller_stopped', 'accepted_artifacts': ledger.state['accepted_artifacts'],
                       'automatic_success': ledger.state['automatic_success']})
