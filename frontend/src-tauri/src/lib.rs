use std::net::TcpStream;
use std::path::PathBuf;
use std::process::{Child, Command, Stdio};
use std::sync::Mutex;
use std::time::Duration;
use tauri::Manager;

#[cfg(windows)]
const CREATE_NO_WINDOW: u32 = 0x0800_0000;

const BACKEND_PORT: u16 = 8756;

/// 让前端可以拿到后端的固定地址与 AppData 沙盒目录（纯展示用途）
#[tauri::command]
fn backend_base() -> String {
    format!("http://127.0.0.1:{BACKEND_PORT}")
}

/// 后端进程守护：App 退出时自动 kill，避免残留孤儿进程占用端口
struct BackendGuard(Mutex<Option<Child>>);

impl BackendGuard {
    fn kill(&self) {
        if let Ok(mut slot) = self.0.lock() {
            if let Some(mut child) = slot.take() {
                let _ = child.kill();
                let _ = child.wait();
            }
        }
    }
}

impl Drop for BackendGuard {
    fn drop(&mut self) {
        self.kill();
    }
}

/// 端口已被占用 => 认为后端已在运行（例如用户手动起的），不再重复拉起
fn backend_already_running() -> bool {
    let addr = format!("127.0.0.1:{BACKEND_PORT}");
    addr.parse()
        .ok()
        .map(|a| TcpStream::connect_timeout(&a, Duration::from_millis(300)).is_ok())
        .unwrap_or(false)
}

/// 从可执行文件位置向上逐级查找 backend/main.py
/// （dev: target/debug/ ；release: target/release/ ，最终都能回溯到项目根）
fn find_backend_dir() -> Option<PathBuf> {
    let exe = std::env::current_exe().ok()?;
    for ancestor in exe.ancestors() {
        let candidate = ancestor.join("backend");
        if candidate.join("main.py").is_file() {
            return Some(candidate);
        }
    }
    None
}

/// 安装包内置后端：tauri 把 `backend/dist/paperex-server/` 打包进
/// 资源目录的 `backend/` 下；Windows 上资源目录就是 exe 所在目录。
fn find_bundled_server() -> Option<PathBuf> {
    let exe = std::env::current_exe().ok()?;
    let dir = exe.parent()?;
    let candidates = [
        dir.join("backend").join("paperex-server.exe"),
        dir.join("backend").join("paperex-server").join("paperex-server.exe"),
        dir.join("resources").join("backend").join("paperex-server.exe"),
        dir.join("resources").join("backend").join("paperex-server").join("paperex-server.exe"),
    ];
    candidates.into_iter().find(|path| path.is_file())
}

/// 隐藏控制台窗口、输出丢弃、强制 UTF-8 的通用设置。
fn hidden(cmd: &mut Command) -> &mut Command {
    cmd.env("PYTHONUTF8", "1")
        .env("PYTHONIOENCODING", "utf-8")
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::null());

    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        cmd.creation_flags(CREATE_NO_WINDOW);
    }
    cmd
}

/// 源码模式下用项目虚拟环境里的 Python 拉起 `uvicorn main:app`。
fn spawn_python_backend(dir: &std::path::Path) -> Option<Child> {
    // 优先使用项目自带的虚拟环境，避免系统 Python 缺少依赖。
    let venv_python = dir.join(".venv").join("Scripts").join("python.exe");
    let python = if venv_python.is_file() {
        venv_python
    } else {
        PathBuf::from("python")
    };
    let mut cmd = Command::new(python);
    cmd.args([
        "-m",
        "uvicorn",
        "main:app",
        "--host",
        "127.0.0.1",
        "--port",
        &BACKEND_PORT.to_string(),
    ])
    .current_dir(dir);
    hidden(&mut cmd).spawn().ok()
}

/// 安装包模式：直接运行随包分发的 `paperex-server.exe`（内置 Python 运行时）。
fn spawn_bundled_backend(server: &std::path::Path) -> Option<Child> {
    let mut cmd = Command::new(server);
    cmd.args(["--host", "127.0.0.1", "--port", &BACKEND_PORT.to_string()]);
    if let Some(parent) = server.parent() {
        cmd.current_dir(parent);
    }
    hidden(&mut cmd).spawn().ok()
}

/// 拉起 FastAPI 后端：源码模式优先，其次安装包内置后端。
fn spawn_backend() -> Option<Child> {
    if let Some(dir) = find_backend_dir() {
        return spawn_python_backend(&dir);
    }
    let server = find_bundled_server()?;
    spawn_bundled_backend(&server)
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    let backend_child = if backend_already_running() {
        None
    } else {
        spawn_backend()
    };

    let app = tauri::Builder::default()
        .plugin(tauri_plugin_dialog::init())
        .invoke_handler(tauri::generate_handler![backend_base])
        .manage(BackendGuard(Mutex::new(backend_child)))
        .setup(|app| {
            if let Some(window) = app.get_webview_window("main") {
                let _ = window.set_title("paperex · 论文双语阅读器");
            }
            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("error while building paperex");

    // Tauri 在事件循环结束后直接 ExitProcess，析构函数不会执行，
    // 必须在 RunEvent::Exit 回调里同步杀掉后端子进程
    app.run(|app_handle, event| {
        if let tauri::RunEvent::Exit = event {
            if let Some(guard) = app_handle.try_state::<BackendGuard>() {
                guard.kill();
            }
        }
    });
}
