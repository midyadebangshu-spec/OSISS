import type { OsissBridge } from '../../shared/types.ts'

declare global {
  interface Window {
    osiss: OsissBridge
  }
}

export const osiss = window.osiss
