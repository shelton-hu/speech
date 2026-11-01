import io
import os
import re
import subprocess
import tempfile
import threading
import time
from pathlib import Path

import numpy as np
import sherpa_onnx
import soundfile as sf

from .common import MAX_AUDIO_BYTES, MAX_AUDIO_SECONDS, reject


class Engine:
    def __init__(self):
        self.slot = threading.Lock()
        root = Path(os.environ.get('MODEL_DIR', '/models'))
        asr = root / 'sherpa-onnx-sense-voice-zh-en-ja-ko-yue-int8-2024-07-17'
        tts = root / 'vits-melo-tts-zh_en'
        for path in [asr / 'model.int8.onnx', asr / 'tokens.txt', tts / 'model.onnx', tts / 'tokens.txt', tts / 'lexicon.txt']:
            if not path.is_file():
                raise RuntimeError(f'Missing model file: {path}. Run download_models.py first.')
        threads = int(os.environ.get('INFERENCE_THREADS', '2'))
        if not 1 <= threads <= 8:
            raise RuntimeError('INFERENCE_THREADS must be 1..8')
        self.asr = sherpa_onnx.OfflineRecognizer.from_sense_voice(
            model=str(asr / 'model.int8.onnx'), tokens=str(asr / 'tokens.txt'),
            num_threads=threads, use_itn=True, language='auto', provider='cpu',
        )
        config = sherpa_onnx.OfflineTtsConfig(
            model=sherpa_onnx.OfflineTtsModelConfig(
                vits=sherpa_onnx.OfflineTtsVitsModelConfig(
                    model=str(tts / 'model.onnx'), tokens=str(tts / 'tokens.txt'),
                    lexicon=str(tts / 'lexicon.txt'), dict_dir=str(tts / 'dict'),
                ), num_threads=threads, provider='cpu',
            ), rule_fsts=','.join(str(tts / f) for f in ['date.fst', 'number.fst', 'phone.fst'] if (tts / f).exists()),
            max_num_sentences=1,
        )
        if not config.validate():
            raise RuntimeError('TTS model configuration is invalid')
        self.tts = sherpa_onnx.OfflineTts(config)
        # Load kernels and verify both model paths before marking ready.
        self.tts.generate('你好。', sid=0, speed=1.0)
        stream = self.asr.create_stream()
        stream.accept_waveform(16000, np.zeros(16000, dtype=np.float32))
        self.asr.decode_stream(stream)

    def transcribe(self, raw: bytes):
        if not self.slot.acquire(blocking=False):
            reject(429, 'speech_busy', '正在处理另一条语音，请稍后重试')
        start = time.monotonic()
        try:
            if not raw:
                reject(422, 'empty_audio', '请选择或录制音频')
            if len(raw) > MAX_AUDIO_BYTES:
                reject(413, 'audio_too_large', '音频文件不能超过 10MiB')
            with tempfile.TemporaryDirectory(prefix='speech-') as temp:
                source, output = Path(temp) / 'input', Path(temp) / 'decoded.wav'
                source.write_bytes(raw)
                try:
                    subprocess.run([
                        'ffmpeg', '-nostdin', '-v', 'error', '-y', '-protocol_whitelist', 'file,pipe',
                        '-format_whitelist', 'wav,mp3,ogg,matroska,webm,mov,aac,flac',
                        '-i', str(source), '-t', str(MAX_AUDIO_SECONDS + 0.1),
                        '-vn', '-ac', '1', '-ar', '16000', '-c:a', 'pcm_s16le', str(output),
                    ], check=True, capture_output=True, timeout=15)
                except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
                    reject(415, 'invalid_audio', '无法解码音频，请使用有效的 WAV、WebM、MP3 或 MP4 音频')
                samples, rate = sf.read(output, dtype='float32')
            duration = len(samples) / rate
            if duration > MAX_AUDIO_SECONDS:
                reject(413, 'audio_too_long', '录音不能超过 30 秒')
            if not len(samples):
                reject(422, 'empty_audio', '音频中没有采样数据')
            if np.max(np.abs(samples)) < 0.0001:
                text = ''
            else:
                stream = self.asr.create_stream()
                stream.accept_waveform(rate, samples)
                self.asr.decode_stream(stream)
                text = re.sub(r'<\|.*?\|>', '', stream.result.text).strip()
            return {'text': text, 'audio_seconds': round(duration, 3), 'elapsed_seconds': round(time.monotonic() - start, 3)}
        finally:
            self.slot.release()

    def synthesize(self, text: str, speed: float):
        if not self.slot.acquire(blocking=False):
            reject(429, 'speech_busy', '正在处理另一条语音，请稍后重试')
        start = time.monotonic()
        try:
            audio = self.tts.generate(text, sid=0, speed=speed)
            if not len(audio.samples):
                reject(422, 'unpronounceable_text', '文字未生成可播放的音频')
            output = io.BytesIO()
            sf.write(output, audio.samples, audio.sample_rate, format='WAV', subtype='PCM_16')
            return output.getvalue(), {
                'X-Audio-Seconds': f'{len(audio.samples) / audio.sample_rate:.3f}',
                'X-Elapsed-Seconds': f'{time.monotonic() - start:.3f}',
            }
        finally:
            self.slot.release()
