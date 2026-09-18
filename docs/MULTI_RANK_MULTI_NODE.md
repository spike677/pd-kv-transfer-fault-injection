# v0.2：node-local multi-rank control

**离线控制/原生方法夹具已验证；A3在线E2E仍BLOCKED。** 多机是模拟，不是硬件验证。
不修改vLLM、vLLM-Ascend、Mooncake或网络，不杀P，不增加runtime HTTP服务。

## 身份与隔离

```text
deployment / engine_base / node / dp / pp / tp / request
```

runtime identity还记录runtime_engine_id、tp_size、pid、hostname、session_id及字段来源。
engine只接受精确base或 `base-32个小写hex`；拒绝D00、D01、D0-test等前缀误匹配。
UUID重启后可改变。TP直接读取receiver.tp_rank/tp_size；DP取runtime parallel_config；
PP优先receiver/已初始化的get_pp_group，再使用显式环境变量。未知rank不注入，不猜0。
kv_role读取真实config，producer不能由错误的decode环境变量覆盖；config不可得时才使用显式role。

五层门禁是role、deployment、engine、node/rank、request。每个rank各claim一次；
第一个rank不会删除manifest。第一次claim绑定**D request_id和remote_request_id**，
其余rank必须相同。只有每个目标participant都有实际return_override=-1才算完成。

`GLOBAL_COMPLETED`只表示manifest内目标全部命中，不代表物理链路中断或模型退化。
若manifest只选一个rank，不能把它称作整个TP组故障。

## 当前单机TP2命令

在D的实际worker环境中设置（模型TP/DP及P配置保持原样）：

```bash
export PD_FAULT_DIR=/experiment/pd-control/qwen32/D0/master
export PD_FAULT_OWNER=experiment-001
export PD_FAULT_DEPLOYMENT_ID=qwen32-a3-pd
export PD_FAULT_NODE_ID=master
export PD_FAULT_ENGINE_BASE=D0
export PD_FAULT_ROLE=decode
```

`bash scripts/run_decode.sh` 前缀仍叠加到**已有完整D启动命令**，不更换connector。
wrapper每次生成新的PD_FAULT_SESSION_ID，并由所有子worker继承。不要在各rank
分别生成不同session。多机各节点可有自己的session，但同节点rank必须共享。
若平台已在别的节点启动worker，必须在那个节点也部署hook、设置其NODE_ID和本地目录。

PP/DP正常应由runtime解析；确实无法读取且已知布局时才显式设置
`PD_FAULT_DP_RANK` / `PD_FAULT_PP_RANK`。不要把同一环境值盲目分发给不同DP/PP组。

服务健康且实际构造两个receiver后，检查：

```bash
python -m pd_kv_fault status-local
```

必须看到两个 `installed`，tp0/tp1、两个实际worker PID、tp_size=2、正确base/runtimeUUID。
安装hook本身只记录 `hook_ready`，**不能代替rank安装证据**。
arm-local检查PID仍存活、全部rank已注册、TP size一致、同节点session一致。
注册旧PID/混合重启无法通过；arm后更换PID/UUID不能继承旧claim。

先跑DISARMED请求：真实两rank传输、HTTP200、没有claim/override，再执行：

```bash
python -m pd_kv_fault arm \
  --all-tp-ranks --expected-tp-size 2 \
  --isolated-single-request --ttl 120 --output arm.json
```

若只有一个已安装DP/PP组，CLI从注册数据选择该组；否则必须显式
`--dp-rank N --pp-rank N`。不是默认猜dp0/pp0。
现有arm须先disarm；已使用arm_id不能重放。第二次实验必须生成新plan。
输出文件必须不存在，推荐每次独立实验目录。

只向实验P/D代理发一条请求，然后：

```bash
python -m pd_kv_fault status-local
python -m pd_kv_fault aggregate --manifest arm.json "$PD_FAULT_DIR/events.jsonl"
python -m pd_kv_fault disarm-local
```

tp0/tp1都override才是LOCAL_COMPLETED和GLOBAL_COMPLETED。只有claim没有override、
异常提前发生、一个rank未到达，均不能通过；过期但未齐全是PARTIAL_EXPIRED。
不自动等到另一个rank才返回第一个错误；这不是分布式事务。如果第一rank故障阻止
其他rank到达，结果必须保留为PARTIAL，不改写为成功。

## 多节点：一个plan，各节点独立落盘

```bash
python -m pd_kv_fault plan \
  --deployment qwen32-prod --engine-base D0 --expected-tp-size 4 \
  --participant dnode-a:dp0:pp0:tp0 --participant dnode-a:dp0:pp0:tp1 \
  --participant dnode-b:dp0:pp0:tp2 --participant dnode-b:dp0:pp0:tp3 \
  --request-id ACTUAL_D_REQUEST_ID --ttl 120 --output arm.json
```

复制完全相同的manifest给两节点；每台的DIR位于本机文件系统、NODE_ID不同。
各自执行 `python -m pd_kv_fault arm-local --manifest arm.json`。
正确性不依赖SSH，核心不存储SSH凭据、不发网络请求。

每节点只管理本节点participant。**不要共享NFS/CIFS上的控制目录**。
Linux用flock、Windows测试用字节区间锁，更新采用临时文件/fsync/replace；事件append也在锁内。
state内保存immutable manifest、注册快照、bound request、各participant claim/override。
控制目录同时有owner和deployment/engine/node作用域；不能借用其他节点目录。

收集事件到独立审计目录：

```bash
python -m pd_kv_fault aggregate --manifest arm.json node-a/events.jsonl node-b/events.jsonl
```

汇总核对manifest SHA、arm_id、participant、role、严格engine、TP size、请求、
claim_id、PID、session、事件先后和TTL。重复输入同一event_id去重；不同事件的重复
override拒绝。4/4为GLOBAL_COMPLETED，3/4为PARTIAL，并列缺失和reject原因。
各节点需同步时钟，TTL以manifest绝对时间为准；过期或尚未来到created_at的plan拒绝arm。

## 请求与流量隔离

实验proxy必须只注册目标P和D0，或证明router可确定pin到D0。
普通proxy若随机路由到D1/D2，不能据未命中判断注入器失败。
D1/D2不加wrapper；即使误加载，engine guard也应拒绝。

next-transfer只限独占单请求。多节点本地首次绑定不是分布式共识：若两节点分别
绑定X/Y，aggregator必须PARTIAL。**多节点＋并发应使用显式request identity和
上游路由隔离**；当前工具没有request ID映射服务，跨节点ID不同需先解决归属证明。

## 恢复与限制

disarm阻止未确认的override；若claim后已disarm/过期，临时engine回到真实读取。
已完成的异常不能撤回。观察同进程请求后，正式恢复要clean D restart并做两rank
真实传输/HTTP验证。不能只靠同进程buffer hash或组件测试宣称模型恢复。

只验证schema v2 RET_NEG1；legacy CONNECTOR_FAIL/单worker接口保留，不支持旧接口TP2。
不支持自动跨节点barrier、事务性全有全无注入、共享目录、多租户路由或任意上游版本。
无中心daemon；registry不是持续heartbeat，arm的存活检查不消除随后worker退出的可能，
缺失participant仍会最终PARTIAL。检查服务健康是在线验收的独立门禁。

## 复核命令

```bash
python -m unittest discover -s tests -v
python scripts/verify_scoped.py
python scripts/verify.py
```

scoped脚本用真实spawn多进程、锁定原生方法和host transport fixture保存完整事件与日志。
Linux/Windows均可运行；不导入torch或Mooncake，不使用NPU。
