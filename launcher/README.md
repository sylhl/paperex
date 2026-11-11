# launcher/

This folder holds **local build artifacts only** — `paperex.exe` (Tauri 2 desktop
launcher) and an optional shortcut. Both are listed in `.gitignore`, so a fresh
clone starts with just `.gitkeep`.

## Why it lives at the repository root

The launcher walks up from its own location to find `backend/main.py`, so
`launcher/` and `backend/` must stay in the same directory tree:

```
paperex/
├── launcher/paperex.exe   ← you are here
└── backend/               ← discovered at runtime
```

## Build it yourself

```powershell
cd frontend
npm ci
npm run tauri build -- --no-bundle
copy src-tauri\target\release\paperex.exe ..\launcher\paperex.exe
```

The launcher expects `backend\.venv\Scripts\python.exe` to exist (create it once
with `python -m venv .venv` and install `backend/requirements.txt`). On start it
launches the backend on a free local port, waits for `/api/health`, and shuts the
child process down when the window closes.

See [README → 快速开始](../README.md#快速开始) for the full setup and
[frontend/src-tauri](/frontend/src-tauri) for the Rust shell.
