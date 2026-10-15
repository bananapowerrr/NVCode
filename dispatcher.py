"""NVCode CLI entry."""
from __future__ import annotations
from typing import Any

def _ensure_agentbus_paths() -> Any:
    '''"""Internal: _ensure_agentbus_paths()."""'''
    import sys
    from pathlib import Path
    root = Path(__file__).resolve().parent
    for p in (root, root / 'src', root / 'plugins'):
        s = str(p if p.name != 'plugins' else root)
        if s not in sys.path:
            sys.path.insert(0, s)
_ensure_agentbus_paths()
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parent
SRC = ROOT / 'src'
for p in (str(SRC), str(ROOT)):
    if p not in sys.path:
        sys.path.insert(0, p)
from core.dispatcher_main import main
if __name__ == '__main__':
    raise SystemExit(main())
