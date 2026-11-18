DESCRIPTION='Auditable league standings from fixtures results and deductions'
import argparse, json, sys
from pathlib import Path
from .common import read, save
from .model import parse
from .reconciliation import reconcile, validate
from .standings import calculate

def run(source,observations=None):
    data,digest=read(source)
    teams,fixtures,results,deductions=parse(data)
    baseline=calculate(teams,fixtures,results,deductions)
    if observations is None:
        return {'schema_version':1,'source_sha256':digest,'result':baseline}
    payload,observations_digest=read(observations)
    records=validate(teams,fixtures,results,payload)
    reconciliation,imported=reconcile(fixtures,records)
    merged=dict(results);merged.update(imported)
    result=calculate(teams,fixtures,merged,deductions)
    return {'schema_version':2,'source_sha256':digest,'observations_sha256':observations_digest,
            'result':result,'reconciliation':reconciliation}

def main():
    parser=argparse.ArgumentParser(description=DESCRIPTION)
    parser.add_argument('--source',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--observations',type=Path)
    args=parser.parse_args()
    try:
        inputs=[args.source] if args.observations is None else [args.source,args.observations]
        for path in inputs:
            if path.resolve()==args.output.resolve():raise ValueError('input and output must differ')
        report=run(args.source,args.observations);save(args.output,report)
    except (ValueError,TypeError,KeyError,OSError,OverflowError,RecursionError) as error:
        print(str(error),file=sys.stderr);return 2
    print(json.dumps(report,sort_keys=True,ensure_ascii=False,allow_nan=False));return 0

if __name__=='__main__':raise SystemExit(main())
