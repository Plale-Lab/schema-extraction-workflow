"""Collect accepted schema-only exports and separate evidence without changing bytes."""
import argparse
from pathlib import Path
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from schema_extract.compiler import strict_json
from schema_extract.native import atomic, atomic_bytes, sha, utc
from schema_extract.runner import verify_accepted


def collect(campaign):
    root=Path(campaign)
    configuration=strict_json((root/'campaign.json').read_text(encoding='utf-8'))
    if configuration.get('arrow_dependency_path'):sys.path.insert(0,configuration['arrow_dependency_path'])
    entries=[]
    for case in configuration['cases']:
        accepted=Path(case['directory'])/'acceptance.json'
        row={'case_id':case['case_id'],'pair_id':case['pair_id'],'kind':case['kind'],'name':case['name'],'status':'pending'}
        if accepted.exists():
            result=verify_accepted(case)
            exports={}
            for name in ['full-schema.json','evidence.json']:
                source=result['delivery']['deliverables'][name]
                path=Path(source['artifact'])
                destination=root/'deliverables'/case['pair_id']/case['kind']/name
                if destination.exists() and sha(destination)!=source['sha256']:
                    raise ValueError('Existing collected deliverable differs: '+str(destination))
                atomic_bytes(destination,path.read_bytes())
                if sha(destination)!=source['sha256']:raise ValueError('Collected delivery byte identity differs')
                exports[name]={'path':str(destination),'sha256':sha(destination),'bytes':destination.stat().st_size}
            row.update(status='accepted',deliverables=exports,acceptance_receipt=str(accepted),acceptance_sha256=sha(accepted))
        entries.append(row)
    report={'observed_utc':utc(),'required_artifacts':len(entries),'accepted_artifacts':sum(e['status']=='accepted' for e in entries),
            'schema_contains_evidence':False,'evidence_is_separate':True,'cases':entries}
    atomic(root/'deliverables'/'index.json',report)
    return report


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--campaign',required=True)
    args=parser.parse_args()
    report=collect(args.campaign)
    print({'accepted_artifacts':report['accepted_artifacts'],'required_artifacts':report['required_artifacts'],'index':str(Path(args.campaign)/'deliverables/index.json')})
