import argparse
import json
import os
from .control import Control

def main():
    p=argparse.ArgumentParser()
    p.add_argument('action',choices=['arm','disarm','status'])
    p.add_argument('--directory',default=os.getenv('PD_FAULT_DIR'))
    p.add_argument('--owner',default=os.getenv('PD_FAULT_OWNER'))
    p.add_argument('--engine',default=os.getenv('PD_FAULT_ENGINE'))
    selection=p.add_mutually_exclusive_group()
    selection.add_argument('--request-id')
    selection.add_argument('--next-transfer',action='store_true')
    p.add_argument('--isolated-single-request',action='store_true')
    p.add_argument('--ttl',type=float,default=120)
    p.add_argument('--mode',choices=['RET_NEG1','CONNECTOR_FAIL'],default='RET_NEG1')
    a=p.parse_args()
    if not all((a.directory,a.owner,a.engine)):p.error('directory/owner/engine required')
    if a.next_transfer and not a.isolated_single_request:p.error('next-transfer requires --isolated-single-request')
    c=Control(a.directory,a.owner,a.engine)
    if a.action=='arm':result=c.arm(a.request_id,a.next_transfer,a.ttl,a.mode)
    elif a.action=='disarm':c.disarm();result=c.status()
    else:result=c.status()
    print(json.dumps(result,indent=2))
