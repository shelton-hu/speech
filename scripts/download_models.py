"""Fetch named upstream release archives safely, publish only complete model directories."""
import argparse
import hashlib
import json
import shutil
import tarfile
import tempfile
import urllib.request
from pathlib import Path

MODELS = {
    'asr': ('asr-models', 'sherpa-onnx-sense-voice-zh-en-ja-ko-yue-int8-2024-07-17', ['model.int8.onnx', 'tokens.txt']),
    'tts': ('tts-models', 'vits-melo-tts-zh_en', ['model.onnx', 'tokens.txt', 'lexicon.txt', 'dict']),
}

def download(kind, root):
    release, name, required = MODELS[kind]
    target = root / name
    if target.exists():
        if not (target / 'download.json').exists() or not all((target / p).exists() for p in required):
            raise RuntimeError(f'Incomplete existing model {target}; move it aside and retry')
        print(f'{kind}: complete model already present', flush=True)
        return
    url = f'https://github.com/k2-fsa/sherpa-onnx/releases/download/{release}/{name}.tar.bz2'
    root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.download-', dir=root) as temp:
        temp = Path(temp)
        archive = temp / 'model.tar.bz2'
        print(f'{kind}: downloading {name}', flush=True)
        digest = hashlib.sha256()
        req = urllib.request.Request(url, headers={'User-Agent': 'speech-model-setup/0.2'})
        with urllib.request.urlopen(req, timeout=90) as response, archive.open('wb') as output:
            while chunk := response.read(1024 * 1024):
                output.write(chunk)
                digest.update(chunk)
        with tarfile.open(archive) as tar:
            for member in tar.getmembers():
                if not (member.isfile() or member.isdir()) or not (member.name == name or member.name.startswith(name + '/')):
                    raise RuntimeError(f'Unexpected archive entry: {member.name}')
                if '..' in Path(member.name).parts:
                    raise RuntimeError('Unsafe archive path')
            tar.extractall(temp, filter='data')
        extracted = temp / name
        if not all((extracted / p).exists() for p in required):
            raise RuntimeError('Downloaded archive is missing required model files')
        (extracted / 'download.json').write_text(json.dumps({'url': url, 'archive_sha256': digest.hexdigest()}, indent=2))
        # Atomic on the same filesystem; refuses to overwrite an existing model.
        if target.exists():
            raise RuntimeError(f'Model appeared concurrently: {target}')
        extracted.rename(target)
        print(f'{kind}: ready', flush=True)

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--dir', type=Path, default=Path(__file__).resolve().parents[1] / 'models')
    parser.add_argument('--only', choices=list(MODELS))
    args = parser.parse_args()
    for kind in ([args.only] if args.only else MODELS):
        download(kind, args.dir)
