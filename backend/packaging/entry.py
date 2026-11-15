# -*- coding: utf-8 -*-
"""Frozen (PyInstaller) entry point of the paperex backend.

Two modes share one executable so that an installed copy needs neither a
system Python nor a virtual environment:

* ``paperex-server.exe [--host H] [--port P]`` — start the FastAPI service
  (the Tauri launcher starts it on 127.0.0.1:8756 and kills it on exit).
* ``paperex-server.exe --qa-worker <spec.json>`` — isolated QA checker
  subprocess spawned by ``qa.py``; it reuses the same frozen interpreter so
  the checker stays fully isolated from the API process.
"""

from __future__ import annotations

import sys


def main() -> None:
    argv = list(sys.argv[1:])
    if argv and argv[0] == "--qa-worker":
        from qa_worker import main as qa_worker_main

        raise SystemExit(qa_worker_main(argv[1:]))

    import uvicorn

    from main import _listen_address, app

    address = _listen_address()
    uvicorn.run(
        app,
        host=str(address["host"]),
        port=int(address["port"]),
        log_level="info",
    )


if __name__ == "__main__":
    main()
