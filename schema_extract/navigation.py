"""Source-order integration of verified native classification evidence.

The original request/response sequence is preserved. Reordering decisions is
an explicit deterministic projection of the same native results, never a new
classification answer or inferred relevance label.
"""
from pathlib import Path

from .compiler import strict_json, identity, validate_decisions, canonical, classification_response
from .native import atomic, atomic_bytes, sha
from .preparation import replay_preparation


def ordered_registry(case, registry):
    paths = {Path(item['path']).name: index for index, item in enumerate(case['input_manifest'])}
    if any(unit['source_path'] not in paths for unit in registry.values()):
        raise ValueError('Source file absent from ordered original input manifest')
    return dict(sorted(registry.items(), key=lambda pair: (
        paths[pair[1]['source_path']], pair[1]['physical_page'],
        pair[1]['start_char'], pair[1]['end_char'], pair[0])))


def integrate_navigation(case):
    out = Path(case['directory'])
    registry = replay_preparation(case)
    plan = strict_json((out / 'classification-plan.json').read_text(encoding='utf-8'))
    decisions = strict_json((out / 'classification-decisions.json').read_text(encoding='utf-8'))
    validate_decisions(registry, decisions)
    replayed, proofs = [], []
    for index, prepared in enumerate(plan['requests']):
        checkpoint = strict_json((out / 'classification' / (str(index).zfill(5) + '.json')).read_text(encoding='utf-8'))
        if checkpoint['request_sha256'] != identity(prepared['wire']):
            raise ValueError('Classification planned request identity differs')
        receipts = checkpoint.get('native_receipts') or [checkpoint['native_receipt']]
        actual = []
        for receipt in receipts:
            folder = Path(receipt['folder'])
            transaction_receipt = strict_json((folder / 'receipt.json').read_text(encoding='utf-8'))
            if transaction_receipt.get('status') != 'received' or sha(folder / 'response.raw') != transaction_receipt.get('response_sha256'):
                raise ValueError('Original classification raw bytes differ from the received receipt')
            envelope = strict_json((folder / 'response.raw').read_bytes().decode('utf-8'))
            transaction = strict_json((folder / 'request.json').read_text(encoding='utf-8'))
            if envelope['choices'][0]['finish_reason'] != 'stop' or envelope['model'] != transaction['model']:
                raise ValueError('Native classification response/model incomplete')
            response = strict_json(envelope['choices'][0]['message']['content'])
            payload = strict_json(transaction['messages'][1]['content'])
            ids = [unit['source_id'] for unit in payload['target_units']]
            actual.extend(classification_response(response, ids)['units'])
            proofs.append(str(folder))
        if {'units': actual} != checkpoint['result']:
            raise ValueError('Classification checkpoint differs from native response replay')
        replayed.extend(actual)
    if replayed != decisions:
        raise ValueError('Classification full decision replay differs')
    ordered = ordered_registry(case, registry)
    by_id = {decision['source_id']: decision for decision in decisions}
    projected = [by_id[sid] for sid, unit in ordered.items() if not unit.get('citation_only')]
    validate_decisions(ordered, projected)
    # Preserve insertion order in this derived source-order artifact explicitly.
    target = out / 'ordered-source-registry.json'
    atomic_bytes(target, (__import__('json').dumps(ordered, ensure_ascii=False, separators=(',', ':')) + '\n').encode('utf-8'))
    atomic(out / 'ordered-classification-decisions.json', projected)
    atomic(out / 'navigation-integration.json', {
        'method': 'original-file-manifest-page-character-order-projection/v1',
        'original_decisions_sha256': identity(decisions), 'ordered_decisions_sha256': identity(projected),
        'original_request_sequence_retained': True, 'native_response_replay_passed': True,
        'source_order_sha256': identity(list(ordered)), 'generation_calls_added': 0,
        'native_receipts': proofs, 'automatic_success': False})
    return ordered, projected
