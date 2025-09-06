const {contextBridge, ipcRenderer} = require('electron');

contextBridge.exposeInMainWorld('api', {
    onNotesChanged: (handler) => {
        const listener = () => {
            console.log('[preload] notes-changed 이벤트 수신'); // 이벤트 수신 확인용 로그
            handler();
        };
        ipcRenderer.on('notes-changed', listener);
        return () => ipcRenderer.removeListener('notes-changed', listener);
    }
});