from contextlib import asynccontextmanager

from fastapi import FastAPI, UploadFile
from fastapi.responses import Response

from .common import BodyLimit, MAX_AUDIO_BYTES, MAX_TEXT, configure, reject
from .engine import Engine
from .schemas import SpeechInput
from .version import VERSION


@asynccontextmanager
async def lifespan(app):
    app.state.engine = Engine()
    yield


app = FastAPI(title='NAS 本地语音服务', version=VERSION, lifespan=lifespan)
configure(app, 'SPEECH_TOKEN')
app.add_middleware(BodyLimit)


@app.get('/healthz')
def health():
    return {'status': 'ok'}


@app.get('/readyz')
def ready():
    return {'status': 'ready', 'asr': 'sensevoice-small-int8', 'tts': 'melo-zh-en-onnx'}


@app.post('/v1/transcriptions')
def transcribe(file: UploadFile):
    raw = file.file.read(MAX_AUDIO_BYTES + 1)
    return app.state.engine.transcribe(raw)


@app.post('/v1/speech', response_class=Response, responses={200: {'content': {'audio/wav': {}}}})
def synthesize(payload: SpeechInput):
    wav, headers = app.state.engine.synthesize(payload.text, payload.speed)
    return Response(wav, media_type='audio/wav', headers=headers)
