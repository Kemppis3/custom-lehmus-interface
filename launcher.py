"""Start Lehmus Chat (if not already running) and open it in the browser."""

import json
import sys
import threading
import time
import urllib.request
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parent
CONFIG = ROOT / "config.json"


def fail(msg: str) -> None:
    print("\n" + msg + "\n")
    input("Paina Enter sulkeaksesi…")
    sys.exit(1)


def is_running(url: str) -> bool:
    try:
        with urllib.request.urlopen(url + "/api/health", timeout=1) as r:
            return json.load(r).get("app") == "lehmus-chat"
    except Exception:
        return False


def main() -> None:
    if not CONFIG.exists():
        fail(f"config.json puuttuu.\nLisää config.json (API-osoite ja avaimet) tähän kansioon: {ROOT}")
    try:
        port = int(json.loads(CONFIG.read_text(encoding="utf-8")).get("port", 8765))
    except Exception as e:
        fail(f"config.json ei ole kelvollista JSONia: {e}")
    url = f"http://127.0.0.1:{port}"

    if is_running(url):
        webbrowser.open(url)
        return

    def open_when_ready() -> None:
        for _ in range(100):
            if is_running(url):
                webbrowser.open(url)
                return
            time.sleep(0.2)

    threading.Thread(target=open_when_ready, daemon=True).start()
    print(f"Lehmus Chat on käynnissä osoitteessa {url}\nSulje tämä ikkuna pysäyttääksesi sen.")
    import uvicorn

    sys.path.insert(0, str(ROOT))
    # Bound to 127.0.0.1 only: nobody else on the network can reach it.
    uvicorn.run("app.main:app", host="127.0.0.1", port=port, log_level="warning")


if __name__ == "__main__":
    main()
