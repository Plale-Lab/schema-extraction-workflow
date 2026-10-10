from pathlib import Path
import argparse,json
from .compiler import strict_json,classification_plan,extraction_plan

def main():
    p=argparse.ArgumentParser(description='Prepare bounded requests; makes zero model calls.')
    p.add_argument('layer',choices=['classification','extraction']);p.add_argument('--kind',choices=['paper','dataset'],required=True)
    p.add_argument('--registry',required=True);p.add_argument('--decisions');p.add_argument('--dataset-inventory');p.add_argument('--out',required=True)
    a=p.parse_args(); registry=strict_json(Path(a.registry).read_text(encoding='utf-8'))
    if a.layer=='classification': result=classification_plan(registry,a.kind)
    else:
        if not a.decisions:p.error('extraction requires --decisions')
        decisions=strict_json(Path(a.decisions).read_text(encoding='utf-8'))
        fields=strict_json(Path(a.dataset_inventory).read_text(encoding='utf-8')) if a.dataset_inventory else None
        result=extraction_plan(registry,decisions,a.kind,fields)
    out=Path(a.out)
    if out.exists():raise ValueError('Use a new output path; preserved plans are not overwritten')
    out.parent.mkdir(parents=True,exist_ok=True);out.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({'status':result['status'],'requests':len(result['requests']),'model_calls':0,'out':str(out.resolve())}))

if __name__=='__main__':main()
