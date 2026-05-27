<template>
  <div class="welcome">
    <HeaderBar/>
    <div class="operation-bar">
      <h2 class="page-title">{{ $t('replacementWordManagement.pageTitle') }}</h2>
    </div>
    <div class="main-wrapper">
      <div class="content-panel">
        <div class="content-area">
          <el-card class="params-card" shadow="never">
            <el-table
              ref="paramsTable"
              :data="paramsList"
              class="transparent-table"
              v-loading="loading"
              :element-loading-text="$t('serverSideManager.loading')"
              element-loading-spinner="el-icon-loading"
              element-loading-background="rgba(255, 255, 255, 0.7)"
            >
              <el-table-column :label="$t('modelConfig.select')" align="center" width="120">
                <template slot-scope="scope">
                  <el-checkbox v-model="scope.row.selected" @change="handleCheckboxChange(scope.row)"></el-checkbox>
                </template>
              </el-table-column>
              <el-table-column :label="$t('replacementWordManagement.fileName')" prop="fileName" align="center"/>
              <el-table-column :label="$t('replacementWordManagement.replacementWordCount')" prop="wordCount" align="center"/>
              <el-table-column :label="$t('replacementWordManagement.replacementWordContent')" prop="content" align="center">
                <template slot-scope="scope">
                  <el-tooltip placement="right" effect="light" popper-class="replace-word-tooltip">
                    <div slot="content" class="replace-word-content">
                      <el-tag v-for="(item, index) in scope.row.content" :key="index" size="mini" class="custom-tag">{{ item }}</el-tag>
                    </div>
                    <span class="content-text">{{ formatContent(scope.row.content) }}</span>
                  </el-tooltip>
                </template>
              </el-table-column>
              <el-table-column :label="$t('replacementWordManagement.createTime')" prop="createdAt" align="center"/>
              <el-table-column :label="$t('replacementWordManagement.updateTime')" prop="updatedAt" align="center"/>
              <el-table-column :label="$t('replacementWordManagement.operation')" prop="operator" align="center">
                <template slot-scope="scope">
                  <el-button size="medium" type="text" @click="handleEdit(scope.row)">
                    {{ $t('replacementWordManagement.edit') }}
                  </el-button>
                  <el-button size="medium" type="text" @click="handleDownload(scope.row)">
                    {{ $t('replacementWordManagement.download') }}
                  </el-button>
                  <el-button size="medium" type="text" @click="handleDelete(scope.row)">
                    {{ $t('replacementWordManagement.delete') }}
                  </el-button>
                </template>
              </el-table-column>
            </el-table>
            <div class="table_bottom">
              <div class="ctrl_btn">
                <el-button size="mini" type="primary" class="select-all-btn" @click="handleSelectAll">
                  {{ allSelected ? $t('user.deselectAll') : $t('user.selectAll') }}
                </el-button>
                <el-button size="mini" type="success" @click="handleAdd">{{ $t('replacementWordManagement.addFile') }}</el-button>
                <el-button size="mini" type="danger" :disabled="!hasAnySelected" @click="handleBatchDelete">
                  {{ $t('replacementWordManagement.batchDelete') }}
                </el-button>
              </div>
                <div class="custom-pagination">
                  <el-select v-model="pageSize" @change="handlePageSizeChange" class="page-size-select">
                    <el-option
                      v-for="item in pageSizeOptions"
                      :key="item"
                      :label="`${item}${$t('paramManagement.itemsPerPage')}`"
                      :value="item"
                    >
                      </el-option>
                  </el-select>
                  <button class="pagination-btn" :disabled="currentPage === 1" @click="goFirst">
                    {{ $t('paramManagement.firstPage') }}
                  </button>
                  <button class="pagination-btn" :disabled="currentPage === 1" @click="goPrev">
                    {{ $t('paramManagement.prevPage') }}
                  </button>
                  <button
                    v-for="page in visiblePages"
                    :key="page"
                    class="pagination-btn"
                    :class="{ active: page === currentPage }" @click="goToPage(page)"
                  >
                    {{ page }}
                  </button>
                  <button class="pagination-btn" :disabled="currentPage === pageCount" @click="goNext">
                    {{ $t('paramManagement.nextPage') }}
                  </button>
                  <span class="total-text">{{ $t('paramManagement.totalRecords', { total }) }}</span>
                </div>
            </div>
          </el-card>
        </div>
      </div>
    </div>
    <el-footer><VersionFooter/></el-footer>
    <ReplacementWordDialog
      ref="paramDialog"
      :title="dialogTitle"
      :visible.sync="dialogVisible"
      :form="dialogForm"
      @submit="handleSubmit"
      @cancel="dialogVisible = false"
    />
  </div>
</template>

<script>
import Api from "@/apis/api";
import HeaderBar from "@/components/HeaderBar.vue";
import ParamDialog from "@/components/ParamDialog.vue";
import VersionFooter from "@/components/VersionFooter.vue";
import ReplacementWordDialog from "@/components/ReplacementWordDialog.vue";

export default {
  components: { HeaderBar, ParamDialog, VersionFooter, ReplacementWordDialog },
  data() {
    return {
      paramsList: [],
      selectedRows: new Set(),
      currentPage: 1,
      loading: false,
      pageSize: 10,
      pageSizeOptions: [10, 20, 50, 100],
      total: 0,
      dialogVisible: false,
      dialogTitle: '',
      dialogForm: {},
    };
  },
  created() {
    this.fetchFileList();
  },
  mounted() {
    this.dialogTitle = this.$t('replacementWordManagement.addFile');
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

    hasAnySelected() {
      return this.selectedRows.size > 0;
    },

    allSelected() {
      if (this.paramsList.length === 0) {
        return false;
      }
      return this.paramsList.every(row => this.selectedRows.has(row.id));
    },
  },
  methods: {
    formatContent(content) {
      if (!content) return '';
      if (Array.isArray(content)) {
        return content.join(',');
      }
      return content;
    },

    handlePageSizeChange(val) {
      this.pageSize = val;
      this.currentPage = 1;
      this.fetchFileList();
    },
    goToPage(page) {
        if (page !== this.currentPage) {
            this.currentPage = page;
            this.fetchFileList();
        }
    },
    goFirst() {
        if (this.currentPage !== 1) {
            this.currentPage = 1;
            this.fetchFileList();
        }
    },
    goPrev() {
        if (this.currentPage > 1) {
            this.currentPage--;
            this.fetchFileList();
        }
    },
    goNext() {
        if (this.currentPage < this.pageCount) {
            this.currentPage++;
            this.fetchFileList();
        }
    },

    handleCheckboxChange(row) {
      const newSet = new Set(this.selectedRows);
      if (row.selected) {
        newSet.add(row.id);
      } else {
        newSet.delete(row.id);
      }
      this.selectedRows = newSet;
    },

    handleSelectAll() {
      if (this.allSelected) {
        this.paramsList.forEach(row => {
          this.$set(row, 'selected', false);
        });
        this.selectedRows = new Set();
      } else {
        this.paramsList.forEach(row => {
          this.$set(row, 'selected', true);
        });
        this.selectedRows = new Set(this.paramsList.map(row => row.id));
      }
    },

    handleBatchDelete() {
      const ids = Array.from(this.paramsList)
        .filter(row => row.selected)
        .map(row => row.id);

      if (ids.length === 0) {
        return;
      }

      this.$confirm(
        this.$t('replacementWordManagement.confirmBatchDelete', { count: ids.length }),
        this.$t('replacementWordManagement.batchDeleteHint'),
        {
          confirmButtonText: this.$t('common.confirm'),
          cancelButtonText: this.$t('common.cancel')
        }
      ).then(() => {
        Api.correctWord.batchDeleteFile(ids, ({ data }) => {
          if (data.code === 0) {
            this.$message.success(this.$t('common.deleteSuccess'));

            const newSet = new Set(this.selectedRows);
            ids.forEach(id => {
              newSet.delete(id);
            });
            this.selectedRows = newSet;

            this.fetchFileList();
          } else {
            this.$message.error(data.msg || this.$t('common.deleteFailure'));
          }
        });
      }).catch(() => {});
    },

    fetchFileList() {
      this.loading = true;
      Api.correctWord.getFileList({
        page: this.currentPage,
        pageSize: this.pageSize
      }, ({ data }) => {
        this.loading = false;
        if (data.code === 0) {
          this.paramsList = data.data.list || [];

          this.paramsList.forEach(row => {
            if (this.selectedRows.has(row.id)) {
              this.$set(row, 'selected', true);
            } else {
              this.$set(row, 'selected', false);
            }
          });

          this.total = data.data.total || 0;
        } else {
          this.$message.error({
            message: data.msg || this.$t('replacementWordManagement.getListFailed'),
            showClose: true
          });
        }
      });
    },

    handleAdd() {
      this.dialogForm = {
        id: undefined,
        fileName: '',
        content: ''
      };
      this.dialogTitle = this.$t('replacementWordManagement.addFile');
      this.dialogVisible = true;
    },

    handleEdit(row) {
      this.dialogForm = {
        id: row.id,
        fileName: row.fileName,
        content: row.content || ''
      };
      this.dialogTitle = this.$t('replacementWordManagement.edit');
      this.dialogVisible = true;
    },

    handleDownload(row) {
      Api.correctWord.downloadFile(row.id, (res) => {
        const url = window.URL.createObjectURL(new Blob([res.data]));
        const link = document.createElement('a');
        link.href = url;
        link.download = `${row.fileName}.txt`;
        link.click();
        window.URL.revokeObjectURL(url);
      });
    },

    handleDelete(row) {
      this.$confirm(this.$t('replacementWordManagement.confirmDelete'), this.$t('replacementWordManagement.deleteHint'), {
        confirmButtonText: this.$t('common.confirm'),
        cancelButtonText: this.$t('common.cancel')
      }).then(() => {
        Api.correctWord.deleteFile(row.id, ({ data }) => {
          if (data.code === 0) {
            this.$message.success(this.$t('common.deleteSuccess'));
            const newSet = new Set(this.selectedRows);
            newSet.delete(row.id);
            this.selectedRows = newSet;
            this.fetchFileList();
          } else {
            this.$message.error(data.msg || this.$t('common.deleteFailure'));
          }
        });
      }).catch(() => {});
    },

    handleSubmit(formData) {
      if (formData.id) {
        Api.correctWord.updateFile(formData, ({ data }) => {
          if (data.code === 0) {
            this.$message.success(this.$t('replacementWordManagement.saveSuccess'));
            this.dialogVisible = false;
            this.fetchFileList();
          } else {
            this.$message.error(data.msg || this.$t('replacementWordManagement.saveFailed'));
          }
          if (this.$refs.paramDialog) {
            this.$refs.paramDialog.resetSaving();
          }
        });
      } else {
        Api.correctWord.addFile(formData, ({ data }) => {
          if (data.code === 0) {
            this.$message.success(this.$t('common.addSuccess'));
            this.dialogVisible = false;
            this.fetchFileList();
          } else {
            this.$message.error(data.msg || this.$t('replacementWordManagement.addFailed'));
          }
          if (this.$refs.paramDialog) {
            this.$refs.paramDialog.resetSaving();
          }
        });
      }
    }
  },
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
  height: calc(100vh - 63px - 35px - 72px);
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
  padding: 22px 24px;
}

.page-title {
  font-size: 24px;
  margin: 0;
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
  margin-top: 10px;
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
    gap: 10px;

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

    .total-text {
        color: #909399;
        font-size: 14px;
        margin-left: 10px;
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
  }

  &::before {
    display: none;
  }

  .el-table__body tr {
    background-color: white;

    td {
      border-top: 1px solid rgba(0, 0, 0, 0.04);
      border-bottom: 1px solid rgba(0, 0, 0, 0.04);
    }
  }
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

@media (min-width: 1144px) {
  .table_bottom {
    display: flex;
    justify-content: space-between;
    align-items: center;
    margin-top: 40px;
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

:deep(.el-table .el-button--text) {
  color: #7079aa;
}

:deep(.el-table .el-button--text:hover) {
  color: #5a64b5;
}

.el-button--success {
  background: #5bc98c;
  color: white;
}

:deep(.el-table .cell) {
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;

  .content-text {
    display: block;
    max-width: 300px;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
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

:deep(.el-table) {
  .el-table__body-wrapper {
    transition: height 0.3s ease;
  }
}

.el-table {
  max-height: var(--table-max-height);

  .el-table__body-wrapper {
    max-height: calc(var(--table-max-height) - 40px);
    overflow-y: auto;
  }
}
.custom-tag {
  background: #e6ebff;
  color: #5778ff;
  border-radius: 8px;
  font-size: 12px;
  font-weight: normal;
  border: none;
}
</style>

<style>
.replace-word-tooltip {
  .popper__arrow {
    border-top-color: transparent !important;
    border-right-color: #e4e7ed !important;
  }
}
.replace-word-content {
  max-width: 400px;
  max-height: 60vh;
  overflow-y: auto;
  scrollbar-width: thin;
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
}
</style>