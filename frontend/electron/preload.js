// electron/preload.js
const { contextBridge, ipcRenderer } = require('electron');

// 在渲染进程中暴露安全的API
contextBridge.exposeInMainWorld('electronAPI', {
  // 可以在这里添加与主进程通信的方法
  sendMessage: (message) => ipcRenderer.send('message', message),
  onMessage: (callback) => ipcRenderer.on('message', callback)
});