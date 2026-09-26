# 第一轮优化（2026-09-09）

## 已改动

- `service_app/session.py`：每个音频包发送对应的比例文本前缀，末包携带末尾文本，句中取消保留已发送文本；转写 done 改为每个 response 一次。文本按字节时长比例近似对齐，尚非 TTS 词级时间戳。
- `service_app/session.py`：所有已提交的用户片段排队保留，一次响应读取按序累积的内容；ASR 提交经过相同队列入口，避免清掉此前片段。
- `service_app/session.py`：确认打断、新用户提交或 ASR 正在处理时，丢弃尚未发出的旧工具计划；继续读完 LLM 流，但不再把它的旧动作提交。已发出的工具仍等待真实结果。
- `service_app/session.py`：每次 completion 最多发出第一个工具，只记录实际选中的调用；其余调用不进入历史，收到结果后让模型重新规划。拒绝调用 session 未提供的工具名。
- `service_app/backends.py`：请求声明 `parallel_tool_calls=false`；TTS 分句保留跨 delta 的小数、邮箱、域名、常见缩写、时间及 URL。
- `service_app/session.py`：ASR 词表作为拼写提示而非强制候选集；清理返回的 `<asr_text>` 包裹标签。
- `start.sh`：取消鼓励并行调用的附加提示，强调原始 policy、按订单汇总所有修改、直接回答事实问题、结束/转接前检查剩余请求。
- `service_app/README.md`：同步实际实现，包括现有的“最近三次中两次 positive”投票逻辑；本次没有调整该交互阈值。

## 验证

26 个测试全部通过，包括两项使用真实 tau BR adapter / event parser / tick 缓冲与转写算法的离线集成测试。模拟的仅是网络、TTS 音频数据及播放时钟，没有调用模型服务。

覆盖：用户补充顺序、ASR 后仍保留旧片段、取消后的旧工具不执行、并行工具只发送一项、reasoning 内容保留、工具回执后续响应、音频转写逐 tick 可见、句中取消、短尾包、缓冲播放、小数/邮箱所有可能的两段切分边界及逐字符流。

可在本项目目录运行（`TAU_SRC` 指向 tau2-bench 的 src）：

```sh
TAU_SRC='/Users/admin/Desktop/换机/git_root/agent_eval/tau2-bench/src'
PYTHONPATH="$TAU_SRC" LITELLM_LOCAL_MODEL_COST_MAP=true .venv/bin/python -m unittest discover -s tests -p 'test_*.py'
```

不提供 tau 源码路径时，普通测试仍可运行，adapter 集成测试自动跳过。

## 使用与边界

需要重启运行该代码的 realtime service 进程才能生效。本次只改本地代码，没有触碰远端服务，也没有重跑付费评测。

建议重新跑 task 3、6、11、21、23、28、35、36、37、40、41、43，保留之前的成功题 3/37 作为退化检查；使用新的结果目录、相同模型与语音配置。

“工具成功前不宣称完成”“不漏处理请求”目前由原始 policy 和附加提示指导，不能声称已用代码完全消除 LLM 假完成。后续仍需服务端日志及配对实测确认改善幅度。`conversation.item.truncate` 客户端播放边界的完整历史回写仍未实现，本轮修复的是服务发送进度上的文本同步。

原 `REPORT.md`、`reproduction.json` 保留为修复前的诊断证据；原 `reproduce.py` 的旧行为断言在修复后应不再通过，应使用本轮回归测试验证新行为。
