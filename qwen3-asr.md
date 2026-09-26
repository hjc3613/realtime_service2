# qwen3-asr

# 模型介绍

![image.png](https://alidocs.oss-cn-zhangjiakou.aliyuncs.com/res/AJdl65Aow2ak2Oke/img/51a0a07a-2456-458b-8ddc-08732a3d894e.png)

Qwen3-ASR 系列包括 Qwen3-ASR-1.7B 和 Qwen3-ASR-0.6B，支持 52 种语言和方言的语言识别与语音识别（ASR）。两者均利用大规模语音训练数据以及其基础模型 Qwen3-Omni 强大的音频理解能力。实验表明，1.7B 版本在开源 ASR 模型中达到业界领先水平，并可与最强的商业闭源 API 相媲美。主要特性如下：

*   **一体化**：Qwen3-ASR-1.7B 和 Qwen3-ASR-0.6B 支持 30 种语言和 22 种中文方言的语言识别与语音识别，同时涵盖来自多个国家和地区的英语口音。
    
*   **卓越且高效**：Qwen3-ASR 系列模型在复杂声学环境和具有挑战性的文本模式下仍能保持高质量、鲁棒的识别效果。Qwen3-ASR-1.7B 在开源和内部基准测试中均表现出色；而 0.6B 版本则在精度与效率之间取得良好平衡，在并发数为 128 时吞吐量可达 2000 倍。两者均支持单模型统一进行流式/离线推理，并可处理长音频转录。
    
*   **新颖且强大的强制对齐方案**：我们推出了 Qwen3-ForcedAligner-0.6B，支持对最多 5 分钟的语音在 11 种语言中任意单元进行时间戳预测。评估显示，其时间戳精度超越了基于端到端（E2E）的强制对齐模型。
    
*   **全面的推理工具包**：除了开源 Qwen3-ASR 系列的架构和权重外，我们还发布了一个功能强大、特性完备的推理框架，支持基于 vLLM 的批处理推理、异步服务、流式推理、时间戳预测等功能。
    

## 模型架构

![image.png](https://alidocs.oss-cn-zhangjiakou.aliyuncs.com/res/AJdl65Aow2ak2Oke/img/3e037c7d-dc16-4b8b-897d-09d1be05aceb.png)

## 已发布模型说明与下载

以下是 Qwen3-ASR 模型的介绍及下载信息，请根据需求选择并下载合适的模型。

| **模型** | **支持的语言** | **支持的方言** | **推理模式** | **音频类型** |
| --- | --- | --- | --- | --- |
| Qwen3-ASR-1.7B & Qwen3-ASR-0.6B | 中文 (zh)、英文 (en)、粤语 (yue)、阿拉伯语 (ar)、德语 (de)、法语 (fr)、西班牙语 (es)、葡萄牙语 (pt)、印尼语 (id)、意大利语 (it)、韩语 (ko)、俄语 (ru)、泰语 (th)、越南语 (vi)、日语 (ja)、土耳其语 (tr)、印地语 (hi)、马来语 (ms)、荷兰语 (nl)、瑞典语 (sv)、丹麦语 (da)、芬兰语 (fi)、波兰语 (pl)、捷克语 (cs)、菲律宾语 (fil)、波斯语 (fa)、希腊语 (el)、匈牙利语 (hu)、马其顿语 (mk)、罗马尼亚语 (ro) | 安徽、东北、福建、甘肃、贵州、河北、河南、湖北、湖南、江西、宁夏、山东、陕西、山西、四川、天津、云南、浙江、粤语（香港口音）、粤语（广东口音）、吴语、闽南语 | 离线 / 流式 | 语音、歌声、带背景音乐的歌曲 |
| Qwen3-ForcedAligner-0.6B | 中文、英文、粤语、法语、德语、意大利语、日语、韩语、葡萄牙语、俄语、西班牙语 | \-- | NAR | 语音 |

# 网关使用方式

[《百融大模型API网关访问申请.adoc》](https://alidocs.dingtalk.com/document/edit?scene=universalSpace&mainsiteOrigin=mainsite&docKey=oJGq75k3KB9BzlAK&dentryKey=3xokNWakToQLgxaX&type=d)

[《百融大模型API网关使用文档.adoc》](https://alidocs.dingtalk.com/document/edit?chInfo=im&utm_medium=im_card&utm_source=im&docKey=Q35O8512moWeLl9V&dentryKey=XmWkDGBNsybew1lr&type=d)

# 网关调用方式

## openai transcription模式（仅转录）

```python
# SPDX-License-Identifier: Apache-2.0
import asyncio
import json
import os

import httpx
from openai import OpenAI

audio_path = "/path/to/your.wav"
language = "zh"

model = "qwen3-asr"
openai_api_key = "Your API Key"
# legacy方式
openai_api_base = f"http://pre-llm-inference-api.brapp.com/{model}/v1"
# 统一网关方式
openai_api_base = f"http://api-pre.cybotforge.100credit.cn/v1"

client = OpenAI(
    api_key=openai_api_key,
    base_url=openai_api_base,
)

# 推荐使用如下方式进行非流式转录内容返回
def sync_openai():
    with open(audio_path, "rb") as f:
        transcription = client.audio.transcriptions.create(
            file=f,
            model=model,
            language=language,
            response_format="json",
            # 不设置stream参数，默认为False
            temperature=0.0)
        print("\nSync transcription result:", transcription.text)


sync_openai()


# 流式转录内容返回的响应体非openai标准响应体，需要注意
async def stream_openai_response():
    data = {
        "language": language,
        'stream': True,
        "model": model,
    }
    url = openai_api_base + "/audio/transcriptions"
    print("Stream transcription result:", end=' ')

    async with httpx.AsyncClient() as async_client:
        with open(audio_path, "rb") as f:
            async with async_client.stream('POST', url, files={'file': f},
                                     data=data) as response:
                async for line in response.aiter_lines():
                    if line:
                        if line.startswith('data: '):
                            line = line[len('data: '):]
                        if line.strip() == '[DONE]':
                            break
                        try:
                            chunk = json.loads(line)
                            content = chunk['choices'][0].get('delta',
                                                              {}).get('content')
                            if content:
                                print(content, end='', flush=True)
                        except json.JSONDecodeError:
                            continue
    print()

asyncio.run(stream_openai_response())
```

```shell
$ python3 transcription_example.py
Sync transcription result: 那显示的是什么呢？我在手机上看到的显示是什么？
Stream transcription result: 那显示的是什么呢？我在手机上看到的显示是什么？
```

也可使用openai客户端进行流式转录内容返回，但响应事件非标准化格式，需要简单单独处理一下

```python
# SPDX-License-Identifier: Apache-2.0
import asyncio
import json
import os

import httpx
from openai import OpenAI

# 目前使用soundfile进行音频文件解码，支持的文件格式请查看附录
audio_path = "/path/to/your.wav"
language = "zh"

model = "qwen3-asr"
openai_api_key = "Your API Key"
# legacy方式
openai_api_base = f"http://pre-llm-inference-api.brapp.com/{model}/v1"
# 统一网关方式
openai_api_base = f"http://api-pre.cybotforge.100credit.cn/v1"

client = OpenAI(
    api_key=openai_api_key,
    base_url=openai_api_base,
)


def sync_openai():
    with open(audio_path, "rb") as f:
        stream = client.audio.transcriptions.create(
            file=f,
            model=model,
            # 若设置language=None，流式输出内容将带目标语种信息
            # 若设置了language="zh"，流式输出内容不会带目标语种信息
            language=language,
            # openai标准的流式转录，需要设置response_format=text
            response_format="text",
            stream=True,
            temperature=0.0)
        for event in stream:
            # openai客户端将输出结果强制类型转换为`openai.types.audio.transcription_text_delta_event.TranscriptionTextDeltaEvent`，实际delta属性不可用
            print(repr(event.choices[0]['delta']['content']))


sync_openai()
```
```python
# python3 stream_transcription_example.py 
那
显示
的是
什么呢
？
我在
手机
上
看到
的
显示
是什么
？
```

## openai chat completions模式（可用于多轮对话与转录）

使用openai客户端

```python
import base64
from openai import OpenAI


def encode_audio_file_to_base64(audio_file_path):
    with open(audio_file_path, "rb") as audio_file:
        audio_data = audio_file.read()
        base64_encoded = base64.b64encode(audio_data).decode('utf-8')
        return base64_encoded

model = "qwen3-asr"

# legacy方式
base_url = f"http://pre-llm-inference-api.brapp.com/{model}/v1"
# 预发模型网关方式
base_url = "http://api-pre.cybotforge.100credit.cn/v1"

api_key = "Your api key"

audio_path = "/path/to/your.wav"

messages = [
    {"role": "system", "content": "You are a helpful assistant."},
    {
        "role": "user",
        "content": [
            {
                "type": "text",
                # 不同语种下的用户指令建议针对性调整
                "text": "语音转写："
            },
            {
                "type": "audio_url",
                "audio_url": {
                    "url": f"data:audio/wav;base64,{encode_audio_file_to_base64(audio_path)}"
                }
            }
        ]
    }
]

client = OpenAI(
    base_url=base_url,
    api_key=api_key,
)

response = client.chat.completions.create(
    model=model,
    messages=messages,
    stream=True,
    extra_headers={
      "x-request-id": "111"
    }
)

print("流式响应开始:")
result_text = ""
for chunk in response:
    if chunk.choices[0].delta.content is not None:
        content = chunk.choices[0].delta.content
        result_text += content
        print(content, end='', flush=True)

```

```python
$ python3 chat_completions_example.py
流式响应开始:
language Chinese<asr_text>那显示的是什么呢？我在手机上看到的显示是什么？

```

使用requests工具

```python
import base64
import requests

def encode_audio_file_to_base64(audio_file_path):
    with open(audio_file_path, "rb") as audio_file:
        audio_data = audio_file.read()
        base64_encoded = base64.b64encode(audio_data).decode('utf-8')
        return base64_encoded


api_key = "Your api key"
headers = {
  "content-type": "application/json",
  # 预发网关需要api key
  "authorization": f"Bearer {api_key}",
}
model = "qwen3-asr"
# legacy方式
url = f"http://pre-llm-inference-api.brapp.com/{model}/v1/chat/completions"
# 预发模型网关
url = "http://api-pre.cybotforge.100credit.cn/v1/chat/completions"


audio_path = "/path/to/your.wav"
data = {
    "messages": [
        {
            "role": "user",
            "content": [
                {
                    "type": "audio_url",
                    "audio_url": {
                      "url": f"data:audio/wav;base64,{encode_audio_file_to_base64(audio_path)}"
              },
                }
            ],
        }
    ]
}

response = requests.post(url, headers=headers, json=data, timeout=300)
response.raise_for_status()
content = response.json()['choices'][0]['message']['content']
print(content)
```

# 附录

transcription接口支持的音频格式

| 格式 | 说明 | 常见程度 |
| --- | --- | --- |
| WAV | WAV (Microsoft) | 非常常见 |
| FLAC | FLAC (Free Lossless Audio Codec) | 非常常见 |
| MP3 | MPEG-1/2 Audio | 非常常见 |
| OGG | OGG 容器 (Vorbis/Opus) | 常见 |
| AIFF | AIFF (Apple/SGI) | 常见 |
| CAF | CAF (Apple Core Audio File) | 较常见 |
| AU | AU (Sun/NeXT) | 较少见 |
| RF64 | RF64 (RIFF 64) | 较少见 |
| W64 | W64 (SoundFoundry WAVE 64) | 较少见 |
| WAVEX | WAVEX (Microsoft) | 较少见 |
| RAW | RAW (无头原始数据) | 特殊用途 |
| VOC | VOC (Creative Labs) | 遗留格式 |
| NIST | WAV (NIST Sphere) | 学术/语音研究 |
| SD2 | Sound Designer II | 专业音频 |
| AVR | AVR (Audio Visual Research) | 遗留格式 |
| HTK | HTK (HMM Tool Kit) | 语音识别研究 |
| IRCAM | SF (Berkeley/IRCAM/CARL) | 学术 |
| MAT4 | GNU Octave 2.0 / Matlab 4.2 | 科学计算 |
| MAT5 | GNU Octave 2.1 / Matlab 5.0 | 科学计算 |
| MPC2K | MPC (Akai MPC 2k) | 音乐制作 |
| PAF | PAF (Ensoniq PARIS) | 遗留格式 |
| PVF | PVF (Portable Voice Format) | 少见 |
| SDS | SDS (Midi Sample Dump Standard) | MIDI 相关 |
| SVX | IFF (Amiga IFF/SVX8/SV16) | 遗留格式 |
| WVE | WVE (Psion Series 3) | 遗留格式 |
| XI | XI (FastTracker 2) | 遗留格式 |

注意，以下文件格式目前暂时不支持（有需求可添加）

*   M4A / MP4 — MPEG-4 容器
    
*   AAC / ADTS — AAC 编码
    
*   WebM — Matroska 容器
    
*   WMA — Windows Media Audio
    
*   AMR — 自适应多速率编码（手机录音常见）
    
*   OPUS 文件（独立 .opus 文件，非 OGG 容器内的 Opus）