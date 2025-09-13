// electron/main.js — 백그라운드(트레이) & 핫키 복귀 최종본
const { app, BrowserWindow, globalShortcut, dialog, Tray, Menu, nativeImage, ipcMain } = require('electron');
const path = require('path');
const { spawn, spawnSync } = require('child_process');
const http = require('http');
const fs = require('fs');
const chokidar = require('chokidar');
const { error } = require('console');
const sharp = require('sharp');

let win = null;
let tray = null;
let isQuitting = false;
let pyProc = null;

const API_PORT = 8421;                         // FastAPI 포트
const ROOT_DIR = path.join(__dirname, '..');   // app.py 위치(프로젝트 루트)
const VENV_PY = path.join(ROOT_DIR, '.venv', 'Scripts', 'python.exe'); // 윈도우 venv 파이썬

const NOTES_DIR = path.join(ROOT_DIR, 'data', 'notes');

// --- Python(FastAPI) 구동/정지 ------------------------------------------------
function startPython() {
  const pythonBin = fs.existsSync(VENV_PY)
    ? VENV_PY
    : (process.platform === 'win32' ? 'python' : 'python3');

  pyProc = spawn(
    pythonBin,
    ['-m', 'uvicorn', 'app:app', '--host', '127.0.0.1', '--port', String(API_PORT)],
    {
      cwd: ROOT_DIR,
      env: { ...process.env },
      stdio: 'ignore',
      windowsHide: true,
      shell: false,
    }
  );

  pyProc.on('error', (err) => {
    console.error('[PYTHON ERROR]', err);
    dialog.showErrorBox('Python 실행 실패', `파이썬을 실행할 수 없습니다.\n경로: ${pythonBin}\n\n${err.message}`);
  });
}

function stopPython() {
  if (!pyProc) return;
  try {
    if (process.platform === 'win32') {
      spawnSync('taskkill', ['/pid', String(pyProc.pid), '/f', '/t']);
    } else {
      pyProc.kill('SIGTERM');
    }
  } catch (e) {
    console.error('[PYTHON KILL ERROR]', e);
  }
  pyProc = null;
}

// API 살아났는지 체크
function pingServer(timeoutMs = 500) {
  return new Promise((resolve, reject) => {
    const req = http.request(
      { host: '127.0.0.1', port: API_PORT, path: '/api/notes', method: 'GET', timeout: timeoutMs },
      (res) => { res.destroy(); resolve(true); }
    );
    req.on('error', () => resolve(false));
    req.end();
  });
}

async function waitForServer(maxAttempts = 50, intervalMs = 100) {
  for (let i = 0; i < maxAttempts; i++) {
    if (await pingServer()) return;
    await new Promise(r => setTimeout(r, intervalMs));
  }
  throw new Error('Python API not responding');
}

// 워치독: 주기적으로 API 상태를 보고 죽으면 자동 재기동
function startApiWatchdog(intervalMs = 5000) {
  setInterval(async () => {
    const ok = await pingServer();
    if (!ok) {
      console.warn('[WATCHDOG] API down. Restarting Python…');
      stopPython();
      startPython();
    }
  }, intervalMs);
}

function watchNoteDir() {
    if (!fs.existsSync(NOTES_DIR)){
        fs.mkdirSync(NOTES_DIR, {recursive: true});
    }
    const watcher = chokidar.watch(NOTES_DIR, {
        ignoreInitial: true,
        depth: 0,
        awaitWriteFinish: {stabilityThreshold: 200, pollInterval: 50}
    });

    const broadcast = () => {
        console.log('[main] notes-changed broadcast 시도, win:', !!win, 'destroyed:', win && win.isDestroyed());
        if (win && !win.isDestroyed()) {
            win.webContents.send('notes-changed');
        }
    };
    watcher.on('add', broadcast);
    watcher.on('change', broadcast);
    watcher.on('unlink', broadcast);
}

//-------------------------------------------------------------
// SVG → PNG 변환 후 저장
//   args: { svgString: string, defaultPath?: string, scale?: number }
//-------------------------------------------------------------
ipcMain.handle('save-svg-as-png', async (event, { svgString, defaultPath = 'mindmap.png', scale = 2 }) => {
  try {
    const { filePath, canceled } = await dialog.showSaveDialog({
      defaultPath,
      filters: [{ name: 'PNG Image', extensions: ['png'] }]
    });
    if (canceled || !filePath) return { ok: false };

    // density 로 해상도 제어 : 기본 72dpi → 72 * scale
    const pngBuffer = await sharp(Buffer.from(svgString), { density: 72 * scale })
      .png({ compressionLevel: 9 })
      .toBuffer();

    await fs.promises.writeFile(filePath, pngBuffer);
    return { ok: true, filePath };
  } catch (e) {
    console.error('save-svg-as-png error:', e);
    return { ok: false, error: String(e) };
  }
});

//-------------------------------------------------------------
// 단순 바이트(예: SVG 문자열 base64) 저장
//   args: { dataBase64: string, defaultPath?: string, mime?: string }
//-------------------------------------------------------------
ipcMain.handle('save-bytes', async (event, { dataBase64, defaultPath = 'file.bin', mime = '' }) => {
  try {
    const { filePath, canceled } = await dialog.showSaveDialog({
      defaultPath,
      filters: [
        mime.includes('svg') ? { name: 'SVG Image', extensions: ['svg'] } :
        mime.includes('png') ? { name: 'PNG Image', extensions: ['png'] } :
        { name: 'All Files', extensions: ['*'] }
      ]
    });
    if (canceled || !filePath) return { ok: false };

    const buffer = Buffer.from(dataBase64, 'base64');
    await fs.promises.writeFile(filePath, buffer);
    return { ok: true, filePath };
  } catch (e) {
    console.error('save-bytes error:', e);
    return { ok: false, error: String(e) };
  }
});

// --- 창 / 트레이 / 핫키 -------------------------------------------------------
function toggleWindow() {
  if (!win) return;
  if (win.isVisible()) {
    win.hide();
  } else {
    win.show();
    win.focus();
  }
}

function createTray() {
  // 아이콘 준비(없으면 기본 텍스트 메뉴로도 동작)
  const iconPath = path.join(__dirname, 'note_app_log.png'); // 직접 아이콘 넣어두면 좋아요
  const trayIcon = fs.existsSync(iconPath)
    ? nativeImage.createFromPath(iconPath)
    : undefined;

  tray = new Tray(trayIcon || nativeImage.createEmpty());

  const contextMenu = Menu.buildFromTemplate([
    { label: '열기 / 숨기기', click: () => toggleWindow() },
    { type: 'separator' },
    { label: '항상 위 (토글)', type: 'checkbox', checked: false, click: (item) => win.setAlwaysOnTop(item.checked) },
    { type: 'separator' },
    { label: '완전히 종료', click: () => { isQuitting = true; app.quit(); } },
  ]);

  tray.setToolTip('Local Notes'); 
  tray.setContextMenu(contextMenu);
  tray.on('click', () => toggleWindow()); // 트레이 아이콘 클릭으로도 토글
}

function createWindow() {
  win = new BrowserWindow({
    width: 920,
    height: 680,
    show: false, // 처음엔 숨김(백그라운드 시작)
    title: 'Local Notes',
    icon: path.join(__dirname, 'note_app_log.png'),
    webPreferences: { preload: path.join(__dirname, 'preload.js') }
  });

  win.loadFile(path.join(__dirname, 'renderer', 'index.html'));

  // 닫기(X) 눌러도 종료하지 않고 '숨김'
  win.on('close', (e) => {
    if (!isQuitting) {
      e.preventDefault();
      win.hide();
    }
  });

  // 모든 창이 닫혀도 앱은 살아있음(트레이 앱)
  // app.on('window-all-closed', ...) 에서 quit 하지 않음
}

// 단일 인스턴스(앱 중복 실행 방지)
const gotLock = app.requestSingleInstanceLock();
if (!gotLock) {
  app.quit();
} else {
  app.on('second-instance', () => {
    if (win) { win.show(); win.focus(); }
  });
}

// --- 부팅 플로우 --------------------------------------------------------------
app.whenReady().then(async () => {
  createTray();
  createWindow();
  watchNoteDir();

  startPython();
  try {
    await waitForServer(); // API 준비 대기(짧게)
  } catch (e) {
    console.warn('[WARN]', e.message);
    // 필요한 경우 renderer에서 "서버 대기 중" 메시지를 띄울 수 있음
  }

  // 전역 핫키: Ctrl+Alt+N → 창 토글
  globalShortcut.register('Control+Alt+N', () => toggleWindow());

  // 앱 시작 시는 최소화(숨김) 상태로 유지 → 사용자가 핫키나 트레이로 호출
  // 원하면 아래 줄을 주석 해제하여 처음 한 번은 보여줄 수도 있음:
  // win.show(); win.focus();

  // API 워치독 시작
  startApiWatchdog(5000);

  app.on('activate', () => {
    // macOS에서 Dock으로 다시 활성화될 때
    if (BrowserWindow.getAllWindows().length === 0) createWindow();
    else { win.show(); win.focus(); }
  });
});

// 종료 처리
app.on('will-quit', () => {
  globalShortcut.unregisterAll();
  stopPython();
});
app.on('window-all-closed', () => {
  // 의도적으로 아무 것도 하지 않음 → 트레이 앱으로 유지
});
process.on('exit', stopPython);
process.on('SIGINT', () => { stopPython(); process.exit(0); });
