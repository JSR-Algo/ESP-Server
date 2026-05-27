import { getServiceUrl } from '../api';
import RequestService from '../httpRequest';


export default {
    // 获取替换词文件列表
    getFileList(params, callback) {
        const queryParams = new URLSearchParams({
            page: params.page,
            pageSize: params.pageSize
        }).toString();

        RequestService.sendRequest()
            .url(`${getServiceUrl()}/correct-word/file/list?${queryParams}`)
            .method('GET')
            .success((res) => {
                RequestService.clearRequestTime()
                callback(res)
            })
            .networkFail((err) => {
                console.error('获取替换词文件列表失败:', err)
                RequestService.reAjaxFun(() => {
                    this.getFileList(params, callback)
                })
            }).send()
    },

    // 获取所有替换词文件（不分页）
    selectAll(callback) {
        RequestService.sendRequest()
            .url(`${getServiceUrl()}/correct-word/file/select`)
            .method('GET')
            .success((res) => {
                RequestService.clearRequestTime()
                callback(res)
            })
            .networkFail((err) => {
                console.error('获取所有替换词文件失败:', err)
                RequestService.reAjaxFun(() => {
                    this.selectAll(callback)
                })
            }).send()
    },

    // 下载替换词文件
    downloadFile(id, callback) {
        RequestService.sendRequest()
            .url(`${getServiceUrl()}/correct-word/file/download/${id}`)
            .method('GET')
            .success((res) => {
                RequestService.clearRequestTime()
                callback(res)
            })
            .fail((err) => {
              RequestService.clearRequestTime()
              callback(err)
            }).send()
    },

    // 新增替换词文件
    addFile(data, callback) {
        RequestService.sendRequest()
            .url(`${getServiceUrl()}/correct-word/file`)
            .method('POST')
            .data(data)
            .success((res) => {
                RequestService.clearRequestTime()
                callback(res)
            })
            .fail((err) => {
              RequestService.clearRequestTime()
              callback(err)
            }).send()
    },

    // 更新替换词文件
    updateFile(data, callback) {
        RequestService.sendRequest()
            .url(`${getServiceUrl()}/correct-word/file/${data.id}`)
            .method('PUT')
            .data({
                fileName: data.fileName,
                content: data.content
            })
            .success((res) => {
                RequestService.clearRequestTime()
                callback(res)
            })
            .fail((err) => {
              RequestService.clearRequestTime()
              callback(err)
            }).send()
    },

    // 删除替换词文件
    deleteFile(id, callback) {
        RequestService.sendRequest()
            .url(`${getServiceUrl()}/correct-word/file/${id}`)
            .method('DELETE')
            .success((res) => {
                RequestService.clearRequestTime()
                callback(res)
            })
            .networkFail((err) => {
                console.error('删除替换词文件失败:', err)
                RequestService.reAjaxFun(() => {
                    this.deleteFile(id, callback)
                })
            }).send()
    },

    // 批量删除替换词文件
    batchDeleteFile(ids, callback) {
        RequestService.sendRequest()
            .url(`${getServiceUrl()}/correct-word/file/batch-delete`)
            .method('POST')
            .data(ids)
            .success((res) => {
                RequestService.clearRequestTime()
                callback(res)
            })
            .networkFail((err) => {
                console.error('批量删除替换词文件失败:', err)
                RequestService.reAjaxFun(() => {
                    this.batchDeleteFile(ids, callback)
                })
            }).send()
    }
}
