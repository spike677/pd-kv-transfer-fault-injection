import contextlib
import json
import math
import os
from pathlib import Path
import time
import uuid


class Control:
    def __init__(self, directory, owner, engine):
        if not owner or not engine:
            raise ValueError('owner and decoder engine are required')
        self.root=Path(directory).resolve()
        self.root.mkdir(parents=True,exist_ok=True,mode=0o700)
        self.owner,self.engine=owner,engine

    @contextlib.contextmanager
    def locked(self):
        # Cross-process lock, also used by arm/disarm/status. Never remove a
        # stale lock automatically: operator checks process health first.
        path=self.root/'control.lock'
        deadline=time.monotonic()+2
        while True:
            try:path.mkdir();break
            except FileExistsError:
                if time.monotonic()>deadline:raise TimeoutError('control lock busy/stale')
                time.sleep(.01)
        try:yield
        finally:path.rmdir()

    def emit(self,event,**fields):
        with (self.root/'events.jsonl').open('a',encoding='utf-8') as f:
            f.write(json.dumps(dict(event=event,pid=os.getpid(),time_ns=time.time_ns(),
                owner=self.owner,engine=self.engine,**fields),sort_keys=True)+'\n')

    def _read(self):
        p=self.root/'arm.json'
        return json.loads(p.read_text()) if p.exists() else None

    def _owns(self,data):
        return data.get('owner')==self.owner and data.get('engine')==self.engine

    def arm(self,request_id=None,next_transfer=False,ttl=120,mode='RET_NEG1'):
        if bool(request_id)==bool(next_transfer):raise ValueError('choose exact request or next-transfer')
        if not math.isfinite(ttl) or not 0<ttl<=600:raise ValueError('TTL must be 0..600 seconds')
        if mode not in ('RET_NEG1','CONNECTOR_FAIL'):raise ValueError('unknown mode')
        with self.locked():
            if self._read() is not None:raise RuntimeError('already armed; disarm first')
            data=dict(owner=self.owner,engine=self.engine,request_id=request_id,
                next_transfer=next_transfer,expires_at=time.time()+ttl,mode=mode,arm_id=uuid.uuid4().hex)
            tmp=self.root/'arm.tmp'
            tmp.write_text(json.dumps(data),encoding='utf-8');tmp.replace(self.root/'arm.json')
            self.emit('armed',config=data)
            return data

    def status(self):
        with self.locked():
            data=self._read()
            if data and not self._owns(data):raise RuntimeError('owner mismatch')
            return dict(armed=bool(data and data['expires_at']>time.time()),config=data)

    def disarm(self):
        with self.locked():
            data=self._read()
            if data and not self._owns(data):raise RuntimeError('owner mismatch')
            (self.root/'arm.json').unlink(missing_ok=True)
            self.emit('disarmed')

    def claim(self,receiver,meta):
        if getattr(receiver,'tp_size',1)!=1:
            return None  # v1 compatibility is TP1 only; use ScopedControl for TP2.
        if (receiver.local_engine_id!=self.engine or not meta.get('local_block_ids')
            or not meta.get('remote_block_ids') or not meta.get('request_id')
            or not meta.get('remote_request_id')):return None
        with self.locked():
            data=self._read()
            if not isinstance(data,dict) or not data or not self._owns(data):return None
            if not isinstance(data.get('expires_at'),(int,float)) or not math.isfinite(data['expires_at']):return None
            if data['expires_at']<=time.time():return None
            if data.get('mode') not in ('RET_NEG1','CONNECTOR_FAIL'):return None
            if not (data.get('next_transfer') is True or data.get('request_id')==meta['request_id']):return None
            (self.root/'arm.json').unlink()
            self.emit('claimed',arm_id=data['arm_id'],mode=data['mode'],
                request_id=meta['request_id'],remote_request_id=meta['remote_request_id'])
            return data
