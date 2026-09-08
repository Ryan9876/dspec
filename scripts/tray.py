from __future__ import annotations
import subprocess
import sys
from pathlib import Path

try:
    import rumps
except ImportError:
    raise SystemExit("Install rumps on macOS to use the optional DSpec menu bar controller.")

RUNNER = Path(__file__).with_name("dspec_runner.py")
class DSpecTray(rumps.App):
    def __init__(self):
        super().__init__("DS", quit_button=None)
        self.menu = ["Open DSpec", "Status", "Stop DSpec", None, "Quit Tray"]
    @rumps.clicked("Open DSpec")
    def open_ui(self, _): subprocess.run(["open", "http://127.0.0.1:3210"])
    @rumps.clicked("Status")
    def status(self, _):
        p = subprocess.run([sys.executable, str(RUNNER), "status"], capture_output=True, text=True)
        rumps.alert("DSpec Status", p.stdout or p.stderr)
    @rumps.clicked("Stop DSpec")
    def stop(self, _): subprocess.run([sys.executable, str(RUNNER), "stop"])
    @rumps.clicked("Quit Tray")
    def quit(self, _): rumps.quit_application()
if __name__ == "__main__": DSpecTray().run()
