# speech

部署在 NAS 上的中文语音与对话服务：本地语音识别、OpenAI 兼容模型问答、本地语音播报，以及浏览器测试页。

**当前版本：0.2.0。NAS 部署、旧版升级和验收请阅读 [DEPLOY.md](DEPLOY.md)。** 可以直接把这份文档交给 NAS 上的运维助手执行。

## 部署

NAS 只需要 `compose.yaml` 和 `.env`。镜像内置语音模型，无需安装 Python、编译代码或运行初始化脚本。支持 `linux/amd64` NAS。

从 [v0.2.0](https://github.com/shelton-hu/speech/tree/v0.2.0) 获取 `compose.yaml`，把 `.env.example` 保存为 `.env`，填写：

```dotenv
APP_TOKEN=自行设置的至少24字符访问口令
LLM_BASE_URL=https://你的sub2api域名/v1
LLM_MODEL=你的模型ID
LLM_API_KEY=你的模型密钥
```

在两个文件所在目录运行：

```sh
docker compose -f compose.yaml --env-file .env pull
docker compose -f compose.yaml --env-file .env up -d --wait --wait-timeout 240
```

浏览器打开 `http://NAS的IP:18080`，输入 `APP_TOKEN`。录音需要通过 HTTPS 访问；可先在局域网验证文字问答与音频播放。

Compose 默认使用 `registry.admin.dressluck.top/ranklier/speech:0.2.0`，项目名统一为 `speech`。从旧版升级时保留原 `.env`、端口、令牌和模型目录，按 [升级步骤](DEPLOY.md#从-010-升级) 操作，不再叠加旧 Compose 覆盖文件。

## 功能

- SenseVoiceSmall INT8 本地语音识别，MeloTTS ONNX 本地语音合成。
- 通过 sub2api 或其他 OpenAI 兼容服务调用模型，支持 Responses / Chat Completions 和上游 JSON / SSE。
- 浏览器文字或录音提问、最近 10 轮页面内上下文、回答播报、单独重播和停止播放。
- 访问令牌、输入大小限制、并发限制，以及可读的上游错误提示。
- 大模型配置可选；未配置时仍可使用语音识别和合成。

0.2.0 已完成本地真实模型问答、语音往返和两文件容器部署检查。具体 NAS 环境、真实麦克风和外网入口仍需按部署文档验收。短句语音识别可能出错；没有宣称一般识别准确率已验收。天猫精灵设备端及官方技能接入不在此版本中，长期记忆与实时语音也尚未实现。

## 开发与发布

```sh
make release
```

自动选择下一版本、构建并验证 amd64 镜像、上传版本标签及 `latest`，并输出 `dist/<版本>/compose.yaml` 与不含密钥的 `.env` 模板。部署文件固定到对应发布版本。该命令不操作 NAS，也不自动提交或推送 Git。

- [部署与升级](DEPLOY.md)
- [开发、验证与镜像发布](DEVELOPMENT.md)
- [API 文档](API.md) · [OpenAPI](openapi.json)
- [架构与范围](docs/ARCHITECTURE.md)

`.env`、模型缓存、录音、验证日志和本机凭据不进入 Git。模型在构建时从上游下载，下载来源与许可见 [scripts/download_models.py](scripts/download_models.py) 以及各上游模型仓库。
