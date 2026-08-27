"""Healthcheck probe for SettleGraph service instances."""

from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request


def check_health(host: str = "127.0.0.1", port: int = 8080) -> bool:
    url = f"http://{host}:{port}/healthz"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "SettleGraph-HealthProbe/1.0"})
        with urllib.request.urlopen(req, timeout=5) as response:
            if response.status == 200:
                data = json.loads(response.read().decode("utf-8"))
                if data.get("status") == "healthy":
                    print(
                        f"[OK] SettleGraph service healthy on {url} (version: {data.get('version')})"
                    )
                    return True
            print(f"[FAIL] Unexpected response status: {response.status}")
            return False
    except urllib.error.URLError as e:
        print(f"[FAIL] Could not connect to SettleGraph on {url}: {e}")
        return False
    except Exception as e:
        print(f"[FAIL] Healthcheck error: {e}")
        return False


if __name__ == "__main__":
    h = sys.argv[1] if len(sys.argv) > 1 else "127.0.0.1"
    p = int(sys.argv[2]) if len(sys.argv) > 2 else 8080
    success = check_health(host=h, port=p)
    sys.exit(0 if success else 1)
