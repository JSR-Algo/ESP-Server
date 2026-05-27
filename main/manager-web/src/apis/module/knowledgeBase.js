import { getServiceUrl } from '../api';
import RequestService from '../httpRequest';

/**
 * Get authtoken
 */
function getAuthToken() {
  return localStorage.getItem('token') || '';
}

/**
 * GeneralAPIRequest wrapper
 * @param {Object} config - Request config
 * @param {string} config.url - RequestURL
 * @param {string} config.method - Request method
 * @param {Object} [config.data] - Request data
 * @param {Object} [config.headers] - ExtraRequest header
 * @param {Function} config.callback - Success callback
 * @param {Function} [config.errorCallback] - ErrorCallback
 * @param {string} [config.errorMessage] - ErrorMessage
 * @param {Function} [config.retryFunction] - Retry function
 */
function makeApiRequest(config) {
  const token = getAuthToken();
  const { url, method, data, headers, callback, errorCallback, errorMessage, retryFunction } = config;

  const requestBuilder = RequestService.sendRequest()
    .url(url)
    .method(method)
    .header({
      'Authorization': `Bearer ${token}`,
      ...headers
    });

  if (data) {
    requestBuilder.data(data);
  }

  requestBuilder
    .success((res) => {
      RequestService.clearRequestTime();
      callback(res);
    })
    .fail((err) => {
      console.error(errorMessage || 'Operation failed', err);
      if (errorCallback) {
        errorCallback(err);
      }
    })
    .networkFail(() => {
      if (retryFunction) {
        RequestService.reAjaxFun(() => {
          retryFunction();
        });
      }
    }).send();
}

/**
 * Knowledge base managementRelatedAPI
 */
export default {
  /**
   * Get knowledge base list
   * @param {Object} params - Query parameters
   * @param {Function} callback - Callback function
   * @param {Function} errorCallback - ErrorCallback
   */
  getKnowledgeBaseList(params, callback, errorCallback) {
    const queryParams = new URLSearchParams({
      page: params.page,
      page_size: params.page_size,
      name: params.name || ''
    }).toString();

    makeApiRequest({
      url: `${getServiceUrl()}/datasets?${queryParams}`,
      method: 'GET',
      callback: callback,
      errorCallback: errorCallback,
      errorMessage: 'Failed to get knowledge base list',
      retryFunction: () => this.getKnowledgeBaseList(params, callback, errorCallback)
    });
  },

  /**
   * Create knowledge base
   * @param {Object} data - Knowledge base data
   * @param {Function} callback - Callback function
   * @param {Function} errorCallback - ErrorCallback
   */
  createKnowledgeBase(data, callback, errorCallback) {
    console.log('createKnowledgeBase called with data:', data);
    console.log('API URL:', `${getServiceUrl()}/datasets`);

    makeApiRequest({
      url: `${getServiceUrl()}/datasets`,
      method: 'POST',
      data: data,
      headers: { 'Content-Type': 'application/json' },
      callback: (res) => {
        console.log('createKnowledgeBase success response:', res);
        callback(res);
      },
      errorCallback: (err) => {
        console.error('Create knowledge base failed:', err);
        if (err.response) {
          console.error('Error response data:', err.response.data);
          console.error('Error response status:', err.response.status);
        }
        if (errorCallback) {
          errorCallback(err);
        }
      },
      errorMessage: 'Create knowledge base failed',
      retryFunction: () => this.createKnowledgeBase(data, callback, errorCallback)
    });
  },

  /**
   * Update knowledge base
   * @param {string} datasetId - Knowledge base ID
   * @param {Object} data - Update data
   * @param {Function} callback - Callback function
   * @param {Function} errorCallback - ErrorCallback
   */
  updateKnowledgeBase(datasetId, data, callback, errorCallback) {
    console.log('updateKnowledgeBase called with datasetId:', datasetId, 'data:', data);
    console.log('API URL:', `${getServiceUrl()}/datasets/${datasetId}`);

    makeApiRequest({
      url: `${getServiceUrl()}/datasets/${datasetId}`,
      method: 'PUT',
      data: data,
      headers: { 'Content-Type': 'application/json' },
      callback: callback,
      errorCallback: errorCallback,
      errorMessage: 'Update knowledge base failed',
      retryFunction: () => this.updateKnowledgeBase(datasetId, data, callback, errorCallback)
    });
  },

  /**
   * Delete single knowledge base
   * @param {string} datasetId - Knowledge base ID
   * @param {Function} callback - Callback function
   * @param {Function} errorCallback - ErrorCallback
   */
  deleteKnowledgeBase(datasetId, callback, errorCallback) {
    console.log('deleteKnowledgeBase called with datasetId:', datasetId);
    console.log('API URL:', `${getServiceUrl()}/datasets/${datasetId}`);

    makeApiRequest({
      url: `${getServiceUrl()}/datasets/${datasetId}`,
      method: 'DELETE',
      callback: callback,
      errorCallback: errorCallback,
      errorMessage: 'Delete knowledge base failed',
      retryFunction: () => this.deleteKnowledgeBase(datasetId, callback, errorCallback)
    });
  },

  /**
   * Batch delete knowledge bases
   * @param {string|Array} ids - Knowledge base IDString or array
   * @param {Function} callback - Callback function
   * @param {Function} errorCallback - ErrorCallback
   */
  deleteKnowledgeBases(ids, callback, errorCallback) {
    // EnsureidsString in correct format
    const idsStr = Array.isArray(ids) ? ids.join(',') : ids;

    makeApiRequest({
      url: `${getServiceUrl()}/datasets/batch?ids=${idsStr}`,
      method: 'DELETE',
      callback: callback,
      errorCallback: errorCallback,
      errorMessage: 'Batch delete knowledge bases failed',
      retryFunction: () => this.deleteKnowledgeBases(ids, callback, errorCallback)
    });
  },

  /**
   * GetDocumentList
   * @param {string} datasetId - Knowledge base ID
   * @param {Object} params - Query parameters
   * @param {Function} callback - Callback function
   * @param {Function} errorCallback - ErrorCallback
   */
  getDocumentList(datasetId, params, callback, errorCallback) {
    const queryParams = new URLSearchParams({
      page: params.page,
      page_size: params.page_size,
      name: params.name || ''
    }).toString();

    makeApiRequest({
      url: `${getServiceUrl()}/datasets/${datasetId}/documents?${queryParams}`,
      method: 'GET',
      callback: callback,
      errorCallback: errorCallback,
      errorMessage: 'Get document list failed',
      retryFunction: () => this.getDocumentList(datasetId, params, callback, errorCallback)
    });
  },

  /**
   * UploadDocument
   * @param {string} datasetId - Knowledge base ID
   * @param {Object} formData - Form data
   * @param {Function} callback - Callback function
   * @param {Function} errorCallback - ErrorCallback
   */
  uploadDocument(datasetId, formData, callback, errorCallback) {
    makeApiRequest({
      url: `${getServiceUrl()}/datasets/${datasetId}/documents`,
      method: 'POST',
      data: formData,
      headers: { 'Content-Type': 'multipart/form-data' },
      callback: callback,
      errorCallback: errorCallback,
      errorMessage: 'Upload document failed',
      retryFunction: () => this.uploadDocument(datasetId, formData, callback, errorCallback)
    });
  },

  /**
   * ParseDocument
   * @param {string} datasetId - Knowledge base ID
   * @param {string} documentId - Document ID
   * @param {Function} callback - Callback function
   * @param {Function} errorCallback - ErrorCallback
   */
  parseDocument(datasetId, documentId, callback, errorCallback) {
    const requestBody = {
      document_ids: [documentId]
    };

    makeApiRequest({
      url: `${getServiceUrl()}/datasets/${datasetId}/chunks`,
      method: 'POST',
      data: requestBody,
      headers: { 'Content-Type': 'application/json' },
      callback: callback,
      errorCallback: errorCallback,
      errorMessage: 'Parse document failed',
      retryFunction: () => this.parseDocument(datasetId, documentId, callback, errorCallback)
    });
  },

  /**
   * DeleteDocument
   * @param {string} datasetId - Knowledge base ID
   * @param {string} documentId - Document ID
   * @param {Function} callback - Callback function
   * @param {Function} errorCallback - ErrorCallback
   */
  deleteDocument(datasetId, documentId, callback, errorCallback) {
    makeApiRequest({
      url: `${getServiceUrl()}/datasets/${datasetId}/documents/${documentId}`,
      method: 'DELETE',
      callback: callback,
      errorCallback: errorCallback,
      errorMessage: 'Delete document failed',
      retryFunction: () => this.deleteDocument(datasetId, documentId, callback, errorCallback)
    });
  },

  /**
   * GetDocumentChunk list
   * @param {string} datasetId - Knowledge base ID
   * @param {string} documentId - Document ID
   * @param {Object} params - Query parameters
   * @param {Function} callback - Callback function
   * @param {Function} errorCallback - ErrorCallback
   */
  listChunks(datasetId, documentId, params, callback, errorCallback) {
    let queryParams = new URLSearchParams({
      page: params.page || 1,
      page_size: params.page_size || 10
    }).toString();

    // AddKeyword searchParameter
    if (params.keywords) {
      queryParams += `&keywords=${encodeURIComponent(params.keywords)}`;
    }

    makeApiRequest({
      url: `${getServiceUrl()}/datasets/${datasetId}/documents/${documentId}/chunks?${queryParams}`,
      method: 'GET',
      callback: callback,
      errorCallback: errorCallback,
      errorMessage: 'Get slice list failed',
      retryFunction: () => this.listChunks(datasetId, documentId, params, callback, errorCallback)
    });
  },

  /**
   * Recall test
   * @param {string} datasetId - Knowledge base ID
   * @param {Object} data - Recall testParameter
   * @param {Function} callback - Callback function
   * @param {Function} errorCallback - ErrorCallback
   */
  retrievalTest(datasetId, data, callback, errorCallback) {
    makeApiRequest({
      url: `${getServiceUrl()}/datasets/${datasetId}/retrieval-test`,
      method: 'POST',
      data: data,
      headers: { 'Content-Type': 'application/json' },
      callback: callback,
      errorCallback: errorCallback,
      errorMessage: 'Recall test failed',
      retryFunction: () => this.retrievalTest(datasetId, data, callback, errorCallback)
    });
  }

};