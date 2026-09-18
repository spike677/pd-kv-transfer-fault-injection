"""Immutable distributed plan, copied unchanged to each node."""
import hashlib
import json
import math
import re
import time
import uuid
from .identity import participant_id

def digest(value):return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':')).encode()).hexdigest()

def validate(m):
    if m.get('schema_version')!=2 or m.get('mode')!='RET_NEG1':raise ValueError('only schema v2 RET_NEG1 supported')
    for key in ('arm_id','deployment_id','engine_base_id'):
        if not isinstance(m.get(key),str) or not re.fullmatch(r'[A-Za-z0-9_.-]+',m[key]):raise ValueError('invalid '+key)
    if m.get('role')!='decode':raise ValueError('decode plan only')
    for key in ('created_at','expires_at','ttl_seconds'):
        if type(m.get(key)) not in (int,float) or not math.isfinite(m[key]):raise ValueError('invalid time')
    if not 0<m['ttl_seconds']<=600 or abs(m['expires_at']-m['created_at']-m['ttl_seconds'])>1e-5:raise ValueError('invalid TTL')
    if type(m.get('expected_tp_size')) is not int or m['expected_tp_size']<1:raise ValueError('expected TP required')
    s=m.get('request_selector',{})
    if s.get('mode')=='exact':
        if not isinstance(s.get('request_id'),str) or not s['request_id']:raise ValueError('request required')
    elif s.get('mode')!='next_transfer' or s.get('isolated_single_request') is not True:raise ValueError('isolated next-transfer only')
    seen=set()
    for p in m.get('participants',[]):
        if not isinstance(p.get('node_id'),str) or not re.fullmatch(r'[A-Za-z0-9_.-]+',p['node_id']):raise ValueError('node required')
        for k in ('dp_rank','pp_rank','tp_rank'):
            if type(p.get(k)) is not int or p[k]<0:raise ValueError('explicit ranks required')
        if p['tp_rank']>=m['expected_tp_size']:raise ValueError('TP outside world')
        key=pid(m,p)
        if key in seen:raise ValueError('duplicate participant')
        seen.add(key)
    if not seen:raise ValueError('participants required')
    return m

def pid(m,p):return participant_id(m['deployment_id'],m['engine_base_id'],p['node_id'],p['dp_rank'],p['pp_rank'],p['tp_rank'])

def plan(deployment,engine,participants,tp_size,request_id=None,isolated=False,ttl=120):
    now=time.time()
    return validate(dict(schema_version=2,arm_id=uuid.uuid4().hex,deployment_id=deployment,
        engine_base_id=engine,role='decode',mode='RET_NEG1',participants=participants,
        expected_tp_size=tp_size,created_at=now,expires_at=now+ttl,ttl_seconds=ttl,
        request_selector=dict(mode='exact' if request_id else 'next_transfer',request_id=request_id,
                              isolated_single_request=isolated)))
