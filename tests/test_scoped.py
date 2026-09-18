import json
import logging
import os
import subprocess
import sys
import multiprocessing as mp
from pathlib import Path
import tempfile
import time
from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch
from pd_kv_fault.identity import Settings,engine_matches,identify
from pd_kv_fault.manifest import plan,validate
from pd_kv_fault.scoped import ScopedControl
from pd_kv_fault.aggregate import aggregate,read_events
from pd_kv_fault.runtime import Binding
import fixture

UUID1='a'*32
def receiver(tp=0,size=2,engine='D0-'+UUID1,role='kv_consumer'):
    return NS(local_engine_id=engine,tp_rank=tp,tp_size=size,pp_rank=0,
        vllm_config=NS(kv_transfer_config=NS(kv_role=role),parallel_config=NS(data_parallel_rank=0)))
def meta(request='X'):
    return dict(request_id=request,remote_request_id='P-'+request,local_block_ids=[1],remote_block_ids=[2])
def settings(node='master',session='s1'):
    return Settings('deployment','D0',node,session)
def participants(nodes=('master',),size=2):
    return [dict(node_id=node,dp_rank=0,pp_rank=0,tp_rank=t) for node in nodes for t in range(size)]
def make_plan(parts=None,size=2):
    return plan('deployment','D0',parts or participants(),size,isolated=True)

def worker(path,node,tp,size,ready,go,done):
    try:
        c=ScopedControl(path,'owner',settings(node));native=fixture.module();r,request=fixture.receiver(native)
        r.tp_rank=tp;r.tp_size=size;r.pp_rank=0
        r.vllm_config.kv_transfer_config=NS(kv_role='kv_consumer')
        r.vllm_config.parallel_config=NS(data_parallel_rank=0)
        r.local_engine_id='D0-'+UUID1
        r.kv_caches_base_addr[r.local_engine_id]=r.kv_caches_base_addr.pop('D0')
        log=logging.FileHandler(Path(path)/f'native_tp{tp}.log',encoding='utf-8')
        native.logger.addHandler(log);native.logger.propagate=False
        with patch.dict('os.environ',{'VLLM_SERVER_DEV_MODE':'1'}):
            binding=Binding(native,c)
        assert c.register(r)
        ready.put((node,tp));assert go.wait(15)
        fixture.handle(r,request)
        assert r.engine.calls==0 and bytes(r.engine.dst)==bytes([205]*16)
        fixture.handle(r,request)  # duplicate rank uses the real host fixture
        assert r.engine.calls==1 and bytes(r.engine.dst)==bytes(range(16))
        assert c.claim(r,request) is None
        native.logger.removeHandler(log);log.close()
        done.put((node,tp,'PASS'))
    except BaseException as exc:
        done.put((node,tp,repr(exc)));raise

def process_matrix(root,nodes=('master',),size=2,layout=None):
    ctx=mp.get_context('spawn');ready=ctx.Queue();done=ctx.Queue();go=ctx.Event();ps=[]
    try:
        layout=layout or {n:list(range(size)) for n in nodes}
        for n in nodes:
            for t in layout[n]:
                p=ctx.Process(target=worker,args=(str(Path(root)/n),n,t,size,ready,go,done));p.start();ps.append(p)
        for _ in ps:ready.get(timeout=20)
        parts=[dict(node_id=n,dp_rank=0,pp_rank=0,tp_rank=t) for n in nodes for t in layout[n]]
        m=make_plan(parts,size)
        for n in nodes:ScopedControl(Path(root)/n,'owner',settings(n)).arm_local(m)
        go.set()
        rows=[done.get(timeout=20) for _ in ps]
        for p in ps:p.join(15);assert p.exitcode==0,p.exitcode
        assert all(r[2]=='PASS' for r in rows),rows
        result=aggregate(m,read_events([Path(root)/n/'events.jsonl' for n in nodes]))
        assert result['status']=='GLOBAL_COMPLETED',result
        return m,result,rows
    finally:
        for p in ps:
            if p.is_alive():p.terminate();p.join()

class ScopeTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.c=ScopedControl(self.tmp.name,'owner',settings())
        self.r0=receiver();self.r1=receiver(1)
        self.c.register(self.r0);self.c.register(self.r1)
        self.m=make_plan()
    def events(self):return read_events([Path(self.tmp.name)/'events.jsonl'])
    def arm(self):self.c.arm_local(self.m)
    def hit(self,r,request='X'):
        cl=self.c.claim(r,meta(request));self.assertIsNotNone(cl);self.assertTrue(self.c.confirm_override(cl));return cl
    def test_engine_matching(self):
        for v in ('D0','D0-'+UUID1,'D0-'+'b'*32):self.assertTrue(engine_matches('D0',v))
        for v in ('D00-'+UUID1,'D01-'+UUID1,'myD0-'+UUID1,'D0-test','D0-other','D0-'+'A'*32,'D0-'+'a'*31):self.assertFalse(engine_matches('D0',v))
    def test_tp2_completion(self):
        self.arm();self.hit(self.r0)
        self.assertFalse(self.c.status()['local_completed']);self.hit(self.r1)
        self.assertTrue(self.c.status()['local_completed'])
        self.assertEqual(aggregate(self.m,self.events())['status'],'GLOBAL_COMPLETED')
    def test_duplicate(self):
        self.arm();cl=self.hit(self.r0)
        self.assertIsNone(self.c.claim(self.r0,meta()));self.assertFalse(self.c.confirm_override(cl))
        self.assertIn('duplicate_claim',[e['event'] for e in self.events()])
    def test_wrong_request(self):
        self.arm();self.hit(self.r0)
        self.assertIsNone(self.c.claim(self.r1,meta('Y')))
        self.assertEqual(self.events()[-1]['event'],'reject_wrong_request')
    def test_wrong_engine(self):
        self.arm();self.assertIsNone(self.c.claim(receiver(engine='D1-'+UUID1),meta()))
        self.assertEqual(self.events()[-1]['event'],'reject_wrong_engine')
    def test_role_crosscheck(self):
        self.arm();self.assertIsNone(self.c.claim(receiver(role='kv_producer'),meta()))
        self.assertEqual(self.events()[-1]['event'],'reject_wrong_role')
    def test_wrong_node(self):
        self.arm();real=self.c.identity(self.r0);real['node_id']='wrong'
        with patch.object(self.c,'identity',return_value=real):self.assertIsNone(self.c.claim(self.r0,meta()))
        self.assertEqual(self.events()[-1]['event'],'reject_wrong_node')
    def test_wrong_deployment(self):
        self.arm();real=self.c.identity(self.r0);real['deployment_id']='other'
        with patch.object(self.c,'identity',return_value=real):self.assertIsNone(self.c.claim(self.r0,meta()))
        self.assertEqual(self.events()[-1]['event'],'reject_wrong_deployment')
    def test_size_mismatch(self):
        self.arm();self.r0.tp_size=4
        self.assertIsNone(self.c.claim(self.r0,meta()))
        self.assertEqual(self.events()[-1]['event'],'reject_wrong_rank')
    def test_unknown_rank(self):
        r=receiver();del r.pp_rank
        i=identify(r,settings());self.assertIsNone(i['pp_rank']);self.assertFalse(self.c.register(r))
    def test_topology_incomplete(self):
        self.m['expected_tp_size']=3;self.m['participants'].append(dict(node_id='master',dp_rank=0,pp_rank=0,tp_rank=2))
        with self.assertRaisesRegex(RuntimeError,'TOPOLOGY_INCOMPLETE'):self.arm()
    def test_expiry(self):
        self.arm();self.hit(self.r0)
        with patch('pd_kv_fault.scoped.time.time',return_value=self.m['expires_at']+1):
            self.assertIsNone(self.c.claim(self.r1,meta()));self.assertEqual(self.c.status()['status'],'PARTIAL_EXPIRED')
        self.assertEqual(aggregate(self.m,self.events(),now=self.m['expires_at']+1)['status'],'PARTIAL_EXPIRED')
    def test_disarm_before_override(self):
        self.arm();cl=self.c.claim(self.r0,meta());self.c.disarm()
        self.assertFalse(self.c.confirm_override(cl))
    def test_restart_after_disarm(self):
        self.arm();self.hit(self.r0);self.c.disarm()
        new=ScopedControl(self.tmp.name,'owner',settings(session='s2'))
        r0=receiver(engine='D0-'+'b'*32);r1=receiver(1,engine='D0-'+'b'*32)
        new.register(r0);new.register(r1)
        new.arm_local(make_plan());self.assertIsNotNone(new.claim(r0,meta('new')))
    def test_restart_during_arm(self):
        self.arm();self.r0.local_engine_id='D0-'+'b'*32
        self.assertIsNone(self.c.claim(self.r0,meta()))
        self.assertEqual(self.events()[-1]['event'],'reject_stale_registration')
    def test_mixed_restart(self):
        new=ScopedControl(self.tmp.name,'owner',settings(session='s2'));new.register(receiver(engine='D0-'+'b'*32))
        with self.assertRaisesRegex(RuntimeError,'MIXED_RESTART'):new.arm_local(self.m)
    def test_replay(self):
        self.arm();self.c.disarm()
        with self.assertRaisesRegex(RuntimeError,'replay'):self.arm()
    def test_tp1(self):
        with tempfile.TemporaryDirectory() as d:
            c=ScopedControl(d,'owner',settings());r=receiver(size=1);c.register(r)
            m=make_plan(participants(size=1),1);c.arm_local(m);c.confirm_override(c.claim(r,meta()))
            self.assertTrue(c.status()['local_completed'])
    def test_exact_selector(self):
        self.m['request_selector']=dict(mode='exact',request_id='Y');self.arm()
        self.assertIsNone(self.c.claim(self.r0,meta('X')));self.hit(self.r0,'Y')
    def test_multiprocessing_tp2(self):
        with tempfile.TemporaryDirectory() as d:process_matrix(d)
    def test_multinode_four_and_partial(self):
        with tempfile.TemporaryDirectory() as d:
            m,result,rows=process_matrix(d,('nodeA','nodeB'),4,dict(nodeA=[0,1],nodeB=[2,3]))
            events=read_events([Path(d)/n/'events.jsonl' for n in ('nodeA','nodeB')])
            reduced=[e for e in events if not(e['event']=='return_override' and e['node_id']=='nodeB' and e['tp_rank']==3)]
            self.assertEqual(aggregate(m,reduced)['status'],'PARTIAL')
            self.assertEqual(result['overridden'],4)
    def test_aggregator_does_not_count_forged_or_mixed(self):
        self.arm();self.hit(self.r0);self.hit(self.r1);events=self.events()
        for e in events:
            if e['event']=='return_override' and e['tp_rank']==1:e['request_id']='Y'
        self.assertEqual(aggregate(self.m,events)['status'],'PARTIAL')
    def test_claim_without_override(self):
        self.arm();self.c.claim(self.r0,meta());self.c.claim(self.r1,meta())
        self.assertEqual(aggregate(self.m,self.events())['overridden'],0)
    def test_aggregator_duplicate_input_files(self):
        self.arm();self.hit(self.r0);self.hit(self.r1)
        self.assertEqual(aggregate(self.m,self.events()*2)['status'],'GLOBAL_COMPLETED')
    def test_cli_plan_arm_aggregate(self):
        path=Path(self.tmp.name)/'plan.json'
        env=dict(os.environ,PD_FAULT_DIR=self.tmp.name,PD_FAULT_OWNER='owner',
            PD_FAULT_DEPLOYMENT_ID='deployment',PD_FAULT_ENGINE_BASE='D0',PD_FAULT_NODE_ID='master')
        def cli(*args):
            p=subprocess.run([sys.executable,'-m','pd_kv_fault',*args],env=env,capture_output=True,text=True)
            self.assertEqual(p.returncode,0,p.stderr);return json.loads(p.stdout)
        cli('plan','--participant','master:dp0:pp0:tp0','--participant','master:dp0:pp0:tp1',
            '--expected-tp-size','2','--isolated-single-request','--output',str(path))
        cli('arm-local','--manifest',str(path));self.hit(self.r0);self.hit(self.r1)
        result=cli('aggregate','--manifest',str(path),str(Path(self.tmp.name)/'events.jsonl'))
        self.assertEqual(result['status'],'GLOBAL_COMPLETED')
        cli('disarm');result=cli('arm','--all-tp-ranks','--expected-tp-size','2','--isolated-single-request')
        self.assertEqual(result['expected'],2)
    def test_stale_registration_process(self):
        with patch('pd_kv_fault.scoped.process_alive',return_value=False):
            with self.assertRaisesRegex(RuntimeError,'STALE_PROCESS'):self.arm()

if __name__=='__main__':unittest.main(verbosity=2)
