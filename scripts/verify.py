"""Run offline tests and preserve real pinned-method log-chain evidence."""
import hashlib
import io
import json
import logging
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'tests')]
import fixture
from pd_kv_fault.control import Control
from pd_kv_fault.runtime import Binding,SOURCE_SHA256

out=ROOT/'evidence'
out.mkdir(exist_ok=True)
test=subprocess.run([sys.executable,'-m','unittest','discover','-s','tests','-v'],
    cwd=ROOT,stdout=subprocess.PIPE,stderr=subprocess.STDOUT)
(out/'tests.log').write_bytes(test.stdout)
assert test.returncode==0,test.stdout.decode(errors='replace')
native=fixture.module();receiver,meta=fixture.receiver(native)
stream=io.StringIO();handler=logging.StreamHandler(stream)
handler.setFormatter(logging.Formatter('%(levelname)s %(filename)s:%(lineno)d %(message)s'))
native.logger.addHandler(handler)
with tempfile.TemporaryDirectory() as tmp,patch.dict(os.environ,{'VLLM_SERVER_DEV_MODE':'1'}):
    c=Control(tmp,'offline-demo','D0');binding=Binding(native,c);rows=[]
    try:
        for label in ('B','F','R'):
            for i in range(16):receiver.engine.dst[i]=205
            if label=='F':c.arm(request_id='D-request')
            if label=='R':binding.uninstall()
            before=receiver.engine.calls
            fixture.handle(receiver,meta)
            rows.append(dict(phase=label,transport_fixture_calls=receiver.engine.calls-before,
                receiver_bytes=list(receiver.engine.dst),
                finished_requests=sorted(receiver.task_tracker.get_and_clear_finished_requests())))
        assert [r['transport_fixture_calls'] for r in rows]==[1,0,1]
        assert rows[0]['receiver_bytes']==rows[2]['receiver_bytes']!=rows[1]['receiver_bytes']
        (out/'events.jsonl').write_bytes((Path(tmp)/'events.jsonl').read_bytes())
    finally:
        if native.KVCacheRecvingThread._transfer_kv_cache is binding.wrapper:binding.uninstall()
native.logger.removeHandler(handler)
(out/'native_chain.log').write_text(stream.getvalue(),encoding='utf-8')
for text in ('Mooncake transfer failed for request','Failed to transfer KV cache for request',
             'RuntimeError: Mooncake transfer failed, ret: -1','fault_injected=true'):
    assert text in stream.getvalue()
summary=dict(tests_passed=19,source_sha256=SOURCE_SHA256,
    evidence_level='PINNED_NATIVE_METHODS_HOST_FIXTURE',ret_neg1_native_log_chain='PASS',
    bfr=rows,model_e2e='NOT_TESTED',current_github_main='UNSUPPORTED',
    old_handler_still_marks_finished=True)
(out/'summary.json').write_text(json.dumps(summary,indent=2),encoding='utf-8')
hashes={str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest()
    for p in ROOT.rglob('*') if p.is_file() and '__pycache__' not in p.parts
    and p.name!='sha256.json' and '.git' not in p.parts}
(out/'sha256.json').write_text(json.dumps(hashes,indent=2),encoding='utf-8')
print(json.dumps(summary,indent=2))
