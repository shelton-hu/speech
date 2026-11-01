"""Build, verify and publish a versioned speech image and the latest alias."""
import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile

from check_deployment import check_image
from registry_upload import prepare_uploader, upload

DEFAULT_IMAGE = 'registry.admin.dressluck.top/ranklier/speech'
VERSION_PATTERN = re.compile(r'(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\Z')
MODEL_NAMES = ('sherpa-onnx-sense-voice-zh-en-ja-ko-yue-int8-2024-07-17', 'vits-melo-tts-zh_en')


def parts(version):
    match = VERSION_PATTERN.fullmatch(version)
    if not match:
        raise ValueError('版本格式应为 0.2.0 这样的三段数字')
    return tuple(map(int, match.groups()))


def increment(version):
    major, minor, patch = parts(version)
    return f'{major}.{minor}.{patch + 1}'


def manifest(reference):
    result = subprocess.run(['docker', 'buildx', 'imagetools', 'inspect', '--raw', reference], capture_output=True)
    if result.returncode:
        message = (result.stderr + result.stdout).decode(errors='replace').strip()
        if re.search(r'manifest unknown|no such manifest|:\s*not found\s*$', message, re.I):
            return None
        raise RuntimeError(f'无法读取镜像 {reference}；请检查 Docker 登录与网络。\n{message}')
    value = json.loads(result.stdout)
    if value.get('schemaVersion') != 2:
        raise RuntimeError('镜像仓库返回了无法识别的清单')
    value['digest'] = 'sha256:' + hashlib.sha256(result.stdout).hexdigest()
    return value


def choose_version(image, current, explicit='', inspect=manifest):
    parts(current)
    latest = inspect(image + ':latest')
    remote = (latest or {}).get('annotations', {}).get('org.opencontainers.image.version')
    if latest and not remote:
        raise RuntimeError('latest 缺少版本信息，请先核对已有发布，不自动覆盖未知版本')
    candidate = explicit or current
    parts(candidate)
    if remote:
        parts(remote)
        if explicit and parts(explicit) <= parts(remote):
            raise RuntimeError('指定版本必须高于已发布的 latest')
        if not explicit and parts(candidate) <= parts(remote):
            candidate = increment(remote)
    for _ in range(100):
        if inspect(f'{image}:{candidate}') is None and inspect(f'{image}:{candidate}-amd64') is None:
            return candidate
        if explicit:
            raise RuntimeError('指定版本已存在，禁止覆盖；请使用新版本')
        candidate = increment(candidate)
    raise RuntimeError('连续版本已占用，请检查版本记录')


def execute(args, **kwargs):
    subprocess.run(args, check=True, **kwargs)


def prepare_builder(root):
    builder = os.getenv('BUILDER') or 'speech-release'
    if not re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9_.-]*', builder):
        raise ValueError('BUILDER 名称格式不正确')
    result = subprocess.run(['docker', 'buildx', 'inspect', builder], capture_output=True)
    if result.returncode:
        execute(['docker', 'buildx', 'create', '--name', builder, '--driver', 'docker-container',
                 '--buildkitd-config', str(root / 'buildkitd.toml')])
    execute(['docker', 'buildx', 'inspect', builder, '--bootstrap'], stdout=subprocess.DEVNULL)
    return builder


def snapshot(root, destination, version):
    for name in ('Dockerfile', 'requirements.txt', '.dockerignore', 'buildkitd.toml'):
        shutil.copyfile(root / name, destination / name)
    (destination / 'VERSION').write_text(version + '\n')
    shutil.copytree(root / 'app', destination / 'app', ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
    digest = hashlib.sha256()
    for path in sorted(p for p in destination.rglob('*') if p.is_file()):
        digest.update(str(path.relative_to(destination)).encode() + b'\0' + path.read_bytes())
    for name in MODEL_NAMES:
        source = root / 'models' / name
        digest.update((source / 'download.json').read_bytes())
        # Cached model files are immutable; same-filesystem hardlinks avoid a second large copy.
        def copy(source, target):
            try:
                os.link(source, target)
            except OSError:
                shutil.copy2(source, target)
        shutil.copytree(source, destination / 'models' / name, copy_function=copy)
    return digest.hexdigest()


def write_json(path, value):
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')
    temporary.replace(path)


def deployment_files(root, version, image):
    directory = root / 'dist' / version
    directory.mkdir(parents=True, exist_ok=True)
    compose = re.sub(r'\$\{SPEECH_IMAGE:-[^}]+\}',
                     lambda _: '${SPEECH_IMAGE:-' + image + ':' + version + '}',
                     (root / 'compose.yaml').read_text())
    (directory / 'compose.yaml').write_text(compose)
    shutil.copyfile(root / '.env.example', directory / '.env')
    return directory


@contextmanager
def locked(directory):
    directory.mkdir(exist_ok=True)
    with (directory / 'lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError('已有发布任务正在运行') from None
        try:
            yield
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def release(root, dry_run=False):
    image = os.getenv('IMAGE') or DEFAULT_IMAGE
    if not re.fullmatch(r'[a-z0-9][a-z0-9.:-]*/[a-z0-9][a-z0-9._/-]*', image):
        raise ValueError('IMAGE 必须是完整镜像仓库路径，不包含版本或凭据')
    current = (root / 'VERSION').read_text().strip()
    explicit = os.getenv('VERSION', '').strip()
    if dry_run:
        version = choose_version(image, current, explicit)
        print(f'版本：{version}\n镜像：{image}:{version}-amd64\n同时发布：{version}、latest\n平台：linux/amd64\n仅预览，未构建或上传。')
        return
    directory = root / '.release'
    with locked(directory):
        version = choose_version(image, current, explicit)
        reference = f'{image}:{version}-amd64'
        print(f'发布 {reference}：准备模型 → 构建 → 两文件部署验证 → 上传', flush=True)
        uploader = prepare_uploader(directory)
        builder = prepare_builder(root)
        execute([sys.executable, str(root / 'scripts/download_models.py'), '--dir', str(root / 'models')])
        with tempfile.TemporaryDirectory(prefix='source-', dir=directory) as temporary:
            source_hash = snapshot(root, Path(temporary), version)
            build = ['docker', 'buildx', 'build', '--builder', builder, '--platform', 'linux/amd64',
                     '--provenance=false', '--build-arg', 'VERSION=' + version,
                     '--label', 'org.opencontainers.image.revision=' + source_hash, '--tag', reference]
            execute(build + ['--load', temporary])
            inspection = json.loads(subprocess.check_output(['docker', 'image', 'inspect', reference]))[0]
            if inspection['Architecture'] != 'amd64' or inspection['Config']['Labels'].get('org.opencontainers.image.version') != version:
                raise RuntimeError('构建镜像架构或版本不匹配')
            checks = check_image(root, reference, version)
            # Re-check after a potentially long build; never silently replace an occupied version.
            if manifest(reference) or manifest(f'{image}:{version}'):
                raise RuntimeError('构建期间版本已被其他发布占用，请重新执行 make release')
            # Export the same cached build graph, then use bounded requests through Harbor's proxy.
            with tempfile.TemporaryDirectory(prefix='image-', dir=directory) as output:
                layout = Path(output) / 'oci'
                execute(build + ['--output', f'type=oci,dest={layout},tar=false', temporary])
                upload(uploader, layout, reference, inspection['Id'])
        published = manifest(reference)
        local_digest = inspection['Id']
        if not published or published.get('config', {}).get('digest') != local_digest:
            raise RuntimeError('远端镜像与本次通过验证的本地镜像不一致，不更新 latest')
        latest = manifest(image + ':latest')
        remote = (latest or {}).get('annotations', {}).get('org.opencontainers.image.version')
        if latest and (not remote or parts(remote) >= parts(version)):
            raise RuntimeError('latest 在构建期间已变化，不回退或覆盖；版本镜像已上传')
        execute(['docker', 'buildx', 'imagetools', 'create', '--prefer-index=true',
                 '--tag', f'{image}:{version}',
                 '--annotation', f'index:org.opencontainers.image.version={version}',
                 image + '@' + published['digest']])
        versioned = manifest(f'{image}:{version}')
        platforms = {(m.get('platform', {}).get('os'), m.get('platform', {}).get('architecture')) for m in versioned.get('manifests', [])}
        if platforms != {('linux', 'amd64')} or versioned.get('annotations', {}).get('org.opencontainers.image.version') != version:
            raise RuntimeError('远端版本镜像验证失败，不更新 latest')
        execute(['docker', 'buildx', 'imagetools', 'create', '--tag', image + ':latest', image + '@' + versioned['digest']])
        if manifest(image + ':latest')['digest'] != versioned['digest']:
            raise RuntimeError('latest 摘要验证失败')
        (root / 'VERSION').write_text(version + '\n')
        record = {'version': version, 'image': image, 'digest': versioned['digest'], 'platform_digest': published['digest'],
                  'source_sha256': source_hash, 'published_at': datetime.now(timezone.utc).isoformat(), 'checks': checks}
        write_json(directory / (version + '.json'), record)
        write_json(directory / 'latest.json', record)
        files = deployment_files(root, version, image)
        print(f'发布完成：{image}:{version} / {version}-amd64 / latest\n部署文件：{files}\nNAS 执行：docker compose pull && docker compose up -d', flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dry-run', action='store_true')
    options = parser.parse_args()
    try:
        release(Path(__file__).resolve().parents[1], options.dry_run)
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
        parser.exit(1, f'发布停止：{exc}\n')
