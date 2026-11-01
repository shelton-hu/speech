import hashlib
import hmac
import json
import os
import uuid

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

MAX_AUDIO_BYTES = 10 * 1024 * 1024
MAX_BODY_BYTES = MAX_AUDIO_BYTES + 64 * 1024
MAX_AUDIO_SECONDS = 30
MAX_TEXT = 400


def service_token(token_name: str):
    token = os.environ.get(token_name, '')
    if token_name == 'SPEECH_TOKEN' and not token:
        access_token = service_token('APP_TOKEN')
        token = hmac.new(access_token.encode(), b'speech-internal-v1', hashlib.sha256).hexdigest()
    if len(token) < 24:
        raise RuntimeError(f'{token_name} must contain at least 24 characters; set it in .env')
    return token


def configure(app: FastAPI, token_name: str):
    token = service_token(token_name)

    original_openapi = app.openapi

    def documented_openapi():
        schema = original_openapi()
        components = schema.setdefault('components', {})
        components.setdefault('securitySchemes', {})['DeviceToken'] = {'type': 'http', 'scheme': 'bearer'}
        components.setdefault('schemas', {})['ErrorEnvelope'] = {
            'type': 'object', 'required': ['error'], 'properties': {'error': {
                'type': 'object', 'required': ['code', 'message', 'request_id'],
                'properties': {key: {'type': 'string'} for key in ('code', 'message', 'request_id')},
            }},
        }
        for path, methods in schema['paths'].items():
            for method, operation in methods.items():
                if method not in ('get', 'post'):
                    continue
                if path.startswith('/v1/') or path == '/readyz':
                    operation['security'] = [{'DeviceToken': []}]
                    statuses = [401, 503]
                    if method == 'post':
                        statuses += [413, 422, 429, 502, 504]
                    if path == '/v1/transcriptions':
                        statuses += [415]
                    for status in statuses:
                        operation['responses'][str(status)] = {
                            'description': 'See API.md for error codes and recovery',
                            'content': {'application/json': {'schema': {'$ref': '#/components/schemas/ErrorEnvelope'}}},
                        }
        return schema

    app.openapi = documented_openapi

    @app.middleware('http')
    async def protect(request: Request, call_next):
        request.state.request_id = uuid.uuid4().hex
        if request.url.path.startswith('/v1/') or request.url.path == '/readyz':
            actual = request.headers.get('authorization', '')
            if not hmac.compare_digest(actual.encode(), f'Bearer {token}'.encode()):
                return failure(request, 401, 'unauthorized', '设备令牌无效')
        response = await call_next(request)
        response.headers['X-Request-ID'] = request.state.request_id
        response.headers['Cache-Control'] = 'no-store'
        response.headers['X-Content-Type-Options'] = 'nosniff'
        return response

    @app.exception_handler(HTTPException)
    async def http_error(request, exc):
        detail = exc.detail
        if isinstance(detail, dict):
            return failure(request, exc.status_code, detail['code'], detail['message'])
        return failure(request, exc.status_code, 'request_failed', str(detail))

    @app.exception_handler(RequestValidationError)
    async def validation_error(request, exc):
        return failure(request, 422, 'validation_error', '请检查必填字段、文字长度和语速范围')


def failure(request, status, code, message):
    return JSONResponse(status_code=status, content={'error': {
        'code': code, 'message': message,
        'request_id': getattr(request.state, 'request_id', uuid.uuid4().hex),
    }})


def reject(status, code, message):
    raise HTTPException(status, {'code': code, 'message': message})


class BodyLimit:
    """Bound request bytes even when Content-Length is missing or dishonest."""
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http' or scope['method'] not in ('POST', 'PUT', 'PATCH'):
            return await self.app(scope, receive, send)
        chunks, size = [], 0
        while True:
            message = await receive()
            if message['type'] == 'http.disconnect':
                return
            size += len(message.get('body', b''))
            if size > MAX_BODY_BYTES:
                response = JSONResponse(status_code=413, content={'error': {
                    'code': 'body_too_large', 'message': '上传内容超过大小限制', 'request_id': uuid.uuid4().hex,
                }})
                return await response(scope, receive, send)
            chunks.append(message.get('body', b''))
            if not message.get('more_body', False):
                break
        delivered = False

        async def bounded_receive():
            nonlocal delivered
            if not delivered:
                delivered = True
                return {'type': 'http.request', 'body': b''.join(chunks), 'more_body': False}
            return await receive()
        await self.app(scope, bounded_receive, send)
