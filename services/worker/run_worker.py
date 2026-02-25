import asyncio
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
WORKER_ROOT = Path(__file__).resolve().parent

# Keep runtime paths stable regardless of launcher cwd.
os.chdir(PROJECT_ROOT)
if str(WORKER_ROOT) not in sys.path:
    sys.path.insert(0, str(WORKER_ROOT))

from main import main as worker_main


if __name__ == "__main__":
    asyncio.run(worker_main())
