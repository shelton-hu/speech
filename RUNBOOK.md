# NAS 运维入口

部署、从 0.1.0 升级到 0.2.0、验收和回退步骤统一维护在 [DEPLOY.md](DEPLOY.md)。

默认镜像固定为 `registry.admin.dressluck.top/ranklier/speech:0.2.0`。NAS 运行时仅需 `compose.yaml` 与私有 `.env`；升级时保留原口令、模型配置和端口。

源码维护者的构建与发布步骤见 [DEVELOPMENT.md](DEVELOPMENT.md)，接口见 [API.md](API.md)。
