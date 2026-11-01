# 语音与问答接口

当前开发版增加通过 sub2api 调用 OpenAI 模型的同步问答，保留独立 ASR 与 TTS。持久会话、人物记忆和设备流式协议仍属于后续范围。

HTTP 接口的机器可读合同由已实现的 FastAPI 模型和路由生成，位于 `/openapi.json`，项目中的同名文件是其导出快照。所有 `/v1/` 和 `/readyz` 请求必须带 `Authorization: Bearer <APP_TOKEN>`；UI 不在浏览器持久保存令牌。

| 接口 | 请求 | 成功结果 |
| --- | --- | --- |
| `GET /healthz` | 无 | 200 存活 |
| `GET /readyz` | Bearer | 200 模型就绪，或 503 语音服务不可用 |
| `GET /v1/capabilities` | Bearer | 200 当前模型、输入格式和限制 |
| `POST /v1/transcriptions` | multipart `file` | 200 JSON：text、audio_seconds、elapsed_seconds；中文整句识别 |
| `POST /v1/speech` | JSON：text，speed 可选且范围 0.5 到 2.0 | 200 单声道 PCM16 WAV，含生成耗时与音频时长响应头 |
| `POST /v1/chat` | JSON：text，history 可选 | 200 JSON：text、model、elapsed_seconds |

`capabilities` 的 `llm_configured` 只表示配置完整，不表示已验证上游密钥或模型；`llm` 返回模型、协议和无敏感值的配置提示。`speech_ready` 单独报告语音服务状态；语音服务故障时仍返回 200，便于继续使用文字问答。`/readyz` 仍保留原有的语音就绪检查含义。

## 同步问答合同

`POST /v1/chat` 使用同一设备令牌；`text` 为非空字符串，最多 4000 字符。`history` 默认为空，最多 20 条，必须按 user、assistant 成对排列；每条包含 `role` 和 `content`（最多 12000 字符），历史加当前问题总计不超过 32000 字符。客户端不能提交 system 或 developer 角色。成功返回的 `text` 只包含回答正文，不包含模型推理；拒答作为可展示的回答返回。未完成、空白或超限的模型输出返回明确错误，不作为完整回答播报。

服务端配置协议、Base URL、API Key 和模型，客户端不能覆盖。未配置时返回 503 `llm_not_configured`，不影响原有识别和播报。上游鉴权/模型/参数错误返回 502 `llm_rejected`，限流返回 429 `llm_rate_limited`，服务端并发已满返回 429 `llm_busy`，网络故障返回 503 `llm_unavailable`，超时返回 504 `llm_timeout`，无效响应返回 502 `llm_invalid_response`，未完成输出返回 502 `llm_incomplete`。不透传上游错误正文，也不自动重试可能产生费用的请求。

历史由客户端维护；浏览器仅在当前页面内存保存最近的完整问答，刷新或点击“新对话”清空。每次请求将历史传给模型，服务端不落库；它不是长期记忆。文字问答独立于语音服务；测试页使用既有识别和播报接口串联语音对话，播报失败时保留已经生成的文字回答，允许单独重播。

文字去除首尾空白后必须非空，最多 400 字符。音频上传最大 10MiB（另有 multipart 包装余量），最多 30 秒；FFmpeg 解码为 16kHz 单声道，原上传在请求结束后删除。浏览器 WebM、WAV、MP3、MP4 等格式按运行容器中的 FFmpeg 支持处理，MIME 不是安全判定依据。

统一错误格式为 `{"error":{"code":"...","message":"...","request_id":"..."}}`。401 鉴权失败；413 输入体积或时长超限；415 无法解码的音频；422 参数校验失败；429 推理槽位已占用；502 内部语音服务错误；503 模型未就绪；504 语音服务超时。

speech 服务仅在 Docker 内部网络开放，使用独立令牌。未配置 `SPEECH_TOKEN` 时，两个服务通过同一 `APP_TOKEN` 和固定用途标识派生内部令牌；直接使用网页令牌访问内部语音接口会被拒绝。旧配置中的显式 `SPEECH_TOKEN` 仍优先使用。一个推理任务正在运行时，后续推理立即返回 429，不积压无限队列。客户端可稍后手动重试。当前请求在客户端断开后可能仍完成 CPU 推理，槽位在实际推理结束后才释放。

模型缺失时 speech 启动失败并在日志说明缺失路径；存活探针不执行推理。当前接口按轮同步返回，未实现 README 中的异步任务、取消、人物与长期记忆。
