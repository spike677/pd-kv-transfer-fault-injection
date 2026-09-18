import argparse
import json
import os
from pathlib import Path
import re
from .identity import Settings
from .manifest import plan
from .scoped import ScopedControl
from .aggregate import aggregate,read_events

def main(argv=None):
    p=argparse.ArgumentParser(description='Node-local schema v2 control; no network transport')
    p.add_argument('action',choices=['plan','arm','arm-local','status-local','disarm-local','aggregate'])
    p.add_argument('events',nargs='*')
    p.add_argument('--directory',default=os.getenv('PD_FAULT_DIR'))
    p.add_argument('--owner',default=os.getenv('PD_FAULT_OWNER'))
    p.add_argument('--deployment',default=os.getenv('PD_FAULT_DEPLOYMENT_ID'))
    p.add_argument('--engine-base',default=os.getenv('PD_FAULT_ENGINE_BASE'))
    p.add_argument('--node',default=os.getenv('PD_FAULT_NODE_ID'))
    p.add_argument('--participant',action='append',default=[])
    p.add_argument('--all-tp-ranks',action='store_true')
    p.add_argument('--expected-tp-size',type=int)
    p.add_argument('--dp-rank',type=int);p.add_argument('--pp-rank',type=int)
    p.add_argument('--request-id');p.add_argument('--next-transfer',action='store_true')
    p.add_argument('--isolated-single-request',action='store_true')
    p.add_argument('--ttl',type=float,default=120)
    p.add_argument('--manifest');p.add_argument('--output')
    a=p.parse_intermixed_args(argv)
    if a.action=='aggregate':
        if not a.manifest or not a.events:p.error('manifest and event files required')
        result=aggregate(json.loads(Path(a.manifest).read_text()),read_events(a.events))
    else:
        if not a.deployment or not a.engine_base:p.error('deployment/engine-base required')
        if a.action!='plan':
            if not all((a.directory,a.owner,a.node)):p.error('directory/owner/node required')
            c=ScopedControl(a.directory,a.owner,Settings(a.deployment,a.engine_base,a.node,'cli'))
        if a.action in ('plan','arm'):
            parts=[]
            for value in a.participant:
                m=re.fullmatch(r'([^:]+):dp(\d+):pp(\d+):tp(\d+)',value)
                if not m:p.error('participant format node:dp0:pp0:tp0')
                parts.append(dict(node_id=m[1],dp_rank=int(m[2]),pp_rank=int(m[3]),tp_rank=int(m[4])))
            if a.all_tp_ranks:
                if parts or not a.node or not a.expected_tp_size:p.error('all-tp requires node/expected-tp-size and no participant list')
                dp,pp=a.dp_rank,a.pp_rank
                if (dp is None or pp is None) and a.action=='arm':
                    regs=c.status()['installed']
                    groups={(i['dp_rank'],i['pp_rank']) for i in regs}
                    if len(groups)!=1:p.error('cannot infer unique installed DP/PP group; provide explicit ranks')
                    resolved=next(iter(groups));dp=resolved[0] if dp is None else dp;pp=resolved[1] if pp is None else pp
                if dp is None or pp is None:p.error('explicit DP/PP required for offline plan')
                parts=[dict(node_id=a.node,dp_rank=dp,pp_rank=pp,tp_rank=t) for t in range(a.expected_tp_size)]
            if not a.expected_tp_size:p.error('expected-tp-size required')
            m=plan(a.deployment,a.engine_base,parts,a.expected_tp_size,a.request_id,a.isolated_single_request,a.ttl)
            if a.action=='plan':
                if not a.output:p.error('output required')
                with Path(a.output).open('x',encoding='utf-8') as f:json.dump(m,f,indent=2)
                result=m
            else:
                if a.output:
                    with Path(a.output).open('x',encoding='utf-8') as f:json.dump(m,f,indent=2)
                result=c.arm_local(m)
        elif a.action=='arm-local':
            if not a.manifest:p.error('manifest required')
            result=c.arm_local(json.loads(Path(a.manifest).read_text()))
        elif a.action=='disarm-local':c.disarm();result=c.status()
        else:result=c.status()
    print(json.dumps(result,indent=2))
