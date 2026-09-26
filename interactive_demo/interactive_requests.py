import json
import requests
import io
import base64
from tqdm import tqdm

import soundfile as sf


def encode_audio_file_to_base64(audio_file_path, max_seconds=30):
    with sf.SoundFile(audio_file_path) as audio_file:
        sample_rate = audio_file.samplerate
        frames = int(sample_rate * max_seconds)

        audio_data = audio_file.read(
            frames=frames,
            dtype="float32",
        )

    # 重新编码为 WAV，保证 Base64 内容是一个完整可解析的音频文件
    buffer = io.BytesIO()
    sf.write(
        buffer,
        audio_data,
        sample_rate,
        format="WAV",
        subtype="PCM_16",
    )

    return base64.b64encode(buffer.getvalue()).decode("utf-8")

BASE_URL = 'http://localhost:9004/v1'
API_KEY = 'EMPTY'

URL = f"{BASE_URL}/chat/completions"
    
HEADERS = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {API_KEY}"
    }
MODEL_NAME = 'interactive_model'
PAYLOAD = {
        "model": MODEL_NAME,
        "messages": [],
        "top_p": 0.9,
        "temperature": 0.0,
        "max_tokens": 5,
        "stream": False,
        "presence_penalty": 1,
    }
interrupt_test_file = '/data/junchao.hu/tau2_bench_ft_v2/duplex_realtime/build_interrupt_v2/created_datas/interrupt_training_v2_swift_dev_fix.jsonl'
total_count = 0
correct_count = 0
with open(interrupt_test_file) as f:
    for line in tqdm(f):
        line = json.loads(line)
        base64_str = [encode_audio_file_to_base64(i) for i in line['audios']]
        messages = line['messages'][:-1]
        label = line['messages'][-1]['content']
        for item in messages:
            if '<audio>' in item['content']:
                content = item['content'].replace('<audio>', '')
                if content:
                    content = [{'type':'text', 'text':content}]
                else:
                    content = []
                audio_url = {
                        "audio_url": {
                            "url": f"data:audio/wav;base64,{base64_str.pop(0)}"
                        },
                        "type": "audio_url"
                    }
                content.append(audio_url)
                item['content'] = content
        PAYLOAD['messages'] = messages
        PAYLOAD['structured_outputs'] = {"choice": ["interrupt", "backchannel", "ignorable"]}
        response = requests.post(URL, headers=HEADERS, json=PAYLOAD, stream=PAYLOAD.get("stream", False), proxies={"http": None, "https": None},)
        if not response.ok:
                print(response.raise_for_status())
            
        res = response.json()["choices"][0]["message"]["content"]
        res
        total_count += 1
        if res == label:
            correct_count += 1
    print('interrupt correct_count/total_count: ', correct_count/total_count)

semantic_complete_test_file = '/data/junchao.hu/tau2_bench_ft_v2/duplex_realtime/build_turnsense_v1/created_datas/turnsense_training_v1.val.fix.jsonl'
total_count = 0
correct_count = 0
with open(semantic_complete_test_file) as f:
    lines = [json.loads(i) for i in f.readlines()]
    for line in tqdm(lines[:]):
        base64_str = [encode_audio_file_to_base64(i) for i in line['audios']]
        messages = line['messages'][:-1]
        label = line['messages'][-1]['content']
        for item in messages:
            if '<audio>' in item['content']:
                content = item['content'].replace('<audio>', '')
                if content:
                    content = [{'type':'text', 'text':content}]
                else:
                    content = []
                audio_url = {
                        "audio_url": {
                            "url": f"data:audio/wav;base64,{base64_str.pop(0)}"
                        },
                        "type": "audio_url"
                    }
                content.append(audio_url)
                item['content'] = content
        PAYLOAD['messages'] = messages
        PAYLOAD['structured_outputs'] = {"choice": ["complete", "incomplete"]}
        response = requests.post(URL, headers=HEADERS, json=PAYLOAD, stream=PAYLOAD.get("stream", False), proxies={"http": None, "https": None},)
        if not response.ok:
                print(response.raise_for_status())
            
        res = response.json()["choices"][0]["message"]["content"]
        res
        total_count += 1
        if res == label:
            correct_count += 1
    print('semantic complete correct_count/total_count: ', correct_count/total_count)