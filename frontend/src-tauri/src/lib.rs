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

/// 拉起 FastAPI 后端：隐藏控制台窗口，输出全部丢弃
fn spawn_backend() -> Option<Child> {
    let dir = find_backend_dir()?;
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
    .current_dir(&dir)
    // Windows 默认 GBK；强制 UTF-8，避免读取引擎 JSON / 打印日志时 Unicode 解码失败。
    .env("PYTHONUTF8", "1")
    .env("PYTHONIOENCODING", "utf-8")
    .stdin(Stdio::null())
    .stdout(Stdio::null())
    .stderr(Stdio::null());

    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        cmd.creation_flags(CREATE_NO_WINDOW);
    }

    cmd.spawn().ok()
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
