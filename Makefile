.DEFAULT_GOAL := release

PYTHON ?= python3
IMAGE ?= registry.admin.dressluck.top/ranklier/speech
BUILDER ?= speech-release
export IMAGE VERSION BUILDER

.PHONY: release publish release-plan login help

release publish:
	@$(PYTHON) tools/release.py

release-plan:
	@$(PYTHON) tools/release.py --dry-run

login:
	@docker login registry.admin.dressluck.top

help:
	@printf '%s\n' \
	  'make release       自动选版本，构建并验证 amd64 镜像，上传版本与 latest' \
	  'make release-plan  预览下一版本和目标，不构建、不上传' \
	  'make release VERSION=0.3.0  指定未使用的新版本' \
	  'make login         首次登录 Harbor；已有 Docker 登录可直接发布'
