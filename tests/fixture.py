"""Unchanged pinned native AST methods + explicit host-only transport fixture."""
import ast
from collections import OrderedDict
import ctypes
import hashlib
import logging
from pathlib import Path
import queue
import threading
import time
import types
import numpy as np
from pd_kv_fault.runtime import SOURCE_SHA256

SOURCE=Path(__file__).parent/'source_snapshot/mooncake_connector.py'
def module():
    assert hashlib.sha256(SOURCE.read_bytes()).hexdigest()==SOURCE_SHA256
    selected=[]
    for node in ast.parse(SOURCE.read_text()).body:
        if isinstance(node,ast.ClassDef) and node.name=='KVCacheTaskTracker':selected.append(node)
        if isinstance(node,ast.ClassDef) and node.name=='KVCacheRecvingThread':
            selected.append(ast.ClassDef(name=node.name,bases=[],keywords=[],decorator_list=[],body=[m for m in node.body
                if isinstance(m,ast.FunctionDef) and m.name in ('_handle_request','_transfer_kv_cache','_send_done_signal_to_free_remote_port')]))
        if isinstance(node,ast.FunctionDef) and node.name=='group_concurrent_contiguous':selected.append(node)
    m=types.ModuleType('pinned_native');m.__file__=str(SOURCE.resolve())
    m.__dict__.update(threading=threading,time=time,OrderedDict=OrderedDict,np=np,
        logger=logging.getLogger('native_connector_fixture'),get_ip=lambda:'127.0.0.1',
        get_ascend_config=lambda:types.SimpleNamespace(enable_kv_nz=False),
        ascend_envs=types.SimpleNamespace(VLLM_ASCEND_FUSION_OP_TRANSPOSE_KV_CACHE_BY_BLOCK=False))
    tree=ast.fix_missing_locations(ast.Module(body=[ast.ImportFrom(module='__future__',names=[ast.alias(name='annotations')],level=0)]+selected,type_ignores=[]))
    exec(compile(tree,str(SOURCE.resolve()),'exec'),m.__dict__)
    return m

class HostEngine:
    def __init__(self,src,dst):self.src,self.dst,self.calls=src,dst,0
    def batch_transfer_sync_read(self,session,local,remote,lengths):
        assert local==[ctypes.addressof(self.dst)] and remote==[ctypes.addressof(self.src)] and lengths==[16]
        self.calls+=1;ctypes.memmove(local[0],remote[0],16);return 0

def receiver(m):
    r=m.KVCacheRecvingThread.__new__(m.KVCacheRecvingThread)
    src=(ctypes.c_ubyte*16)(*range(16));dst=(ctypes.c_ubyte*16)(*([205]*16))
    r.__dict__.update(local_engine_id='D0',local_handshake_port=17002,side_channel_port=17002,
        tp_rank=0,_prefill_pp_size=1,block_len=[16],pp_layer_indices={0:(0,1)},
        kv_caches={'layer':[None]},remote_te_port={'P0':{17001:17001}},
        kv_caches_base_addr={'D0':{17002:[ctypes.addressof(dst)]},'P0':{17001:[ctypes.addressof(src)]}},
        vllm_config=types.SimpleNamespace(speculative_config=None),proc_not_transfer_request={},
        task_tracker=m.KVCacheTaskTracker(),request_queue=queue.Queue(),engine=HostEngine(src,dst))
    r._send_done_recv_signal=lambda *a:None
    meta=dict(request_id='D-request',remote_request_id='P-request',remote_host='127.0.0.1',
        remote_handshake_port=17001,remote_engine_id='P0',local_block_ids=[0],remote_block_ids=[0],
        remote_port_send_num={},all_task_done=True,offset=0,tp_num_need_pulls=1)
    return r,meta

def handle(r,meta):
    r.task_tracker.add_req_to_process(meta['request_id']);r.request_queue.put(meta)
    r._handle_request(r.request_queue.get())
    assert r.request_queue.unfinished_tasks==0
