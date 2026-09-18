# PD KV Transfer Fault Injection（独立故障注入项目）

## v0.2 / multi-rank 分支状态

新控制层支持 topology identity、严格 engine-base UUID 匹配、逐participant claim、
本节点多进程锁、不可变manifest分发和离线全局汇总。

- **离线已测**：Windows多进程、Linux flock、多进程TP2原生方法夹具；两节点目录TP4模拟。
- **A3 单机 TP2在线E2E：BLOCKED**，本会话未提供对应A3连接/容器；当前可访问入口仍是A2卡2/5。
- **MULTI_NODE_HARDWARE_E2E_NOT_CLAIMED**。
- 新使用方法见 [多rank/多节点手册](docs/MULTI_RANK_MULTI_NODE.md)，验收见 [本轮状态](docs/SCOPED_VALIDATION.md)。

下方单worker命令作为v0.1历史兼容说明保留。**TP2必须使用schema v2和ENGINE_BASE，
不能沿用单marker的v0.1控制器。** 不因架构实现而把online状态改为PASS。

**类别：有损故障注入，不是日志模拟。** 默认 `RET_NEG1` 会跳过目标请求本次真实
KV传输并驱动原生异常处理，可能干扰推理输出、导致请求失败或触发重算；具体表现由
部署版本决定。**当前只完成组件级证据，尚未证明在线模型输出错误或完整服务恢复。**

独立仓库：`spike677/pd-kv-transfer-fault-injection`。
本项目不合并到此前 `vllm_ascend_op_fi`，也不把既有 Fault18/20/26/39/41 的
张量/算子数值注入能力作为本项目能力。

- [操作速查](docs/USAGE_QUICKSTART.md)：安装位置、启动、arm/disarm、日志与恢复。
- [故障分类与证据边界](docs/FAULT_MODEL.md)：与只打印日志、数值注入、物理断网区分。
- [机器可读验证结果](evidence/summary.json)：组件验证，非在线推理证据。

最小的 Decode 侧 Mooncake KV 接收故障工具。与 DemonCATPFI 无运行时依赖，
不修改 vLLM/Mooncake 安装源码，不修改网络，不停止 P 服务。

## 实现的链路

```text
一次 arm
  → D worker 的 KVCacheRecvingThread
  → 当前接收调用使用临时 engine facade
  → batch_transfer_sync_read 等价返回 -1（不发起本次数据传输）
  → 原生 connector: ret < 0
  → 原生 “Mooncake transfer failed ...”
  → 原生 RuntimeError("Mooncake transfer failed, ret: -1")
  → 原生 _handle_request 捕获
  → 原生 “Failed to transfer KV cache ...” + traceback
```

这是 **TRANSPORT_RETURN_FAULT_EMULATION**。不是物理断链，不证明 Mooncake
自身产生连接失败。不会伪造 ADXL/HCCL/Socket connection refused 日志。
唯一由工具主动写入的服务日志是明确的 `[PD-KV-FAULT] fault_injected=true` 标记。
上面两层 ERROR 文本和 RuntimeError 由锁定版本原生代码产生。

另一模式 `CONNECTOR_FAIL` 只在 connector 入口抛异常，因此不保证出现下层
`Mooncake transfer failed` 日志；要得到本项目主链路请使用默认 `RET_NEG1`。

## 日志到底在哪里？

| 日志 | 产生位置 | 查看方式 |
|---|---|---|
| 两层原生 ERROR / traceback | **D worker → KV connector 接收线程** | D服务 stdout/stderr、D容器日志 |
| `[PD-KV-FAULT]` 来源标记 | 同一 worker，复用 connector logger | 同上 |
| arm/claimed/return_override | 工具结构化证据 | `$PD_FAULT_DIR/events.jsonl` |
| HTTP 响应/访问日志 | API Server | 不能单靠它判断 KV 成功或注入成功 |

所以它属于广义 **vLLM serving 日志**，但不是 `api_server.py` 主动生成。
普通本地 MP worker 通常继承服务输出；多容器/多节点则应看 **Decode worker
所在容器/节点**。Ray等部署也可能写到各自 worker 日志，不保证只看 API 容器就能找到。

```bash
# 直接启动服务时先保存 stdout+stderr
bash scripts/run_decode.sh vllm serve ... > /path/to/decode-server.log 2>&1
tail -f /path/to/decode-server.log

# Docker：工具必须挂到实际 D worker 容器
docker logs -f YOUR_DECODE_CONTAINER
# 若使用专用 Docker daemon：
docker -H unix:///run/fault26-docker-20260817.sock logs -f YOUR_DECODE_CONTAINER
```

工具不设置第二套 vLLM logger，也不保证外部日志采集系统的路由。

## 版本支持与实测边界（先读）

当前版本 **仅支持已经审计的旧版普通 Mooncake connector**：

- vLLM-Ascend commit `99e1ea0fe685e93f53ee5adfe4b41cdd42fb809f`。
- `mooncake_connector.py` SHA256
  `275bee054b182e1c6177ec6e5c6ce3f0deb6afa7dcd43cd66d9ef274d9231316`。
- 方法 `KVCacheRecvingThread._transfer_kv_cache`。

附件的 GitHub main / hybrid 给出了目标日志链，但它们不是当前部署版本。
**main 的 `_transfer_kv_cache_all_groups` 和 hybrid 暂不支持**，不能仅改白名单
hash就宣称兼容。对应新版本需要独立适配和测试。没有 `--force`/跳过源码校验选项。

旧版原生 handler 出错后仍可能在 finally 标记 finished。这个项目保留原行为，
不会偷偷实现 fail/recompute；**HTTP200不等于真实 KV传输成功，错误日志也不等于请求必然500**。

v0.1冻结基线为19项离线测试通过；v0.2的完整测试数量见 `evidence/tests.log`。
测试实际执行锁定源码中的原生方法，验证两层日志、一次命中、
原 engine 未改变、B/F/R与恢复。测试传输为显式 CPU fixture，不是模型服务。
前一轮另有实际安装类的 CONNECTOR_FAIL 验证；不能挪用为本版 RET_NEG1 在线证据。
**本版服务启动挂载和模型 E2E 尚未验证。** 当前A2正常Mooncake通信仍受设备IP重复阻断。

## 项目结构

```text
pd-kv-fault/
├── pyproject.toml
├── pd_kv_fault/
│   ├── runtime.py       # call-local engine/receiver facade
│   ├── control.py       # atomic one-shot control + metadata evidence
│   ├── bootstrap.py     # deferred native-module import hook
│   └── cli.py           # arm/disarm/status
├── bootstrap/sitecustomize.py
├── scripts/run_decode.sh
├── scripts/verify.py
├── tests/               # pinned source + explicit host fixtures
└── evidence/            # tests, native log chain, B/F/R, SHA256
```

## 如何使用

### 1. 先准备一个正常的 P/D 服务环境

以下是v0.1兼容用法：先验证无工具时真实 KV 传输成功、D继续生成。只支持独占开发服务，首版 D=1个
worker、并发=1、无其他请求。**不要用于共享/公开服务。**

P 使用原启动方式，不加载本工具。D保留原模型、原 `--kv-transfer-config`、原
connector配置。工具不提供臆测的整套P/D配置；这里叠加到你已经跑通的D启动命令上。

把完整项目复制到 D worker 环境，例如 `/home/haoran/pd-kv-fault`。
Docker场景把项目与控制目录 bind mount 到 D容器；下面命令中的路径必须是容器内路径。
项目无需 pip 安装即可运行（从项目根目录执行）。运行时只使用Python标准库。

```bash
cd /home/haoran/pd-kv-fault
mkdir -p /home/haoran/pd-kv-fault-control/run-001
chmod 700 /home/haoran/pd-kv-fault-control/run-001

export PD_FAULT_DIR=/home/haoran/pd-kv-fault-control/run-001
export PD_FAULT_OWNER=experiment-001
# 必须与原 D --kv-transfer-config 中的 engine_id 完全一致
export PD_FAULT_ENGINE=D0
```

### 2. 给 D 启动命令加一层 wrapper

```bash
bash scripts/run_decode.sh \
  vllm serve "$MODEL_PATH" \
  ...你原来已跑通的D服务参数... \
  > /home/haoran/pd-kv-fault-control/run-001/decode-server.log 2>&1
```

以上省略号是**占位符，不是可直接复制的参数**。只在你已有的完整命令前加
`bash scripts/run_decode.sh`，不要改原 connector 为这个项目，也不需要
`--worker-extension-cls` 或新的 HTTP 控制接口。

wrapper启用开发模式，并给本次D进程及子进程设置专属PYTHONPATH。
sitecustomize只注册延迟import finder；不会在Python启动时导入torch/Mooncake。
原生模块自然导入后才安装hook，避免提前初始化ACL/通信库。

若已有 sitecustomize（例如其他探针），wrapper会拒绝覆盖；需显式整合，
不要删除既有探针或每步强行重新挂hook。模块版本不符会中止该模块加载。

等待服务健康，并确认 `events.jsonl` 有 `installed`。默认没有arm，所以不注入。
开发模式接口只应绑定loopback或隔离网络，不要暴露给外部用户。

### 3. 服务启动后触发一次

在第二个终端进入同一项目，设置相同的DIR/OWNER/ENGINE。
CLI终端不需要设置PD_FAULT_ENABLE，避免无关Python进程也挂载hook。

```bash
python -m pd_kv_fault status

# 最简单：服务完全独占、无在途请求，触发下一次真实非空D接收
python -m pd_kv_fault arm --next-transfer --isolated-single-request --ttl 120

# 现在通过你原来的 P/D 代理发送一条正常请求
# 不要直接绕过P、只向普通D文本生成端点请求，否则可能没有KV接收可命中

python -m pd_kv_fault status
```

`--isolated-single-request` 是使用条件的显式确认，不是自动隔离调度器。
如果有其他流量，不使用 next-transfer。已知D内部request ID时可精确指定：

```bash
python -m pd_kv_fault arm --request-id ACTUAL_D_INTERNAL_REQUEST_ID --ttl 120
```

**内部request ID不保证等于HTTP头里的ID。** 工具同时记录真实request_id和remote_request_id。
过期不会注入，但需disarm清掉过期marker才能重新arm。

### 4. 验收日志

```bash
grep -E 'PD-KV-FAULT|Mooncake transfer failed|Failed to transfer KV cache' \
  /home/haoran/pd-kv-fault-control/run-001/decode-server.log
cat "$PD_FAULT_DIR/events.jsonl"
```

必须同时看到：

1. `claimed` 与 **`return_override`、ret=-1**，关联同一arm_id/请求。
2. `[PD-KV-FAULT] fault_injected=true mode=RET_NEG1`。
3. 原生 `Mooncake transfer failed ...`。
4. 原生 `Failed to transfer KV cache ...` 和 `RuntimeError: ... ret: -1`。

`armed=false` 单独不能证明命中；`claimed_but_no_return_override` 表示进入engine之前
已发生其他异常，**不能算成功注入**。日志中的源码行号、worker前缀取决于版本和部署。
无缓存传输、全prefix命中、错误engine、请求不匹配都不会消费有效trigger。

### 5. 停止触发与恢复

```bash
python -m pd_kv_fault disarm
python -m pd_kv_fault status
```

disarm只阻止后续claim，不能撤销已触发异常。下一次未触发的调用走原生engine，
但请求状态/服务健康恢复仍需实测；正式恢复建议按原D命令（不加wrapper）干净重启。
不要删plog或控制证据。不要在接收in-flight时重新arm。

## 测试命令

离线测试需要NumPy（锁定原方法自身使用NumPy），不需要torch/NPU/Mooncake。

```bash
python -m unittest discover -s tests -v
python scripts/verify.py
```

第二个命令保存 `evidence/native_chain.log` 和 `summary.json`。
这些明确标为 `PINNED_NATIVE_METHODS_HOST_FIXTURE`，不能作为服务日志冒充在线测试。

## 原生源码参考

- [vLLM-Ascend 普通 connector main](https://github.com/vllm-project/vllm-ascend/blob/main/vllm_ascend/distributed/kv_transfer/kv_p2p/mooncake_connector.py)
- [本项目锁定版本](https://github.com/vllm-project/vllm-ascend/blob/99e1ea0fe685e93f53ee5adfe4b41cdd42fb809f/vllm_ascend/distributed/kv_transfer/kv_p2p/mooncake_connector.py)

`tests/source_snapshot/mooncake_connector.py` 保留上游Apache-2.0 SPDX标记，仅用作可复核测试夹具。
