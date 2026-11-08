// Preload: the only bridge between renderer and main. Renderer code never
// touches node/Electron APIs or the network directly.
'use strict';

const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('dash', {
  status: () => ipcRenderer.invoke('dash:status'),
  tools: () => ipcRenderer.invoke('dash:tools'),
  skills: () => ipcRenderer.invoke('dash:skills'),
  modules: () => ipcRenderer.invoke('dash:modules'),
  setModule: (module, enabled) => ipcRenderer.invoke('dash:set-module', module, enabled),
  setSkill: (id, enabled) => ipcRenderer.invoke('dash:set-skill', id, enabled),
  updateSkill: (id, live) => ipcRenderer.invoke('dash:update-skill', id, live),
  updateAll: (live) => ipcRenderer.invoke('dash:update-all', live),
  daemon: (action) => ipcRenderer.invoke('dash:daemon', action),
  openOutput: () => ipcRenderer.invoke('dash:open-output'),
  onTray: (fn) => ipcRenderer.on('dash:tray', (_e, state) => fn(state)),
});
