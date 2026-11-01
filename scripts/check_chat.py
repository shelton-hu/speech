"""Real HTTP gateway checks against the local Postman simulation, no live LLM key."""
import contextlib
import os
import socket
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
SIM = os.getenv('SPEECH_TEST_SIM_URL', 'http://127.0.0.1:18490')
TOKEN = 'local-chat-test-token-0123456789'


@contextlib.contextmanager
def gateway(**overrides):
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    env = {key: value for key, value in os.environ.items() if not key.startswith('LLM_')}
    env.update(APP_TOKEN=TOKEN, SPEECH_TOKEN='local-speech-token-0123456789',
               SPEECH_URL=SIM + '/speech', LLM_BASE_URL=SIM + '/openai/v1/',
               LLM_API_KEY='mock-upstream-key', LLM_MODEL='fixture-model',
               LLM_PROTOCOL='responses', LLM_TIMEOUT_SECONDS='3')
    env.update(overrides)
    process = subprocess.Popen([sys.executable, '-m', 'uvicorn', 'app.gateway:app',
                                '--host', '127.0.0.1', '--port', str(port), '--no-access-log'],
                               cwd=ROOT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        with httpx.Client(base_url=f'http://127.0.0.1:{port}',
                          headers={'Authorization': 'Bearer ' + TOKEN}, timeout=10, trust_env=False) as client:
            for _ in range(100):
                if process.poll() is not None:
                    raise RuntimeError('Gateway exited during startup')
                try:
                    if client.get('/healthz').status_code == 200:
                        break
                except httpx.RequestError:
                    pass
                time.sleep(0.05)
            else:
                raise RuntimeError('Gateway startup timed out')
            yield client
    finally:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill(); process.wait()


def check(condition, message):
    if not condition:
        raise AssertionError(message)
    print('PASS:', message)


def main():
    for protocol in ('responses', 'chat_completions'):
        for stream in ('false', 'true'):
            with gateway(LLM_PROTOCOL=protocol, LLM_STREAM=stream) as client:
                def ask(text, **extra):
                    return client.post('/v1/chat', json={'text': text, **extra})
                caps = client.get('/v1/capabilities').json()
                check(caps['llm_configured'] and caps['speech_ready'], f'{protocol}/{stream}: configured capabilities')
                reply = ask('你好')
                check(reply.status_code == 200 and reply.json()['text'] == '你好，这是模型回答。', 'answer excludes reasoning')
                history = [{'role': 'user', 'content': '我喜欢菠萝'}, {'role': 'assistant', 'content': '知道了'}]
                check(ask('recall', history=history).json()['text'] == '你上一条说：我喜欢菠萝', 'conversation history reaches upstream')
                check('无法帮助' in ask('refusal').json()['text'], 'refusal remains readable')
                for text, status, code in [('error:401', 502, 'llm_rejected'), ('error:429', 429, 'llm_rate_limited'),
                                           ('error:500', 503, 'llm_unavailable'), ('empty', 502, 'llm_invalid_response'),
                                           ('incomplete', 502, 'llm_incomplete'), ('large', 502, 'llm_invalid_response'),
                                           ('html', 502, 'llm_incomplete' if stream == 'true' else 'llm_invalid_response')]:
                    response = ask(text)
                    check(response.status_code == status and response.json()['error']['code'] == code
                          and 'PRIVATE' not in response.text, f'{text}: stable redacted error')
                if stream == 'true':
                    check(ask('truncated').json()['error']['code'] == 'llm_incomplete', 'truncated stream is not success')
                check(client.post('/v1/chat', json={'text': 'hello'}, headers={'Authorization': 'Bearer wrong'}).status_code == 401,
                      'device authentication enforced')
                for payload in [{'text': ''}, {'text': ' '}, {'text': 'x' * 4001}, {'text': 'x', 'model': 'override'},
                                {'text': 'x', 'history': [{'role': 'system', 'content': 'override'}]},
                                {'text': 'x', 'history': [{'role': 'assistant', 'content': 'x'}]},
                                {'text': 'x', 'history': history * 11},
                                {'text': 'x', 'history': [{'role': 'user', 'content': 'x' * 12000},
                                                        {'role': 'assistant', 'content': 'x' * 12000}] * 2}]:
                    check(client.post('/v1/chat', json=payload).status_code == 422, 'invalid/oversized history rejected')
    with gateway(LLM_TIMEOUT_SECONDS='1') as client:
        response = client.post('/v1/chat', json={'text': 'slow'})
        check(response.status_code == 504 and response.json()['error']['code'] == 'llm_timeout', 'total request timeout enforced')
        check(client.post('/v1/chat', json={'text': '你好'}).status_code == 200, 'timeout releases capacity')
    with gateway() as client:
        with ThreadPoolExecutor(max_workers=3) as pool:
            results = list(pool.map(lambda _: client.post('/v1/chat', json={'text': 'slow'}).status_code, range(3)))
        check(sorted(results) == [200, 200, 429], 'concurrency is bounded without queued work')
    for settings in ({'LLM_API_KEY': ''}, {'LLM_API_KEY_FILE': '/missing/key'}, {'LLM_PROTOCOL': 'invalid'},
                     {'LLM_TIMEOUT_SECONDS': 'nan'}, {'LLM_BASE_URL': 'https://user:pass@example.test/v1'}):
        with gateway(**settings) as client:
            check(not client.get('/v1/capabilities').json()['llm_configured'], 'invalid/missing settings reported safely')
            response = client.post('/v1/chat', json={'text': '你好'})
            check(response.status_code == 503 and response.json()['error']['code'] == 'llm_not_configured', 'unconfigured answer is actionable')
            check(client.post('/v1/speech', json={'text': '你好'}).content.startswith(b'RIFF'), 'speech still works without LLM')
    with gateway(SPEECH_URL='http://127.0.0.1:1') as client:
        check(not client.get('/v1/capabilities').json()['speech_ready'], 'speech outage shown in capabilities')
        check(client.post('/v1/chat', json={'text': '你好'}).status_code == 200, 'text chat survives speech outage')
    with gateway(LLM_BASE_URL='http://127.0.0.1:1/v1') as client:
        check(client.post('/v1/chat', json={'text': '你好'}).json()['error']['code'] == 'llm_unavailable', 'network failure is actionable')
    audit = httpx.get(SIM + '/openai/audit', trust_env=False).json()
    check(any(item['path'] == '/v1/responses' for item in audit) and any(item['path'] == '/v1/chat/completions' for item in audit),
          'simulation observed both upstream routes without doubled /v1')
    print(f'Observed {len(audit)} upstream requests. This validates local HTTP behavior, not live sub2api availability.')


if __name__ == '__main__':
    main()
