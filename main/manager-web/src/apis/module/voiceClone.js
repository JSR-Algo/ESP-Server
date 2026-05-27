import { getServiceUrl } from '../api';
import RequestService from '../httpRequest';

export default {
    // Page query voice resources
    getVoiceCloneList(params, callback) {
        RequestService.sendRequest()
            .url(`${getServiceUrl()}/voiceClone`)
            .method('GET')
            .data(params)
            .success((res) => {
                RequestService.clearRequestTime();
                callback(res);
            })
            .networkFail((err) => {
                console.error('Failed to get voice list:', err);
                RequestService.reAjaxFun(() => {
                    this.getVoiceCloneList(params, callback);
                });
            }).send();
    },

    // UploadAudio file
    uploadVoice(formData, callback) {
        RequestService.sendRequest()
            .url(`${getServiceUrl()}/voiceClone/upload`)
            .method('POST')
            .data(formData)
            .success((res) => {
                RequestService.clearRequestTime();
                callback(res);
            })
            .networkFail((err) => {
                console.error('Upload audio failed:', err);
                RequestService.reAjaxFun(() => {
                    this.uploadVoice(formData, callback);
                });
            }).send();
    },

    // UpdateVoice name
    updateName(params, callback) {
        RequestService.sendRequest()
            .url(`${getServiceUrl()}/voiceClone/updateName`)
            .method('POST')
            .data(params)
            .success((res) => {
                RequestService.clearRequestTime();
                callback(res);
            })
            .networkFail((err) => {
                console.error('Update name failed:', err);
                RequestService.reAjaxFun(() => {
                    this.updateName(params, callback);
                });
            }).send();
    },

    // Get audio download ID
    getAudioId(id, callback) {
        RequestService.sendRequest()
            .url(`${getServiceUrl()}/voiceClone/audio/${id}`)
            .method('POST')
            .success((res) => {
                RequestService.clearRequestTime();
                callback(res);
            })
            .networkFail((err) => {
                console.error('Failed to get audio ID:', err);
                RequestService.reAjaxFun(() => {
                    this.getAudioId(id, callback);
                });
            }).send();
    },

    // Get audio playbackURL
    getPlayVoiceUrl(uuid) {
        return `${getServiceUrl()}/voiceClone/play/${uuid}`;
    },

    // Cloned audio
    cloneAudio(params, callback, errorCallback) {
        RequestService.sendRequest()
            .url(`${getServiceUrl()}/voiceClone/cloneAudio`)
            .method('POST')
            .data(params)
            .success((res) => {
                RequestService.clearRequestTime();
                callback(res);
            })
            .fail((res) => {
                // Business failure callback
                RequestService.clearRequestTime();
                if (errorCallback) {
                    errorCallback(res);
                } else {
                    callback(res);
                }
            })
            .networkFail((err) => {
                console.error('Upload failed:', err);
                RequestService.reAjaxFun(() => {
                    this.cloneAudio(params, callback, errorCallback);
                });
            }).send();
    }
}
