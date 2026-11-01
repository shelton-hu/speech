const http = require('http');
http.createServer((req, res) => {
  const json = (status, data) => {res.writeHead(status, {'Content-Type': 'application/json'}); res.end(JSON.stringify(data));};
  if (req.url === '/readyz') return json(200, {asr: 'fixture-asr', tts: 'fixture-tts'});
  if (req.url === '/v1/transcriptions') return json(200, {text: '录音问题', audio_seconds: 1, elapsed_seconds: 0.01});
  if (req.url === '/v1/speech') {
    if (Array.from(req.body.text).length > 400) return json(422, {error: {code: 'validation_error', message: 'Too long'}});
    const wav = Buffer.alloc(44 + 16000);
    wav.write('RIFF', 0); wav.writeUInt32LE(wav.length - 8, 4); wav.write('WAVEfmt ', 8);
    wav.writeUInt32LE(16, 16); wav.writeUInt16LE(1, 20); wav.writeUInt16LE(1, 22);
    wav.writeUInt32LE(16000, 24); wav.writeUInt32LE(32000, 28); wav.writeUInt16LE(2, 32); wav.writeUInt16LE(16, 34);
    wav.write('data', 36); wav.writeUInt32LE(16000, 40);
    res.writeHead(200, {'Content-Type': 'audio/wav', 'X-Audio-Seconds': '0.5', 'X-Elapsed-Seconds': '0.01'});
    return res.end(wav);
  }
  json(501, {error: {code: 'not_modelled', message: 'Unmodelled route'}});
}).listen(process.env.PORT);
