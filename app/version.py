import os
from pathlib import Path

VERSION = os.getenv('SPEECH_VERSION') or (Path(__file__).resolve().parents[1] / 'VERSION').read_text().strip()
