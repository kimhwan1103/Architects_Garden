const {contextBridge, ipcRenderer} = require('electron');

contextBridge.exposeInMainWorld('api', {
    onNotesChanged: (handler) => {
        const listener = () => {
            console.log('[preload] notes-changed 이벤트 수신'); // 이벤트 수신 확인용 로그
            handler();
        };
        ipcRenderer.on('notes-changed', listener);
        return () => ipcRenderer.removeListener('notes-changed', listener);
    },
    saveSvgAsPng: (args /* { svgString, defaultPath?: string, scale?: number } */) =>
        ipcRenderer.invoke('save-svg-as-png', args),

    saveBytes: (args /* { dataBase64, defaultPath?: string, mime?: string } */) =>
        ipcRenderer.invoke('save-bytes', args),
});