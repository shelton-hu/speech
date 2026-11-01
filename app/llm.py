"""OpenAI-compatible text generation; credentials never leave the server."""
import asyncio
import json
import math
import os
import time
from pathlib import Path
from urllib.parse import urlsplit

import httpx

from .common import reject

MAX_REPLY = 12000
MAX_RESPONSE_BYTES = 2 * 1024 * 1024
SYSTEM_PROMPT = (
    '你是中文语音助手。直接、自然地回答用户，适合朗读，默认简短回答，通常不超过200字。'
    '不用Markdown表格、代码块和装饰符号。用户需要详细内容时再展开。'
    '如无法确认事实就明确说明；不要声称执行了没有执行的操作或保存了长期记忆。'
)


class LanguageModel:
    def __init__(self):
        self.client = None
        self.active = 0
        self.protocol = os.getenv('LLM_PROTOCOL', 'responses').strip()
        self.model = os.getenv('LLM_MODEL', '').strip()
        base = os.getenv('LLM_BASE_URL', '').strip().rstrip('/')
        key = os.getenv('LLM_API_KEY', '').strip()
        key_file = os.getenv('LLM_API_KEY_FILE', '').strip()
        self.problem = None
        try:
            if key_file:
                key = Path(key_file).read_text().strip()
            if not base or not self.model or not key:
                self.problem = '请在服务端配置大模型地址、模型名称和密钥'
                return
            url = urlsplit(base)
            if (url.scheme not in ('http', 'https') or not url.hostname or url.username
                    or url.password or url.query or url.fragment):
                raise ValueError('base URL')
            if self.protocol not in ('responses', 'chat_completions'):
                raise ValueError('protocol')
            self.timeout = float(os.getenv('LLM_TIMEOUT_SECONDS', '90'))
            self.max_tokens = int(os.getenv('LLM_MAX_OUTPUT_TOKENS', '2048'))
            if not math.isfinite(self.timeout) or not 1 <= self.timeout <= 300:
                raise ValueError('timeout')
            if not 1 <= self.max_tokens <= 8192:
                raise ValueError('output tokens')
            stream = os.getenv('LLM_STREAM', 'false').strip().lower()
            if stream not in ('true', 'false'):
                raise ValueError('stream')
            self.stream = stream == 'true'
            # Preserve an explicit prefix (including /v1); never silently add /v1 twice.
            self.client = httpx.AsyncClient(
                base_url=base + '/', headers={'Authorization': 'Bearer ' + key},
                timeout=httpx.Timeout(self.timeout, connect=min(10, self.timeout)),
                trust_env=False, follow_redirects=False,
            )
        except (OSError, UnicodeError, ValueError):
            self.problem = '大模型配置无效，请检查地址、协议、密钥文件及超时/输出限制'

    def status(self):
        return {'configured': self.client is not None,
                'model': self.model if self.client else None,
                'protocol': self.protocol if self.client else None,
                'message': self.problem}

    async def close(self):
        if self.client:
            await self.client.aclose()

    async def answer(self, payload):
        if not self.client:
            reject(503, 'llm_not_configured', self.problem)
        if self.active >= 2:
            reject(429, 'llm_busy', '正在回答其他问题，请稍后重试')
        self.active += 1
        start = time.monotonic()
        messages = [item.model_dump() for item in payload.history]
        messages.append({'role': 'user', 'content': payload.text})
        body = {'model': self.model, 'stream': self.stream}
        if self.protocol == 'responses':
            path = 'responses'
            body.update(input=messages, instructions=SYSTEM_PROMPT, store=False,
                        max_output_tokens=self.max_tokens)
        else:
            path = 'chat/completions'
            body.update(messages=[{'role': 'system', 'content': SYSTEM_PROMPT}] + messages,
                        max_completion_tokens=self.max_tokens)
        try:
            async with asyncio.timeout(self.timeout):
                async with self.client.stream('POST', path, json=body) as response:
                    if response.status_code == 429:
                        reject(429, 'llm_rate_limited', '大模型服务限流或额度不足，请稍后重试或检查服务端账户')
                    if response.status_code >= 500:
                        reject(503, 'llm_unavailable', '大模型服务暂时不可用')
                    if not 200 <= response.status_code < 300:
                        reject(502, 'llm_rejected', '大模型请求被拒绝，请检查服务端密钥、模型及协议配置')
                    if self.stream:
                        data = await self.read_events(response)
                    else:
                        raw = bytearray()
                        async for chunk in response.aiter_bytes():
                            raw.extend(chunk)
                            if len(raw) > MAX_RESPONSE_BYTES:
                                reject(502, 'llm_invalid_response', '大模型响应超过大小限制')
                        data = json.loads(raw)
                    text = self.extract(data)
            return {'text': text, 'model': self.model,
                    'elapsed_seconds': round(time.monotonic() - start, 3)}
        except (httpx.TimeoutException, TimeoutError):
            reject(504, 'llm_timeout', '大模型回答超时，请稍后重试')
        except httpx.RequestError:
            reject(503, 'llm_unavailable', '无法连接大模型服务')
        except (ValueError, KeyError, TypeError, AttributeError, IndexError):
            reject(502, 'llm_invalid_response', '大模型未返回有效的回答正文')
        finally:
            self.active -= 1

    async def read_events(self, response):
        """Consume upstream SSE internally; the device API still returns one JSON reply."""
        size, pending, event_data = 0, b'', []
        parts, finish = [], None

        def event(lines):
            nonlocal finish
            raw = '\n'.join(lines)
            if not raw or raw == '[DONE]':
                return None
            item = json.loads(raw)
            if self.protocol == 'responses':
                kind = item.get('type')
                if kind in ('response.completed', 'response.incomplete', 'response.failed'):
                    return item['response']
                if kind == 'error':
                    reject(502, 'llm_invalid_response', '大模型返回错误事件')
            else:
                if 'error' in item:
                    reject(502, 'llm_invalid_response', '大模型返回错误事件')
                for choice in item.get('choices', []):
                    if choice.get('index', 0) != 0:
                        continue
                    delta = choice.get('delta', {})
                    part = delta.get('content') or delta.get('refusal')
                    if part is not None:
                        if not isinstance(part, str):
                            raise ValueError('invalid delta')
                        parts.append(part)
                    finish = choice.get('finish_reason') or finish
            return None

        # Limit bytes before line decoding, including an upstream with no newlines.
        async for chunk in response.aiter_bytes():
            size += len(chunk)
            if size > MAX_RESPONSE_BYTES:
                reject(502, 'llm_invalid_response', '大模型响应超过大小限制')
            pending += chunk
            while b'\n' in pending:
                line, pending = pending.split(b'\n', 1)
                line = line.rstrip(b'\r').decode('utf-8')
                if line.startswith('data:'):
                    event_data.append(line[5:].lstrip(' '))
                elif not line:
                    result = event(event_data)
                    event_data = []
                    if result is not None:
                        return result
        if pending.startswith(b'data:'):
            event_data.append(pending[5:].strip().decode('utf-8'))
        if event_data:
            result = event(event_data)
            if result is not None:
                return result
        if self.protocol == 'chat_completions' and finish:
            return {'choices': [{'finish_reason': finish, 'message': {'content': ''.join(parts)}}]}
        reject(502, 'llm_incomplete', '大模型回答中断，请重试')

    def extract(self, data):
        if self.protocol == 'responses':
            if data.get('status') != 'completed':
                reject(502, 'llm_incomplete', '大模型回答未完成，请调整输出限制或重试')
            parts = []
            for item in data['output']:
                if item.get('type') != 'message' or item.get('role') != 'assistant':
                    continue
                for content in item.get('content', []):
                    if content.get('type') == 'output_text':
                        parts.append(content['text'])
                    elif content.get('type') == 'refusal':
                        parts.append(content['refusal'])
            text = '\n'.join(parts)
        else:
            choice = data['choices'][0]
            if choice.get('finish_reason') != 'stop':
                reject(502, 'llm_incomplete', '大模型回答未完成，请调整输出限制或重试')
            text = choice['message'].get('content') or choice['message'].get('refusal')
        if not isinstance(text, str) or not text.strip() or len(text) > MAX_REPLY:
            raise ValueError('invalid answer')
        return text.strip()
