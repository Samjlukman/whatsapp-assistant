import os
import sys
from pathlib import Path
from zoneinfo import ZoneInfo

HERE = Path(__file__).parent
TZ = ZoneInfo("Europe/London")
MODEL = "qwen2.5:14b"
DB_URL = f"sqlite:///{HERE / 'assistant.db'}"
BRIDGE_URL = "http://127.0.0.1:3001"
# Shared with whatsapp_bridge.js so no other local program can talk to either side.
BRIDGE_SECRET = os.environ.get("BRIDGE_SECRET") or sys.exit("Set the BRIDGE_SECRET environment variable first.")
