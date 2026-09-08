from __future__ import annotations

import json
import subprocess
import sys
import webbrowser

from .config import PORT, runtime_dir
from .runner import status, stop

TRAY_PID = runtime_dir() / "tray.pid"


def main() -> None:
    if sys.platform != "darwin":
        raise SystemExit("The DSpec menu bar controller is macOS-only.")
    try:
        import rumps
    except ImportError as exc:
        raise SystemExit("Install tray extras: pip install 'dspec-ai[tray]'") from exc

    class DSpecTray(rumps.App):
        def __init__(self) -> None:
            super().__init__("DS", quit_button=None)
            self.menu = [
                rumps.MenuItem("Open DSpec", callback=self.open_dspec),
                rumps.MenuItem("Status", callback=self.show_status),
                None,
                rumps.MenuItem("Stop DSpec", callback=self.stop_dspec),
                rumps.MenuItem("Quit Menu", callback=self.quit_menu),
            ]

        def open_dspec(self, _sender) -> None:
            webbrowser.open(f"http://localhost:{PORT}")

        def show_status(self, _sender) -> None:
            snapshot = status()
            health = snapshot.get("health")
            if health:
                active = health.get("active_provider", {})
                message = (
                    f"DSpec: healthy\n"
                    f"Port: {health.get('port', PORT)}\n"
                    f"Provider: {active.get('provider') or active.get('name', 'unknown')}\n"
                    f"Model: {active.get('model', 'unknown')}"
                )
            else:
                message = "DSpec backend is not running."
            rumps.alert("DSpec Status", message)

        def stop_dspec(self, _sender) -> None:
            try:
                result = stop(include_tray=False)
                rumps.notification("DSpec", "Stopped", json.dumps(result))
            finally:
                TRAY_PID.unlink(missing_ok=True)
                rumps.quit_application()

        def quit_menu(self, _sender) -> None:
            TRAY_PID.unlink(missing_ok=True)
            rumps.quit_application()

    TRAY_PID.write_text(str(__import__("os").getpid()), encoding="utf-8")
    try:
        DSpecTray().run()
    finally:
        TRAY_PID.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
