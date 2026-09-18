"""Call-local engine facade: does not assign a pybind method or shared engine."""
import functools
import hashlib
import inspect
import os
from pathlib import Path
import threading

SOURCE_SHA256='275bee054b182e1c6177ec6e5c6ce3f0deb6afa7dcd43cd66d9ef274d9231316'
MODULE='vllm_ascend.distributed.kv_transfer.kv_p2p.mooncake_connector'
METHOD='_transfer_kv_cache'


class EngineView:
    def __init__(self,real,control,meta,claim,logger):
        self.real,self.control,self.meta,self.claim,self.logger=real,control,meta,claim,logger
        self.hits=0
    def __getattr__(self,name):return getattr(self.real,name)
    def batch_transfer_sync_read(self,*args,**kwargs):
        self.hits+=1
        if self.hits!=1:raise RuntimeError('unexpected second read on one-shot facade')
        if hasattr(self.control,'confirm_override'):
            if not self.control.confirm_override(self.claim):
                return self.real.batch_transfer_sync_read(*args,**kwargs)
        else:
            self.control.emit('return_override',arm_id=self.claim['arm_id'],ret=-1,
                request_id=self.meta['request_id'],remote_request_id=self.meta['remote_request_id'],fault_injected=True)
        # Only the provenance marker is ours; native transfer/handler code
        # produces both canonical error messages and RuntimeError traceback.
        self.logger.warning('[PD-KV-FAULT] fault_injected=true mode=RET_NEG1 arm_id=%s request_id=%r remote_request_id=%r',
            self.claim['arm_id'],self.meta['request_id'],self.meta['remote_request_id'])
        return -1


class ReceiverView:
    def __init__(self,real,engine):self._real,self.engine=real,engine
    def __getattr__(self,name):return getattr(self._real,name)


class Binding:
    def __init__(self,module,control):
        if os.environ.get('VLLM_SERVER_DEV_MODE')!='1':raise RuntimeError('developer mode required')
        path=Path(module.__file__).resolve()
        if hashlib.sha256(path.read_bytes()).hexdigest()!=SOURCE_SHA256:
            raise RuntimeError('UNSUPPORTED_SOURCE: no bypass; audit and test a new adapter')
        cls=module.KVCacheRecvingThread
        original=getattr(cls,METHOD)
        if getattr(original,'_pd_kv_fault_owner',None):raise RuntimeError('hook already installed')
        if Path(inspect.getsourcefile(original)).resolve()!=path:raise RuntimeError('callable/source mismatch')
        self.cls,self.original,self.control=cls,original,control
        self.lock=threading.RLock();self.inflight=0;self.active=True
        logger=original.__globals__['logger']
        @functools.wraps(original)
        def wrapped(receiver,meta):
            with self.lock:self.inflight+=1;active=self.active
            try:
                try:claim=control.claim(receiver,meta) if active else None
                except (ValueError,OSError,TimeoutError) as exc:
                    logger.warning('[PD-KV-FAULT] control rejected; no injection: %s',exc)
                    claim=None
                if not claim:return original(receiver,meta)
                if claim['mode']=='CONNECTOR_FAIL':
                    logger.warning('[PD-KV-FAULT] fault_injected=true mode=CONNECTOR_FAIL')
                    raise RuntimeError('[PD-KV-FAULT] injected connector failure')
                proxy=EngineView(receiver.engine,control,meta,claim,logger)
                try:return original(ReceiverView(receiver,proxy),meta)
                finally:
                    if proxy.hits==0:
                        control.emit('claimed_but_no_return_override',arm_id=claim['arm_id'])
            finally:
                with self.lock:self.inflight-=1
        wrapped._pd_kv_fault_owner=control.owner
        self.wrapper=wrapped
        setattr(cls,METHOD,wrapped)
        self.original_init=None
        if hasattr(control,'register'):
            self.original_init=cls.__init__
            @functools.wraps(self.original_init)
            def registered_init(receiver,*args,**kwargs):
                self.original_init(receiver,*args,**kwargs)
                control.register(receiver)
            self.init_wrapper=registered_init
            cls.__init__=registered_init
        control.emit('hook_ready' if self.original_init else 'installed',method=METHOD,source_sha256=SOURCE_SHA256)
    def uninstall(self):
        with self.lock:
            if getattr(self.cls,METHOD) is not self.wrapper:raise RuntimeError('ownership lost')
            if self.original_init and self.cls.__init__ is not self.init_wrapper:raise RuntimeError('constructor ownership lost')
            if self.inflight:raise RuntimeError('receive in flight')
            self.control.disarm();self.active=False
            setattr(self.cls,METHOD,self.original)
            if self.original_init:self.cls.__init__=self.original_init
            self.control.emit('uninstalled')
