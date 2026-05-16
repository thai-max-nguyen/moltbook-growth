#!/usr/bin/env python3
"""Auto-recover Reddit token_v2 by decrypting it from the Chrome cookie DB.

Replaces the manual "user must re-login at reddit.com" step. Works whenever
Chrome's Default profile holds a logged-in reddit session cookie (no CDP,
no remote-debugging port needed).

Exit 0 = config updated with a fresh JWT. Non-zero = could not recover
(no Chrome cookie DB / no reddit token_v2 / decrypt not a clean JWT) — caller
should fall back to flagging the user.

Decrypt chain (macOS Chrome v10): keychain "Chrome Safe Storage" key →
PBKDF2-SHA1(salt=b'saltysalt', 1003, dklen=16) → AES-128-CBC(IV=b' '*16) →
strip 'v10' prefix → strip PKCS7 pad → strip 32-byte SHA256(host_key) prefix.
"""
import sqlite3, shutil, tempfile, os, subprocess, hashlib, json, datetime, sys

CFG = os.path.expanduser("~/.config/mundo-bot/reddit_config.json")
COOKIES = os.path.expanduser(
    "~/Library/Application Support/Google/Chrome/Default/Cookies")


def recover() -> str:
    if not os.path.exists(COOKIES):
        raise RuntimeError("no Chrome Default cookie DB")
    key = subprocess.check_output(
        ["security", "find-generic-password", "-w",
         "-a", "Chrome", "-s", "Chrome Safe Storage"]).strip()
    aes_key = hashlib.pbkdf2_hmac("sha1", key, b"saltysalt", 1003, dklen=16)
    tmp = tempfile.mktemp(suffix=".db")
    shutil.copy2(COOKIES, tmp)
    try:
        con = sqlite3.connect(tmp)
        row = con.execute(
            "SELECT encrypted_value FROM cookies "
            "WHERE host_key LIKE '%reddit.com%' AND name='token_v2'"
        ).fetchone()
        con.close()
    finally:
        os.unlink(tmp)
    if not row:
        raise RuntimeError("Chrome has no reddit token_v2 cookie "
                           "(log in to reddit.com in Chrome once)")
    from Crypto.Cipher import AES
    dec = AES.new(aes_key, AES.MODE_CBC, IV=b"\x20" * 16).decrypt(bytes(row[0])[3:])
    pad = dec[-1]
    if 1 <= pad <= 16:
        dec = dec[:-pad]
    val = dec[32:].decode("utf-8", errors="ignore")
    if not (val.startswith("eyJ") and val.count(".") == 2):
        raise RuntimeError("decrypted value is not a clean JWT")
    return val


def main() -> int:
    try:
        val = recover()
    except Exception as e:
        print(f"reddit token recover FAILED: {e}", file=sys.stderr)
        return 1
    # Validate the JWT's own exp claim — Chrome may hold a token_v2 cookie whose
    # container is still alive but whose inner JWT expired. Writing such a token
    # gives the bot a dead Bearer + false "recovered" success.
    try:
        import base64
        pl = val.split(".")[1]
        pl += "=" * (-len(pl) % 4)
        exp = json.loads(base64.urlsafe_b64decode(pl)).get("exp", 0)
    except Exception:
        exp = 0
    hrs = (exp - datetime.datetime.now().timestamp()) / 3600
    if hrs <= 0.5:
        print(f"reddit token recover FAILED: Chrome cookie JWT expired "
              f"({hrs:+.1f}h) — log in to reddit.com in Chrome to mint a fresh session",
              file=sys.stderr)
        return 1
    shutil.copy2(CFG, CFG + ".bak")
    cfg = json.load(open(CFG))
    cfg["token_v2"] = val
    cfg["token_expires"] = datetime.datetime.fromtimestamp(exp).isoformat()
    json.dump(cfg, open(CFG, "w"), indent=2)
    print(f"reddit token recovered from Chrome cookie DB; "
          f"JWT expires {cfg['token_expires']} ({hrs:+.1f}h)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
