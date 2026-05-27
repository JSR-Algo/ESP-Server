<template>
  <div class="welcome">
    <HeaderBar />

    <div class="operation-bar">
      <div class="left-operations">
        <el-button class="btn-back" type="text" @click="$router.back()">
          &lt;
        </el-button>
        <h2 class="knowledge-base-title">{{ knowledgeBaseName }}</h2>
      </div>
      <div class="right-operations">
        <el-input :placeholder="$t('knowledgeFileUpload.searchPlaceholder')" v-model="searchName" class="search-input"
          @keyup.enter.native="handleSearch" clearable />
        <el-button class="btn-search" @click="handleSearch">{{ $t('knowledgeFileUpload.search') }}</el-button>
      </div>
    </div>

    <div class="main-wrapper">
      <div class="content-panel">
        <div class="content-area">
          <el-card class="params-card" shadow="never">
            <div class="table-wrapper">
              <el-table ref="fileTable" :data="fileList" class="transparent-table" v-loading="loading"
                element-loading-text="Loading" element-loading-spinner="el-icon-loading"
                element-loading-background="rgba(255, 255, 255, 0.7)" :header-cell-class-name="headerCellClassName"
                @selection-change="handleSelectionChange">
                <el-table-column type="selection" width="55" align="center"></el-table-column>
                <el-table-column :label="$t('knowledgeFileUpload.documentName')" prop="name" align="left">
                  <template slot-scope="scope">
                    <span class="document-name">{{ scope.row.name }}</span>
                  </template>
                </el-table-column>
                <el-table-column :label="$t('knowledgeFileUpload.uploadTime')" prop="createdAt" align="center"
                  width="300">
                  <template slot-scope="scope">
                    <span>{{ formatDate(scope.row.createdAt) }}</span>
                  </template>
                </el-table-column>
                <el-table-column :label="$t('knowledgeFileUpload.status')" align="center" width="120">
                  <template slot-scope="scope">
                    <el-tag :type="getParseStatusType(scope.row.parseStatusCode)" size="small">
                      {{ getParseStatusText(scope.row.parseStatusCode) }}
                    </el-tag>
                  </template>
                </el-table-column>
                <el-table-column :label="$t('knowledgeFileUpload.sliceCount')" align="center" width="100">
                  <template slot-scope="scope">
                    <span>{{ scope.row.sliceCount || 0 }}</span>
                  </template>
                </el-table-column>
                <el-table-column :label="$t('knowledgeFileUpload.operation')" align="center" width="200">
                  <template slot-scope="scope">
                    <el-button size="mini" type="text" @click="handleParse(scope.row)"
                      :disabled="scope.row.parseStatusCode === 1 || scope.row.parseStatusCode === 3 || scope.row.parseStatusCode === 4" v-if="!scope.row.sliceCount || scope.row.sliceCount <= 0">
                      {{ $t('knowledgeFileUpload.parse') }}
                    </el-button>
                    <el-button size="mini" type="text" @click="handleViewSlices(scope.row)"
                      v-if="scope.row.sliceCount && scope.row.sliceCount > 0">
                      {{ $t('knowledgeFileUpload.viewSlices') }}
                    </el-button>
                    <el-button size="mini" type="text" @click="handleDelete(scope.row)">
                      {{ $t('knowledgeFileUpload.delete') }}
                    </el-button>
                  </template>
                </el-table-column>
              </el-table>
            </div>

            <div class="table_bottom">
              <div class="ctrl_btn">
                <el-button type="primary" @click="handleSelectAll">
                  {{ isAllSelected ? $t('knowledgeFileUpload.deselectAll') : $t('knowledgeFileUpload.selectAll') }}
                </el-button>
                <el-button type="success" @click="showUploadDialog">
                  {{ $t('knowledgeFileUpload.add') }}
                </el-button>
                <el-button type="danger" @click="handleBatchDelete" :disabled="selectedFiles.length === 0">
                  {{ $t('knowledgeFileUpload.batchDelete') }}
                </el-button>
                <el-button type="primary" icon="el-icon-search" @click="showRetrievalTestDialog"
                  style="background-color: #409EFF; border-color: #409EFF;">
                  {{ $t('knowledgeFileUpload.retrievalTest') }}
                </el-button>
              </div>
              <div class="custom-pagination">
                <el-select v-model="pageSize" @change="handlePageSizeChange" class="page-size-select">
                  <el-option v-for="item in pageSizeOptions" :key="item"
                    :label="`${item}${$t('knowledgeFileUpload.itemsPerPage')}`" :value="item">
                  </el-option>
                </el-select>
                <button class="pagination-btn" :disabled="currentPage === 1" @click="goFirst">
                  {{ $t('knowledgeFileUpload.firstPage') }}
                </button>
                <button class="pagination-btn" :disabled="currentPage === 1" @click="goPrev">
                  {{ $t('knowledgeFileUpload.prevPage') }}
                </button>
                <button v-for="page in visiblePages" :key="page" class="pagination-btn"
                  :class="{ active: page === currentPage }" @click="goToPage(page)">
                  {{ page }}
                </button>
                <button class="pagination-btn" :disabled="currentPage === pageCount" @click="goNext">
                  {{ $t('knowledgeFileUpload.nextPage') }}
                </button>
                <span class="total-text">{{ $t('knowledgeFileUpload.totalRecords', { total }) }}</span>
              </div>
            </div>
          </el-card>
        </div>
      </div>
    </div>

    <!-- UploadDocumentDialog -->
    <el-dialog :title="$t('knowledgeFileUpload.uploadDocument')" :visible.sync="uploadDialogVisible" width="800px">
      <el-upload class="document-uploader" drag :action="uploadUrl" :auto-upload="false" :on-change="handleFileChange"
        :multiple="true" :show-file-list="false" accept=".doc,.docx,.pdf,.txt,.md,.mdx,.csv,.xls,.xlsx,.ppt,.pptx">
        <i class="el-icon-upload"></i>
        <div class="el-upload__text">{{ $t('knowledgeFileUpload.dragOrClick') }}</div>
        <div class="el-upload__tip">{{ $t('knowledgeFileUpload.uploadTip') }}</div>
      </el-upload>

      <!-- Selected file list -->
      <div class="selected-files-section" v-if="selectedFilesList.length > 0">
        <h4>{{ $t('knowledgeFileUpload.selectedFiles') }} ({{ selectedFilesList.length }})</h4>
        <div class="selected-files-list">
          <div v-for="(file, index) in selectedFilesList" :key="index" class="selected-file-item">
            <div class="file-info">
              <i class="el-icon-document"></i>
              <span class="file-name">{{ file.name }}</span>
              <span class="file-size">{{ formatFileSize(file.size) }}</span>
            </div>
            <el-button type="text" class="remove-btn" @click="removeSelectedFile(index)">
              <i class="el-icon-close"></i>
            </el-button>
          </div>
        </div>
      </div>

      <div slot="footer" class="dialog-footer">
        <el-button @click="uploadDialogVisible = false">{{ $t('knowledgeFileUpload.cancel') }}</el-button>
        <el-button type="primary" @click="handleBatchUploadSubmit" :loading="uploading"
          :disabled="selectedFilesList.length === 0">
          {{ $t('knowledgeFileUpload.confirm') }} {{ selectedFilesList.length > 0 ?
            `(${selectedFilesList.length}${$t('knowledgeFileUpload.itemsPerPage').replace('items/page', 'files')})` : '' }}
        </el-button>
      </div>
    </el-dialog>

    <!-- Slice management dialog -->
    <el-dialog :title="`${$t('knowledgeFileUpload.viewSlices')} - ${currentDocumentName}`"
      :visible.sync="sliceDialogVisible" width="1200px" class="slice-dialog">
      <div class="slice-management">
        <!-- Chunk list -->
        <div class="slice-list-section">
          <!-- Slice content card layout -->
          <div v-loading="sliceLoading" class="slice-content-container">
            <div v-if="sliceList.length > 0" class="slice-cards-container">
              <div v-for="(slice, index) in sliceList" :key="index" class="slice-card">
                <div class="slice-header-info">
                  <p><strong>{{ $t('knowledgeFileUpload.slice') }} {{ (sliceCurrentPage - 1) * slicePageSize + index + 1
                      }}</strong></p>
                </div>
                <div class="slice-card-content">
                  <div class="content-text">{{ slice.content }}</div>
                </div>
              </div>
            </div>
            <div v-else class="no-slice-data">
              <el-alert type="info" :title="$t('knowledgeFileUpload.noSliceData')"></el-alert>
            </div>
          </div>

          <!-- SlicePagination -->
          <div class="slice-pagination" style="margin-top: 20px; text-align: right;">
            <div class="custom-pagination">
              <!-- items/pageSelector -->
              <el-select v-model="slicePageSize" @change="handleSliceSizeChange" class="page-size-select"
                :popper-append-to-body="false">
                <el-option v-for="item in pageSizeOptions" :key="item"
                  :label="`${item}${$t('knowledgeFileUpload.itemsPerPage')}`" :value="item">
                </el-option>
              </el-select>

              <!-- Home button -->
              <button class="pagination-btn" :disabled="sliceCurrentPage === 1" @click="goToSliceFirstPage">
                {{ $t('knowledgeFileUpload.firstPage') }}
              </button>

              <!-- Previous page button -->
              <button class="pagination-btn" :disabled="sliceCurrentPage === 1" @click="goToSlicePrevPage">
                {{ $t('knowledgeFileUpload.prevPage') }}
              </button>

              <!-- Page numberButton -->
              <button v-for="page in sliceVisiblePages" :key="page" class="pagination-btn"
                :class="{ active: page === sliceCurrentPage }" @click="goToSlicePage(page)">
                {{ page }}
              </button>

              <!-- Next page button -->
              <button class="pagination-btn" :disabled="sliceCurrentPage === slicePageCount" @click="goToSliceNextPage">
                {{ $t('knowledgeFileUpload.nextPage') }}
              </button>

              <!-- Total records -->
              <span class="total-text">{{ $t('knowledgeFileUpload.totalRecords', { total: sliceTotal }) }}</span>
            </div>
          </div>
        </div>
      </div>
    </el-dialog>

    <!-- Recall testPopup -->
    <el-dialog :title="$t('knowledgeFileUpload.retrievalTest')" :visible.sync="retrievalTestDialogVisible"
      width="1200px" class="retrieval-test-dialog">
      <div class="retrieval-test-form">
        <el-form :model="retrievalTestForm" label-width="80px" @submit.native.prevent="runRetrievalTest">
          <el-form-item :label="$t('knowledgeFileUpload.testQuestion')" required>
            <el-input v-model="retrievalTestForm.question"
              :placeholder="$t('knowledgeFileUpload.testQuestionPlaceholder')" style="width: 100%; max-height: 80px;"
              @keyup.enter.native="runRetrievalTest">
            </el-input>
          </el-form-item>
        </el-form>

        <div class="retrieval-test-actions" style="text-align: center;">
          <el-button type="primary" @click="runRetrievalTest" :loading="retrievalTestLoading">
            {{ $t('knowledgeFileUpload.runTest') }}
          </el-button>
          <el-button @click="retrievalTestDialogVisible = false">{{ $t('knowledgeFileUpload.cancel') }}</el-button>
        </div>

        <div v-if="retrievalTestResult" class="retrieval-test-result">
          <h4>{{ $t('knowledgeFileUpload.testResult') }}</h4>
          <el-card v-if="retrievalTestResult.chunks && retrievalTestResult.chunks.length > 0">
            <div class="result-chunk-container">
              <div v-for="(chunk, index) in retrievalTestResult.chunks" :key="index" class="result-chunk">
                <p><strong>{{ $t('knowledgeFileUpload.slice') }} {{ index + 1 }}</strong></p>
                <div class="similarity-scores">
                  <div class="score-item">
                    <span class="score-label">{{ $t('knowledgeFileUpload.comprehensiveSimilarity') }}</span>
                    <span class="score-value">{{ (chunk.similarity || 0).toFixed(4) }}</span>
                  </div>
                </div>
                <div class="chunk-content">
                  <p><strong>{{ $t('knowledgeFileUpload.content') }}</strong></p>
                  <p>{{ chunk.content }}</p>
                </div>
                <el-divider v-if="index < retrievalTestResult.chunks.length - 1"></el-divider>
              </div>
            </div>
          </el-card>
          <el-alert v-else type="info" :title="$t('knowledgeFileUpload.noRelatedSlices')"></el-alert>
        </div>
      </div>
    </el-dialog>

    <el-footer>
      <version-footer />
    </el-footer>
  </div>
</template>

<script>
import Api from "@/apis/api";
import KnowledgeBaseAPI from "@/apis/module/knowledgeBase";
import HeaderBar from "@/components/HeaderBar.vue";
import VersionFooter from "@/components/VersionFooter.vue";

export default {
  components: { HeaderBar, VersionFooter },
  data() {
    return {
      datasetId: '',
      knowledgeBaseName: '',
      searchName: "",
      fileList: [],
      selectedFiles: [],
      currentPage: 1,
      loading: false,
      pageSize: 10,
      pageSizeOptions: [10, 20, 50, 100],
      total: 0,
      uploadDialogVisible: false,
      uploading: false,
      uploadForm: {
        file: null
      },
      uploadUrl: '',
      isAllSelected: false,
      selectedFilesList: [], // Batch upload selected file list

      // Slice management related data
      sliceDialogVisible: false,
      currentDocumentId: '',
      currentDocumentName: '',
      sliceList: [],
      sliceLoading: false,
      sliceCurrentPage: 1,
      slicePageSize: 10,
      sliceTotal: 0,

      // Recall testRelated data
      retrievalTestDialogVisible: false,
      retrievalTestForm: {
        question: ''
      },
      retrievalTestResult: null,
      retrievalTestLoading: false,
      
      // StatusPoll related data
      statusPollingTimer: null,
      statusPollingInterval: 5000, // 5Poll once per second
      maxStatusPollingTime: 300000, // Max polling time5Minute
      statusPollingStartTime: null
    };
  },
  created() {
    this.datasetId = this.$route.query.datasetId || '';
    this.knowledgeBaseName = this.$route.query.knowledgeBaseName || '';
    this.uploadUrl = `${Api.getServiceUrl()}/api/v1/documents/upload`;
    this.fetchFileList();
  },
  
  beforeDestroy() {
    this.stopStatusPolling();
  },
  computed: {
    pageCount() {
      return Math.ceil(this.total / this.pageSize);
    },
    visiblePages() {
      const pages = [];
      const maxVisible = 3;
      let start = Math.max(1, this.currentPage - 1);
      let end = Math.min(this.pageCount, start + maxVisible - 1);

      if (end - start + 1 < maxVisible) {
        start = Math.max(1, end - maxVisible + 1);
      }

      for (let i = start; i <= end; i++) {
        pages.push(i);
      }
      return pages;
    },
    // SlicePaginationPagesCalculate
    slicePageCount() {
      return Math.ceil(this.sliceTotal / this.slicePageSize);
    },
    // SlicePaginationVisiblePage numberCalculate (max display3countPage number)
    sliceVisiblePages() {
      const pages = [];
      const maxVisible = 3;
      let start = Math.max(1, this.sliceCurrentPage - 1);
      let end = Math.min(this.slicePageCount, start + maxVisible - 1);

      if (end - start + 1 < maxVisible) {
        start = Math.max(1, end - maxVisible + 1);
      }

      for (let i = start; i <= end; i++) {
        pages.push(i);
      }
      return pages;
    },
  },
  methods: {
    goBack() {
      this.$router.push('/knowledge-base-management');
    },
    handlePageSizeChange: function (val) {
      this.pageSize = val;
      this.currentPage = 1;
      this.fetchFileList();
    },
    fetchFileList: function () {
      this.loading = true;
      const params = {
        page: this.currentPage,
        page_size: this.pageSize,
        name: this.searchName
      };

      KnowledgeBaseAPI.getDocumentList(this.datasetId, params,
        async ({ data }) => {
          this.loading = false;
          if (data && data.code === 0) {
            this.fileList = data.data.list;
            this.total = data.data.total;

            // For eachDocumentGet chunkQuantity
            await this.fetchSliceCountsForDocuments();
            
            // Automatically for processingDocumentStartStatusDetect
            this.startStatusPolling();
          } else {
            this.$message.error(data?.msg || this.$t('knowledgeFileUpload.getListFailed'));
            this.fileList = [];
            this.total = 0;
          }
        },
        (err) => {
          this.loading = false;
          console.log('Error callback received:', err);
          if (err && err.data) {
            console.log('Backend returned error message:', err.data.msg || err.msg);
            this.$message.error(err.data.msg || err.msg || this.$t('knowledgeFileUpload.getListFailed'));
          } else {
            this.$message.error(this.$t('knowledgeFileUpload.getListFailed'));
          }
          console.error('Failed to get document list:', err);
          this.fileList = [];
          this.total = 0;
        }
      );
    },
    
    // Start document status polling
    startStatusPolling: function () {
      // Check whether polling already in progress
      if (this.statusPollingTimer) {
        console.log('Status polling already running');
        return;
      }
      
      // Check if processing existsDocument
      const hasProcessingDocuments = this.fileList.some(document => 
        document.parseStatusCode === 1
      );
      
      if (!hasProcessingDocuments) {
        console.log('No documents processing, status polling not started');
        return;
      }
      
      console.log('Start document status polling');
      this.statusPollingStartTime = Date.now();
      
      // Execute once immediatelyStatusCheck
      this.pollDocumentStatus();
      
      // Start polling
      this.statusPollingTimer = setInterval(() => {
        this.pollDocumentStatus();
      }, this.statusPollingInterval);
    },
    
    // Stop document status polling
    stopStatusPolling: function () {
      if (this.statusPollingTimer) {
        clearInterval(this.statusPollingTimer);
        this.statusPollingTimer = null;
        console.log('Stop document status polling');
      }
    },
    
    // PollDocumentStatus
    pollDocumentStatus: async function () {
      // Check whether max polling time exceeded
      if (Date.now() - this.statusPollingStartTime > this.maxStatusPollingTime) {
        console.log('Max polling time reached, stop status polling');
        this.stopStatusPolling();
        return;
      }
      
      try {
        const params = {
          page: this.currentPage,
          page_size: this.pageSize,
          name: this.searchName
        };
        
        const response = await new Promise((resolve, reject) => {
          KnowledgeBaseAPI.getDocumentList(this.datasetId, params,
            ({ data }) => resolve(data),
            (err) => reject(err)
          );
        });
        
        if (response && response.code === 0) {
          const updatedFileList = response.data.list;
          
          // UpdateDocumentStatus
          this.updateDocumentStatuses(updatedFileList);
          
          // Check if any still processingDocument
          const hasProcessingDocuments = updatedFileList.some(document => 
            document.parseStatusCode === 1
          );
          
          if (!hasProcessingDocuments) {
            console.log('All documents processed, stop status polling');
            this.stopStatusPolling();
          }
        }
      } catch (error) {
        console.warn('Failed to poll document status:', error);
      }
    },
    
    // UpdateDocumentStatus
    updateDocumentStatuses: function (updatedFileList) {
      let hasChanges = false;
      
      updatedFileList.forEach(updatedDoc => {
        const existingDoc = this.fileList.find(doc => doc.id === updatedDoc.id);
        if (existingDoc && existingDoc.parseStatusCode !== updatedDoc.parseStatusCode) {
          // StatusChanged, updateDocument
          Object.assign(existingDoc, updatedDoc);
          hasChanges = true;
          console.log(`Document ${existingDoc.name} StatusUpdated: ${existingDoc.parseStatusCode} -> ${updatedDoc.parseStatusCode}`);
          
          // IfStatusBecomes complete, start slicingQuantityDetect
          if (updatedDoc.parseStatusCode === 3) {
            this.fetchSliceCountForSingleDocument(updatedDoc.id);
          }
        }
      });
      
      if (hasChanges) {
        this.$forceUpdate();
      }
    },

    // forDocumentEach in listDocumentGet chunkQuantity
    fetchSliceCountsForDocuments: async function () {
      if (!this.fileList || this.fileList.length === 0) {
        return;
      }

      // For eachDocumentGet chunkQuantity
      for (const document of this.fileList) {
        this.fetchSliceCountForSingleDocument(document.id);
      }
    },

    // Get singleDocumentSlice ofQuantity
    fetchSliceCountForSingleDocument: function (documentId) {
      const document = this.fileList.find(doc => doc.id === documentId);
      if (!document) {
        console.warn('Document not found:', documentId);
        return;
      }

      const params = {
        page: 1,
        page_size: 1  // Only need get total, so per page1records enough
      };

      KnowledgeBaseAPI.listChunks(this.datasetId, documentId, params,
        ({ data }) => {
          if (data && data.code === 0) {
            const sliceCount = data.data.total || 0;
            // UpdateDocumentSlice ofQuantity
            this.$set(document, 'sliceCount', sliceCount);
            // Force update view
            this.$forceUpdate();
            console.log(`Document ${document.name} chunk count updated to:`, sliceCount);
          } else {
            console.warn(`Failed to get chunk count for document ${document.name}:`, data?.msg);
          }
        },
        (err) => {
          console.warn(`Failed to get chunk count for document ${document.name}:`, err);
        }
      );
    },

    // Smart detect slice generationStatusand auto refresh
    smartRefreshSliceCount: function (documentId) {
      const document = this.fileList.find(doc => doc.id === documentId);
      if (!document) {
        console.warn('Document not found:', documentId);
        return;
      }

      // Delay2Get slice after secondsQuantity, give backend more processing time
      setTimeout(() => {
        this.fetchSliceCountForSingleDocument(documentId);
      }, 2000);
    },
    handleSearch: function () {
      this.currentPage = 1;
      this.fetchFileList();
    },
    headerCellClassName: function ({ row, column, rowIndex, columnIndex }) {
      if (columnIndex === 0) {
        return 'header-cell-first';
      }
      return 'header-cell';
    },
    showUploadDialog: function () {
      this.uploadForm = {
        name: '',
        file: null
      };
      this.selectedFilesList = []; // Clear selected file list
      this.uploadDialogVisible = true;
    },
    handleFileChange: function (file, fileList) {
      if (!file || !file.raw) return;

      // Validation before file upload
      const isLt10M = file.size / 1024 / 1024 < 10;
      if (!isLt10M) {
        this.$message.error('File size cannot exceed 10MB!');
        return;
      }

      // Add to selected file list
      this.selectedFilesList.push({
        name: file.name,
        size: file.size,
        raw: file.raw
      });
    },
    beforeUpload: function (file) {
      // Validation before file upload
      const isLt10M = file.size / 1024 / 1024 < 10;
      if (!isLt10M) {
        this.$message.error('File size cannot exceed 10MB!');
        return false;
      }
      // SaveFile touploadForm
      this.uploadForm.file = file;
      return false; // Prevent auto upload, use custom upload logic
    },
    // Remove selected files
    removeSelectedFile: function (index) {
      this.selectedFilesList.splice(index, 1);
    },

    // FormatFile size
    formatFileSize: function (bytes) {
      if (bytes === 0) return '0 B';
      const k = 1024;
      const sizes = ['B', 'KB', 'MB', 'GB'];
      const i = Math.floor(Math.log(bytes) / Math.log(k));
      return parseFloat((bytes / Math.pow(k, i)).toFixed(2)) + ' ' + sizes[i];
    },

    // Batch upload submit
    handleBatchUploadSubmit: function () {
      if (this.selectedFilesList.length === 0) {
        this.$message.error('Please select file to upload');
        return;
      }

      this.uploading = true;

      // Create upload task array
      const uploadPromises = this.selectedFilesList.map(file => {
        return new Promise((resolve, reject) => {
          const formData = new FormData();
          formData.append('file', file.raw);

          KnowledgeBaseAPI.uploadDocument(this.datasetId, formData,
            ({ data }) => {
              if (data && data.code === 0) {
                resolve({ success: true, fileName: file.name });
              } else {
                reject({ success: false, fileName: file.name, error: data?.msg || this.$t('knowledgeFileUpload.uploadFailed') });
              }
            },
            (err) => {
              // ErrorCallback handles backend-returnedError info
              if (err && err.data) {
                reject({ success: false, fileName: file.name, error: err.data.msg || err.msg || this.$t('knowledgeFileUpload.uploadFailed') });
              } else {
                reject({ success: false, fileName: file.name, error: this.$t('knowledgeFileUpload.uploadFailed') });
              }
              console.error('Upload document failed:', err);
            }
          );
        });
      });

      // Execute all upload tasks
      Promise.all(uploadPromises.map(p => p.catch(e => e)))
        .then(results => {
          this.uploading = false;

          const successCount = results.filter(r => r.success).length;
          const failedCount = results.filter(r => !r.success).length;

          if (successCount > 0) {
            this.$message.success(`Successfully uploaded ${successCount} files`);
          }

          if (failedCount > 0) {
            const failedFiles = results.filter(r => !r.success).map(r => r.fileName);
            this.$message.error(`Upload failed ${failedCount} files: ${failedFiles.join(', ')}`);
          }

          if (successCount > 0) {
            this.uploadDialogVisible = false;
            this.fetchFileList();
          }
        })
        .catch(error => {
          this.uploading = false;
          this.$message.error('Batch upload failed');
          console.error('Batch upload failed:', error);
        });
    },

    // Single file upload (keep original function)
    handleUploadSubmit: function () {
      if (!this.uploadForm.file) {
        this.$message.error(this.$t('knowledgeFileUpload.fileRequired'));
        return;
      }

      this.uploading = true;

      const formData = new FormData();
      formData.append('file', this.uploadForm.file);

      KnowledgeBaseAPI.uploadDocument(this.datasetId, formData,
        ({ data }) => {
          this.uploading = false;
          if (data && data.code === 0) {
            this.$message.success(this.$t('knowledgeFileUpload.uploadSuccess'));
            this.uploadDialogVisible = false;
            this.fetchFileList();
          } else {
            this.$message.error(data?.msg || this.$t('knowledgeFileUpload.uploadFailed'));
          }
        },
        (err) => {
          this.uploading = false;
          // ErrorCallback handles backend-returnedError info
          if (err && err.data) {
            this.$message.error(err.data.msg || err.msg || this.$t('knowledgeFileUpload.uploadFailed'));
          } else {
            this.$message.error(this.$t('knowledgeFileUpload.uploadFailed'));
          }
          console.error('Upload document failed:', err);
        }
      );
    },
    handleParse: function (row) {
      this.$confirm(this.$t('knowledgeFileUpload.confirmParse'), this.$t('warning'), {
        confirmButtonText: this.$t('knowledgeFileUpload.confirm'),
        cancelButtonText: this.$t('knowledgeFileUpload.cancel'),
        type: 'warning'
      }).then(() => {
        KnowledgeBaseAPI.parseDocument(this.datasetId, row.id,
          ({ data }) => {
            if (data && data.code === 0) {
              this.$message.success('Request submitted, parsing');
              
              // Update nowDocumentStatusAs processing
              const document = this.fileList.find(doc => doc.id === row.id);
              if (document) {
                document.parseStatusCode = 1; // ProcessingStatus
                this.$forceUpdate();
              }
              
              // StartStatusPoll
              this.startStatusPolling();
              
              // Use smart detection to auto refresh slicesQuantity
              this.smartRefreshSliceCount(row.id);
            } else {
              this.$message.error(data?.msg || this.$t('knowledgeFileUpload.parseFailed'));
            }
          },
          (err) => {
            // ErrorCallback handles backend-returnedError info
            if (err && err.data) {
              this.$message.error(err.data.msg || err.msg || this.$t('knowledgeFileUpload.parseFailed'));
            } else {
              this.$message.error(this.$t('knowledgeFileUpload.parseFailed'));
            }
            console.error('Parse document failed:', err);
          }
        );
      }).catch(() => {
        this.$message.info(this.$t('knowledgeFileUpload.parseCancelled'));
      });
    },
    handleViewSlices: function (row) {
      // View chunk
      this.currentDocumentId = row.id;
      this.currentDocumentName = row.name;
      this.sliceDialogVisible = true;
      this.sliceCurrentPage = 1;
      this.sliceSearchKeyword = '';
      this.fetchSlices();
    },
    handleDelete: function (row) {
      this.$confirm(this.$t('knowledgeFileUpload.confirmDelete'), this.$t('warning'), {
        confirmButtonText: this.$t('knowledgeFileUpload.confirm'),
        cancelButtonText: this.$t('knowledgeFileUpload.cancel'),
        type: 'warning'
      }).then(() => {
        KnowledgeBaseAPI.deleteDocument(this.datasetId, row.id,
          ({ data }) => {
            if (data && data.code === 0) {
              this.$message.success(this.$t('knowledgeFileUpload.deleteSuccess'));
              this.fetchFileList();
            } else {
              this.$message.error(data?.msg || this.$t('knowledgeFileUpload.deleteFailed'));
            }
          },
          (err) => {
            // ErrorCallback handles backend-returnedError info
            if (err && err.data) {
              this.$message.error(err.data.msg || err.msg || this.$t('knowledgeFileUpload.deleteFailed'));
            } else {
              this.$message.error(this.$t('knowledgeFileUpload.deleteFailed'));
            }
            console.error('Delete document failed:', err);
          }
        );
      }).catch(() => {
        this.$message.info(this.$t('knowledgeFileUpload.deleteCancelled'));
      });
    },
    handleSelectionChange: function (selection) {
      this.selectedFiles = selection;
    },
    handleSelectAll: function () {
      if (this.isAllSelected) {
        this.$refs.fileTable.clearSelection();
        this.isAllSelected = false;
      } else {
        this.$refs.fileTable.clearSelection();
        this.fileList.forEach(row => {
          this.$refs.fileTable.toggleRowSelection(row, true);
        });
        this.isAllSelected = true;
      }
    },
    handleBatchDelete: function () {
      if (this.selectedFiles.length === 0) {
        this.$message.warning(this.$t('knowledgeFileUpload.selectFilesFirst'));
        return;
      }

      this.$confirm(this.$t('knowledgeFileUpload.confirmBatchDelete', { count: this.selectedFiles.length }), this.$t('warning'), {
        confirmButtonText: this.$t('knowledgeFileUpload.confirm'),
        cancelButtonText: this.$t('knowledgeFileUpload.cancel'),
        type: 'warning'
      }).then(() => {
        const deletePromises = this.selectedFiles.map(file => {
          return new Promise((resolve, reject) => {
            KnowledgeBaseAPI.deleteDocument(this.datasetId, file.id,
              ({ data }) => {
                if (data && data.code === 0) {
                  resolve();
                } else {
                  reject(data?.msg || this.$t('knowledgeFileUpload.deleteFailed'));
                }
              },
              (err) => {
                // ErrorCallback handles backend-returnedError info
                if (err && err.data) {
                  reject(err.data.msg || err.msg || this.$t('knowledgeFileUpload.deleteFailed'));
                } else {
                  reject(this.$t('knowledgeFileUpload.deleteFailed'));
                }
                console.error('Delete document failed:', err);
              }
            );
          });
        });

        Promise.all(deletePromises)
          .then(() => {
            this.$message.success(this.$t('knowledgeFileUpload.batchDeleteSuccess', { count: this.selectedFiles.length }));
            this.selectedFiles = [];
            this.fetchFileList();
          })
          .catch((error) => {
            this.$message.error(error || this.$t('knowledgeFileUpload.batchDeleteFailed'));
          });
      }).catch(() => {
        this.$message.info(this.$t('knowledgeFileUpload.deleteCancelled'));
      });
    },
    getParseStatusType: function (parseStatusCode) {
      switch (parseStatusCode) {
        case 0:
          return 'info'; // Gray - Not started
        case 1:
          return 'primary'; // Blue - Processing
        case 2:
          return 'warning'; // Yellow - Canceled
        case 3:
          return 'success'; // Green - Complete
        case 4:
          return 'danger'; // Red - Fail
        default:
          return 'info'; // Default gray
      }
    },
    getParseStatusText: function (parseStatusCode) {
      switch (parseStatusCode) {
        case 0:
          return this.$t('knowledgeFileUpload.statusNotStarted');
        case 1:
          return this.$t('knowledgeFileUpload.statusProcessing');
        case 2:
          return this.$t('knowledgeFileUpload.statusCancelled');
        case 3:
          return this.$t('knowledgeFileUpload.statusCompleted');
        case 4:
          return this.$t('knowledgeFileUpload.statusFailed');
        default:
          return this.$t('knowledgeFileUpload.statusNotStarted');
      }
    },
    goToPage: function (page) {
      if (page !== this.currentPage) {
        this.currentPage = page;
        this.fetchFileList();
      }
    },
    goFirst: function () {
      if (this.currentPage !== 1) {
        this.currentPage = 1;
        this.fetchFileList();
      }
    },
    goPrev: function () {
      if (this.currentPage > 1) {
        this.currentPage--;
        this.fetchFileList();
      }
    },
    goNext: function () {
      if (this.currentPage < this.pageCount) {
        this.currentPage++;
        this.fetchFileList();
      }
    },
    formatDate: function (dateString) {
      if (!dateString) return '';
      const date = new Date(dateString);
      return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, '0')}-${String(date.getDate()).padStart(2, '0')} ${String(date.getHours()).padStart(2, '0')}:${String(date.getMinutes()).padStart(2, '0')}`;
    },

    // Slice management related methods
    fetchSlices: function () {
      this.sliceLoading = true;

      const params = {
        page: this.sliceCurrentPage,
        page_size: this.slicePageSize
      };

      if (this.sliceSearchKeyword) {
        params.keywords = this.sliceSearchKeyword;
      }

      KnowledgeBaseAPI.listChunks(this.datasetId, this.currentDocumentId, params,
        ({ data }) => {
          this.sliceLoading = false;
          if (data && data.code === 0) {
            // Parse chunkList data
            this.parseSliceData(data.data);
          } else {
            this.$message.error(data?.msg || 'Get slice list failed');
            this.sliceList = [];
            this.sliceTotal = 0;
          }
        },
        (err) => {
          this.sliceLoading = false;
          // ErrorCallback handles backend-returnedError info
          if (err && err.data) {
            this.$message.error(err.data.msg || err.msg || 'Get slice list failed');
          } else {
            this.$message.error('Get slice list failed');
          }
          console.error('Failed to get chunk list:', err);
          this.sliceList = [];
          this.sliceTotal = 0;
        }
      );
    },

    parseSliceData: function (data) {
      try {
        if (data && data.list) {
          // Format backend already parsed
          this.sliceList = data.list;
          this.sliceTotal = data.total || data.list.length;
        } else if (data && data.chunks && Array.isArray(data.chunks)) {
          // RAGFlow APIOriginal format
          this.sliceList = data.chunks;
          this.sliceTotal = data.total || data.chunks.length;
        } else if (data && Array.isArray(data)) {
          this.sliceList = data;
          this.sliceTotal = data.length;
        } else {
          this.sliceList = [];
          this.sliceTotal = 0;
        }

        console.log('Parsed chunk data:', {
          list: this.sliceList,
          total: this.sliceTotal
        });
      } catch (error) {
        console.error('Failed to parse chunk data:', error);
        this.sliceList = [];
        this.sliceTotal = 0;
      }
    },

    handleSliceSizeChange: function (pageSize) {
      this.slicePageSize = pageSize;
      this.sliceCurrentPage = 1;
      this.fetchSlices();
    },

    handleSlicePageChange: function (page) {
      this.sliceCurrentPage = page;
      this.fetchSlices();
    },

    // Go to first page of slice management
    goToSliceFirstPage: function () {
      if (this.sliceCurrentPage !== 1) {
        this.sliceCurrentPage = 1;
        this.fetchSlices();
      }
    },

    // Chunk management previous page
    goToSlicePrevPage: function () {
      if (this.sliceCurrentPage > 1) {
        this.sliceCurrentPage--;
        this.fetchSlices();
      }
    },

    // Slice management go to specified page
    goToSlicePage: function (page) {
      if (page !== this.sliceCurrentPage) {
        this.sliceCurrentPage = page;
        this.fetchSlices();
      }
    },

    // Chunk management next page
    goToSliceNextPage: function () {
      if (this.sliceCurrentPage < this.slicePageCount) {
        this.sliceCurrentPage++;
        this.fetchSlices();
      }
    },

    // Recall testRelated methods
    showRetrievalTestDialog: function () {
      // InitializeRecall testForm
      this.retrievalTestForm = {
        question: ''
      };
      this.retrievalTestResult = null;
      this.retrievalTestDialogVisible = true;
    },

    runRetrievalTest: function () {
      if (!this.retrievalTestForm.question.trim()) {
        this.$message.error(this.$t('knowledgeFileUpload.testQuestionRequired'));
        return;
      }

      this.retrievalTestLoading = true;
      this.retrievalTestResult = null;

      // Prepare request data
      const requestData = {
        question: this.retrievalTestForm.question.trim()
      };

      // CallRecall testAPI
      KnowledgeBaseAPI.retrievalTest(this.datasetId, requestData,
        ({ data }) => {
          this.retrievalTestLoading = false;
          if (data && data.code === 0) {
            this.retrievalTestResult = data.data || data;
            this.$message.success('Recall test complete');
          } else {
            this.$message.error(data?.msg || 'Recall test failed');
          }
        },
        (err) => {
          this.retrievalTestLoading = false;
          // ErrorCallback handles backend-returnedError info
          if (err && err.data) {
            this.$message.error(err.data.msg || err.msg || 'Recall test failed');
          } else {
            this.$message.error('Recall test failed');
          }
          console.error('Recall test failed:', err);
        }
      );
    },

    handleRetrievalTestDialogClose: function () {
      this.retrievalTestDialogVisible = false;
      this.retrievalTestResult = null;
    }
  }
};
</script>

<style lang="scss" scoped>
.welcome {
  min-width: 900px;
  min-height: 506px;
  height: 100vh;
  display: flex;
  position: relative;
  flex-direction: column;
  background-size: cover;
  background: linear-gradient(to bottom right, #dce8ff, #e4eeff, #e6cbfd) center;
  -webkit-background-size: cover;
  -o-background-size: cover;
  overflow: hidden;
}

.main-wrapper {
  // Top 63px Bottom 35px Query58px
  height: calc(100vh - 63px - 35px - 58px);
  margin: 0 22px;
  border-radius: 15px;
  box-shadow: 0 2px 12px rgba(0, 0, 0, 0.1);
  position: relative;
  background: rgba(237, 242, 255, 0.5);
  display: flex;
  flex-direction: column;
}

.operation-bar {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 16px 24px;
}

.left-operations {
  display: flex;
  align-items: center;
  gap: 16px;
}

.btn-back {
  font-size: 20px;
  font-weight: bold;
  color: #606266;
  padding: 8px 12px;
  border-radius: 4px;
  transition: all 0.3s ease;
}

.btn-back:hover {
  background-color: #f5f7fa;
  color: #409eff;
}

.knowledge-base-title {
  font-size: 24px;
  margin: 0;
  color: #303133;
  font-weight: 600;
}

.right-operations {
  display: flex;
  gap: 10px;
  margin-left: auto;
}

.search-input {
  width: 240px;
}

.btn-search {
  background: linear-gradient(135deg, #6b8cff, #a966ff);
  border: none;
  color: white;
}

.content-panel {
  flex: 1;
  display: flex;
  overflow: hidden;
  height: 100%;
  border-radius: 15px;
  background: transparent;
  border: 1px solid #fff;
}

.content-area {
  flex: 1;
  height: 100%;
  min-width: 600px;
  overflow: auto;
  background-color: white;
  display: flex;
  flex-direction: column;
}

.params-card {
  background: white;
  flex: 1;
  display: flex;
  flex-direction: column;
  border: none;
  box-shadow: none;
  overflow: hidden;

  ::v-deep .el-card__body {
    padding: 15px;
    display: flex;
    flex-direction: column;
    flex: 1;
    overflow: hidden;
  }
}

.table_bottom {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-top: auto;
  padding-bottom: 10px;
  width: 100%;
}

.ctrl_btn {
  display: flex;
  gap: 8px;
  padding-left: 26px;

  .el-button {
    min-width: 72px;
    height: 32px;
    padding: 7px 12px 7px 10px;
    font-size: 12px;
    border-radius: 4px;
    line-height: 1;
    font-weight: 500;
    border: none;
    transition: all 0.3s ease;
    box-shadow: 0 2px 6px rgba(0, 0, 0, 0.1);

    &:hover {
      transform: translateY(-1px);
      box-shadow: 0 4px 8px rgba(0, 0, 0, 0.15);
    }
  }

  .el-button--primary {
    background: #5f70f3;
    color: white;
  }

  .el-button--danger {
    background: #fd5b63;
    color: white;
  }
}

.custom-pagination {
  display: flex;
  align-items: center;
  gap: 5px;

  .el-select {
    margin-right: 8px;
  }

  .pagination-btn:first-child,
  .pagination-btn:nth-child(2),
  .pagination-btn:nth-last-child(2),
  .pagination-btn:nth-child(3) {
    min-width: 60px;
    height: 32px;
    padding: 0 12px;
    border-radius: 4px;
    border: 1px solid #e4e7ed;
    background: #dee7ff;
    color: #606266;
    font-size: 14px;
    cursor: pointer;
    transition: all 0.3s ease;

    &:hover {
      background: #d7dce6;
    }

    &:disabled {
      opacity: 0.6;
      cursor: not-allowed;
    }
  }

  .pagination-btn:not(:first-child):not(:nth-child(3)):not(:nth-child(2)):not(:nth-last-child(2)) {
    min-width: 28px;
    height: 32px;
    padding: 0;
    border-radius: 4px;
    border: 1px solid transparent;
    background: transparent;
    color: #606266;
    font-size: 14px;
    cursor: pointer;
    transition: all 0.3s ease;

    &:hover {
      background: rgba(245, 247, 250, 0.3);
    }
  }

  .pagination-btn.active {
    background: #5f70f3 !important;
    color: #ffffff !important;
    border-color: #5f70f3 !important;

    &:hover {
      background: #6d7cf5 !important;
    }
  }
}

.total-text {
  margin-left: 10px;
  color: #606266;
  font-size: 14px;
}

.page-size-select {
  width: 100px;
  margin-right: 10px;

  :deep(.el-input__inner) {
    height: 32px;
    line-height: 32px;
    border-radius: 4px;
    border: 1px solid #e4e7ed;
    background: #dee7ff;
    color: #606266;
    font-size: 14px;
  }

  :deep(.el-input__suffix) {
    right: 6px;
    width: 15px;
    height: 20px;
    display: flex;
    justify-content: center;
    align-items: center;
    top: 6px;
    border-radius: 4px;
  }

  :deep(.el-input__suffix-inner) {
    display: flex;
    align-items: center;
    justify-content: center;
    width: 100%;
  }

  :deep(.el-icon-arrow-up:before) {
    content: "";
    display: inline-block;
    border-left: 6px solid transparent;
    border-right: 6px solid transparent;
    border-top: 9px solid #606266;
    position: relative;
    transform: rotate(0deg);
    transition: transform 0.3s;
  }
}

:deep(.transparent-table) {
  background: white;
  flex: 1;
  width: 100%;
  display: flex;
  flex-direction: column;

  .el-table__body-wrapper {
    flex: 1;
    overflow-y: auto;
    max-height: none !important;
  }

  .el-table__header-wrapper {
    flex-shrink: 0;
  }

  .el-table__header th {
    background: white !important;
    color: black;
    font-weight: 600;
    height: 40px;
    padding: 8px 0;
    font-size: 14px;
    border-bottom: 1px solid #e4e7ed;
  }

  .el-table__body tr {
    background-color: white;

    td {
      border-top: 1px solid rgba(0, 0, 0, 0.04);
      border-bottom: 1px solid rgba(0, 0, 0, 0.04);
      padding: 8px 0;
      height: 40px;
      color: #606266;
      font-size: 14px;
    }
  }

  .el-table__row:hover>td {
    background-color: #f5f7fa !important;
  }

  &::before {
    display: none;
  }
}

:deep(.el-table .el-button--text) {
  color: #7079aa !important;
}

:deep(.el-table .el-button--text:hover) {
  color: #5a64b5 !important;
}

:deep(.el-checkbox__inner) {
  background-color: #ffffff !important;
  border-color: #cccccc !important;
}

:deep(.el-checkbox__inner:hover) {
  border-color: #cccccc !important;
}

:deep(.el-checkbox__input.is-checked .el-checkbox__inner) {
  background-color: #5f70f3 !important;
  border-color: #5f70f3 !important;
}

:deep(.el-loading-mask) {
  background-color: rgba(255, 255, 255, 0.6) !important;
  backdrop-filter: blur(2px);
}

:deep(.el-loading-spinner .path) {
  stroke: #6b8cff;
}

:deep(.el-table__empty-block) {
  display: flex;
  flex-direction: column;
  justify-content: center;
  align-items: center;
  min-height: 300px;
}

/* Drag upload area style */
.document-uploader {
  :deep(.el-upload-dragger) {
    width: 600px;
    height: 200px;
    min-height: 200px;
    border: 2px dashed #c0c4cc;
    border-radius: 16px;
    cursor: pointer;
    position: relative;
    overflow: hidden;
    transition: border-color 0.3s ease;

    &:hover {
      border-color: #409eff;
    }

    .el-icon-upload {
      font-size: 48px;
      color: #c0c4cc;
      margin: 20px 0 16px;
      line-height: 1;
    }

    .el-upload__text {
      font-size: 16px;
      color: #606266;
      margin-bottom: 8px;
      line-height: 1.5;
    }

    .el-upload__tip {
      font-size: 14px;
      color: #909399;
      line-height: 1.5;
    }
  }
}

/* Recall testDialog style */
.retrieval-test-dialog {
  ::v-deep .el-dialog__wrapper {
    display: block !important;
  }

  ::v-deep .el-dialog {
    position: absolute !important;
    top: 50% !important;
    left: 50% !important;
    transform: translate(-50%, -50%) !important;
    margin: 0 !important;
    width: 1200px !important;
    height: 90vh !important;
    max-height: 90vh !important;
    min-height: 90vh !important;
  }

  ::v-deep .el-dialog__body {
    height: calc(100% - 90px) !important;
    max-height: calc(100% - 90px) !important;
    overflow: hidden;
    padding: 15px 25px;
    display: flex;
    flex-direction: column;
  }
}

.retrieval-test-form {
  flex: 1;
  display: flex;
  flex-direction: column;
  overflow: hidden;
}

.retrieval-test-result {
  margin-top: 20px;
  flex: 1;
  display: flex;
  flex-direction: column;
  overflow: hidden;

  h4 {
    margin: 0 0 12px 0;
    font-size: 16px;
    font-weight: 600;
    color: #303133;
    flex-shrink: 0;
  }

  :deep(.el-card) {
    flex: 1;
    display: flex;
    flex-direction: column;
    overflow: hidden;

    .el-card__body {
      flex: 1;
      display: flex;
      flex-direction: column;
      overflow: hidden;
      padding: 0;
    }
  }
}

.result-chunk-container {
  flex: 1;
  overflow-y: auto;
  max-height: 100%;
  padding: 16px;

  /* Scrollbar style */
  &::-webkit-scrollbar {
    width: 6px;
    height: 6px;
  }

  &::-webkit-scrollbar-track {
    background: #fafafa;
    border-radius: 3px;
  }

  &::-webkit-scrollbar-thumb {
    background: #e0e0e0;
    border-radius: 3px;
    border: 1px solid #f0f0f0;
  }

  &::-webkit-scrollbar-thumb:hover {
    background: #d0d0d0;
  }
}

.result-chunk {
  background-color: #f8f9fa;
  border: 1px solid #e4e7ed;
  border-radius: 8px;
  padding: 16px;
  margin-bottom: 16px;

  &:last-child {
    margin-bottom: 0;
  }

  p {
    margin: 0 0 8px 0;
    font-size: 14px;
    line-height: 1.5;

    &:last-child {
      margin-bottom: 0;
    }

    strong {
      color: #303133;
      font-weight: 600;
    }
  }

  .similarity-scores {
    display: flex;
    gap: 16px;
    margin-bottom: 12px;

    .score-item {
      display: flex;
      flex-direction: column;

      .score-label {
        font-size: 12px;
        color: #909399;
        margin-bottom: 4px;
      }

      .score-value {
        font-size: 14px;
        font-weight: 600;
        color: #409eff;
      }
    }
  }

  .chunk-content {
    background-color: white;
    border: 1px solid #f0f0f0;
    border-radius: 4px;
    padding: 12px;
    margin-top: 8px;
    max-height: 120px;
    overflow-y: auto;

    /* ContentScrollbar style */
    &::-webkit-scrollbar {
      width: 4px;
    }

    &::-webkit-scrollbar-track {
      background: #fafafa;
    }

    &::-webkit-scrollbar-thumb {
      background: #e0e0e0;
      border-radius: 2px;
    }

    &::-webkit-scrollbar-thumb:hover {
      background: #d0d0d0;
    }
  }

  :deep(.el-divider) {
    margin: 12px 0;
  }
}

/* Selected file list style */
.selected-files-section {
  margin-top: 20px;
  border: 1px solid #e4e7ed;
  border-radius: 8px;
  padding: 16px;
  background-color: #f8f9fa;

  h4 {
    margin: 0 0 12px 0;
    font-size: 14px;
    font-weight: 600;
    color: #606266;
  }
}

.selected-files-list {
  max-height: 200px;
  overflow-y: auto;
}

.selected-file-item {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 8px 12px;
  background-color: white;
  border: 1px solid #e4e7ed;
  border-radius: 4px;
  margin-bottom: 8px;

  &:last-child {
    margin-bottom: 0;
  }

  .file-info {
    display: flex;
    align-items: center;
    flex: 1;

    .el-icon-document {
      color: #409eff;
      margin-right: 8px;
      font-size: 16px;
    }

    .file-name {
      font-size: 14px;
      color: #303133;
      margin-right: 12px;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
      max-width: 300px;
    }

    .file-size {
      font-size: 12px;
      color: #909399;
    }
  }

  .remove-btn {
    color: #f56c6c;
    padding: 4px;

    &:hover {
      color: #f78989;
      background-color: #fef0f0;
      border-radius: 4px;
    }
  }
}

/* Upload dialog container style */
:deep(.el-dialog) {
  border-radius: 16px !important;
  overflow: hidden;
}

.el-table {
  --table-max-height: calc(100vh - 40vh);
  max-height: var(--table-max-height);
  flex: 1;

  .el-table__body-wrapper {
    max-height: calc(var(--table-max-height) - 40px);
  }
}

.slice-dialog-content {
  max-height: 70vh;
  overflow-y: auto;
}

/* Slice management popup fixed container size */
.slice-dialog {
  ::v-deep .el-dialog__wrapper {
    display: block !important;
  }

  /* Slice management popup scrollbar style */
  ::v-deep .el-dialog::-webkit-scrollbar {
    width: 8px;
    height: 8px;
  }

  ::v-deep .el-dialog::-webkit-scrollbar-track {
    background: #f8f9fa;
    border-radius: 4px;
  }

  ::v-deep .el-dialog::-webkit-scrollbar-thumb {
    background: #f0f0f0;
    border-radius: 4px;
    border: 1px solid #e8e8e8;
  }

  ::v-deep .el-dialog::-webkit-scrollbar-thumb:hover {
    background: #e8e8e8;
  }

  ::v-deep .el-dialog {
    position: absolute !important;
    top: 50% !important;
    left: 50% !important;
    transform: translate(-50%, -50%) !important;
    margin: 0 !important;
    width: 1200px !important;
    height: 90vh !important;
    max-height: 90vh !important;
    min-height: 90vh !important;
  }

  :deep(.el-dialog__body) {
    height: calc(100% - 90px) !important;
    max-height: calc(100% - 90px) !important;
    overflow: hidden;
    padding: 15px 20px;
  }

  .slice-management {
    height: 100%;
    display: flex;
    flex-direction: column;
  }

  .slice-list-section {
    flex: 1;
    display: flex;
    flex-direction: column;
    height: 100%;
    overflow: hidden;
  }

  /* Chunk contentContainer style */
  .slice-content-container {
    flex: 1;
    height: 100%;
    display: flex;
    flex-direction: column;
    overflow: hidden;
  }

  .slice-cards-container {
    flex: 1;
    overflow-y: auto;
    max-height: 100%;
    padding: 16px;

    /* Scrollbar style */
    &::-webkit-scrollbar {
      width: 6px;
      height: 6px;
    }

    &::-webkit-scrollbar-track {
      background: #fafafa;
      border-radius: 3px;
    }

    &::-webkit-scrollbar-thumb {
      background: #e0e0e0;
      border-radius: 3px;
      border: 1px solid #f0f0f0;
    }

    &::-webkit-scrollbar-thumb:hover {
      background: #d0d0d0;
    }
  }

  .slice-card {
    background-color: #f8f9fa;
    border: 1px solid #e4e7ed;
    border-radius: 8px;
    padding: 16px;
    margin-bottom: 16px;

    &:last-child {
      margin-bottom: 0;
    }

    .slice-header-info {
      margin-bottom: 12px;

      p {
        margin: 0;
        font-size: 14px;
        line-height: 1.5;

        strong {
          color: #303133;
          font-weight: 600;
        }
      }
    }

    .slice-card-content {
      background-color: white;
      border: 1px solid #f0f0f0;
      border-radius: 4px;
      padding: 12px;
      max-height: 280px;
      min-height: 120px;
      overflow-y: auto;

      /* ContentScrollbar style */
      &::-webkit-scrollbar {
        width: 4px;
      }

      &::-webkit-scrollbar-track {
        background: #fafafa;
      }

      &::-webkit-scrollbar-thumb {
        background: #e0e0e0;
        border-radius: 2px;
      }

      &::-webkit-scrollbar-thumb:hover {
        background: #d0d0d0;
      }

      .content-text {
        font-size: 14px;
        line-height: 1.6;
        text-align: left;
        color: #333;
        word-wrap: break-word;
        white-space: pre-wrap;
        font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', 'PingFang SC', 'Hiragino Sans GB', 'Microsoft YaHei', sans-serif;

        /* Ensure text displays normally, including spaces and line breaks */
        white-space: pre-wrap;
        word-break: break-word;
        overflow-wrap: break-word;

        /* Paragraph style */
        p {
          margin: 0 0 12px 0;
          line-height: 1.6;

          &:last-child {
            margin-bottom: 0;
          }
        }

        /* List style */
        ul,
        ol {
          margin: 8px 0;
          padding-left: 24px;

          li {
            margin-bottom: 4px;
            line-height: 1.5;
          }
        }

        /* TitleStyle */
        h1,
        h2,
        h3,
        h4,
        h5,
        h6 {
          margin: 16px 0 8px 0;
          font-weight: 600;
          line-height: 1.4;
        }

        h1 {
          font-size: 18px;
        }

        h2 {
          font-size: 16px;
        }

        h3 {
          font-size: 15px;
        }

        h4,
        h5,
        h6 {
          font-size: 14px;
        }

        /* Emphasized text */
        strong,
        b {
          font-weight: 600;
          color: #1a1a1a;
        }

        /* Code style */
        code {
          background-color: #f5f5f5;
          padding: 2px 4px;
          border-radius: 3px;
          font-family: 'Courier New', monospace;
          font-size: 13px;
        }

        /* Quote style */
        blockquote {
          margin: 12px 0;
          padding: 8px 12px;
          border-left: 4px solid #e0e0e0;
          background-color: #f9f9f9;
          font-style: italic;
          color: #666;
        }
      }
    }
  }

  .no-slice-data {
    flex: 1;
    display: flex;
    align-items: center;
    justify-content: center;
    height: 100%;
  }
}

.slice-count {
  display: flex;
  align-items: center;
  gap: 4px;
  font-size: 14px;
  color: #606266;
  background-color: #f5f7fa;
  padding: 6px 12px;
  border-radius: 4px;
  border: 1px solid #e4e7ed;
}

.count-label {
  font-weight: 500;
  color: #303133;
}

.count-value {
  font-weight: 600;
  color: #409eff;
  font-size: 15px;
}

.count-unit {
  color: #909399;
}

.slice-actions {
  display: flex;
  align-items: center;
  gap: 10px;
}

.slice-pagination {
  text-align: right;
  margin-top: 20px;

  .custom-pagination {
    display: flex;
    align-items: center;
    justify-content: flex-end;
    gap: 5px;
  }
}

@media (min-width: 1144px) {
  .table_bottom {
    display: flex;
    justify-content: space-between;
    align-items: center;
    margin-top: auto;
  }

  :deep(.transparent-table) {
    .el-table__body tr {
      td {
        padding-top: 16px;
        padding-bottom: 16px;
      }

      &+tr {
        margin-top: 10px;
      }
    }
  }
}
</style>
