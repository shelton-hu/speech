# 开发与发布

源码目录与 NAS 部署目录分开：本目录保留代码、测试与发布工具；NAS 只收到 `compose.yaml` 和 `.env`。

## 发布

```sh
make release
```

默认目标为 `registry.admin.dressluck.top/ranklier/speech`，平台为飞牛 NAS 使用的 `linux/amd64`。本机需要 Docker（含 Compose、Buildx）、Python 3 和 make；使用 Docker 已有登录状态，首次需要时运行 `make login`。

发布流程：

1. 根据 `VERSION`、远端 `latest` 和已占用标签选择下一版本。首次使用 `0.2.0`，后续自动递增补丁号，不覆盖版本镜像。
2. 准备 `models/` 缓存，完整模型直接复用，缺失时下载。打包过程采用明确的源码白名单，不包含 `.env`、本机配置、测试记录或密钥。
3. 自动创建并复用 `speech-release` 构建器，将源码复制为构建快照，构建含模型的 amd64 镜像；模型单独成层，后续代码更新可复用层。
4. 在临时目录仅放两个部署文件，实际启动镜像，验证版本、鉴权、内置模型、真实识别与播报。验证容器退出时自动清理。
5. 从同一快照导出 OCI 镜像，先比对通过部署验证的镜像 ID，再通过 regctl 以 8 MiB 分块串行上传 `<版本>-amd64`。首次自动下载固定版本工具并核对内置 SHA-256，凭据复用 Docker 登录；无需用户安装额外工具。核对远端与本地镜像一致后建立 `<版本>` 与 `latest`，核对摘要相同后记录成功。
6. 写入 `.release/<版本>.json` 与 `.release/latest.json`，更新 `VERSION`，输出固定到本次版本的 `dist/<版本>/compose.yaml` 和不含密钥的 `.env` 模板。

发布命令不操作 NAS 容器，不创建 Git 提交，也不推送源码。项目以独立 Git 仓库维护；发布记录仍使用实际构建上下文的 SHA-256，用于核对镜像内容。公开源码版本通过 Git 标签标识；NAS 部署见 [DEPLOY.md](DEPLOY.md)。

其他入口：

```sh
make release-plan
make release VERSION=0.3.0
```

`release-plan` 只读取版本和仓库，不构建、不上传。显式版本必须未占用且高于已发布 latest。构建、验证或上传失败不会标记发布成功；某个版本标签部分上传后，下一次自动跳过该版本，不覆盖。

初次实测普通 Docker push 遇到 504，BuildKit 的整层上传也长时间等待，小块上传验证成功，因此采用分块上传，单次上传任务最多等待 2 小时。首次镜像包含约 438 MB 的模型，在低带宽环境需要较长时间；后续仅修改代码时复用模型和依赖层。传输设置只写到发布临时目录，不修改用户的 Docker 或 regctl 全局配置。工具和分块配置参考 [regctl 配置说明](https://regclient.org/usage/regctl/) 与 [registry set](https://regclient.org/cli/regctl/registry/set/)。

## 本地检查

发布工具的版本安全测试无需额外依赖：

```sh
python3 -m unittest discover -s tests -v
```

应用开发依赖可用 uv 安装：

```sh
uv venv .venv
uv pip install --python .venv/bin/python -r requirements.txt
```

使用 Postman 的既有本地模拟验证模型协议（两个终端分别运行）：

```sh
postman simulation run postman/simulations/chat.sim.yaml --port 18490 --output ndjson --no-history
.venv/bin/python scripts/check_chat.py
```

浏览器脚本 `scripts/check_chat_browser.py` 还需要 Playwright 与本机 Chrome。真实模型验收脚本 `scripts/check_chat_live.py --base-url <gateway地址>` 从环境变量读取 `APP_TOKEN`，会发送三次模型请求；测试结果位于 `artifacts/verification/live-chat/`。不要把真实密钥写进测试文件。

## 已有验证证据

2026-11-01 本机通过 sub2api、请求模型 `gpt-6-sol` 的 Responses JSON/SSE 与历史追问。自然语音问题的 ASR / 模型 / TTS 三阶段样本耗时约 0.25 / 2.43 / 0.56 秒；这是功能样本而非性能基准。

首次“二加三”合成录音曾被 ASR 识别成“外加三”，模型询问是否指“五加三”并回答八。原测试只搜索“五/5”会误判，断言已加强，失败记录保存在 `initial-roundtrip-finding.json`。自然问题复测成功不代表此短句识别问题已修复。
