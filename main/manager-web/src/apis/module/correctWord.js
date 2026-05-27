import { getServiceUrl } from '../api';
import RequestService from '../httpRequest';


export default {
    // GetReplacement wordFile list
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
                console.error('Failed to get replacement word file list:', err)
                RequestService.reAjaxFun(() => {
                    this.getFileList(params, callback)
                })
            }).send()
    },

    // Get allReplacement wordFile (notPagination)
    selectAll(callback) {
        RequestService.sendRequest()
            .url(`${getServiceUrl()}/correct-word/file/select`)
            .method('GET')
            .success((res) => {
                RequestService.clearRequestTime()
                callback(res)
            })
            .networkFail((err) => {
                console.error('Failed to get all replacement word files:', err)
                RequestService.reAjaxFun(() => {
                    this.selectAll(callback)
                })
            }).send()
    },

    // Download replacement word file
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

    // Add replacement wordFile
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

    // UpdateReplacement wordFile
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

    // DeleteReplacement wordFile
    deleteFile(id, callback) {
        RequestService.sendRequest()
            .url(`${getServiceUrl()}/correct-word/file/${id}`)
            .method('DELETE')
            .success((res) => {
                RequestService.clearRequestTime()
                callback(res)
            })
            .networkFail((err) => {
                console.error('Failed to delete replacement word file:', err)
                RequestService.reAjaxFun(() => {
                    this.deleteFile(id, callback)
                })
            }).send()
    },

    // BatchDeleteReplacement wordFile
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
                console.error('Failed to batch delete replacement word files:', err)
                RequestService.reAjaxFun(() => {
                    this.batchDeleteFile(ids, callback)
                })
            }).send()
    }
}
