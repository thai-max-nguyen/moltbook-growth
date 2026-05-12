#!/usr/bin/env python3
"""Refresh claude OAuth token cache from keychain. Run before engage/daily_post."""
import sys
sys.path.insert(0, '/Users/lap15964/.config/mundo-bot')
from _claude_auth import env_with_token, _read_keychain, _write_cache
blob = _read_keychain()
if blob:
    _write_cache(blob)
    print("token refreshed ok")
else:
    print("keychain read failed — using existing cache")
