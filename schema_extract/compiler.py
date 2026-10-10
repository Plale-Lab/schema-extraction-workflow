"""Lossless source navigation and bounded offline request planning."""
from pathlib import Path
import hashlib, json, re

ROOT = Path(__file__).resolve().parents[1]
STATUSES = {'relevant', 'none'}

class RequestTooLarge(ValueError):
    pass

def canonical(obj):
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)

def strict_json(text):
    def pairs(items):
        out = {}
        for key, value in items:
            if key in out: raise ValueError('Duplicate JSON key: ' + key)
            out[key] = value
        return out
    return json.loads(text, object_pairs_hook=pairs,
                      parse_constant=lambda v: (_ for _ in ()).throw(ValueError('Nonfinite JSON')))

def identity(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()

def validate_registry(registry):
    if not isinstance(registry, dict) or not registry: raise ValueError('Nonempty source registry required')
    for key, unit in registry.items():
        if not isinstance(unit, dict) or unit.get('source_id') != key: raise ValueError('Source identity mismatch')
        if not re.fullmatch(r'[0-9a-f]{64}', unit.get('source_sha256', '')): raise ValueError('Source hash required')
        if not isinstance(unit.get('text'), str): raise ValueError('Original source text required')
        a, b = unit.get('start_char'), unit.get('end_char')
        if type(a) is not int or type(b) is not int or a < 0 or b-a != len(unit['text']):
            raise ValueError('Exact character interval differs: ' + key)
        if type(unit.get('physical_page')) is not int or unit['physical_page'] < 1:
            raise ValueError('Physical page / dataset locator required')
    return registry

def base_ids(registry):
    # Registry insertion order is the original preparation order. Do not sort
    # foreign supporting documents into PDF page order or collapse duplicates.
    return [key for key, u in registry.items() if not u.get('citation_only')]

def quote_options(key, registry):
    base=registry[key]; options=[key]
    for qid, u in registry.items():
        if not u.get('citation_only') or u['source_sha256'] != base['source_sha256'] or u['physical_page'] != base['physical_page']:
            continue
        a=u['start_char']-base['start_char']; b=u['end_char']-base['start_char']
        if 0 <= a <= b <= len(base['text']) and base['text'][a:b] == u['text']:
            options.append(qid)
    return options

def validate_decisions(registry, decisions):
    validate_registry(registry)
    ids=base_ids(registry)
    if not isinstance(decisions, list) or [d.get('source_id') for d in decisions] != ids:
        raise ValueError('Classification must cover every source unit exactly once, in original order')
    for decision in decisions:
        if set(decision) != {'source_id','label','quote_ids','reason'}: raise ValueError('Classification contract differs')
        key=decision['source_id']; label=decision['label']; quotes=decision['quote_ids']
        if label not in STATUSES or not isinstance(quotes,list) or len(set(quotes)) != len(quotes):
            raise ValueError('Invalid classification decision')
        if not isinstance(decision['reason'],str) or not decision['reason'].strip(): raise ValueError('Reason required')
        if label == 'none' and quotes: raise ValueError('None must have no navigation quote IDs')
        if label == 'relevant' and not 1 <= len(quotes) <= 3: raise ValueError('Relevant needs one to three navigation IDs')
        allowed=quote_options(key,registry)
        if any(q not in allowed for q in quotes): raise ValueError('Foreign or altered navigation quote')
        if not registry[key]['text'] and label != 'none': raise ValueError('Empty source must be none')
    return decisions

def classification_contract(ids):
    return {'type':'object','additionalProperties':False,'required':['units'],
            'properties':{'units':{'type':'array','minItems':len(ids),'maxItems':len(ids),
                'items':{'type':'object','additionalProperties':False,
                         'required':['source_id','label','quote_ids','reason'],
                         'properties':{'source_id':{'enum':ids},'label':{'enum':['relevant','none']},
                                       'quote_ids':{'type':'array','maxItems':3,'items':{'type':'string'}},
                                       'reason':{'type':'string','minLength':1}}}}}}

def keyed_classification_contract(ids):
    decision = {'type': 'object', 'additionalProperties': False,
                'required': ['label', 'quote_ids', 'reason'],
                'properties': {'label': {'enum': ['relevant', 'none']},
                               'quote_ids': {'type': 'array', 'maxItems': 3, 'items': {'type': 'string'}},
                               'reason': {'type': 'string', 'minLength': 1}}}
    return {'type': 'object', 'additionalProperties': False, 'required': ['unit_results'],
            'properties': {'unit_results': {'type': 'object', 'additionalProperties': False,
                                          'required': list(ids), 'properties': {sid: decision for sid in ids}}}}

def classification_response(result, ids):
    """Lossless ID/order integration; no missing/duplicate label can be invented."""
    if 'unit_results' in result:
        if set(result['unit_results']) != set(ids): raise ValueError('Keyed classification lacks an original target')
        units = [{'source_id': sid, **result['unit_results'][sid]} for sid in ids]
    else:
        units = result['units']
        actual = [unit['source_id'] for unit in units]
        if len(actual) != len(set(actual)) or set(actual) != set(ids):
            raise ValueError('Native classification omitted or repeated an original target')
        by_id = {unit['source_id']: unit for unit in units}
        units = [by_id[sid] for sid in ids]
    return {'units': units}

def settings():
    from .run_policy import active
    cfg = strict_json((ROOT/'config/workflow.json').read_text(encoding='utf-8'))
    policy = active()
    if policy:
        cfg.update(policy['value']['workflow_overrides'])
    return cfg

def bounded_request(layer, task, payload, contract=None):
    cfg=settings(); messages=[{'role':'system','content':task}, {'role':'user','content':canonical(payload)}]
    wire={'messages':messages,'max_tokens':cfg['output_tokens'][layer],'thinking':False}
    if contract: wire['response_contract']=contract
    raw=canonical(wire).encode(); byte_count=len(raw)
    # A conservative byte upper bound is useful offline; native dispatch must
    # additionally use the actual checkpoint tokenizer and chat template.
    if byte_count>cfg['max_wire_bytes']: raise RequestTooLarge('Wire bound exceeded; split source packets explicitly')
    reserve=cfg['output_tokens'][layer]+cfg['safety_reserve_tokens']
    if byte_count+reserve>cfg['context_tokens']: raise RequestTooLarge('Conservative context bound exceeded; no silent truncation')
    return {'layer':layer,'request_sha256':identity(wire),'wire':wire,
            'sizing':{'wire_bytes':byte_count,'conservative_input_token_upper_bound':byte_count,
                      'reserved_tokens':reserve,'actual_tokenizer_required_before_dispatch':True},
            'model_calls':0,'automatic_success':False}

def packetize(keys, registry, target=3000):
    packets=[]; current=[]; used=0
    for key in keys:
        n=len(registry[key]['text'])
        if n>target: raise ValueError('Source unit needs explicit linked subdivision: '+key)
        if current and used+n>target: packets.append(current);current=[];used=0
        current.append(key); used+=n
    if current: packets.append(current)
    return packets

def bounded_packets(packet, factory, depth=0):
    try:
        result=factory(packet);result['split_depth']=depth
        return [result]
    except RequestTooLarge:
        if depth>=settings()['max_split_depth'] or len(packet)<=1:raise
        middle=len(packet)//2
        return bounded_packets(packet[:middle],factory,depth+1)+bounded_packets(packet[middle:],factory,depth+1)

def classification_plan(registry, kind):
    validate_registry(registry)
    if kind not in {'paper','dataset'}: raise ValueError('Unknown source lane')
    task=(ROOT/'prompts/classification.txt').read_text(encoding='utf-8')
    ids=base_ids(registry); requests=[]
    def build(packet):
        units=[{**registry[key],'quote_options':[{'source_id':q,'text':registry[q]['text']} for q in quote_options(key,registry)]} for key in packet]
        payload={'kind':kind,'target_units':units,'schema_template':strict_json((ROOT/'templates/template.json').read_text()),
                 'registry_sha256':identity(registry)}
        request=bounded_request('classification',task,payload,classification_contract(packet))
        request['input_ids']=packet;return request
    for packet in packetize(ids,registry):requests.extend(bounded_packets(packet,build))
    return {'workflow':settings()['name'],'kind':kind,'registry_sha256':identity(registry),'requests':requests,
            'coverage_ids':ids,'status':'prepared_not_dispatched','model_calls':0}

def extraction_plan(registry, decisions, kind, dataset_fields=None):
    validate_decisions(registry,decisions)
    if kind not in {'paper','dataset'}: raise ValueError('Unknown source lane')
    if kind=='dataset':
        if not isinstance(dataset_fields,list) or not dataset_fields: raise ValueError('Original ordered dataset inventory required')
        handles=[]
        for f in dataset_fields:
            if set(f)!={'field_id','name','source_id'} or not isinstance(f['name'],str) or not isinstance(f['field_id'],str):
                raise ValueError('Dataset inventory contract differs')
            if f['source_id'] not in registry: raise ValueError('Dataset field lacks original source')
            handles.append(f['field_id'])
        if len(set(handles))!=len(handles): raise ValueError('Repeated names require distinct field handles')
    elif dataset_fields is not None: raise ValueError('Dataset facts cannot enter Paper extraction')
    ids=base_ids(registry); selected=[d['source_id'] for d in decisions if d['label']=='relevant'];requests=[]
    task=(ROOT/'prompts/extraction.txt').read_text(encoding='utf-8')+'\n'+(ROOT/f'templates/{"Paper" if kind=="paper" else "Dataset"}_prompt.md').read_text(encoding='utf-8')
    def build(packet):
        context=[]
        for key in packet:
            i=ids.index(key)
            for other in ids[max(0,i-1):i+2]:
                # Neighbors only belong to the same original file; cross-file
                # supporting links require explicit later table attribution.
                if registry[other]['source_sha256']==registry[key]['source_sha256'] and other not in packet and other not in context:
                    context.append(other)
        # Full definitions remain intact. Alternative quote spans duplicate
        # their text; only classification-selected spans need to be repeated.
        selected_quotes={q for d in decisions if d['source_id'] in packet for q in d['quote_ids']}
        alternatives=[q for k in packet+context for q in quote_options(k,registry) if registry[q].get('citation_only')]
        supplied=[q for q in dict.fromkeys(alternatives) if q in selected_quotes]
        payload={'kind':kind,'target_units':[registry[k] for k in packet],'context_units':[registry[k] for k in context],
                 'citation_spans':[registry[q] for q in supplied],
                 'navigation_alternatives_not_repeated':[q for q in dict.fromkeys(alternatives) if q not in supplied],
                 'classification_decisions':[d for d in decisions if d['source_id'] in packet],
                 'classification_ledger_sha256':identity(decisions),'registry_sha256':identity(registry),
                 'template':strict_json((ROOT/'templates/template.json').read_text()),
                 'evidence_contract':strict_json((ROOT/'contracts/evidence-contract.json').read_text())}
        if kind=='dataset':payload['original_field_inventory']=dataset_fields
        request=bounded_request('extraction',task,payload)
        request['target_ids']=packet;request['context_ids']=context;return request
    for packet in packetize(selected,registry):requests.extend(bounded_packets(packet,build))
    return {'workflow':settings()['name'],'kind':kind,'registry_sha256':identity(registry),
            'classification_sha256':identity(decisions),'requests':requests,'coverage':decisions,
            'selected_ids':selected,'status':'prepared_not_dispatched','model_calls':0,'automatic_success':False,
            'pending_gates':['native_transport_and_exact_tokenizer','extraction_contract_integration','independent_semantic_review_and_repair',
                             'table_field_coverage_assembly','original_source_replay','snapshot_and_delivery_byte_equality']}
