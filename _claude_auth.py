"""Helper to inject Claude CLI OAuth token from macOS keychain.

Why: cron jobs run in a session that cannot access the login keychain via
Claude CLI's normal flow ("Not logged in / Please run /login"). The `security`
CLI can read the keychain entry directly (subject to ACL), and we can pass the
access token to claude via CLAUDE_CODE_OAUTH_TOKEN env var to bypass keychain.

If the access token is expired or the keychain read fails, we fall back to
letting claude try its normal auth path. Tokens last ~8 hours so daily refresh
during interactive use keeps the keychain entry fresh.
"""
import json, os, subprocess

_KEYCHAIN_SVC = "Claude Code-credentials"
_KEYCHAIN_PATH = "/Users/lap15964/Library/Keychains/login.keychain-db"
# Cache file written whenever we successfully read keychain — cron reads this
# as a fallback when keychain access from its session fails.
_CACHE = os.path.expanduser("~/.config/mundo-bot/.claude_oauth_cache.json")


def _read_keychain():
    try:
        r = subprocess.run(
            ["/usr/bin/security", "find-generic-password",
             "-s", _KEYCHAIN_SVC, "-w", _KEYCHAIN_PATH],
            capture_output=True, text=True, timeout=5
        )
        if r.returncode != 0 or not r.stdout.strip():
            return None
        return json.loads(r.stdout.strip())
    except Exception:
        return None


def _read_cache():
    try:
        with open(_CACHE) as f:
            return json.load(f)
    except Exception:
        return None


def _write_cache(blob):
    try:
        os.makedirs(os.path.dirname(_CACHE), exist_ok=True)
        # Restrictive perms — contains an OAuth access token.
        with open(_CACHE, "w") as f:
            json.dump(blob, f)
        os.chmod(_CACHE, 0o600)
    except Exception:
        pass


def env_with_token(base_env=None):
    """Return an env dict containing CLAUDE_CODE_OAUTH_TOKEN if available.

    Tries keychain first, falls back to disk cache. Always returns a dict
    (possibly identical to base_env) so caller can pass it to subprocess.
    """
    env = dict(base_env if base_env is not None else os.environ)
    blob = _read_keychain()
    if blob:
        _write_cache(blob)
    else:
        blob = _read_cache()
    try:
        token = blob["claudeAiOauth"]["accessToken"]
        env["CLAUDE_CODE_OAUTH_TOKEN"] = token
    except (KeyError, TypeError):
        pass
    return env
