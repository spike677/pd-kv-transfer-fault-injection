"""Persistent multiprocessing evidence; host fixtures only, no NPU claims."""
import json
from pathlib import Path
import sys
import time

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'tests')]
from test_scoped import process_matrix
from pd_kv_fault.aggregate import aggregate,read_events

def main():
    root=ROOT/'evidence'/'scoped'/str(time.time_ns())
    root.mkdir(parents=True)
    reports={}
    for label,nodes,size,layout in [('tp2',('master',),2,None),
        ('tp4_two_node_simulation',('nodeA','nodeB'),4,dict(nodeA=[0,1],nodeB=[2,3]))]:
        case=root/label;case.mkdir()
        m,result,rows=process_matrix(case,nodes,size,layout)
        (case/'manifest.json').write_text(json.dumps(m,indent=2))
        (case/'result.json').write_text(json.dumps(dict(result=result,processes=rows),indent=2))
        reports[label]=result
        if size==4:
            events=read_events([case/n/'events.jsonl' for n in nodes])
            partial=[e for e in events if not(e['event']=='return_override' and e['node_id']=='nodeB' and e['tp_rank']==3)]
            reports['tp4_missing_one']=aggregate(m,partial)
            assert reports['tp4_missing_one']['status']=='PARTIAL'
    reports.update(evidence_level='MULTIPROCESS_PINNED_NATIVE_METHODS_HOST_FIXTURE',
        a3_single_node_tp2_e2e='BLOCKED_NO_A3_CONNECTION',multi_node_hardware_e2e='NOT_CLAIMED')
    (root/'summary.json').write_text(json.dumps(reports,indent=2))
    (ROOT/'evidence/scoped/latest.json').write_text(json.dumps(dict(path=str(root.relative_to(ROOT))),indent=2))
    print(json.dumps(reports,indent=2))

if __name__=='__main__':main()
