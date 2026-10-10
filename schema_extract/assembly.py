"""Deterministic current-format assembly and owned byte-identical delivery."""
from pathlib import Path
import base64
import hashlib
import jsonschema
import os
import subprocess
import uuid

from .compiler import canonical, identity, strict_json, ROOT
from .native import atomic, atomic_bytes, sha

TABLE_ATTRIBUTES = ['name', 'description', 'conformsTo', 'encoding.fileType', 'encoding.header']
FIELD_ATTRIBUTES = ['name', 'description', 'conformsTo', 'role', 'required', 'unique', 'value.datatype',
                    'value.unit.system', 'value.unit.code', 'value.enum.values', 'value.enum.valueSet']
RANK = {'declared': 3, 'observed': 2, 'inferred': 1, 'unknown': 0}
SANDBOX_IMAGE = os.environ.get('SCHEMA_SNAPSHOT_IMAGE', 'python@sha256:a6e34c598f2467ed0e9a8d349809fcd8b5c603269512df273a0bb1784edc11b1')


def bind(ref, registry, evidence_registry):
    unit = registry[ref['source_id']]
    if not unit['text'].strip():
        raise ValueError('An empty source span cannot support an assertion')
    anchor = {key: unit[key] for key in ['source_sha256', 'physical_page', 'start_char', 'end_char', 'text']}
    anchor.update(section_context=unit.get('section_context', unit.get('source_path', '')),
                  source_path=unit.get('source_path'), source_id=unit['source_id'],
                  machine_readout=bool(unit.get('machine_readout')))
    if unit.get('original_binary_sha256'):
        anchor['original_binary_sha256'] = unit['original_binary_sha256']
    key = 'ev-' + identity(anchor)[:24]
    evidence_registry[key] = anchor
    return key


def location(anchor):
    return {'physical_page': anchor['physical_page'], 'start_char': anchor['start_char'],
            'end_char': anchor['end_char'], 'section': anchor['section_context']}


def unknown(reason):
    return {'status': 'unknown', 'evidence_ids': [], 'support': None, 'reason': reason,
            'location': None, 'supporting_locations': []}


def select_claims(claims, attributes, registry, evidence_registry):
    """Current prompt precedence, retaining every disagreement as evidence."""
    values, evidence, conflicts = {}, {}, []
    for attribute in attributes:
        all_claims = [claim for claim in claims if claim['attribute'] == attribute]
        candidates = [claim for claim in all_claims if claim['status'] != 'unknown']
        if not all_claims:
            raise ValueError('Missing complete attribute decision: ' + attribute)
        if not candidates:
            evidence[attribute] = unknown(all_claims[0]['reason'])
            continue
        rank = max(RANK[claim['status']] for claim in candidates)
        strongest = [claim for claim in candidates if RANK[claim['status']] == rank]
        chosen = strongest[0] if len({canonical(claim['value']) for claim in strongest}) == 1 else None
        if len({canonical(claim['value']) for claim in candidates}) > 1:
            bound = []
            for claim in candidates:
                ids = [bind(ref, registry, evidence_registry) for ref in claim['refs']]
                if not ids: raise ValueError('Nonunknown conflict claim lacks original evidence')
                bound.append({'value': claim['value'], 'status': claim['status'], 'evidence_ids': ids,
                              'support': evidence_registry[ids[0]]['text'], 'reason': claim['reason'],
                              'supporting_locations': [{'evidence_id': key, 'support': evidence_registry[key]['text']}
                                                      for key in ids[1:]]})
            conflicts.append({'attribute': attribute, 'claims': bound,
                              'selected': chosen['value'] if chosen else None,
                              'reason': 'declared > observed > inferred' if chosen else 'Same-status disagreement remains unresolved'})
        if chosen is None:
            evidence[attribute] = unknown('Conflicting claims with the same strongest status remain unresolved.')
            continue
        ids = [bind(ref, registry, evidence_registry) for ref in chosen['refs']]
        if not 1 <= len(ids) <= 4: raise ValueError('A selected claim needs one primary and at most three supporting locations')
        anchors = [evidence_registry[key] for key in ids]
        evidence[attribute] = {'status': chosen['status'], 'evidence_ids': ids,
                               'support': anchors[0]['text'], 'reason': chosen['reason'], 'location': location(anchors[0]),
                               'supporting_locations': [{'evidence_id': key, 'support': anchor['text'], 'location': location(anchor)}
                                                       for key, anchor in zip(ids[1:], anchors[1:])]}
        values[attribute] = chosen['value']
    return values, evidence, conflicts


def put(target, attribute, value):
    parts = attribute.split('.')
    for key in parts[:-1]: target = target.setdefault(key, {})
    target[parts[-1]] = value


def assemble(kind, job_id, tables, decisions, registry, coverage):
    output, evidence_registry, retention = [], {}, []
    for table in tables:
        decision = decisions[table['handle']]
        if not decision['keep']: raise ValueError('An established table was dropped during assembly')
        values, evidence, conflicts = select_claims(decision['claims'], TABLE_ATTRIBUTES, registry, evidence_registry)
        if values.get('name') != decision['name']:
            raise ValueError('Table name is unresolved or conflicts')
        body = {'name': values['name'], 'structure': {'fields': []}, 'encoding': {'fileType': 'unknown'}}
        for attribute, value in values.items(): put(body, attribute, value)
        sidecar = {'job_id': job_id, 'table': values['name'], 'evidence': {'table': evidence},
                   'sourceViews': [], 'conflicts': [{**conflict, 'field': None} for conflict in conflicts]}
        for sid in table.get('source_ids', []):
            ev = bind({'source_id': sid}, registry, evidence_registry)
            sidecar['sourceViews'].append({'location': location(evidence_registry[ev]), 'evidence_ids': [ev],
                                           'reason': table.get('reason', 'Current native table identity and source-view membership')})
        for field in table['fields']:
            item = decisions[field['handle']]
            if not item['keep']: raise ValueError('An established field was dropped during assembly')
            field_values, field_evidence, field_conflicts = select_claims(item['claims'], FIELD_ATTRIBUTES, registry, evidence_registry)
            if field_values.get('name') != item['name']:
                raise ValueError('Field name is unresolved or conflicts')
            obj = {'name': item['name'], 'required': False, 'unique': False, 'value': {}}
            for attribute, value in field_values.items(): put(obj, attribute, value)
            if obj['name'] in sidecar['evidence']:
                raise ValueError('Repeated field/evidence namespace needs explicit reconciliation; do not silently rename or discard it')
            body['structure']['fields'].append(obj)
            sidecar['evidence'][obj['name']] = field_evidence
            sidecar['conflicts'].extend({**conflict, 'field': obj['name']} for conflict in field_conflicts)
            retention.append({'table_handle': table['handle'], 'field_handle': field['handle'], 'name': obj['name'],
                              'native_keep': item['keep'], 'native_reason': item['reason'],
                              'name_evidence_ids': field_evidence['name']['evidence_ids'],
                              'membership_source_ids': field.get('source_ids', [])})
        jsonschema.validate({'table': body}, strict_json((ROOT / 'contracts' / 'final-schema.json').read_text(encoding='utf-8')))
        output.append({'schema': {'table': body}, 'evidence': sidecar})
    if not output: raise ValueError('No complete table candidate exists')
    if kind == 'paper': result = {'tables': output, 'coverage': coverage}
    else:
        if len(output) != 1: raise ValueError('Each prepared Dataset case must retain its one original physical table')
        result = output[0]
        result['evidence']['coverage'] = coverage
    return result, evidence_registry, retention


def replay_anchors(evidence_registry, source_texts):
    for key, anchor in evidence_registry.items():
        text = source_texts[anchor['source_sha256']][str(anchor['physical_page'])]
        if text[anchor['start_char']:anchor['end_char']] != anchor['text']:
            raise ValueError('Exact original-source quote replay differs: ' + key)
        if anchor['end_char'] - anchor['start_char'] != len(anchor['text']):
            raise ValueError('Evidence character interval differs: ' + key)
    return {'passed': True, 'exact_anchors': len(evidence_registry), 'whitespace_normalization_used': False}


def schema_projection(candidate):
    """Pure schema artifact; evidence and coverage remain separate deliverables."""
    if 'tables' in candidate:
        return {'tables': [entry['schema'] for entry in candidate['tables']]}, {
            'tables': [entry['evidence'] for entry in candidate['tables']], 'coverage': candidate['coverage']}
    return candidate['schema'], candidate['evidence']


def owned_delivery(candidate, output, validation, deliverables=None):
    """Validation callback operates on a checked snapshot, then exact bytes ship."""
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    raw = (canonical(candidate) + '\n').encode('utf-8')
    if len(raw) > 4_000_000: raise ValueError('Candidate exceeds explicit per-file artifact limit')
    atomic_bytes(output / 'assembled-candidate.json', raw)
    exports = {'full-schema.json': candidate} if deliverables is None else deliverables
    if 'full-schema.json' not in exports or any(Path(name).name != name for name in exports):
        raise ValueError('Confined named schema deliverables are required')
    export_bytes = {name: (canonical(value)+'\n').encode('utf-8') for name, value in exports.items()}
    if any(len(value) > 4_000_000 for value in export_bytes.values()) or sum(map(len, export_bytes.values())) > 16_000_000:
        raise ValueError('Explicit schema/evidence export bound exceeded')
    owner = identity([str(output.resolve()), os.getpid(), uuid.uuid4().hex])
    name = 'two-layer-snapshot-' + owner[:20]
    lease = {'container_name': name, 'owner': owner, 'pid': os.getpid(), 'image': SANDBOX_IMAGE,
             'candidate_sha256': hashlib.sha256(raw).hexdigest(), 'status': 'allocating'}
    atomic(output / 'snapshot-lease.json', lease)
    flags = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
    def docker(args, data=None):
        result = subprocess.run(['docker', *args], input=data, capture_output=True, timeout=45, creationflags=flags)
        if result.returncode: raise ValueError('Owned snapshot operation failed: ' + result.stderr.decode(errors='replace')[-1000:])
        return result.stdout
    created = False
    try:
        docker(['create', '--name', name, '--network', 'none', '--read-only',
                '--label', 'schema-extract-owner=' + owner, '--tmpfs', '/workspace:rw,noexec,nosuid,size=64m',
                '--entrypoint', 'python', SANDBOX_IMAGE, '-c', 'import time; time.sleep(3600)'])
        created = True
        docker(['start', name])
        code = ('import sys,base64,hashlib,json; from pathlib import Path; '
                'raw=base64.b64decode(sys.stdin.buffer.read()); p=Path("/workspace/candidate.json"); '
                'p.write_bytes(raw); snapshot=p.read_bytes(); '
                'print(json.dumps({"bytes":len(snapshot),"sha256":hashlib.sha256(snapshot).hexdigest(),"base64":base64.b64encode(snapshot).decode()}))')
        snapshot = strict_json(docker(['exec', '-i', name, 'python', '-c', code], base64.b64encode(raw)).decode('utf-8'))
        checked = base64.b64decode(snapshot['base64'], validate=True)
        if checked != raw or hashlib.sha256(checked).hexdigest() != snapshot['sha256'] or len(checked) != snapshot['bytes']:
            raise ValueError('Owned container snapshot differs from assembled bytes')
        report = validation(strict_json(checked.decode('utf-8')))
        atomic(output / 'snapshot-validation.json', report)
        if not report.get('passed'): raise ValueError('Checked container candidate failed full validation')
        payload = canonical({name: base64.b64encode(data).decode() for name, data in export_bytes.items()}).encode('utf-8')
        code = ('import sys,base64,hashlib,json; from pathlib import Path; '
                'exports=json.loads(sys.stdin.buffer.read()); result={}; '
                '\nfor name,encoded in exports.items():\n'
                ' raw=base64.b64decode(encoded); p=Path("/workspace")/name; p.write_bytes(raw); checked=p.read_bytes(); '
                'result[name]={"sha256":hashlib.sha256(checked).hexdigest(),"bytes":len(checked),"base64":base64.b64encode(checked).decode()}\n'
                'print(json.dumps(result))')
        snapshots = strict_json(docker(['exec', '-i', name, 'python', '-c', code], payload).decode('utf-8'))
        delivered = {}
        for filename, expected in export_bytes.items():
            observed = snapshots[filename]
            data = base64.b64decode(observed['base64'], validate=True)
            if data != expected or len(data) != observed['bytes'] or hashlib.sha256(data).hexdigest() != observed['sha256']:
                raise ValueError('Pure schema/evidence export snapshot bytes differ')
            target = output/'delivery'/filename
            atomic_bytes(target, data)
            if target.read_bytes() != data: raise ValueError('Delivered artifact bytes differ from checked export snapshot')
            delivered[filename] = {'artifact': str(target), 'sha256': sha(target), 'bytes': len(data),
                                   'container_snapshot_sha256': observed['sha256']}
        receipt = {'passed': True, **delivered['full-schema.json'], 'deliverables': delivered,
                   'assembled_bundle_sha256': snapshot['sha256'], 'byte_identical_delivery': True,
                   'checked_candidate_validation_passed': True, 'container_name': name, 'owner': owner}
        atomic(output / 'delivery-receipt.json', receipt)
        lease['status'] = 'delivered'
        return receipt
    finally:
        if created:
            actual = docker(['inspect', '--format', '{{index .Config.Labels "schema-extract-owner"}}', name]).decode().strip()
            if actual != owner: raise ValueError('Snapshot container ownership changed; refusing cleanup')
            docker(['rm', '-f', name])
            lease['owned_container_removed'] = True
        atomic(output / 'snapshot-lease.json', lease)
