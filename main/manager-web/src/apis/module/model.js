import { getServiceUrl } from '../api';
import RequestService from '../httpRequest';

export default {
  // Get model config list
  getModelList(params, callback) {
    const queryParams = new URLSearchParams({
      modelType: params.modelType,
      modelName: params.modelName || '',
      page: params.page || 0,
      limit: params.limit || 10
    }).toString();

    RequestService.sendRequest()
      .url(`${getServiceUrl()}/models/list?${queryParams}`)
      .method('GET')
      .success((res) => {
        RequestService.clearRequestTime()
        callback(res)
      })
      .networkFail((err) => {
        console.error('Get model list failed:', err)
        RequestService.reAjaxFun(() => {
          this.getModelList(params, callback)
        })
      }).send()
  },
  // Get model provider list
  getModelProviders(modelType, callback) {
    RequestService.sendRequest()
      .url(`${getServiceUrl()}/models/${modelType}/provideTypes`)
      .method('GET')
      .success((res) => {
        RequestService.clearRequestTime()
        callback(res.data?.data || [])
      })
      .networkFail((err) => {
        console.error('Failed to get provider list:', err)
        this.$message.error('Get provider list failed')
        RequestService.reAjaxFun(() => {
          this.getModelProviders(modelType, callback)
        })
      }).send()
  },

  // Add model config
  addModel(params, callback) {
    const { modelType, provideCode, formData } = params;
    const postData = {
      id: formData.id,
      modelCode: formData.modelCode,
      modelName: formData.modelName,
      isDefault: formData.isDefault ? 1 : 0,
      isEnabled: formData.isEnabled ? 1 : 0,
      configJson: formData.configJson,
      docLink: formData.docLink,
      remark: formData.remark,
      sort: formData.sort || 0
    };

    RequestService.sendRequest()
      .url(`${getServiceUrl()}/models/${modelType}/${provideCode}`)
      .method('POST')
      .data(postData)
      .success((res) => {
        RequestService.clearRequestTime()
        callback(res)
      })
      .networkFail((err) => {
        console.error('Add model failed:', err)
        this.$message.error(err.msg || 'Add model failed')
        RequestService.reAjaxFun(() => {
          this.addModel(params, callback)
        })
      }).send()
  },
  // Delete model config
  deleteModel(id, callback) {
    RequestService.sendRequest()
      .url(`${getServiceUrl()}/models/${id}`)
      .method('DELETE')
      .success((res) => {
        RequestService.clearRequestTime()
        callback(res)
      })
      .networkFail((err) => {
        console.error('Delete model failed:', err)
        this.$message.error(err.msg || 'Delete model failed')
        RequestService.reAjaxFun(() => {
          this.deleteModel(id, callback)
        })
      }).send()
  },
  // GetModel nameList
  getModelNames(modelType, modelName, callback) {
    const queryParams = new URLSearchParams({
      modelType: modelType || '',
      modelName: modelName || ''
    }).toString();
    RequestService.sendRequest()
      .url(`${getServiceUrl()}/models/names?${queryParams}`)
      .method('GET')
      .success((res) => {
        RequestService.clearRequestTime();
        callback(res);
      })
      .networkFail(() => {
        RequestService.reAjaxFun(() => {
          this.getModelNames(modelType, modelName, callback);
        });
      }).send();
  },
  // GetLLMModel nameList
  getLlmModelCodeList(modelName, callback) {
    const queryParams = new URLSearchParams({
      modelName: modelName || ''
    }).toString();
    RequestService.sendRequest()
      .url(`${getServiceUrl()}/models/llm/names?${queryParams}`)
      .method('GET')
      .success((res) => {
        RequestService.clearRequestTime();
        callback(res);
      })
      .networkFail(() => {
        RequestService.reAjaxFun(() => {
          this.getLlmModelCodeList(modelName, callback);
        });
      }).send();
  },
  // Get model voiceList
  getModelVoices(modelId, voiceName, callback) {
    const queryParams = new URLSearchParams({
      voiceName: voiceName || ''
    }).toString();
    RequestService.sendRequest()
      .url(`${getServiceUrl()}/models/${modelId}/voices?${queryParams}`)
      .method('GET')
      .success((res) => {
        RequestService.clearRequestTime();
        callback(res);
      })
      .networkFail(() => {
        RequestService.reAjaxFun(() => {
          this.getModelVoices(modelId, voiceName, callback);
        });
      }).send();
  },
  // Get singleModel config
  getModelConfig(id, callback) {
    RequestService.sendRequest()
      .url(`${getServiceUrl()}/models/${id}`)
      .method('GET')
      .success((res) => {
        RequestService.clearRequestTime()
        callback(res)
      })
      .networkFail((err) => {
        console.error('Get model config failed:', err)
        this.$message.error(err.msg || 'Get model config failed')
        RequestService.reAjaxFun(() => {
          this.getModelConfig(id, callback)
        })
      }).send()
  },
  // Enable/DisableModelStatus
  updateModelStatus(id, status, callback) {
    RequestService.sendRequest()
      .url(`${getServiceUrl()}/models/enable/${id}/${status}`)
      .method('PUT')
      .success((res) => {
        RequestService.clearRequestTime()
        callback(res)
      })
      .networkFail((err) => {
        console.error('Update model status failed:', err)
        this.$message.error(err.msg || 'Update model status failed')
        RequestService.reAjaxFun(() => {
          this.updateModelStatus(id, status, callback)
        })
      }).send()
  },
  // UpdateModel config
  updateModel(params, callback) {
    const { modelType, provideCode, id, formData } = params;
    const payload = {
      ...formData,
      configJson: formData.configJson
    };
    RequestService.sendRequest()
      .url(`${getServiceUrl()}/models/${modelType}/${provideCode}/${id}`)
      .method('PUT')
      .data(payload)
      .success((res) => {
        RequestService.clearRequestTime();
        callback(res);
      })
      .networkFail((err) => {
        console.error('Update model failed:', err);
        this.$message.error(err.msg || 'Update model failed');
        RequestService.reAjaxFun(() => {
          this.updateModel(params, callback);
        });
      }).send();
  },
  // Set default model
  setDefaultModel(id, callback) {
    RequestService.sendRequest()
      .url(`${getServiceUrl()}/models/default/${id}`)
      .method('PUT')
      .success((res) => {
        RequestService.clearRequestTime()
        callback(res)
      })
      .networkFail((err) => {
        console.error('Set default model failed:', err)
        this.$message.error(err.msg || 'Set default model failed')
        RequestService.reAjaxFun(() => {
          this.setDefaultModel(id, callback)
        })
      }).send()
  },

  /**
   * Get model config list(supports query params)
   * @param {Object} params - Query parameter object, e.g. { name: 'test', modelType: 1 }
   * @param {Function} callback - Callback function
   */
  getModelProvidersPage(params, callback) {
    // Build query params
    const queryParams = new URLSearchParams();
    if (params.name) queryParams.append('name', params.name);
    if (params.modelType !== undefined) queryParams.append('modelType', params.modelType);
    if (params.page !== undefined) queryParams.append('page', params.page);
    if (params.limit !== undefined) queryParams.append('limit', params.limit);

    RequestService.sendRequest()
      .url(`${getServiceUrl()}/models/provider?${queryParams.toString()}`)
      .method('GET')
      .success((res) => {
        RequestService.clearRequestTime();
        callback(res);
      })
      .networkFail((err) => {
        this.$message.error(err.msg || 'Get provider list failed');
        RequestService.reAjaxFun(() => {
          this.getModelProviders(params, callback);
        });
      }).send();
  },

  /**
   * Add model providerConfig
   * @param {Object} params - Request parameter object, e.g. { modelType: '1', providerCode: '1', name: '1', fields: '1', sort: 1 }
   * @param {Function} callback - Success callback function
   */
  addModelProvider(params, callback) {
    const postData = {
      modelType: params.modelType || '',
      providerCode: params.providerCode || '',
      name: params.name || '',
      fields: JSON.stringify(params.fields || []),
      sort: params.sort || 0
    };

    RequestService.sendRequest()
      .url(`${getServiceUrl()}/models/provider`)
      .method('POST')
      .data(postData)
      .success((res) => {
        RequestService.clearRequestTime();
        callback(res);
      })
      .networkFail((err) => {
        console.error('Failed to add model provider:', err)
        this.$message.error(err.msg || 'Add model provider failed')
        RequestService.reAjaxFun(() => {
          this.addModelProvider(params, callback);
        });
      }).send();
  },

  /**
   * UpdateModel providerConfig
   * @param {Object} params - Request parameter object, e.g. { id: '111', modelType: '1', providerCode: '1', name: '1', fields: '1', sort: 1 }
   * @param {Function} callback - Success callback function
   */
  updateModelProvider(params, callback) {
    const putData = {
      id: params.id || '',
      modelType: params.modelType || '',
      providerCode: params.providerCode || '',
      name: params.name || '',
      fields: JSON.stringify(params.fields || []),
      sort: params.sort || 0
    };

    RequestService.sendRequest()
      .url(`${getServiceUrl()}/models/provider`)
      .method('PUT')
      .data(putData)
      .success((res) => {
        RequestService.clearRequestTime();
        callback(res);
      })
      .networkFail((err) => {
        this.$message.error(err.msg || 'Update model provider failed')
        RequestService.reAjaxFun(() => {
          this.updateModelProvider(params, callback);
        });
      }).send();
  },
  // Delete
  deleteModelProviderByIds(ids, callback) {
    RequestService.sendRequest()
      .url(`${getServiceUrl()}/models/provider/delete`)
      .method('POST')
      .data(ids)
      .success((res) => {
        RequestService.clearRequestTime()
        callback(res);
      })
      .networkFail((err) => {
        this.$message.error(err.msg || 'Delete model provider failed')
        RequestService.reAjaxFun(() => {
          this.deleteModelProviderByIds(ids, callback)
        })
      }).send()
  },
  // Get plugin list
  getPluginFunctionList(params, callback) {
    RequestService.sendRequest()
      .url(`${getServiceUrl()}/models/provider/plugin/names`)
      .method('GET')
      .success((res) => {
        RequestService.clearRequestTime()
        callback(res)
      })
      .networkFail((err) => {
        this.$message.error(err.msg || 'Get plugin list failed')
        RequestService.reAjaxFun(() => {
          this.getPluginFunctionList(params, callback)
        })
      }).send()
  },

  // Get RAG model list
  getRAGModels(callback) {
    RequestService.sendRequest()
      .url(`${getServiceUrl()}/datasets/rag-models`)
      .method('GET')
      .success((res) => {
        RequestService.clearRequestTime()
        callback(res)
      })
      .networkFail((err) => {
        console.error('Failed to get RAG model list:', err)
        this.$message.error(err.msg || 'Failed to get RAG model list')
        RequestService.reAjaxFun(() => {
          this.getRAGModels(callback)
        })
      }).send()
  }
}
