from pathlib import Path
BASE=Path(__file__).resolve().parent
SERVICE=BASE.parents[1]
TAU=SERVICE.parent/'tau2-bench'
RUN=TAU/'data/simulations/br_voice_retail4'
def link(label,path,line=None):
 return f'[{label}]({path}'+(f':{line}' if line else '')+')'
def task(i):return link(f'task {i}',BASE/f'task_{i:03}.md')
report=f'''# br_voice_retail4 失败分析

分析日期：2026-09-09。范围：本地已保存的 45 个完成样本、48 个任务 artifact 目录、当前 realtime service 与 tau2-bench BR/OpenAI adapter 源码。没有修改服务/评测代码，没有发起模型调用或重新跑分。

## 结论

当前完成率为 **28/45 = 62.2%**。不能把它当成“LLM 能力只有 62%”，也不能认为只是 ASR 或打断模型不好。现有证据指向三层问题叠加：

1. **服务与评测的音频/文本同步存在确定的实现问题**。当前源码把一个 TTS 分句播放完后才发音频转写增量，途中打断会失去该分句的转写；tau 用户模拟器主要用这份文本理解助手与决定打断。这会干扰整个交互闭环。
2. **业务执行存在独立缺陷**：有“没有执行写工具却声称完成”、错误拒绝可修改订单、选错符合偏好的商品、漏做多订单请求、过早提交部分退货等明确案例。
3. **用户模拟与固定目标也有偏离**：部分用户在复杂重述中改变了任务原定策略，最终对话认可的动作与固定 gold DB 不同；这些失败不能一概归给助手模型。

优先修正交互/文本同步与输入保留，再做同模型的纯文本对照和 ASR/交互消融。现在直接扩大 airline、telecom 跑量，很可能只是把诊断混杂因素扩大；尚不能预测跑完分数一定更低。

## 数据口径

| 项目 | 结果 |
|---|---:|
| 配置中的 retail 任务 | 114 |
| 完成并保存评分 | 45，task 0–44，每题 1 trial |
| 成功 / 失败 | 28 / 17 |
| 失败且 DB 不匹配 | 15 |
| DB 正确，仅 NL assertion 失败 | 2：36、38 |
| 正常终止类型 | user_stop 38；agent_stop 7 |
| 6000 tick / 1200 秒上限导致的终止 | 已完成的 45 个中为 0 |
| 已有 artifact 但无完成结果 | 45、46、47，不能计为已失败 |
| 单次对话模拟时长中位数 | 372 秒 |
| 单次 wall-clock 时长中位数 | 909 秒，包含模拟用户 LLM/TTS 等等待 |
| speech_started 事件 | 582 次；不是误打断次数 |
| 助手有音频的 tick 中文本为空 | 44,017 / 46,471 = 94.72% |

最后一项是“没有新增文本的 200ms tick 比例”，**不是丢字率、ASR 错误率或故障占比**。正常语速也可能有部分无新字 tick；这里的异常需要结合“整句末才发布转写”的源码与复现判断。成功和失败样本的该比例都约 94.7%，说明是普遍结构问题，不能仅凭比例证明哪一题因它失败。

评分通常按 DB 和 NL_ASSERTION 相乘；DB 或指定自然语言断言为 0，整题就为 0。`action_checks` 是参考路径对齐信息，不能把每一个 read action 不匹配都当作扣分原因。参考查询本身也包含预期失败的探测。{link('评分组合逻辑',TAU/'src/tau2/evaluator/evaluator.py',215)}

结果来源：{link('results.json',RUN/'results.json')}；逐任务指标：{link('metrics.json',BASE/'metrics.json')}。

## 确定的服务实现问题与优先级

### P0：音频转写没有随音频同步送给 tau

调用链：`BRRealtimeProvider` 继承 OpenAI provider，`DiscreteTimeBRRealtimeAdapter` 继承 OpenAI adapter。BR 并不是一个独立实现了文本同步的适配器。

- 服务的 LLM 流式文本走 `response.text.delta`。
- OpenAI adapter 的文本累计只处理 `AudioTranscriptDeltaEvent`，即 `response.output_audio_transcript.delta`；`response.text.delta` 不进入这里的 utterance transcript。
- 服务 `_play_clause_audio()` 把整句音频播放完成后，才发送 `response.output_audio_transcript.delta`。
- `get_proportional_transcript()` 只在该 tick 有播放音频时分配文本。迟到文本若没有后续同 item 音频，就没有自然的补发机会；若同 item 后续还有分句，则可能串到后面的音频。
- 用户模拟器用线性化的 tick 文本形成响应和打断判断的 LLM 上下文。因此音频虽然发出了，模拟用户看到的内容可能滞后、不完整或错位。

证据：{link('服务发转写的位置',SERVICE/'service_app/session.py',1462)}；{link('adapter 只累计音频转写增量',TAU/'src/tau2/voice/audio_native/openai/discrete_time_adapter.py',258)}；{link('无音频时不分配文本',TAU/'src/tau2/voice/audio_native/tick_result.py',463)}；{link('模拟用户读取 tick 文本',TAU/'src/tau2/user/user_simulator_streaming.py',1225)}。

对当前实际方法体做了独立离线复现：6 秒音频的播放等待点中，音频转写增量始终为 0，结束后才发 1 条；播到 2 秒取消，已发出 2 秒音频，却总共发出 0 条音频转写。不是从 README 推断。

**修复方向**：以实际发送/播放进度逐段发送对应的文本前缀，让音频和转写在同一播放 tick 可用；中断保留已播放前缀，尾句也必须可见。不要简单在开头把整句文本全部塞给 adapter——在逐 tick 发送音频时，adapter 的比例分母是“已收到音频”，这样可能反过来提前泄露整句。应针对 BR 明确每段音频的文本对齐，或者让服务在每个音频片段发送时同步发送对应文本增量。

验收必须通过实际 BR adapter：正常句、分句间隙、句中打断、末句无下一条音频，以及 no unheard text 的检查。

### P1：latest-wins 覆盖了用户内容，而不只是过时的响应任务

`_queue_pending_user_turn()` 用新转写直接替换整个 pending 列表；启动时只取最后一个。连续两个已提交片段“改成蓝色”“还要八号”，会只留下后一句。完整度过早切句或 LLM 等待越长，此风险越明显。

{link('覆盖位置',SERVICE/'service_app/session.py',1353)}。离线调用当前方法已复现：最终只剩 `And make the size eight.`。

**修复方向**：所有已接受用户片段都持久进入按序的对话记录；可以取消旧响应计划，但不能默认新片段替代旧片段语义。显式更正通过模型或结构化 pending intent 合并；“还要”“不是 A 是 B”“刚才那个确认”应区别处理。

本批没有服务端队列日志，不能断言 task 11/15/26 的沉默就是这个分支导致；它是已证实的当前代码风险，样本归因仍需日志。

### P1：数字与邮箱被 TTS 分句拆坏

`CLAUSE_END_RE` 把任意英文句点都当作分句边界。当前源码复现：

```text
The price is $54.85. → ["The price is $54.", "85."]
a.b@example.com → ["a.", "b@example.", "com."]
```

{link('分句实现',SERVICE/'service_app/backends.py',281)}；{task(5)} 的 175–191 秒附近也能看到金额 `54.` / `85.` 分开呈现，多个样本的金额和编号反复被碎片化播报。

**修复方向**：保护小数点、邮箱/域名、缩写和编号；流式 token 恰好结束在 `54.` 时要保留 lookahead，等后续字符确定它是小数还是句末。仅把正则改为“左右都是数字时不切”仍不足以处理跨 delta 小数。再增加最小分句长度和合理最大等待。

### P1：打断时仍可能执行旧计划中的写工具

`_interrupt()` 停 TTS，保留 LLM 生成；`_run_response()` 在 LLM 结束后继续发工具调用。这样能避免工具参数被截断，但对退货/换货/付款等动作，没有显式的“用户最新更正已合并、确认仍适用”的提交检查。

{link('工具发送',SERVICE/'service_app/session.py',1197)}；{link('打断处理',SERVICE/'service_app/session.py',1542)}。

{task(6)} 在 245–254 秒用户已开始说“只换台灯”，255.4 秒却提交了水瓶和台灯两件换货。**错动作已证实**，但要区分“ASR 没听到更正”“切句/排队丢失更正”“旧 LLM 工具请求穿过中断”，还需要服务日志。

**修复方向**：允许读工具继续，写工具增加计划版本/确认版本检查；用户开始新的更正后，暂缓尚未发出的旧写动作，先收齐最新意图，再决定提交。不能取消已经成功执行的工具后伪装成未执行，也不能为了抢速度跳过确认。

### P2：配置与协议边界需要收敛

- 当前启动脚本的 instruction suffix 要求尽可能并行调用工具；retail policy 却明确“每次至多一个工具”。实际轨迹也出现同 tick 多个查询，服务当前源码会发全部调用，README 的“只发第一个”已过时。应尊重原始 domain policy，在模型侧禁用不允许的 parallel tool calls，并在服务侧排队串行执行/让模型逐步决策。
- README 写“连续两次 interrupt”；当前代码是最近 3 次投票数满足门槛，因此 `interrupt, ignorable, interrupt` 也可触发。它会影响误打断/漏打断权衡，必须以实际配置和版本为准。
- `_truncate()` 当前是空实现，`response.cancel` 也没有专门分支。server 发送字节数与 adapter 实际播放进度在缓冲/截断时并不必然相同。需要消费客户端针对具体 item 的播放边界，而不是假设两端历史一致。
- ASR completed 事件虽由服务发送，adapter 目前只在兜底事件日志记录收到事件，没有写入每 tick 的 `user_transcript`。本批 `user_transcript` 都为空，`user_labels.txt` 是用户模拟发言，**不是服务真正识别到的内容**。

{link('启动附加指令',SERVICE/'start.sh',40)}；{link('三次投票窗口',SERVICE/'service_app/session.py',822)}；{link('truncate 空实现',SERVICE/'service_app/session.py',1629)}；{link('事件兜底处理',TAU/'src/tau2/voice/audio_native/openai/discrete_time_adapter.py',343)}。

## 17 个失败逐题梳理

时间为 simulation audio clock 秒数；标签展示超过 200 字会截断，详细档案末尾补充了未限制长度的 tick 文本。表中是观察到的失败机制，不是互斥的模型/代码因果统计。

| 任务 | 评分失败与关键证据 | 判断与下一步 |
|---|---|---|
| {task(5)} | gold 要求最终退水瓶；实际 439.8s 只换台灯。用户 351.8s 说“只换台灯，水瓶先不动”，后续并未落实任务要求的“再次确认后改为退水瓶”，还转向改邮箱问题。 | 交互混乱 + 用户条件策略偏离。不能只说助手漏掉一条已经明确提出的退货指令。检查模拟用户完整生成与实际听到的确认文本。 |
| {task(6)} | 用户 245.8s 起尝试限制为只换台灯；255.4s 同时换水瓶和台灯，之后纠错已太晚。 | 确认/中断与写动作竞争，兼有意图识别问题。P1 写工具提交门槛的核心回归题。 |
| {task(7)} | 373.8s 两件都换，gold 只允许台灯；但用户 359.8s 又说“okay proceeding with the whole exchange”。 | 用户模拟输出与原任务策略矛盾，助手也应澄清歧义。不能把 DB 失败简单算作工具参数幻觉。 |
| {task(11)} | 只有 4 次读工具，没有任何退货调用；反复问能否跨支付方式退款，最终离开。最后可见助手文本后约 87s 无新文本。 | 未完成政策解释/确认闭环 + 无响应体验。需服务 ASR、LLM 请求与调度日志区分卡住还是内容未发布。 |
| {task(15)} | 已查到 boots 和尺码 8 选项，但无 modify 工具；出现“I'm not Paul”等角色混淆，反复追问后离开。 | 交互/识别/背景内容干扰的强疑点，不能据此断言 ASR 把 pause 识别成 Paul。需要真实识别文本与音频。 |
| {task(20)} | 订单 `#W9911714` 工具明确为 pending；助手后来声称无法升级；整题无写工具，并说过 75.30 超过 86 的自相矛盾理由。 | 明确业务推理与状态跟踪缺陷，交互可能放大。用同模型纯文本复跑验证。 |
| {task(21)} | 先把鞋目标 `4107812777` 调成 `410781277`，后恢复；最终只改鞋，错误拒绝 keyboard 变体，gift card 余额目标 44.08 未满足。 | 数字识别链 + 多请求合并/商品 ID 推理。只靠重试 ASR 不足以解决 keyboard 遗漏。 |
| {task(23)} | 先查 `Sofia + 981193`，再查 `Sophia + 98193`；始终没有正确组合 `Sofia Hernandez + 98193`，转人工。 | 输入值恢复失败。ASR 还是 LLM 改写错，现有数据不可分；确定的是没有将已纠正字段组合重试。 |
| {task(26)} | 已查订单，用户确认退 kettle 和 keyboard、保留 pet bed，最后没有 return 工具。最后可见助手文本后约 42s 无新文本。 | 确认后执行链断裂。调度/转写可见性/LLM 执行三者待区分。 |
| {task(28)} | skateboard 已退；159.8s 只退 hose，将同单变成 return requested；200s 再退 backpack 失败，后续 keyboard/bed 未完成，总退款 918.43 未告知。 | 一次性写动作过早提交；用户已在多次片段中提及 backpack。应在按订单提交前收齐该订单退货清单。 |
| {task(31)} | charger 已取消，sneaker 无退货调用；用户 374.6s 同意退 sneaker 到 gift card，后因“无邮箱”讨论又于 497.8s 说先不要提交。 | 执行迟滞/不必要的流程障碍引起用户改变意图。不能在后续撤回后强行操作；应修前面的闭环。 |
| {task(35)} | 退 speaker 正确；laptop 选 `6056040996` space grey，gold 是 `5052031638` silver。工具结果中 silver i5 13-inch 确实 available，助手却称只有灰色符合。 | 确定的候选检索/偏好排序错误；用户同意灰色是在助手错误介绍之后。 |
| {task(36)} | DB 正确，camera 481.50 已提到，但未明确回答“最贵的是 camera”，NL=0。 | 评测可见回答不完整，也可能受到转写缺失影响；需先保证可见性，再训练直接回答问题。 |
| {task(38)} | 正确取消订单，和 task 36 一样没有明确“最贵的是 camera”。 | 同上；不是取消工具失败。成功对照 task 37 的同一类断言通过。 |
| {task(40)} | 最后说“已切换 Visa 8902”，全程 7 次工具都是读/计算，**没有 modify_pending_order_payment**。 | 确定的虚假完成宣称；是否由输入丢失诱发也不能改变此事实。写工具成功回执必须作为完成宣称依据。 |
| {task(41)} | puzzle、一个订单地址、profile 更新成功；另一个 pending 订单 `#W9583042` 未改。两单原地址均是 443，需要 445。 | 多订单覆盖不全；原任务要求所有订单，但模拟用户多用单数“the order”，也存在目标表达变窄。需主动列出两个订单并确认修改范围。 |
| {task(43)} | 已查到 tablet storage=64GB，却先谈缺少投递照片、要转人工；用户用 TRANSFER 结束。storage 未告知、default address 未改，且没有真正调用 transfer 工具。 | 提前转移话题/转接承诺使用户退出，遗漏可独立完成的请求。应先完成可办理项，或先明确与用户确认剩余事项，再发实际转接工具。 |

其中 11、15、20、23、26、40、43 共 7 题完全没有数据库写工具调用，但仍不能把这 7 题全部归为同一种“LLM 不调用工具”。

## 模型能力、ASR 和评测环境应怎样区分

**现有证据足以确认**：task 40 假完成；task 35 错误候选介绍与选择；task 20 对 pending 订单的错误拒绝；task 41 多订单覆盖不全；task 28 过早写入。它们说明业务层有独立优化空间，即使转写同步修好也不应预期自动全通过。

**现有证据不足以确认**：ASR WER、专有名词准确率、交互模型误打断率、完整度过早结束率、LLM 首 token 延迟、TTS 首音频延迟。输入的 gold/script 与工具参数不同，只能说明“从用户音频到工具参数的链路出错”，不能跳过 ASR 实际文本直接归责某一个模型。

当前 startup 文件写的是 `qwen3-32b-tauvoice` / `qwen3_omni` / `qwen3_tts`，但结果文件只记录包装名 `br:br-voice-realtime`，不能证明这一批实际后端模型、权重和启动参数。服务代码在当前工作区还有未提交变更；评测结果的 git_commit 也不是服务部署版本。因此本报告严格区分“本批已发生的轨迹”与“当前代码已复现的行为”。

音频场景为 regular，带 telephony、背景噪声、突发噪声、frame drop、咳嗽/非目标插话，且每题 voice config 不同。按 persona 的描述性成功率：arjun 6/6、mamadou 7/11、mildred 6/9、priya 4/6、wei 5/13。**任务/persona 绑定且样本少，不能推出某个音色是罪魁祸首。**

另一个必须固定的实验变量：项目存在 ElevenLabs → 本地 TTS 的 override。如果这次也启用了它，日志里的 `eleven_v3`/voice_id 只代表上层参数，不能证明用户真的用 ElevenLabs 合成；本地兼容接口会忽略部分 voice settings/seed。`[pause]`、`[cough]` 等标签的真实声学效果也需要核对。应将“用户 TTS 是否替换”“固定语音配置”“音频缓存来源”写进运行元数据，保持前后实验可比。

ASR 当前还有固定 domain hot_words，以及要求名字/账号使用给定术语的提示。应记录词表来源、版本与覆盖范围，并保留无强制替换的对照；不要把修成词表里另一个人当作识别正确。对外报告诊断性 gold-text 注入与真实语音结果要分开。

## 优化与验证顺序

| 阶段 | 操作 | 主要回答的问题 |
|---|---|---|
| 1：先使评测闭环可信 | 修音频/文本同步；保留中断已听文本；修小数/邮箱分句；用户片段不丢失 | 实现问题是否在系统性污染交互？ |
| 2：业务闭环 | 写工具成功后才宣称完成；按订单汇总确认；保留未完成请求清单；读/写工具按 policy 串行；更正使旧确认失效 | 可靠性约束能解决哪些零分题？ |
| 3：同模型纯文本 | 使用相同 domain policy/tools、同后端权重和参数，跑配对 task | 去掉语音后业务能力上限是多少？ |
| 4：语音消融 | 在独立诊断结果目录中，分别用干净音频、无打断、确定的用户 turn 边界、gold 用户文字注入 | ASR、切轮/打断、噪声各自有多少损失？ |
| 5：比较 LLM | 固定修好的服务、同 ASR/TTS、任务与 seed，只替换后端 LLM | 换更强模型是否值得，或主要仍是交互瓶颈？ |
| 6：扩域 | 先给 airline/telecom 各跑少量按业务类型分层的样本，再全量 | 日期/姓名/PNR、数字/长标识、工具链是否暴露新问题？ |

建议先用 12 题回归：**3、6、11、21、23、28、35、36、37、40、41、43**。3 和 37 是已成功的对照；其余覆盖更正、沉默、ID、一次性操作、选品、明确回答、假完成、多订单和提前转接。不要只重跑原来失败的题；不要只看单 seed 的单次涨分。基础修复稳定后再在完整 retail 上跑多个 trial，最终比较配对任务的成功/失败转移。

业务提示建议短而具体：回答用户问的事实先给直接答案；状态更新/确认催问不撤销之前请求；每项请求按“待收集信息→待确认→已提交→工具成功/失败”记录；读工具查到的状态不得被口头猜测覆盖；同订单所有修改集中在一次写操作中。task 40 的问题应以动作回执校验兜底，不能只再加一句“不要幻觉”。

关键观测：输入音频起止时刻、VAD/交互窗口与标签、commit 原因、实际 ASR 文本、每次 LLM 完整 messages/tools、计划版本/确认版本、工具参数与结果、每段音频对应文本、server sent / adapter played 字节、取消/截断原因。所有日志用 `provider_session_id` 串联。

## 交付物与复现

- {link('逐任务指标',BASE/'metrics.json')}：45 题结果、session id、时长、工具调用与文本可见性统计。
- `task_000.md` 到 `task_044.md`：任务目标、评分细节、对话标签、完整工具参数/结果、按 utterance 汇总的原始 tick 文本。
- {link('extract.py',BASE/'extract.py')}：只读分析脚本，可重新生成证据文件。
- {link('reproduce.py',BASE/'reproduce.py')} 与 {link('reproduction.json',BASE/'reproduction.json')}：从当前源码 AST 读取实际方法体，在替代时钟/传输下复现三项确定行为；未加载/调用模型。

验证范围是离线数据核对与选定方法行为复现，不是 live end-to-end 测试。本地默认 Python 缺少项目依赖，未运行完整项目测试。服务端 uvicorn/ASR/interactive_debug 日志当前未提供，因此不编造“代码占多少、模型占多少”或修复后能达到的分数。
'''
(BASE/'REPORT.md').write_text(report)
print(BASE/'REPORT.md')
