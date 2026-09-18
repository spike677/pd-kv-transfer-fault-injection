# 使用速查（叠加到已有正常 D 服务）

前置条件：独占开发服务、单D worker、并发1、无在途请求、正常P→D传输已验证。
本仓库的在线启动路径仍待现场验证。不要跳过正常基线，直接用故障日志宣称成功。

## 1. 项目与控制目录

将独立仓库克隆/复制到D worker环境。Docker需额外挂载两项：

```text
项目目录 → /opt/pd-kv-fault（只读即可）
本次控制和证据目录 → /experiment/pd-control（可写、私有）
```

在**D容器内**执行（P容器不挂hook）：

```bash
cd /opt/pd-kv-fault
export PD_FAULT_DIR=/experiment/pd-control
export PD_FAULT_OWNER=experiment-001
export PD_FAULT_ENGINE=D0
```

`D0`只是示例，必须替换为真实D配置的 `engine_id`。控制目录不得被其他实验共用。
无需pip安装，包路径由启动wrapper添加。开发模式接口仅允许隔离网络/loopback。

## 2. 启动

在原来**完整且已跑通**的D命令前加 `bash /opt/pd-kv-fault/scripts/run_decode.sh`：

```text
原：vllm serve 模型路径 原D参数...
新：bash /opt/pd-kv-fault/scripts/run_decode.sh vllm serve 模型路径 原D参数...
```

这里是命令改法说明，省略号不可直接执行。保持原connector和kv_transfer_config不变。
默认没有arm，不会注错。无需新HTTP端点，也无需worker extension。
已有sitecustomize时拒绝覆盖，需先明确组合方案。

## 3. 一次注入

第二个终端进入项目根目录并设置同样的三个环境变量：

```bash
python -m pd_kv_fault status
python -m pd_kv_fault arm --next-transfer --isolated-single-request --ttl 120
# 通过原有P/D代理发送且只发送一个实验请求
python -m pd_kv_fault status
```

默认RET_NEG1。不是直接向普通D生成端点发送请求，否则可能不经过remote KV接收。
`--isolated-single-request`只确认实验前置条件，不会替你隔离其他请求。
若已掌握准确D内部request_id，可用：

```bash
python -m pd_kv_fault arm --request-id ACTUAL_D_INTERNAL_REQUEST_ID --ttl 120
```

HTTP request ID不一定等于此ID。一次claim后自动停止触发，不要在接收处理中重新arm。

## 4. 日志与验收

查看**D worker所在服务/容器** stdout/stderr，例如：

```bash
docker logs -f YOUR_DECODE_CONTAINER
cat "$PD_FAULT_DIR/events.jsonl"
```

若原服务启动重定向到文件，则查看对应D日志文件。自定义Docker daemon需加原来的 `-H`。

| 必查项 | 含义 |
|---|---|
| installed | 当前进程源码匹配、hook已装载 |
| claimed + return_override（ret=-1） | 同一arm_id/请求确实走到返回值代理 |
| `[PD-KV-FAULT] fault_injected=true` | 明确为注入，不冒充自然故障 |
| Mooncake transfer failed | 原生下层错误分支 |
| Failed to transfer KV cache + RuntimeError | 原生handler捕获分支 |
| 完整HTTP响应及文本 | 单独观察真实推理影响，不根据日志推断 |

`armed=false`不代表命中；`claimed_but_no_return_override`不算目标故障成功。
两条原生ERROR不是API Server自己打印，而是Decode worker线程打印。

## 5. 停止与恢复

```bash
python -m pd_kv_fault disarm
python -m pd_kv_fault status
```

等待当前实验请求完成/失败，再按原D命令（不加wrapper）干净重启，
用新请求对比baseline。保留B/F/R完整响应和日志，不把组件恢复当作模型恢复。

## 常见问题

- **UNSUPPORTED_SOURCE**：不是受支持源码；不要修改白名单强行启动。
- **没有installed**：项目/PYTHONPATH未到达实际D worker，或未使用该connector。
- **armed但没hit**：engine/request不匹配、无remote transfer、TTL过期或全prefix命中。
- **control.lock超时**：先确认控制进程退出状态；工具不会自动删除可疑锁。
- **出错但HTTP200**：旧版可能仍发完成通知；不证明重算或KV成功。
- **看到TCP/HCCL失败**：可能是原有通信故障；先恢复无注入基线再实验。

## 离线自检

测试需要NumPy，不需要NPU、模型、torch或Mooncake：

```bash
python -m unittest discover -s tests -v
python scripts/verify.py
```

`evidence/`中的日志明确属于CPU fixture，不能作为在线server日志引用。
