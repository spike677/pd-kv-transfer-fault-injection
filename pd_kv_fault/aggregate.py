"""Offline global evidence gate, not a runtime coordinator or E2E scorer."""
import json
import time
from .manifest import validate,digest,pid
from .identity import engine_matches

def aggregate(manifest,events,now=None):
    validate(manifest);now=time.time() if now is None else now
    expected={pid(manifest,p) for p in manifest['participants']}
    claims={};overrides={};seen={};errors=[];requests=set();rejections=[]
    for e in events:
        if e.get('arm_id')!=manifest['arm_id']:continue
        eid=e.get('event_id')
        if not eid:errors.append('missing_event_id');continue
        if eid in seen:
            if seen[eid]!=e:errors.append('event_id_collision')
            continue
        seen[eid]=e
        if e.get('event','').startswith('reject_') or e.get('event') in ('expired','override_cancelled'):
            rejections.append(e['event'])
        if e.get('event') not in ('claimed','return_override'):continue
        key=e.get('participant_id')
        if (e.get('schema_version')!=2 or e.get('manifest_sha256')!=digest(manifest)
            or key not in expected or e.get('deployment_id')!=manifest['deployment_id']
            or e.get('engine_base_id')!=manifest['engine_base_id'] or e.get('role')!='decode'
            or not engine_matches(manifest['engine_base_id'],e.get('runtime_engine_id'))
            or e.get('tp_size')!=manifest['expected_tp_size']):
            errors.append('wrong_identity_or_manifest');continue
        reconstructed=pid(manifest,e)
        if reconstructed!=key:errors.append('participant_identity_mismatch');continue
        if not (manifest['created_at']<=e.get('timestamp',0)<=manifest['expires_at']):errors.append('expired_or_invalid_time');continue
        request=(e.get('request_id'),e.get('remote_request_id'))
        if not all(isinstance(x,str) and x for x in request):errors.append('missing_request');continue
        if manifest['request_selector']['mode']=='exact' and request[0]!=manifest['request_selector']['request_id']:
            errors.append('wrong_request');continue
        requests.add(request)
        if e['event']=='claimed':
            if key in claims:errors.append('duplicate_claim_evidence')
            claims[key]=e
        else:
            if key in overrides:errors.append('duplicate_override_evidence')
            if e.get('ret')!=-1 or e.get('fault_injected') is not True:errors.append('wrong_return')
            overrides[key]=e
    valid=[]
    for key,e in overrides.items():
        c=claims.get(key)
        if not c or any(c.get(k)!=e.get(k) for k in ('claim_id','runtime_engine_id','pid','session_id','request_id','remote_request_id')) or c['timestamp']>e['timestamp']:
            errors.append('override_without_matching_claim');continue
        valid.append(key)
    if len(requests)>1:errors.append('mixed_requests')
    missing=sorted(expected-set(valid))
    status='GLOBAL_COMPLETED' if not missing and not errors else 'PARTIAL_EXPIRED' if now>=manifest['expires_at'] else 'PARTIAL'
    return dict(status=status,arm_id=manifest['arm_id'],expected=len(expected),overridden=len(valid),
        missing_participants=missing,errors=sorted(set(errors)),requests=sorted(requests),rejections=sorted(set(rejections)),
        evidence_level='CONTROL_EVENTS_ONLY',model_e2e='NOT_INFERRED')

def read_events(paths):
    rows=[]
    for path in paths:
        with open(path,encoding='utf-8') as f:
            rows.extend(json.loads(line) for line in f if line.strip())
    return rows
