import os
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from fastapi import FastAPI, HTTPException, UploadFile
from fastapi.responses import FileResponse, Response

from .common import BodyLimit, MAX_AUDIO_BYTES, MAX_AUDIO_SECONDS, MAX_TEXT, configure, reject, service_token
from .llm import LanguageModel
from .schemas import ChatInput, ChatOutput, SpeechInput
from .version import VERSION


@asynccontextmanager
async def lifespan(app):
    app.state.llm = LanguageModel()
    app.state.client = httpx.AsyncClient(
        base_url=os.environ.get('SPEECH_URL', 'http://speech:8001'),
        headers={'Authorization': 'Bearer ' + service_token('SPEECH_TOKEN')},
        timeout=httpx.Timeout(120, connect=5), trust_env=False,
    )
    try:
        yield
    finally:
        await app.state.client.aclose()
        await app.state.llm.close()


app = FastAPI(title='speech 语音与对话后端', version=VERSION, lifespan=lifespan)
configure(app, 'APP_TOKEN')
app.add_middleware(BodyLimit)


async def forward(method, path, **kwargs):
    try:
        result = await app.state.client.request(method, path, **kwargs)
    except httpx.TimeoutException:
        reject(504, 'speech_timeout', '语音处理超时，请检查服务状态')
    except httpx.RequestError:
        reject(503, 'speech_unavailable', '语音服务尚未就绪')
    if result.is_error:
        try:
            detail = result.json()['error']
            code, message = detail['code'], detail['message']
        except (ValueError, KeyError, TypeError):
            reject(502, 'speech_failed', '语音服务处理失败')
        reject(result.status_code if result.status_code in (413, 415, 422, 429, 503) else 502, code, message)
    return result


@app.get('/', include_in_schema=False)
def index():
    return FileResponse(Path(__file__).parent / 'static/index.html')


@app.get('/chat.js', include_in_schema=False)
def chat_script():
    return FileResponse(Path(__file__).parent / 'static/chat.js', media_type='text/javascript')


@app.get('/healthz')
def health():
    return {'status': 'ok', 'version': VERSION}


@app.get('/readyz')
async def ready():
    result = await forward('GET', '/readyz')
    return result.json()


@app.get('/v1/capabilities')
async def capabilities():
    # Text chat remains discoverable when the independent speech service is down.
    try:
        result = await forward('GET', '/readyz', timeout=5)
        models = result.json()
        speech_ready = True
    except (HTTPException, ValueError):
        models = {'asr': None, 'tts': None}
        speech_ready = False
    llm = app.state.llm.status()
    return {'version': VERSION, 'models': models, 'speech_ready': speech_ready,
            'input': ['text', 'audio'], 'output': ['text', 'audio/wav'],
            'max_audio_bytes': MAX_AUDIO_BYTES, 'max_audio_seconds': MAX_AUDIO_SECONDS,
            'max_text_characters': MAX_TEXT, 'streaming': False,
            'llm_configured': llm['configured'], 'llm': llm}


@app.post('/v1/chat', response_model=ChatOutput)
async def chat(payload: ChatInput):
    return await app.state.llm.answer(payload)


@app.post('/v1/transcriptions')
async def transcribe(file: UploadFile):
    raw = await file.read(MAX_AUDIO_BYTES + 1)
    if len(raw) > MAX_AUDIO_BYTES:
        reject(413, 'audio_too_large', '音频文件不能超过 10MiB')
    result = await forward('POST', '/v1/transcriptions', files={'file': ('audio', raw, 'application/octet-stream')})
    return result.json()


@app.post('/v1/speech', response_class=Response, responses={200: {'content': {'audio/wav': {}}}})
async def synthesize(payload: SpeechInput):
    result = await forward('POST', '/v1/speech', json=payload.model_dump())
    return Response(result.content, media_type='audio/wav', headers={
        name: result.headers[name] for name in ('X-Audio-Seconds', 'X-Elapsed-Seconds') if name in result.headers
    })
