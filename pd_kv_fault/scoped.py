"""Node-local state only. Linux flock / Windows byte lock, no NFS contract."""
import contextlib
import json
import os
from pathlib import Path
import threading
import time
import uuid
from .identity import engine_matches,identify
from .manifest import validate,digest,pid

def process_alive(pid_value):
    if type(pid_value) is not int or pid_value<=0:return False
    if os.name=='nt':
        import ctypes
        k=ctypes.WinDLL('kernel32',use_last_error=True)
        k.OpenProcess.argtypes=[ctypes.c_ulong,ctypes.c_int,ctypes.c_ulong]
        k.OpenProcess.restype=ctypes.c_void_p
        k.GetExitCodeProcess.argtypes=[ctypes.c_void_p,ctypes.POINTER(ctypes.c_ulong)]
        k.CloseHandle.argtypes=[ctypes.c_void_p]
        handle=k.OpenProcess(0x1000,False,pid_value)
        if not handle:return False
        try:
            code=ctypes.c_ulong()
            return bool(k.GetExitCodeProcess(handle,ctypes.byref(code))) and code.value==259
        finally:k.CloseHandle(handle)
    try:os.kill(pid_value,0);return True
    except (ProcessLookupError,PermissionError):return False

class ScopedControl:
    def __init__(self,directory,owner,settings,pp_getter=None):
        if not owner:raise ValueError('owner required')
        self.root=Path(directory).resolve();self.root.mkdir(parents=True,exist_ok=True,mode=0o700)
        self.owner=owner;self.settings=settings;self.engine=settings.engine_base_id
        self.pp_getter=pp_getter;self.thread_lock=threading.RLock()

    @contextlib.contextmanager
    def locked(self):
        with self.thread_lock, (self.root/'control.lock').open('a+b') as f:
            if os.name=='nt':
                import msvcrt
                f.seek(0,2)
                if f.tell()==0:f.write(b'0');f.flush()
                f.seek(0);msvcrt.locking(f.fileno(),msvcrt.LK_LOCK,1)
            else:
                import fcntl
                fcntl.flock(f,fcntl.LOCK_EX)
            try:yield
            finally:
                if os.name=='nt':f.seek(0);msvcrt.locking(f.fileno(),msvcrt.LK_UNLCK,1)
                else:fcntl.flock(f,fcntl.LOCK_UN)

    def scope(self):return dict(owner=self.owner,deployment_id=self.settings.deployment_id,
        engine_base_id=self.engine,node_id=self.settings.node_id)
    def read(self):
        path=self.root/'state.json'
        s=json.loads(path.read_text()) if path.exists() else dict(scope=self.scope(),registry={},active=None,used_arms=[])
        if s['scope']!=self.scope():raise ValueError('LOCAL_CONTROL_SCOPE_MISMATCH')
        return s
    def write(self,s):
        p=self.root/('state.'+uuid.uuid4().hex+'.tmp')
        with p.open('w',encoding='utf-8',newline='\n') as f:
            json.dump(s,f,sort_keys=True);f.flush();os.fsync(f.fileno())
        os.replace(p,self.root/'state.json')
        if os.name!='nt':
            fd=os.open(self.root,os.O_RDONLY)
            try:os.fsync(fd)
            finally:os.close(fd)
    def event(self,name,identity=None,arm=None,**kw):
        row=dict(schema_version=2,event_id=uuid.uuid4().hex,timestamp=time.time(),event=name,
            owner=self.owner,arm_id=None,manifest_sha256=None,deployment_id=self.settings.deployment_id,
            engine_base_id=self.engine,node_id=self.settings.node_id,runtime_engine_id=None,hostname=None,
            dp_rank=None,pp_rank=None,tp_rank=None,tp_size=None,pid=os.getpid(),
            request_id=None,remote_request_id=None)
        if identity:row.update(identity)
        if arm:row.update(arm_id=arm['arm_id'],manifest_sha256=digest(arm))
        row.update(kw)
        with (self.root/'events.jsonl').open('a',encoding='utf-8',newline='\n') as f:
            f.write(json.dumps(row,sort_keys=True)+'\n');f.flush();os.fsync(f.fileno())
    def emit(self,event,**kw):
        with self.locked():self.event(event,**kw)
    def identity(self,r):return identify(r,self.settings,self.pp_getter)
    def basic_reject(self,i):
        if i['role']!='decode':return 'reject_wrong_role'
        if i['deployment_id']!=self.settings.deployment_id:return 'reject_wrong_deployment'
        if i['engine_base_id']!=self.engine or not engine_matches(self.engine,i['runtime_engine_id']):return 'reject_wrong_engine'
        if i['node_id']!=self.settings.node_id:return 'reject_wrong_node'
        if any(type(i[k]) is not int or i[k]<0 for k in ('dp_rank','pp_rank','tp_rank','tp_size')) or not i['tp_size'] or i['tp_rank']>=i['tp_size']:return 'reject_wrong_rank'
    def register(self,r):
        i=self.identity(r)
        with self.locked():
            s=self.read();reason=self.basic_reject(i)
            if reason:self.event(reason,i);return False
            old=s['registry'].get(i['participant_id'])
            if old and old['session_id']==i['session_id'] and (old['pid'],old['runtime_engine_id'])!=(i['pid'],i['runtime_engine_id']):
                self.event('reject_duplicate_participant_process',i);return False
            s['registry'][i['participant_id']]=i;self.write(s);self.event('installed',i)
            return True
    def arm_local(self,m):
        validate(m)
        with self.locked():
            s=self.read()
            if m['deployment_id']!=self.settings.deployment_id or m['engine_base_id']!=self.engine:raise ValueError('wrong manifest scope')
            if not m['created_at']<=time.time()<m['expires_at']:raise ValueError('expired/future manifest; check node clocks')
            if s['active'] and s['active']['enabled']:raise RuntimeError('already armed; disarm first')
            if m['arm_id'] in s['used_arms']:raise RuntimeError('arm replay refused')
            wanted=[pid(m,p) for p in m['participants'] if p['node_id']==self.settings.node_id]
            if not wanted:raise ValueError('node not targeted')
            if any(k not in s['registry'] for k in wanted):raise RuntimeError('TOPOLOGY_INCOMPLETE')
            regs={k:s['registry'][k] for k in wanted}
            if any(not process_alive(i['pid']) for i in regs.values()):raise RuntimeError('TOPOLOGY_STALE_PROCESS')
            if any(i['tp_size']!=m['expected_tp_size'] for i in regs.values()):raise RuntimeError('TOPOLOGY_TP_SIZE_MISMATCH')
            if len({i['session_id'] for i in regs.values()})!=1:raise RuntimeError('TOPOLOGY_MIXED_RESTART')
            a=dict(manifest=m,enabled=True,registrations=regs,claims={},overrides={},bound_request=None)
            s['active']=a;s['used_arms'].append(m['arm_id']);self.write(s);self.event('armed',arm=m,participants=wanted)
            return self.status_value(s)
    def status_value(self,s):
        a=s['active']
        if not a:return dict(status='DISARMED',installed=list(s['registry'].values()))
        complete=set(a['overrides'])==set(a['registrations'])
        expired=time.time()>=a['manifest']['expires_at']
        status='LOCAL_COMPLETED' if complete else 'PARTIAL_EXPIRED' if expired else 'ARMED' if a['enabled'] else 'DISARMED'
        return dict(status=status,arm_id=a['manifest']['arm_id'],enabled=a['enabled'] and not expired,
            local_completed=complete,claimed=len(a['claims']),overridden=len(a['overrides']),
            expected=len(a['registrations']),bound_request=a['bound_request'],installed=list(s['registry'].values()))
    def status(self):
        with self.locked():return self.status_value(self.read())
    def disarm(self):
        with self.locked():
            s=self.read();a=s['active']
            if a:a['enabled']=False;self.write(s)
            self.event('disarmed',arm=a['manifest'] if a else None)
    def claim(self,r,meta):
        i=self.identity(r)
        with self.locked():
            s=self.read();a=s['active'];m=a['manifest'] if a else None
            reason=self.basic_reject(i)
            if reason:self.event(reason,i,m);return None
            if not a or not a['enabled']:return None
            if time.time()>=m['expires_at']:self.event('expired',i,m);return None
            key=i['participant_id']
            if i['node_id'] not in {p['node_id'] for p in m['participants']}:reason='reject_wrong_node'
            elif key not in a['registrations'] or i['tp_size']!=m['expected_tp_size']:reason='reject_wrong_rank'
            elif any(i[k]!=a['registrations'][key][k] for k in ('pid','session_id','runtime_engine_id')):reason='reject_stale_registration'
            if reason:self.event(reason,i,m);return None
            request=(meta.get('request_id'),meta.get('remote_request_id'))
            if not all(isinstance(v,str) and v for v in request) or not meta.get('local_block_ids') or not meta.get('remote_block_ids'):
                self.event('reject_empty_transfer',i,m);return None
            selector=m['request_selector']
            if (selector['mode']=='exact' and selector['request_id']!=request[0]) or (a['bound_request'] and a['bound_request']!=list(request)):
                self.event('reject_wrong_request',i,m,request_id=request[0],remote_request_id=request[1]);return None
            if key in a['claims']:self.event('duplicate_claim',i,m,request_id=request[0],remote_request_id=request[1]);return None
            a['bound_request']=list(request)
            claim=dict(mode='RET_NEG1',arm_id=m['arm_id'],claim_id=uuid.uuid4().hex,
                identity=i,request_id=request[0],remote_request_id=request[1])
            a['claims'][key]=claim;self.write(s)
            self.event('claimed',i,m,claim_id=claim['claim_id'],request_id=request[0],remote_request_id=request[1])
            return claim
    def confirm_override(self,claim):
        with self.locked():
            s=self.read();a=s['active'];i=claim['identity'];key=i['participant_id']
            if not a or a['manifest']['arm_id']!=claim['arm_id']:return False
            m=a['manifest']
            if not a['enabled'] or time.time()>=m['expires_at']:
                self.event('override_cancelled',i,m,claim_id=claim['claim_id']);return False
            if a['claims'].get(key)!=claim or key in a['overrides']:return False
            a['overrides'][key]=claim;self.write(s)
            self.event('return_override',i,m,ret=-1,claim_id=claim['claim_id'],
                request_id=claim['request_id'],remote_request_id=claim['remote_request_id'],fault_injected=True)
            if set(a['overrides'])==set(a['registrations']):
                self.event('local_completed',i,m,participants=sorted(a['overrides']),
                    request_id=claim['request_id'],remote_request_id=claim['remote_request_id'])
            return True
