"""Live model and speech smoke checks. Sends three small billable model requests."""
import argparse
import io
import json
import os
import re
import time
import wave
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx

ROOT = Path(__file__).resolve().parents[1]


def run(client, out):
    out.mkdir(parents=True, exist_ok=True)
    report = {'tested_at': datetime.now(ZoneInfo('Asia/Shanghai')).isoformat(),
              'live_model': True, 'live_speech': True, 'checks': []}

    def request(name, path, **kwargs):
        start = time.monotonic()
        response = client.post(path, **kwargs)
        elapsed = round(time.monotonic() - start, 3)
        if response.status_code != 200:
            # Only the gateway's documented error code, never headers or upstream bodies.
            try:
                code = response.json()['error']['code']
            except (ValueError, KeyError, TypeError):
                code = 'unreadable_error'
            raise AssertionError(f'{name}: HTTP {response.status_code} ({code})')
        item = {'name': name, 'status': response.status_code, 'elapsed_seconds': elapsed}
        report['checks'].append(item)
        return response, item

    def check(condition, label):
        if not condition:
            raise AssertionError(label)
        print('PASS:', label, flush=True)

    def wav_seconds(raw):
        with wave.open(io.BytesIO(raw)) as wav:
            check(wav.getnchannels() == 1 and wav.getsampwidth() == 2 and wav.getnframes() > 0,
                  'audio is nonempty mono PCM16 WAV')
            return round(wav.getnframes() / wav.getframerate(), 3)

    prompt = '这轮对话的水果是菠萝，编号是47。请用一句话复述这两个信息。'
    response, item = request('first_question', '/v1/chat', json={'text': prompt})
    first = response.json()
    check('菠萝' in first['text'] and ('47' in first['text'] or '四十七' in first['text']), 'live first-turn answer')
    report['requested_model'] = first['model']
    item['answer'] = first['text']
    history = [{'role': 'user', 'content': prompt}, {'role': 'assistant', 'content': first['text']}]
    response, item = request('follow_up', '/v1/chat', json={
        'text': '刚才说的水果和编号分别是什么？只回答这两个信息。', 'history': history})
    follow_up = response.json()['text']
    check('菠萝' in follow_up and ('47' in follow_up or '四十七' in follow_up), 'live follow-up preserves context')
    item['answer'] = follow_up

    response, item = request('create_voice_question', '/v1/speech', json={
        'text': '我有两个苹果，又买了三个苹果，现在一共有几个苹果。'})
    question_audio = response.content
    item['audio_seconds'] = wav_seconds(question_audio)
    (out / 'question.wav').write_bytes(question_audio)
    response, item = request('recognize_question', '/v1/transcriptions',
                             files={'file': ('question.wav', question_audio, 'audio/wav')})
    transcript = response.json()['text']
    check(bool(re.search(r'(两|二|2)个苹果', transcript)) and bool(re.search(r'(三|3)个苹果', transcript)),
          'real ASR preserves both quantities and the object in the test question')
    item['transcript'] = transcript
    response, item = request('answer_voice_question', '/v1/chat', json={'text': transcript})
    answer = response.json()['text']
    check(bool(re.search(r'(五|5)个苹果', answer)) and not any(x in answer for x in ('如果', '吗', '?', '？')),
          'real model unambiguously answers five apples, not a guessed clarification')
    item['answer'] = answer
    chunks = [answer[i:i + 350] for i in range(0, len(answer), 350)]
    for i, chunk in enumerate(chunks):
        response, item = request(f'speak_answer_{i + 1}', '/v1/speech', json={'text': chunk})
        item['audio_seconds'] = wav_seconds(response.content)
        (out / f'answer-{i + 1}.wav').write_bytes(response.content)
    report['limitations'] = [
        'Input audio is synthesized test speech, not a real microphone recording.',
        'The gateway ran locally; this does not verify the NAS/public speech deployment or physical speaker.',
        'Latency is a small smoke-test sample, not a benchmark.',
        'An earlier 二加三 test was misrecognized as 外加三; see initial-roundtrip-finding.json. Passing this natural question does not establish general ASR accuracy.',
    ]
    (out / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base-url', required=True, help='Gateway to test; sends real model requests')
    parser.add_argument('--out', type=Path, default=ROOT / 'artifacts/verification/live-chat')
    args = parser.parse_args()
    token = os.getenv('APP_TOKEN', '')
    if not token:
        raise SystemExit('Set APP_TOKEN in the environment without passing it as a command argument.')
    with httpx.Client(base_url=args.base_url, headers={'Authorization': 'Bearer ' + token},
                      timeout=310, trust_env=False, follow_redirects=False) as client:
        run(client, args.out)
