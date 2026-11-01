"""Run the release image from exactly two deployment files, with no host model mounts."""
import io
import json
import os
from pathlib import Path
import secrets
import shutil
import socket
import subprocess
import tempfile
import urllib.error
import urllib.request
import uuid
import wave


def check_image(root, image, version):
    with tempfile.TemporaryDirectory(prefix='speech-deploy-') as folder:
        folder = Path(folder)
        shutil.copyfile(root / 'compose.yaml', folder / 'compose.yaml')
        token = secrets.token_urlsafe(32)
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0))
            port = sock.getsockname()[1]
        (folder / '.env').write_text(
            f'APP_TOKEN={token}\nLLM_BASE_URL=\nLLM_MODEL=\nLLM_API_KEY=\n'
            f'SPEECH_IMAGE={image}\nAPP_PORT={port}\nBIND_ADDRESS=127.0.0.1\n')
        (folder / '.env').chmod(0o600)
        env = {k: v for k, v in os.environ.items() if not (
            k.startswith(('LLM_', 'SPEECH_', 'COMPOSE_')) or k in ('APP_TOKEN', 'APP_PORT', 'BIND_ADDRESS', 'INFERENCE_THREADS'))}
        compose = ['docker', 'compose', '-p', 'speech-check-' + uuid.uuid4().hex[:10],
                   '--project-directory', str(folder), '-f', str(folder / 'compose.yaml')]
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        base = f'http://127.0.0.1:{port}'

        def request(path, data=None, content_type='application/json', auth=token):
            headers = {'Authorization': 'Bearer ' + auth}
            if data is not None:
                headers['Content-Type'] = content_type
            req = urllib.request.Request(base + path, data=data, headers=headers)
            try:
                with opener.open(req, timeout=180) as response:
                    return response.status, response.read()
            except urllib.error.HTTPError as exc:
                return exc.code, exc.read()

        checks = []

        def require(condition, message):
            if not condition:
                raise RuntimeError(message)
            checks.append(message)
            print('PASS:', message, flush=True)

        try:
            subprocess.run(compose + ['up', '-d', '--pull', 'never', '--wait', '--wait-timeout', '240'],
                           env=env, check=True)
            status, data = request('/healthz')
            require(status == 200 and json.loads(data)['version'] == version, 'published version is visible')
            status, _ = request('/v1/capabilities', auth='invalid')
            require(status == 401, 'invalid device token rejected')
            status, data = request('/v1/capabilities')
            caps = json.loads(data)
            require(status == 200 and caps['speech_ready'], 'bundled models load with automatically derived internal auth')
            require(not caps['llm_configured'], 'model key is optional for speech startup')
            status, data = request('/v1/chat', b'{"text":"hello"}')
            require(status == 503 and json.loads(data)['error']['code'] == 'llm_not_configured', 'unconfigured LLM gives actionable error')
            status, audio = request('/v1/speech', json.dumps({'text': '你好，欢迎使用语音助手。'}).encode())
            require(status == 200 and audio.startswith(b'RIFF'), 'bundled TTS produces real WAV')
            with wave.open(io.BytesIO(audio)) as wav:
                require(wav.getnchannels() == 1 and wav.getsampwidth() == 2 and wav.getnframes() > 0,
                        'generated WAV is nonempty mono PCM16')
            boundary = 'speech' + uuid.uuid4().hex
            body = (f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="test.wav"\r\n'
                    'Content-Type: audio/wav\r\n\r\n').encode() + audio + f'\r\n--{boundary}--\r\n'.encode()
            status, data = request('/v1/transcriptions', body, 'multipart/form-data; boundary=' + boundary)
            require(status == 200 and bool(json.loads(data)['text'].strip()), 'bundled ASR recognizes real audio')
            # Direct internal access must reject the public credential; the gateway used a distinct derived key.
            code = (
                "import os,urllib.request,urllib.error; "
                "r=urllib.request.Request('http://127.0.0.1:8001/readyz',headers={'Authorization':'Bearer '+os.environ['APP_TOKEN']}); "
                "\ntry: urllib.request.urlopen(r); raise SystemExit(1)"
                "\nexcept urllib.error.HTTPError as e: assert e.code==401"
            )
            subprocess.run(compose + ['exec', '-T', 'speech', 'python', '-c', code], env=env, check=True)
            require(True, 'public and internal credentials are distinct')
            require(sorted(p.name for p in folder.iterdir()) == ['.env', 'compose.yaml'],
                    'deployment uses only compose.yaml and .env')
            return checks
        except Exception:
            subprocess.run(compose + ['logs', '--tail', '40'], env=env)
            raise
        finally:
            subprocess.run(compose + ['down', '--remove-orphans'], env=env, check=True)
