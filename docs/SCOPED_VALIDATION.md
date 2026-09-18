# v0.2 当前交付状态：WIP / A3 E2E BLOCKED

分支：`feature/multirank-multinode-scope`，从 `9a0e49f` 创建。
仅提交实现及离线证据；**不创建“validate A3 online”成功提交，不合并main。**

## 已实现/已验证

- 严格D0或D0-32lowerhex；UUID重启匹配。
- runtime identity、真实role交叉校验、unknown rank拒绝。
- 每节点本地锁和原子状态更新；逐rank一次claim；同请求绑定。
- 完整rank安装/存活/同session检查，arm期间重启拒绝。
- TTL、disarm、重复claim、旧arm replay、TP size mismatch门禁。
- immutable manifest多节点分发接口；离线global aggregator。
- 原19项TP1测试保留，总45项测试通过。
- 真spawn多进程TP2：两rank各一次RET_NEG1；二次调用回原host fixture。
- 两独立目录TP4：nodeA tp0/1、nodeB tp2/3，4/4完成；剔除1条override后3/4 PARTIAL。
- pinned原生方法产生两层ERROR/RuntimeError；不是工具手写原生日志。

所有新效果证据标为 `MULTIPROCESS_PINNED_NATIVE_METHODS_HOST_FIXTURE`。
测试请求为合成 `D-request` / `P-request`，不是在线HTTP请求。
本次不宣称已经验证服务启动、scheduler失败传播、HTTP故障行为或模型恢复。

## A3 在线阻断

附件要求A3四卡：P=0/1 TP2、D=2/3 TP2，Qwen3-32B W8A8。
本会话现有SSH入口的只读检查仍为910B4/A2，只有物理卡2/5；不是附件中的目标。
已请求A3 SSH入口、现有P/D容器/启动脚本，尚未获得。
没有质疑附件另一个环境的已通过baseline，也没有在错误机器用小模型替代TP2验收。

```text
A3_SINGLE_NODE_TP2_E2E = BLOCKED_NO_A3_CONNECTION
HTTP_FAULT_BEHAVIOR = NOT_TESTED
CLEAN_D_RESTART_RECOVERY = NOT_TESTED
MULTI_NODE_HARDWARE_E2E_NOT_CLAIMED
```

获得正确入口后，先核对源码SHA、布局、资源及已有baseline原件；只clean restart目标D。
顺序：installed两rank→disarmed真实传输→同arm/请求双rank fault→保存完整HTTP响应
→同进程恢复观察→clean D restart正式恢复。任一门禁失败停止，不碰P或其他任务。

## 证据目录

- `evidence/tests.log`：统一测试输出。
- `evidence/scoped/latest.json`：Windows离线多进程证据目录指针。
- `evidence/linux_scoped/`：Linux无网络、无NPU设备容器中flock测试和多进程证据。
- 各case的 `manifest.json`、node目录`events.jsonl/state.json/native_tp*.log`。
- `evidence/sha256.json`：当前交付文件哈希；锁定上游源码hash保持不变。

GLOBAL_COMPLETED只是所有manifest参与者的return-code代理命中，不等于线上模型E2E。
