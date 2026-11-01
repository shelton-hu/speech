"""Upload verified OCI images in bounded chunks using a pinned regctl binary."""
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import subprocess
import urllib.request

REGCTL_VERSION = 'v0.11.3'
# SHA-256 digests from the upstream v0.11.3 GitHub release assets.
REGCTL_HASHES = {
    'darwin-amd64': 'e3724dbff445471e4a93faf24864db4e04f93f72b6811ae1ce085660982f9d61',
    'darwin-arm64': 'c2885851ab28bdc4850a79a96f01af5f3f55a17e404cb7897ea864f0f5e5615b',
    'linux-amd64': 'ac9223101f1fad7beda29c4236d5119d72561a7258f0fd77ce4805bf6d86ab32',
    'linux-arm64': 'a1fa2203b9eec02684af230993d219c8e67827a5180de8f8e79d68b5f14d677b',
}


def prepare_uploader(directory):
    machine = {'x86_64': 'amd64', 'aarch64': 'arm64'}.get(platform.machine(), platform.machine())
    target = platform.system().lower() + '-' + machine
    if target not in REGCTL_HASHES:
        raise RuntimeError('发布支持 macOS / Linux 的 amd64 或 arm64 开发机')
    binary = directory / f'regctl-{REGCTL_VERSION}-{target}'
    expected = REGCTL_HASHES[target]
    if not binary.exists():
        print('首次准备分块上传工具 regctl（固定版本并校验 SHA-256）', flush=True)
        url = f'https://github.com/regclient/regclient/releases/download/{REGCTL_VERSION}/regctl-{target}'
        with urllib.request.urlopen(url, timeout=60) as response:
            data = response.read()
        if hashlib.sha256(data).hexdigest() != expected:
            raise RuntimeError('上传工具下载校验失败，未执行')
        binary.write_bytes(data)
        binary.chmod(0o755)
    if hashlib.sha256(binary.read_bytes()).hexdigest() != expected:
        raise RuntimeError(f'上传工具本地校验失败，请删除 {binary} 后重试')
    return binary


def verified_manifest(layout, image_id):
    index = json.loads((layout / 'index.json').read_text())
    entries = index.get('manifests', [])
    if len(entries) != 1:
        raise RuntimeError('预期导出一个已验证的平台镜像')
    digest = entries[0]['digest']
    if not re.fullmatch(r'sha256:[0-9a-f]{64}', digest):
        raise RuntimeError('OCI 清单摘要格式不正确')
    data = (layout / 'blobs' / 'sha256' / digest.split(':')[1]).read_bytes()
    if 'sha256:' + hashlib.sha256(data).hexdigest() != digest:
        raise RuntimeError('OCI 清单内容与摘要不匹配')
    manifest = json.loads(data)
    if manifest.get('config', {}).get('digest') != image_id:
        raise RuntimeError('待上传镜像与通过部署验证的镜像不一致')
    return digest


def upload(binary, layout, reference, image_id):
    digest = verified_manifest(layout, image_id)
    # Isolated, temporary transport settings; credentials remain in Docker's existing store.
    env = dict(os.environ, REGCTL_CONFIG=str(layout.parent / 'regctl.json'))
    registry = reference.split('/')[0]
    subprocess.run([str(binary), 'registry', 'set', registry, '--blob-chunk', '8388608',
                    '--blob-max', '8388608', '--req-concurrent', '1'], env=env, check=True, timeout=60)
    print('开始按 8 MiB 分块上传，复用仓库中已有的镜像层', flush=True)
    subprocess.run([str(binary), 'image', 'copy', '--platform', 'linux/amd64',
                    'ocidir://' + str(layout) + '@' + digest, reference], env=env, check=True, timeout=7200)
