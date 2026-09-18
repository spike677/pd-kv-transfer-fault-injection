from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from pd_kv_fault.control import Control
from pd_kv_fault.runtime import Binding
import fixture

class Tests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.dev=patch.dict(os.environ,{'VLLM_SERVER_DEV_MODE':'1'});self.dev.start();self.addCleanup(self.dev.stop)
        self.m=fixture.module();self.r,self.meta=fixture.receiver(self.m)
        self.c=Control(self.tmp.name,'owner','D0');self.b=Binding(self.m,self.c)
        self.addCleanup(self.cleanup)
    def cleanup(self):
        (self.c.root/'arm.json').unlink(missing_ok=True)
        if self.m.KVCacheRecvingThread._transfer_kv_cache is self.b.wrapper:self.b.uninstall()
    def test_native_two_errors_and_restore(self):
        original_engine=self.r.engine
        fixture.handle(self.r,self.meta)
        self.assertEqual(bytes(self.r.engine.dst),bytes(range(16)))
        for i in range(16):self.r.engine.dst[i]=205
        self.c.arm(request_id='D-request')
        with self.assertLogs(self.m.logger,level='WARNING') as log:fixture.handle(self.r,self.meta)
        text='\n'.join(log.output)
        for expected in ('fault_injected=true','Mooncake transfer failed for request','Failed to transfer KV cache for request','RuntimeError: Mooncake transfer failed, ret: -1','P-request'):
            self.assertIn(expected,text)
        self.assertEqual(self.r.engine.calls,1);self.assertIs(self.r.engine,original_engine)
        self.assertEqual(bytes(self.r.engine.dst),bytes([205]*16))
        # Pinned version still reports finished, even on error.
        self.assertIn('D-request',self.r.task_tracker.finished_requests)
        self.b.uninstall();fixture.handle(self.r,self.meta)
        self.assertEqual(self.r.engine.calls,2)
        self.assertEqual(bytes(self.r.engine.dst),bytes(range(16)))
        self.assertIs(self.m.KVCacheRecvingThread._transfer_kv_cache,self.b.original)
    def test_next_transfer(self):
        self.c.arm(next_transfer=True)
        with self.assertLogs(self.m.logger,level='WARNING'):fixture.handle(self.r,self.meta)
        fixture.handle(self.r,self.meta)
        self.assertEqual(self.r.engine.calls,1)
    def test_wrong_request(self):
        self.c.arm(request_id='other');fixture.handle(self.r,self.meta)
        self.assertEqual(self.r.engine.calls,1);self.assertTrue(self.c.status()['armed'])
    def test_wrong_engine(self):
        self.c.arm(next_transfer=True);self.r.local_engine_id='other'
        self.assertIsNone(self.c.claim(self.r,self.meta))
    def test_empty_blocks(self):
        self.c.arm(next_transfer=True);self.meta['local_block_ids']=[]
        fixture.handle(self.r,self.meta);self.assertTrue(self.c.status()['armed'])
    def test_owner(self):
        self.c.arm(next_transfer=True)
        with self.assertRaises(RuntimeError):Control(self.tmp.name,'other','D0').disarm()
    def test_expiry(self):
        self.c.arm(next_transfer=True,ttl=1)
        with patch('pd_kv_fault.control.time.time',return_value=1e20):fixture.handle(self.r,self.meta)
        self.assertEqual(self.r.engine.calls,1)
    def test_race(self):
        self.c.arm(next_transfer=True)
        with ThreadPoolExecutor(8) as p:
            results=list(p.map(lambda _:Control(self.tmp.name,'owner','D0').claim(self.r,self.meta),range(32)))
        self.assertEqual(sum(x is not None for x in results),1)
    def test_disarm(self):
        self.c.arm(next_transfer=True);self.c.disarm();fixture.handle(self.r,self.meta)
        self.assertEqual(self.r.engine.calls,1)
    def test_duplicate(self):
        with self.assertRaises(RuntimeError):Binding(self.m,self.c)
    def test_owner_lost(self):
        self.m.KVCacheRecvingThread._transfer_kv_cache=lambda *a:None
        with self.assertRaises(RuntimeError):self.b.uninstall()
    def test_dev_gate(self):
        with patch.dict(os.environ,{'VLLM_SERVER_DEV_MODE':'0'}):
            with self.assertRaises(RuntimeError):Binding(self.m,self.c)
    def test_hash_gate(self):
        self.m.__file__=__file__
        with self.assertRaisesRegex(RuntimeError,'UNSUPPORTED_SOURCE'):Binding(self.m,self.c)
    def test_bootstrap_is_lazy(self):
        p=subprocess.run([sys.executable,'-c',
            'import sys; from pd_kv_fault.bootstrap import activate; activate(); '
            'assert "torch" not in sys.modules; assert "mooncake.engine" not in sys.modules'],
            env=dict(os.environ,PD_FAULT_ENABLE='1',PD_FAULT_SIDE='decode',PD_FAULT_DIR=self.tmp.name,
                     PD_FAULT_OWNER='owner',PD_FAULT_ENGINE='D0'),capture_output=True,text=True)
        self.assertEqual(p.returncode,0,p.stderr)
    def test_invalid_control(self):
        (self.c.root/'arm.json').write_text('{')
        with self.assertLogs(self.m.logger,level='WARNING'):fixture.handle(self.r,self.meta)
        self.assertEqual(self.r.engine.calls,1)
    def test_no_rearm_overwrite(self):
        self.c.arm(next_transfer=True)
        with self.assertRaises(RuntimeError):self.c.arm(next_transfer=True)
    def test_bad_ttl(self):
        with self.assertRaises(ValueError):self.c.arm(next_transfer=True,ttl=float('nan'))
    def test_control_wrong_json_type(self):
        (self.c.root/'arm.json').write_text('[1]')
        fixture.handle(self.r,self.meta)
        self.assertEqual(self.r.engine.calls,1)
    def test_sitecustomize_startup(self):
        root=Path(__file__).resolve().parents[1]
        env=dict(os.environ,PYTHONPATH=os.pathsep.join([str(root/'bootstrap'),str(root)]),
            PD_FAULT_ENABLE='1',PD_FAULT_SIDE='decode',PD_FAULT_DIR=self.tmp.name,
            PD_FAULT_OWNER='owner',PD_FAULT_ENGINE='D0')
        p=subprocess.run([sys.executable,'-c','import sys; from pd_kv_fault.bootstrap import Finder; '
            'assert any(isinstance(f,Finder) for f in sys.meta_path); assert "torch" not in sys.modules'],
            env=env,capture_output=True,text=True)
        self.assertEqual(p.returncode,0,p.stderr)
        env['PD_FAULT_SIDE']='prefill'
        rejected=subprocess.run([sys.executable,'-c','print("should_not_run")'],env=env,capture_output=True,text=True)
        self.assertEqual(rejected.returncode,78)
        self.assertNotIn('should_not_run',rejected.stdout)

if __name__=='__main__':unittest.main(verbosity=2)
