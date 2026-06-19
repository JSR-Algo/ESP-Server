export interface FirmwareType {
  name: string
  key: string
}

export interface Device {
  id: string
  userId: string
  macAddress: string
  lastConnectedAt: string
  autoUpdate: number
  board: string
  alias?: string
  childName?: string
  childAge?: number
  agentId: string
  appVersion: string
  sort: number
  updater?: string
  updateDate: string
  creator: string
  createDate: string
}

export interface DeviceProfileUpdate {
  alias?: string
  childName?: string
  childAge?: number
}
