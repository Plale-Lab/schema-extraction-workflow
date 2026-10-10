import copy, unittest
from schema_extract.compiler import classification_plan,extraction_plan,validate_decisions,strict_json,identity,bounded_packets,RequestTooLarge

def registry():
    texts=['temperature: daily mean in K.','Discussion: validation improved.','']
    out={};start=0
    for i,text in enumerate(texts):
        key=f'source-{i}';out[key]={'source_id':key,'source_sha256':'a'*64,'physical_page':1,'start_char':start,'end_char':start+len(text),'text':text}
        start+=len(text)
    return out

def decisions():
    return [{'source_id':f'source-{i}','label':'relevant' if i==0 else 'none','quote_ids':['source-0'] if i==0 else [],'reason':'Current source navigation.'} for i in range(3)]

class CompilerTests(unittest.TestCase):
    def test_full_coverage_no_field_count_forcing(self):
        r=registry(); p=classification_plan(r,'paper')
        self.assertEqual(p['coverage_ids'],list(r));self.assertEqual(p['model_calls'],0)
        e=extraction_plan(r,decisions(),'paper')
        self.assertEqual(e['selected_ids'],['source-0']);self.assertFalse(e['automatic_success'])
        self.assertEqual(e['requests'][0]['wire']['messages'][1]['content'].count('Discussion: validation improved.'),1)

    def test_missing_reordered_and_duplicate_decisions_stop(self):
        d=decisions()
        for bad in [d[:-1],list(reversed(d)),[d[0],d[0],d[2]]]:
            with self.assertRaises(ValueError):validate_decisions(registry(),bad)

    def test_foreign_quote_and_empty_relevant_stop(self):
        d=decisions();d[0]['quote_ids']=['source-1']
        with self.assertRaises(ValueError):validate_decisions(registry(),d)
        d=decisions();d[2].update(label='relevant',quote_ids=['source-2'])
        with self.assertRaises(ValueError):validate_decisions(registry(),d)

    def test_original_substring_must_match(self):
        r=registry();r['quote']={**r['source-0'],'source_id':'quote','citation_only':True,'text':'Temperature','end_char':11}
        d=decisions();d[0]['quote_ids']=['quote']
        with self.assertRaises(ValueError):validate_decisions(r,d)

    def test_dataset_unknown_fields_and_repeated_names_retained(self):
        fields=[{'field_id':f'f{i}','name':'opaque','source_id':'source-0'} for i in range(2)]
        e=extraction_plan(registry(),decisions(),'dataset',fields)
        payload=strict_json(e['requests'][0]['wire']['messages'][1]['content'])
        self.assertEqual(payload['original_field_inventory'],fields)
        with self.assertRaises(ValueError):extraction_plan(registry(),decisions(),'dataset',[fields[0],fields[0]])

    def test_dataset_facts_cannot_enter_paper(self):
        with self.assertRaises(ValueError):extraction_plan(registry(),decisions(),'paper',[])
        with self.assertRaises(ValueError):extraction_plan(registry(),decisions(),'dataset')

    def test_explicit_oversize_stop_and_original_unchanged(self):
        r=registry();r['source-0']['text']='x'*3001;r['source-0']['end_char']=3001;original=copy.deepcopy(r)
        with self.assertRaises(ValueError):classification_plan(r,'paper')
        self.assertEqual(r,original)

    def test_strict_json_and_source_identity(self):
        for raw in ['{"a":1,"a":2}','{"a":NaN}']:
            with self.assertRaises(ValueError):strict_json(raw)
        r=registry(); before=identity(r);r['source-0']['text']='T'+r['source-0']['text'][1:]
        self.assertNotEqual(before,identity(r))

    def test_bounded_split_and_terminal_failure(self):
        def factory(keys):
            if len(keys)>1:raise RequestTooLarge('fixture: actual sizing overflow')
            return {'input_ids':keys}
        self.assertEqual([r['input_ids'] for r in bounded_packets(['a','b','c'],factory)],[['a'],['b'],['c']])
        with self.assertRaises(RequestTooLarge):bounded_packets(list('abcdefgh'),factory)

    def test_full_definition_retained_without_repeated_quote_text(self):
        r=registry();r['quote']={**r['source-0'],'source_id':'quote','citation_only':True,'text':'temperature','end_char':11}
        e=extraction_plan(r,decisions(),'paper');p=strict_json(e['requests'][0]['wire']['messages'][1]['content'])
        self.assertEqual(p['target_units'][0]['text'],r['source-0']['text'])
        self.assertEqual(p['navigation_alternatives_not_repeated'],['quote'])
        d=decisions();d[0]['quote_ids']=['quote'];e=extraction_plan(r,d,'paper');p=strict_json(e['requests'][0]['wire']['messages'][1]['content'])
        self.assertEqual(p['citation_spans'][0]['text'],'temperature')

if __name__=='__main__':unittest.main()
