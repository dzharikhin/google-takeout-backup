import asyncio
import json
import os
import pathlib
from invisible_playwright.config import get_default_stealth_prefs
from invisible_playwright.download import ensure_binary

downloads_path = pathlib.Path("/app/browser-downloads")
fp_seed = int(os.getenv("FP_SEED", "42"))
fp_locale = os.getenv("FP_LOCALE", "en-US")
fp_timezone = os.getenv("FP_TIMEZONE", "")
fp_humanize = os.getenv("FP_HUMANIZE", "true").lower() in ("true", "1", "yes")

prefs = get_default_stealth_prefs(
    seed=fp_seed,
    locale=fp_locale,
    timezone=fp_timezone,
    humanize=fp_humanize,
)

executable_path = ensure_binary()

config_path = pathlib.Path("/tmp/launch-params.json")
config_path.parent.mkdir(parents=True, exist_ok=True)

config = {
    "executablePath": str(executable_path),
    "userDataDir": str(downloads_path.parent / "browser-local-storage"),
    "downloadsPath": str(downloads_path),
    "headless": False,
    "host": "0.0.0.0",
    "port": int(os.getenv("BROWSER_PORT", "8081")),
    "wsPath": "/srv",
    "args": [],
    "firefoxUserPrefs": prefs,
    "chromiumSandbox": False,
}

config_path.write_text(json.dumps(config, indent=2))

print(f"Generated launch config: {config_path}")
print(f"  executablePath: {executable_path}")
print(f"  firefoxUserPrefs keys: {len(prefs)}")
print(f"  port: {config['port']}")
