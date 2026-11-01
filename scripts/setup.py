"""Create private local configuration without printing credentials."""
from pathlib import Path
import secrets

root = Path(__file__).resolve().parents[1]
config = root / '.env'
if config.exists():
    print('.env already exists; preserved')
else:
    content = (
        f'APP_TOKEN={secrets.token_urlsafe(32)}\n'
        'LLM_BASE_URL=\nLLM_MODEL=\nLLM_API_KEY=\n'
    )
    with open(config, 'x', opener=lambda path, flags: __import__('os').open(path, flags, 0o600)) as f:
        f.write(content)
    print('Created private .env; see RUNBOOK.md for token use')
