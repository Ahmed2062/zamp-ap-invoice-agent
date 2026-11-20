"""Test configuration: isolated data dir and no demo pacing. Must run before `app` is imported."""
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

os.environ["DATA_DIR"] = tempfile.mkdtemp(prefix="ap-agent-test-")
os.environ["STAGE_DELAY_MS"] = "0"
os.environ["AI_MODE"] = "off"
os.environ.pop("GEMINI_API_KEY", None)
os.environ.pop("GOOGLE_API_KEY", None)
