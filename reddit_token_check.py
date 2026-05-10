#!/usr/bin/env python3
"""Reddit token freshness check + alert.

Run as cron pre-flight. Decrypts Chrome cookie DB for `token_v2`, checks JWT exp,
and:
- If alive: silent success (cron continues to reddit_post.py).
- If dead: writes flag to vault Health Profile + macOS notification + exits 2
  (signals cron wrapper to skip reddit_post.py — saves wasted call).

Token has 24h hard TTL with no programmatic refresh path (no OAuth app + password
stored). User must manually re-login to reddit.com in Chrome; next cron will pick
up the new cookie automatically via the existing decrypt path.

Why exit 2 (not 1): launchd treats non-zero as failure but does not retry; matches
the engage preflight pattern.
"""
import base64
import json
import os
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

VAULT_FLAG = Path.home() / "Documents" / "Claude Second Brain" / "02 - User Profile" / "Max - Health Profile.md"
COOKIE_DB = Path.home() / "Library" / "Application Support" / "Google" / "Chrome" / "Default" / "Cookies"


def decrypt_token():
    try:
        from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    except ImportError:
        return None, "cryptography not installed"

    try:
        pwd = subprocess.check_output(
            ["security", "find-generic-password", "-w", "-s", "Chrome Safe Storage"],
            stderr=subprocess.DEVNULL,
        ).strip()
    except subprocess.CalledProcessError as e:
        return None, f"keychain read failed: {e}"

    kdf = PBKDF2HMAC(algorithm=hashes.SHA1(), length=16, salt=b"saltysalt", iterations=1003)
    key = kdf.derive(pwd)

    try:
        conn = sqlite3.connect(f"file:{COOKIE_DB}?mode=ro", uri=True)
        rows = conn.execute(
            "SELECT encrypted_value FROM cookies WHERE host_key LIKE '%reddit.com%' AND name='token_v2'"
        ).fetchall()
        conn.close()
    except sqlite3.Error as e:
        return None, f"cookie DB read failed: {e}"

    if not rows:
        return None, "no token_v2 cookie found"

    encrypted = rows[0][0]
    if not encrypted.startswith(b"v10"):
        return None, "unexpected cookie prefix"

    cipher = Cipher(algorithms.AES(key), modes.CBC(b" " * 16))
    decryptor = cipher.decryptor()
    padded = decryptor.update(encrypted[3:]) + decryptor.finalize()
    plaintext = padded[: -padded[-1]].decode("utf-8", errors="ignore")

    if plaintext.count(".") < 2:
        return None, "decrypted value not JWT-shaped"
    return plaintext, None


def jwt_exp_hours(token):
    payload_b64 = token.split(".")[1]
    payload_b64 += "=" * (-len(payload_b64) % 4)
    payload = json.loads(base64.b64decode(payload_b64))
    exp = payload.get("exp", 0)
    return (exp - time.time()) / 3600


def update_vault_flag(status, hours):
    """Replace any existing reddit-token line in Health Profile LIVE SNAPSHOT."""
    if not VAULT_FLAG.exists():
        return
    content = VAULT_FLAG.read_text()
    flag_marker = "<!-- reddit-token-status -->"
    new_line = f'{flag_marker} **Reddit token**: {status} ({hours:+.1f}h)\n'
    lines = content.split("\n")
    out = []
    replaced = False
    for line in lines:
        if flag_marker in line:
            out.append(new_line.rstrip())
            replaced = True
        else:
            out.append(line)
    if not replaced:
        # Insert after "## LIVE SNAPSHOT" line if not present
        for i, line in enumerate(out):
            if line.startswith("## LIVE SNAPSHOT"):
                out.insert(i + 2, new_line.rstrip())
                break
    VAULT_FLAG.write_text("\n".join(out))


def notify(title, message):
    try:
        subprocess.run(
            ["osascript", "-e", f'display notification "{message}" with title "{title}"'],
            check=False,
            timeout=5,
        )
    except Exception:
        pass


def main():
    token, err = decrypt_token()
    if err:
        print(f"reddit-token-check: ERROR {err}", file=sys.stderr)
        sys.exit(2)

    hours = jwt_exp_hours(token)
    if hours > 0.5:
        print(f"reddit-token-check: alive ({hours:+.2f}h)")
        update_vault_flag("alive 🟢", hours)
        sys.exit(0)
    else:
        print(f"reddit-token-check: DEAD ({hours:+.2f}h) — re-login at reddit.com", file=sys.stderr)
        update_vault_flag("DEAD 🔴 — re-login at reddit.com in Chrome", hours)
        notify("Reddit token expired", f"Re-login at reddit.com (token {hours:+.1f}h)")
        sys.exit(2)


if __name__ == "__main__":
    main()
