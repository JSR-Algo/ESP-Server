// 全局要用的类型放到这里

declare global {
  interface IResData<T> {
    code: number
    msg: string
    data: T
  }

  // uni.uploadFile文件上传参数
  interface IUniUploadFileOptions {
    file?: File
    files?: UniApp.UploadFileOptionFiles[]
    filePath?: string
    name?: string
    formData?: any
  }

  interface IUserInfo {
    nickname?: string
    avatar?: string
    /** 微信的 openid，非微信没有这个字段 */
    openid?: string
    token?: string
  }
}

declare module 'wot-design-uni/components/wd-message-box' {
  type MessageBoxOptions = Record<string, unknown> | string

  export function useMessage(): {
    alert: (options: MessageBoxOptions) => Promise<unknown>
    confirm: (options: MessageBoxOptions) => Promise<unknown>
    prompt: (options: MessageBoxOptions) => Promise<unknown>
  }
}

export {} // 防止模块污染
