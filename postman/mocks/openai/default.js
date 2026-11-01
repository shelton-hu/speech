// Local fixture derived from OpenAI text-generation docs; not a live sub2api claim.
const http = require('http');
let requests = [];
http.createServer(async (req, res) => {
  const json = (status, data) => {
    res.writeHead(status, {'Content-Type': 'application/json'}); res.end(JSON.stringify(data));
  };
  if (req.url === '/audit') return json(200, requests);
  if (!['/v1/responses', '/v1/chat/completions'].includes(req.url)) return json(501, {error: {message: 'Unmodelled route'}});
  const body = req.body;
  const responses = req.url === '/v1/responses';
  const messages = responses ? body.input : body.messages;
  requests.push({path: req.url, body});
  if (req.headers.authorization !== 'Bearer mock-upstream-key') return json(401, {error: {message: 'PRIVATE UPSTREAM ERROR'}});
  if (!body.model || !Array.isArray(messages) || (responses && (!body.instructions || body.store !== false)) ||
      (!responses && messages[0]?.role !== 'system')) return json(400, {error: {message: 'Invalid request contract'}});
  const question = messages.at(-1).content;
  if (question.startsWith('error:')) return json(Number(question.split(':')[1]), {error: {message: 'PRIVATE UPSTREAM ERROR'}});
  if (question === 'slow') await new Promise(r => setTimeout(r, 1500));
  if (question === 'html') {res.writeHead(200, {'Content-Type': 'text/html'}); return res.end('<html>private</html>');}
  if (question === 'large') {res.writeHead(200); return res.end('x'.repeat(2 * 1024 * 1024 + 1));}
  let text = '你好，这是模型回答。';
  if (question === 'recall') text = '你上一条说：' + messages.filter(m => m.role === 'user').at(-2)?.content;
  if (question === 'long') text = '这是较长回答，需要分段播报。'.repeat(35);
  if (question === 'empty') text = '   ';
  const incomplete = question === 'incomplete';
  const refusal = question === 'refusal';
  const response = {id: 'resp_fixture', object: 'response', model: body.model,
    status: incomplete ? 'incomplete' : 'completed', output: [
      {type: 'reasoning', summary: [{type: 'summary_text', text: 'DO NOT SPEAK REASONING'}]},
      {type: 'message', role: 'assistant', status: 'completed', content: [
        refusal ? {type: 'refusal', refusal: '无法帮助处理这个请求。'} : {type: 'output_text', text, annotations: []},
      ]},
    ]};
  const completion = {id: 'chatcmpl_fixture', object: 'chat.completion', model: body.model,
    choices: [{index: 0, finish_reason: incomplete ? 'length' : 'stop',
      message: {role: 'assistant', content: refusal ? null : text, refusal: refusal ? '无法帮助处理这个请求。' : null}}]};
  if (!body.stream) return json(200, responses ? response : completion);
  res.writeHead(200, {'Content-Type': 'text/event-stream'});
  const event = data => res.write('data: ' + JSON.stringify(data) + '\n\n');
  if (responses) {
    event({type: 'response.reasoning_summary_text.delta', delta: 'DO NOT SPEAK REASONING'});
    event({type: 'response.output_text.delta', delta: text});
    if (question !== 'truncated') event({type: incomplete ? 'response.incomplete' : 'response.completed', response});
  } else {
    event({choices: [{index: 0, delta: refusal ? {refusal: '无法帮助处理这个请求。'} : {content: text}, finish_reason: null}]});
    if (question !== 'truncated') event({choices: [{index: 0, delta: {}, finish_reason: incomplete ? 'length' : 'stop'}]});
    res.write('data: [DONE]\n\n');
  }
  res.end();
}).listen(process.env.PORT);
