import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))

from heating_diagnosis.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
