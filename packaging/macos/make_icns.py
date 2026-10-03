"""Build SkyDispatch.icns from the iconset (macOS only; uses the built-in iconutil)."""
import subprocess
from pathlib import Path

here = Path(__file__).resolve().parent
subprocess.run(["iconutil", "-c", "icns", str(here / "SkyDispatch.iconset"), "-o", str(here / "SkyDispatch.icns")],
               check=True)
print("wrote", here / "SkyDispatch.icns")
