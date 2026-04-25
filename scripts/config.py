"""Load Moltbook credentials from env var or ~/.config/moltbook/credentials.json"""
import os, json

def get_api_key():
    if os.environ.get("MOLTBOOK_API_KEY"):
        return os.environ["MOLTBOOK_API_KEY"]
    cfg = os.path.expanduser("~/.config/moltbook/credentials.json")
    if os.path.exists(cfg):
        with open(cfg) as f:
            return json.load(f)["api_key"]
    raise RuntimeError("Set MOLTBOOK_API_KEY env var or create ~/.config/moltbook/credentials.json")
