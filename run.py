"""Dev launcher: python run.py [--backup PATH] [--debug]"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))

from backupviewer.app import main

if __name__ == "__main__":
    sys.exit(main())
