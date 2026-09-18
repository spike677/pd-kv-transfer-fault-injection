"""Read-only capture of this task's isolated CPU test container."""
import json
from pathlib import Path
import subprocess

root=Path('/home/haoran/pd_scope_cpu_20260918/evidence')
cmd=['docker','-H','unix:///run/fault26-docker-20260817.sock']
name='pd-scope-cpu-20260918'
info=json.loads(subprocess.check_output(cmd+['inspect',name]))[0]
assert not info['State']['Running'] and info['State']['ExitCode']==0
assert info['HostConfig']['NetworkMode']=='none'
assert not info['HostConfig']['Devices']
(root/'linux_tests.log').write_bytes(subprocess.check_output(cmd+['logs',name],stderr=subprocess.STDOUT))
(root/'linux_container.json').write_text(json.dumps(dict(state=info['State'],
    network_mode=info['HostConfig']['NetworkMode'],devices=info['HostConfig']['Devices'],
    image=info['Image'],evidence_level='CPU_ONLY_NO_NPU'),indent=2))
