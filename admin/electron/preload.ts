// Exposes the typed `window.osiss` bridge (see shared/types.ts); nothing else from Node reaches the renderer.

import { contextBridge, ipcRenderer } from 'electron'

import type { OsissBridge } from '../shared/types.ts'

const call =
  (channel: string) =>
  (...args: unknown[]) =>
    ipcRenderer.invoke(channel, ...args)

const bridge: OsissBridge = {
  vault: {
    status: call('vault:status') as OsissBridge['vault']['status'],
    beginSetup: call('vault:beginSetup') as OsissBridge['vault']['beginSetup'],
    confirmSetup: call('vault:confirmSetup') as OsissBridge['vault']['confirmSetup'],
    unlock: call('vault:unlock') as OsissBridge['vault']['unlock'],
    lock: call('vault:lock') as OsissBridge['vault']['lock'],
    changePasskey: call('vault:changePasskey') as OsissBridge['vault']['changePasskey'],
    setAutoLock: call('vault:setAutoLock') as OsissBridge['vault']['setAutoLock'],
    reset: call('vault:reset') as OsissBridge['vault']['reset'],
  },
  profiles: {
    list: call('profiles:list') as OsissBridge['profiles']['list'],
    save: call('profiles:save') as OsissBridge['profiles']['save'],
    remove: call('profiles:remove') as OsissBridge['profiles']['remove'],
    tokenFromEnv: call('profiles:tokenFromEnv') as OsissBridge['profiles']['tokenFromEnv'],
  },
  api: {
    request: call('api:request') as OsissBridge['api']['request'],
    upload: call('api:upload') as OsissBridge['api']['upload'],
    openBookPdf: call('api:openBookPdf') as OsissBridge['api']['openBookPdf'],
  },
  files: {
    pickPdf: call('files:pickPdf') as OsissBridge['files']['pickPdf'],
    pickDirectory: call('files:pickDirectory') as OsissBridge['files']['pickDirectory'],
  },
  local: {
    status: call('local:status') as OsissBridge['local']['status'],
    docker: call('local:docker') as OsissBridge['local']['docker'],
    api: call('local:api') as OsissBridge['local']['api'],
    apiLog: call('local:apiLog') as OsissBridge['local']['apiLog'],
  },
  copyText: call('clipboard:write') as OsissBridge['copyText'],
}

contextBridge.exposeInMainWorld('osiss', bridge)
