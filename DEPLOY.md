# speech 0.2.0：NAS 部署与升级

本文件可交给 NAS 上的运维助手执行。目标是运行 **speech 0.2.0**，项目名为 `speech`。已有部署时走“从 0.1.0 升级”，保留现有访问口令、模型密钥、端口和反向代理配置。

## 版本与部署文件

- 源码与文档：<https://github.com/shelton-hu/speech/tree/v0.2.0>
- Compose：<https://raw.githubusercontent.com/shelton-hu/speech/v0.2.0/compose.yaml>
- 配置模板：<https://raw.githubusercontent.com/shelton-hu/speech/v0.2.0/.env.example>
- 镜像：`registry.admin.dressluck.top/ranklier/speech:0.2.0`
- 镜像索引摘要：`sha256:00f76d23da30b1cecc47a23507bebf290c904bd2935b159072d45bfbe5134ddc`
- 支持平台：`linux/amd64`，即 Intel/AMD x86-64 NAS。当前未交付 ARM 镜像。

运行时只需同一目录下的两个文件：

```text
speech/
  compose.yaml
  .env
```

镜像已经包含程序、语音识别和语音合成模型。NAS 无需安装 Python、克隆全部源码、编译镜像、运行 setup/model-init 或下载模型。需要 Docker Engine 与 Docker Compose V2；两个服务的内存上限合计 4.5 GiB，请预留至少约 5 GiB 可用内存和足够镜像磁盘空间。

2026-11-01 已验证镜像清单可以匿名访问。若 NAS 拉取时提示需要认证，使用仓库管理员提供的凭据执行 `docker login registry.admin.dressluck.top`。

## 首次部署

在 NAS 上选择一个持久化目录，创建并进入 `speech` 子目录，然后下载两个文件。以下 `.env` 下载命令仅适用于**新目录**；已有部署不得覆盖其 `.env`。

```sh
mkdir -p speech
cd speech
curl -fL --retry 3 https://raw.githubusercontent.com/shelton-hu/speech/v0.2.0/compose.yaml -o compose.yaml
curl -fL --retry 3 https://raw.githubusercontent.com/shelton-hu/speech/v0.2.0/.env.example -o .env
chmod 600 .env
```

编辑 `.env` 的四项配置：

```dotenv
APP_TOKEN=自行设置的至少24字符访问口令
LLM_BASE_URL=https://你的sub2api域名/v1
LLM_MODEL=你的模型ID
LLM_API_KEY=你的模型密钥
```

`APP_TOKEN` 用于网页/API 登录。后三项连接 sub2api 或其他 OpenAI 兼容服务，密钥只保存在 NAS。Base URL 包含服务要求的路径（常见为 `/v1`），不要追加 `/responses` 或 `/chat/completions`。模板中的 `gpt-6-sol` 是已测试过的模型 ID 示例，使用你的上游实际支持的模型。包含 `$`、`#` 等特殊字符的配置值建议用单引号包裹，避免 Compose 插值或注释截断。

首次启动：

```sh
docker compose -f compose.yaml --env-file .env config --quiet
docker compose -f compose.yaml --env-file .env pull
docker compose -f compose.yaml --env-file .env up -d --wait --wait-timeout 240
```

浏览器打开 `http://NAS的IP:18080`，输入 `APP_TOKEN`。语音录音需要 HTTPS 安全上下文，请通过已有的 HTTPS 反向代理域名访问；普通局域网 HTTP 可先验证文字提问和音频播放。

## 从 0.1.0 升级

1. **定位现有部署目录和 Compose 项目。** 项目名应为 `speech`。可先运行：

   ```sh
   docker ps -a --filter label=com.docker.compose.project=speech --format '{{.Names}}  {{.Image}}  {{.Status}}'
   ```

   必要时读取现有 assistant 容器的 Compose 工作目录标签，或使用 NAS Docker 管理界面定位。升级前记下原来的 Compose 文件、覆盖文件和启动方式。

2. **在原部署目录备份。** 将原 `.env`、Compose 文件、旧覆盖文件复制到带时间戳的备份目录，权限保持私有。保留原 `models/` 目录；新版不挂载它，但回退旧版可能仍需要。不要输出 `.env` 内容，也不要把配置备份提交到 GitHub。

3. **仅替换主 Compose。** 在原部署目录执行：

   ```sh
   curl -fL --retry 3 https://raw.githubusercontent.com/shelton-hu/speech/v0.2.0/compose.yaml -o compose.yaml.new
   mv compose.yaml.new compose.yaml
   ```

4. **合并配置，不覆盖 `.env`。** 保留原 `APP_TOKEN`、`APP_PORT`、`BIND_ADDRESS` 和已有的 `SPEECH_TOKEN`；补充缺失的 `LLM_BASE_URL`、`LLM_MODEL`、`LLM_API_KEY`。若已有 `SPEECH_IMAGE`，把它更新为 `registry.admin.dressluck.top/ranklier/speech:0.2.0`，避免覆盖新版默认值。原令牌不足 24 字符时需要更换，并同步更新使用它的客户端。

5. **拉取并启动。** 显式使用唯一的 `compose.yaml`，不再叠加旧的 `compose.amd64.yaml`、`docker-compose.override.yml` 等覆盖文件：

   ```sh
   docker compose -f compose.yaml --env-file .env config --quiet
   docker compose -f compose.yaml --env-file .env config --images
   docker compose -f compose.yaml --env-file .env pull
   docker compose -f compose.yaml --env-file .env up -d --wait --wait-timeout 240
   ```

   确认 `config --images` 中的版本都是 `0.2.0`。当前终端中若已导出了 `SPEECH_IMAGE`、`APP_PORT`、`BIND_ADDRESS` 或模型配置变量，它们可能优先于 `.env`，执行前清理冲突或核实配置来源。

不要执行 `down -v`、删除模型目录、覆盖原令牌、运行 `make release` 或修改 NAS 全局 Docker 配置。`make release` 是源码维护者的发布命令，NAS 部署只拉取既有镜像。无需先停止旧容器；Compose 在更新时替换服务。

## 部署验收

检查两个服务均为 `healthy`：

```sh
docker compose -f compose.yaml --env-file .env ps
docker compose -f compose.yaml --env-file .env logs --tail 80 assistant speech
```

在 assistant 容器内检查版本和语音就绪状态。此命令使用容器已有令牌，**不会打印密钥，也不会调用大模型**：

```sh
docker compose -f compose.yaml --env-file .env exec -T assistant python - <<'PY'
import json, os, urllib.request
def get(path, auth=False):
    headers = {'Authorization': 'Bearer ' + os.environ['APP_TOKEN']} if auth else {}
    with urllib.request.urlopen(urllib.request.Request('http://127.0.0.1:8000' + path, headers=headers), timeout=15) as r:
        return json.load(r)
health = get('/healthz')
assert health['version'] == '0.2.0', health
capabilities = get('/v1/capabilities', True)
assert capabilities['speech_ready'], 'Speech service is not ready'
print(json.dumps({'version': health['version'], 'speech_ready': capabilities['speech_ready'],
                  'llm_configured': capabilities['llm_configured']}, ensure_ascii=False))
PY
```

`llm_configured=true` 只代表配置完整，不保证上游密钥或模型可用。随后在网页发送“你好，请用一句话介绍自己”，验证回答和播放；再追问上一条内容，验证多轮上下文。大模型未配置时 ASR/TTS 仍可使用，但问答不能通过验收。

如需检查 NAS 对外入口，访问 `http://NAS的IP:端口/healthz`；HTTPS 域名也应返回相同版本。如果容器内正常而公网为 502，检查现有反向代理/frp 到 NAS 端口的映射。不要用修改模型密钥的方法处理反向代理错误。

给部署发起人的验收结果应包含：部署目录、实际镜像版本、两个服务健康状态、局域网/HTTPS 可达性、真实问答和播报结果；不得包含访问口令或模型密钥。

## 回退与后续更新

升级失败时，恢复升级前备份的 `.env`、Compose 及原覆盖文件，按原启动方式执行 `up -d`。回退 0.1.0 要恢复它所需的模型挂载和原配置，不能只把新 Compose 的镜像名换回旧版。

修改 `.env` 后执行 `up -d` 重新创建受影响服务；`restart` 不会重新载入环境变量。将来更新到其他版本时，下载对应 Git 标签的 Compose，并核对 `SPEECH_IMAGE`。默认固定到 0.2.0，不会因仓库 `latest` 改变而自动升级。

可选配置仍放同一个 `.env`：

| 配置 | 默认值 | 用途 |
| --- | --- | --- |
| `APP_PORT` | `18080` | NAS 对外端口 |
| `BIND_ADDRESS` | `0.0.0.0` | 监听地址，升级时保留原值 |
| `SPEECH_IMAGE` | `registry.admin.dressluck.top/ranklier/speech:0.2.0` | 显式指定镜像 |
| `SPEECH_TOKEN` | 自动派生 | 兼容旧版内部令牌，新部署不用填写 |
| `INFERENCE_THREADS` | `2` | 本地语音推理线程数 |
| `LLM_PROTOCOL` | `responses` | 也可设为 `chat_completions` |
| `LLM_STREAM` | `false` | 上游要求 SSE 时设为 `true`，客户端仍按轮显示 |
| `LLM_TIMEOUT_SECONDS` | `90` | 模型回答总超时 |
| `LLM_MAX_OUTPUT_TOKENS` | `2048` | 模型输出预算 |

## 0.2.0 的边界

本版包含浏览器语音问答与 OpenAI 兼容模型接入。对话只保存在当前页面内存，刷新会清空，没有数据库迁移。天猫精灵客户端/官方技能接入尚未交付，部署成功不表示天猫精灵设备已接通。
