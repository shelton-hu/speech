"""Live integration checks against the real Docker services. No model stubs."""
import argparse
import concurrent.futures
import io
import json
import re
import subprocess
import time
import wave
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]

def check(condition, message):
    if not condition:
        raise AssertionError(message)

def run(base, out):
    env = dict(line.split('=', 1) for line in (ROOT / '.env').read_text().splitlines() if '=' in line and not line.startswith('#'))
    out.mkdir(parents=True, exist_ok=True)
    report = {'base_url': base, 'checks': [], 'measurements': {}}
    with httpx.Client(base_url=base, headers={'Authorization': 'Bearer ' + env['APP_TOKEN']}, timeout=120, trust_env=False) as client:
        def passed(name):
            report['checks'].append(name)
            print('PASS:', name, flush=True)

        r = client.get('/healthz')
        check(r.status_code == 200, 'health')
        passed('gateway health')
        r = client.get('/v1/capabilities', headers={'Authorization': 'Bearer invalid'})
        check(r.status_code == 401 and r.json()['error']['code'] == 'unauthorized', 'unauthorized access')
        passed('invalid token rejected')
        check(client.get('/readyz').status_code == 200, 'readiness')
        passed('real models ready')
        for payload in ({'text': ''}, {'text': '   '}, {'text': '测' * 401}, {'text': '你好', 'speed': 3}):
            check(client.post('/v1/speech', json=payload).status_code == 422, 'text validation')
        passed('blank, excessive text and invalid speed rejected')
        r = client.post('/v1/transcriptions', files={'file': ('fake.wav', b'not audio')})
        check(r.status_code == 415, 'invalid media')
        passed('invalid media rejected')
        r = client.post('/v1/transcriptions', files={'file': ('huge.wav', b'x' * (10 * 1024 * 1024 + 1))})
        check(r.status_code == 413, 'oversized upload')
        passed('oversized upload rejected')

        def silent(seconds):
            buffer = io.BytesIO()
            with wave.open(buffer, 'wb') as wav:
                wav.setnchannels(1); wav.setsampwidth(2); wav.setframerate(16000)
                wav.writeframes(b'\0\0' * 16000 * seconds)
            return buffer.getvalue()

        check(client.post('/v1/transcriptions', files={'file': ('long.wav', silent(31))}).status_code == 413, 'duration cap')
        passed('overlong recording rejected')
        r = client.post('/v1/transcriptions', files={'file': ('silence.wav', silent(1))})
        check(r.status_code == 200 and r.json()['text'] == '', 'silence')
        passed('silence returns empty text')

        reference = ROOT / 'models/sherpa-onnx-sense-voice-zh-en-ja-ko-yue-int8-2024-07-17/test_wavs/zh.wav'
        r = client.post('/v1/transcriptions', files={'file': ('zh.wav', reference.read_bytes())})
        check(r.status_code == 200, 'reference ASR: ' + r.text[:200])
        check(len(re.findall(r'[\u4e00-\u9fff]', r.json()['text'])) >= 5, 'reference sample produced no Chinese transcript')
        report['measurements']['reference_asr'] = r.json()
        passed('independent Chinese recording produces a nonempty Chinese transcript')

        text = '你好，欢迎使用语音助手。今天是十一月一日。'
        r = client.post('/v1/speech', json={'text': text, 'speed': 1})
        check(r.status_code == 200, 'real TTS: ' + r.text[:100] if r.status_code != 200 else '')
        check(r.content[:4] == b'RIFF', 'WAV header')
        (out / 'speech.wav').write_bytes(r.content)
        with wave.open(io.BytesIO(r.content)) as wav:
            check(wav.getnchannels() == 1 and wav.getsampwidth() == 2 and wav.getnframes() > wav.getframerate(), 'WAV properties')
        report['measurements']['tts'] = {'text': text, 'audio_seconds': float(r.headers['X-Audio-Seconds']), 'elapsed_seconds': float(r.headers['X-Elapsed-Seconds'])}
        passed('real Chinese TTS produces playable mono PCM16 WAV')
        r = client.post('/v1/transcriptions', files={'file': ('generated.wav', r.content)})
        check(r.status_code == 200 and '语音助手' in r.json()['text'], 'roundtrip ASR: ' + r.text[:200])
        report['measurements']['roundtrip'] = r.json()
        passed('TTS audio transcribed back to Chinese')

        webm = out / 'browser-format.webm'
        subprocess.run(['ffmpeg', '-nostdin', '-v', 'error', '-y', '-i', str(out / 'speech.wav'), '-c:a', 'libopus', str(webm)], check=True)
        r = client.post('/v1/transcriptions', files={'file': ('browser.webm', webm.read_bytes())})
        check(r.status_code == 200 and '语音助手' in r.json()['text'], 'browser WebM')
        passed('browser WebM/Opus decoded and recognized')

        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            def generate():
                return client.post('/v1/speech', json={'text': '这是并发语音处理测试。' * 15}).status_code
            statuses = sorted(pool.map(lambda _: generate(), range(2)))
        check(statuses == [200, 429], f'bounded inference: {statuses}')
        passed('concurrent inference rejected with 429 while real model stays occupied')
    (out / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2))
    print(f"Completed {len(report['checks'])} live checks; report: {out / 'report.json'}")

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--base-url', default='http://127.0.0.1:18080')
    parser.add_argument('--out', type=Path, default=ROOT / 'artifacts/verification')
    args = parser.parse_args()
    run(args.base_url, args.out)
