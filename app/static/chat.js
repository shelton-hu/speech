// All conversation and playback state is scoped to this page, never localStorage.
let chatHistory = [], lastReply = '', chatBusy = false;
let playbackController, replyUrl;
const chatButtons = ['ask', 'askAudio', 'newChat', 'token', 'record', 'recognize', 'speak'];

function renderConversation() {
  el('conversation').textContent = chatHistory.map(item =>
    (item.role === 'user' ? '你：' : '助手：') + item.content).join('\n\n');
}

function stopReply() {
  playbackController?.abort();
  playbackController = null;
  el('replyPlayer').pause();
  el('replyPlayer').removeAttribute('src');
  el('replyPlayer').load();
  if (replyUrl) URL.revokeObjectURL(replyUrl);
  replyUrl = null;
  el('stopReply').disabled = true;
  el('replay').disabled = !lastReply || chatBusy;
}

function speechChunks(text) {
  const chunks = [];
  let chunk = '';
  // Code points, not UTF-16 halves: emoji and uncommon characters stay intact.
  for (const char of text) {
    chunk += char;
    if ([...chunk].length >= 350 || (chunk.length >= 120 && /[。！？\n]/u.test(char))) {
      if (chunk.trim()) chunks.push(chunk); chunk = '';
    }
  }
  if (chunk.trim()) chunks.push(chunk);
  return chunks;
}

function playSegment(signal) {
  const player = el('replyPlayer');
  return new Promise((resolve, reject) => {
    const cleanup = () => {
      player.removeEventListener('ended', ended);
      player.removeEventListener('error', failed);
      signal.removeEventListener('abort', aborted);
    };
    const ended = () => { cleanup(); resolve(); };
    const failed = () => { cleanup(); reject(Error('音频播放失败，可点击“播报上一条回答”重试')); };
    const aborted = () => { cleanup(); reject(new DOMException('Stopped', 'AbortError')); };
    player.addEventListener('ended', ended);
    player.addEventListener('error', failed);
    signal.addEventListener('abort', aborted, { once: true });
    if (signal.aborted) return aborted();
    player.play().catch(() => {
      if (!signal.aborted) show('chatStatus', '回答音频已生成，请点击播放器播放。');
    });
  });
}

async function speakReply(text) {
  if (recorder?.state === 'recording') {
    show('chatStatus', '请先停止录音，再播报回答。', true); return;
  }
  stopReply();
  el('player').pause();
  const controller = new AbortController();
  playbackController = controller;
  el('stopReply').disabled = false;
  el('replay').disabled = true;
  try {
    const chunks = speechChunks(text);
    for (let i = 0; i < chunks.length; i++) {
      show('chatStatus', `回答已生成，正在准备播报 ${i + 1}/${chunks.length}…`);
      const response = await api('/v1/speech', {
        method: 'POST', signal: controller.signal,
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ text: chunks[i], speed: Number(el('speed').value) }),
      });
      const blob = await response.blob();
      if (controller.signal.aborted) return;
      if (replyUrl) URL.revokeObjectURL(replyUrl);
      replyUrl = URL.createObjectURL(blob);
      el('replyPlayer').src = replyUrl;
      show('chatStatus', `正在播报 ${i + 1}/${chunks.length}`);
      await playSegment(controller.signal);
    }
    show('chatStatus', '回答播报完成，可以继续提问。');
  } catch (error) {
    if (!controller.signal.aborted) show('chatStatus', '文字回答已保留；播报失败：' + error.message, true);
  } finally {
    if (playbackController === controller) {
      playbackController = null;
      el('stopReply').disabled = true;
      el('replay').disabled = !lastReply || chatBusy;
    }
  }
}

async function ask(useAudio) {
  if (chatBusy) return;
  if (recorder?.state === 'recording') {
    show('chatStatus', '请先停止录音，再发送问题。', true); return;
  }
  stopReply();
  chatBusy = true;
  chatButtons.forEach(id => el(id).disabled = true);
  el('replay').disabled = true;
  let answer;
  try {
    let text = el('question').value.trim();
    if (useAudio) {
      const file = recorded || el('file').files[0];
      if (!file) throw Error('请先在下方录音或选择音频文件');
      if (file.size > 10 * 1024 * 1024) throw Error('文件超过 10MiB');
      show('chatStatus', '正在识别录音…');
      const form = new FormData(); form.append('file', file, 'recording');
      const data = await (await api('/v1/transcriptions', { method: 'POST', body: form })).json();
      text = data.text.trim();
      el('question').value = text;
      show('asr', text || '未识别到文字');
    }
    if (!text) throw Error(useAudio ? '没有识别到问题，请重新录音' : '请输入问题');
    const history = chatHistory.slice();
    while (history.length && text.length + history.reduce((n, item) => n + item.content.length, 0) > 32000) {
      history.splice(0, 2);
    }
    show('chatStatus', '正在生成回答…');
    answer = await (await api('/v1/chat', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ text, history }),
    })).json();
    chatHistory = [...history, { role: 'user', content: text }, { role: 'assistant', content: answer.text }].slice(-20);
    lastReply = answer.text;
    renderConversation();
    show('chatStatus', `回答完成，用时 ${answer.elapsed_seconds} 秒。`);
    el('question').value = '';
  } catch (error) {
    show('chatStatus', error.message, true);
  } finally {
    chatBusy = false;
    chatButtons.forEach(id => el(id).disabled = false);
    el('replay').disabled = !lastReply;
  }
  if (answer && el('autoSpeak').checked) void speakReply(answer.text);
}

el('ask').onclick = () => ask(false);
el('askAudio').onclick = () => ask(true);
el('replay').onclick = () => speakReply(lastReply);
el('stopReply').onclick = () => { stopReply(); show('chatStatus', '已停止播报，文字回答已保留。'); };
el('newChat').onclick = () => {
  stopReply(); chatHistory = []; lastReply = '';
  el('replay').disabled = true; el('question').value = '';
  renderConversation(); show('chatStatus', '已开始新对话。');
};
el('token').addEventListener('input', () => {
  stopReply(); chatHistory = []; lastReply = '';
  el('replay').disabled = true; renderConversation();
  show('chatStatus', '设备令牌已更改，对话已清空。');
  show('status', '');
});
const recordClick = el('record').onclick;
el('record').onclick = async () => { stopReply(); el('player').pause(); await recordClick(); };
const speakClick = el('speak').onclick;
el('speak').onclick = () => { stopReply(); return speakClick(); };
window.addEventListener('pagehide', stopReply);
