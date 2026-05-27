import { getServiceUrl } from '../api'
import RequestService from '../httpRequest'


export default {
    // Login
    login(loginForm, callback, failCallback) {
        RequestService.sendRequest()
            .url(`${getServiceUrl()}/user/login`)
            .method('POST')
            .data(loginForm)
            .success((res) => {
                RequestService.clearRequestTime()
                callback(res)
            })
            .fail((err) => {
                RequestService.clearRequestTime()
                failCallback(err)
            })
            .networkFail(() => {
                RequestService.reAjaxFun(() => {
                    this.login(loginForm, callback)
                })
            }).send()
    },
    // GetVerification code
    getCaptcha(uuid, callback) {
        RequestService.sendRequest()
            .url(`${getServiceUrl()}/user/captcha?uuid=${uuid}`)
            .method('GET')
            .type('blob')
            .header({
                'Content-Type': 'image/gif',
                'Pragma': 'No-cache',
                'Cache-Control': 'no-cache'
            })
            .success((res) => {
                RequestService.clearRequestTime();
                callback(res);
            })
            .networkFail((err) => {  // AddErrorParameter

            }).send()
    },
    // SendSMS verification code
    sendSmsVerification(data, callback, failCallback) {
        RequestService.sendRequest()
            .url(`${getServiceUrl()}/user/smsVerification`)
            .method('POST')
            .data(data)
            .success((res) => {
                RequestService.clearRequestTime()
                callback(res)
            })
            .fail((err) => {
                RequestService.clearRequestTime()
                failCallback(err)
            })
            .networkFail(() => {
                RequestService.reAjaxFun(() => {
                    this.sendSmsVerification(data, callback, failCallback)
                })
            }).send()
    },
    // RegisterAccount
    register(registerForm, callback, failCallback) {
        RequestService.sendRequest()
            .url(`${getServiceUrl()}/user/register`)
            .method('POST')
            .data(registerForm)
            .success((res) => {
                RequestService.clearRequestTime()
                callback(res)
            })
            .fail((err) => {
                RequestService.clearRequestTime()
                failCallback(err)
            })
            .networkFail(() => {
                RequestService.reAjaxFun(() => {
                    this.register(registerForm, callback, failCallback)
                })
            }).send()
    },
    // SaveDevice config
    saveDeviceConfig(device_id, configData, callback) {
        RequestService.sendRequest()
            .url(`${getServiceUrl()}/user/configDevice/${device_id}`)
            .method('PUT')
            .data(configData)
            .success((res) => {
                RequestService.clearRequestTime();
                callback(res);
            })
            .networkFail((err) => {
                console.error('Save config failed:', err);
                RequestService.reAjaxFun(() => {
                    this.saveDeviceConfig(device_id, configData, callback);
                });
            }).send();
    },
    // Get user info
    getUserInfo(callback) {
        RequestService.sendRequest()
            .url(`${getServiceUrl()}/user/info`)
            .method('GET')
            .success((res) => {
                RequestService.clearRequestTime()
                callback(res)
            })
            .networkFail((err) => {
                console.error('API request failed:', err)
                RequestService.reAjaxFun(() => {
                    this.getUserInfo(callback)
                })
            }).send()
    },
    // Change user password
    changePassword(oldPassword, newPassword, successCallback, errorCallback) {
        RequestService.sendRequest()
            .url(`${getServiceUrl()}/user/change-password`)
            .method('PUT')
            .data({
                password: oldPassword,
                newPassword: newPassword,
            })
            .success((res) => {
                RequestService.clearRequestTime();
                successCallback(res);
            })
            .networkFail((error) => {
                RequestService.reAjaxFun(() => {
                    this.changePassword(oldPassword, newPassword, successCallback, errorCallback);
                });
            })
            .send();
    },
    // ModifyUser status
    changeUserStatus(status, userIds, successCallback) {
        console.log(555, userIds)
        RequestService.sendRequest()
            .url(`${getServiceUrl()}/admin/users/changeStatus/${status}`)
            .method('put')
            .data(userIds)
            .success((res) => {
                RequestService.clearRequestTime()
                successCallback(res);
            })
            .networkFail((err) => {
                console.error('Failed to modify user status:', err)
                RequestService.reAjaxFun(() => {
                    this.changeUserStatus(status, userIds)
                })
            }).send()
    },
    // GetPublic config
    getPubConfig(callback, failCallback) {
        RequestService.sendRequest()
            .url(`${getServiceUrl()}/user/pub-config`)
            .method('GET')
            .success((res) => {
                RequestService.clearRequestTime();
                callback(res);
            })
            .fail((err) => {
                RequestService.clearRequestTime();
                if (failCallback) {
                    failCallback(err);
                }
            })
            .networkFail((err) => {
                console.error('Failed to get public config:', err);
                RequestService.reAjaxFun(() => {
                    this.getPubConfig(callback, failCallback);
                });
            }).send();
    },
    // Recover userPassword
    retrievePassword(passwordData, callback, failCallback) {
        RequestService.sendRequest()
            .url(`${getServiceUrl()}/user/retrieve-password`)
            .method('PUT')
            .data({
                phone: passwordData.phone,
                code: passwordData.code,
                password: passwordData.password,
                captchaId: passwordData.captchaId
            })
            .success((res) => {
                RequestService.clearRequestTime();
                callback(res);
            })
            .fail((err) => {
                RequestService.clearRequestTime();
                failCallback(err);
            })
            .networkFail(() => {
                RequestService.reAjaxFun(() => {
                    this.retrievePassword(passwordData, callback, failCallback);
                });
            }).send()
    },

}
