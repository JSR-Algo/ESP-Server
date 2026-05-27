<template>
  <div class="welcome">
    <HeaderBar />

    <div class="operation-bar">
      <h2 class="page-title">{{ $t("agentTemplateManagement.title") }}</h2>
      <div class="right-operations">
        <el-input
          :placeholder="$t('agentTemplateManagement.searchPlaceholder')"
          v-model="search"
          class="search-input"
          clearable
          @keyup.enter.native="handleSearch"
          style="width: 240px"
        />
        <el-button class="btn-search" @click="handleSearch">
          {{ $t("agentTemplateManagement.search") }}
        </el-button>
      </div>
    </div>

    <!-- BodyContent -->
    <div class="main-wrapper">
      <div class="content-panel">
        <div class="content-area">
          <el-card class="template-card" shadow="never">
            <el-table
              ref="templateTable"
              :data="templateList"
              style="width: 100%"
              v-loading="templateLoading"
              :element-loading-text="$t('agentTemplateManagement.loading')"
              element-loading-spinner="el-icon-loading"
              element-loading-background="rgba(255, 255, 255, 0.7)"
              class="transparent-table"
              :header-cell-style="{ padding: '10px 20px' }"
              :cell-style="{ padding: '10px 20px' }"
            >
              <!-- Remove@row-click="handleRowClick" -->
              <!-- Custom selection column, header is"Select"text, data row is small square -->
              <el-table-column
                :label="$t('agentTemplateManagement.select')"
                align="center"
                min-width="100"
              >
                <template slot-scope="scope">
                  <el-checkbox
                    v-model="scope.row.selected"
                    @change="handleRowSelectionChange(scope.row)"
                    @click.stop
                  ></el-checkbox>
                </template>
              </el-table-column>
              <!-- TemplateName -->
              <el-table-column
                :label="$t('agentTemplateManagement.templateName')"
                prop="agentName"
                min-width="250"
                show-overflow-tooltip
              >
                <template slot-scope="scope">
                  <span>{{ scope.row.agentName }}</span>
                </template>
              </el-table-column>
              <!-- Modifyas index column, and move here -->
              <el-table-column
                :label="$t('agentTemplateManagement.serialNumber')"
                min-width="120"
                align="center"
              >
                <template slot-scope="scope">
                  <span>{{ (currentPage - 1) * pageSize + scope.$index + 1 }}</span>
                </template>
              </el-table-column>
              <!-- Operation column -->
              <el-table-column
                :label="$t('agentTemplateManagement.action')"
                min-width="250"
                align="center"
              >
                <template slot-scope="scope">
                  <div style="display: flex; justify-content: center; gap: 15px">
                    <el-button type="text" @click="editTemplate(scope.row)">{{
                      $t("agentTemplateManagement.editTemplate")
                    }}</el-button>
                    <el-button type="text" @click="deleteTemplate(scope.row)">{{
                      $t("agentTemplateManagement.deleteTemplate")
                    }}</el-button>
                  </div>
                </template>
              </el-table-column>
            </el-table>

            <!-- Table bottom action bar -->
            <div class="table_bottom">
              <div class="ctrl_btn">
                <el-button
                  type="primary"
                  @click="handleSelectAll"
                  size="mini"
                  class="select-all-btn"
                >
                  {{
                    isAllSelected
                      ? $t("agentTemplateManagement.deselectAll")
                      : $t("agentTemplateManagement.selectAll")
                  }}
                </el-button>
                <el-button type="success" @click="showAddTemplateDialog" size="mini">
                  {{ $t("agentTemplateManagement.createTemplate") }}
                </el-button>
                <el-button
                  type="danger"
                  @click="batchDeleteTemplate"
                  :disabled="!hasSelected"
                  size="mini"
                >
                  {{ $t("agentTemplateManagement.batchDelete") }}
                </el-button>
              </div>

              <!-- Pagination -->
              <div class="custom-pagination">
                <el-pagination
                  v-model:current-page="currentPage"
                  v-model:page-size="pageSize"
                  :page-sizes="pageSizeOptions"
                  layout="total, sizes, prev, pager, next, jumper"
                  :total="total"
                  @size-change="handlePageSizeChange"
                  @current-change="handlePageChange"
                />
              </div>
            </div>
          </el-card>
        </div>
      </div>
    </div>
    <el-footer>
      <version-footer />
    </el-footer>
  </div>
</template>

<script>
import HeaderBar from "@/components/HeaderBar";
import agentApi from "@/apis/module/agent";
import VersionFooter from "@/components/VersionFooter.vue";

export default {
  name: "AgentTemplateManagement",
  components: {
    HeaderBar,
    VersionFooter
  },

  data() {
    return {
      // Template related
      templateList: [],
      templateLoading: false,
      selectedTemplates: [],
      isAllSelected: false, // Add select allStatus

      search: "",
      // PaginationRelated data
      pageSizeOptions: [10, 20, 50, 100],
      currentPage: 1,
      pageSize: 10,
      total: 0,
    };
  },
  created() {
    this.loadTemplateList();
  },
  // incomputedPart addhasSelectedProperty
  computed: {
    pageCount() {
      return Math.ceil(this.total / this.pageSize);
    },
    visiblePages() {
      return this.getVisiblePages();
    },
    hasSelected() {
      return this.selectedTemplates.length > 0;
    },
  },
  methods: {
    // Load template list
    // ImproveloadTemplateListMethod ofErrorHandling logic
    loadTemplateList() {
      this.templateLoading = true;
      const params = {
        page: this.currentPage,
        limit: this.pageSize,
      };
      if (this.search) {
        params.agentName = this.search;
      }

      try {
        agentApi.getAgentTemplatesPage(
          params,
          (res) => {
            // More robustResponseHandling logic
            if (res && typeof res === "object") {
              if (res.data && res.data.code === 0) {
                const responseData = res.data.data || {};
                // Add for each templateselectedProperty
                this.templateList = Array.isArray(responseData.list)
                  ? responseData.list.map((item) => ({ ...item, selected: false }))
                  : [];
                this.total =
                  typeof responseData.total === "number" ? responseData.total : 0;
              } else {
                this.templateList = [];
                this.total = 0;
                this.$message.error(
                  res?.data?.msg || this.$t("agentTemplateManagement.fetchTemplateFailed")
                );
              }
            } else {
              this.templateList = [];
              this.total = 0;
              this.$message.error(
                this.$t("agentTemplateManagement.fetchTemplateBackendError")
              );
            }
            this.templateLoading = false;
          },
          (error) => {
            this.templateList = [];
            this.total = 0;
            this.templateLoading = false;
            this.$message.error(this.$t("common.networkError"));
          }
        );
      } catch (error) {
        this.templateList = [];
        this.total = 0;
        this.templateLoading = false;
        this.$message.error(this.$t("agentTemplateManagement.fetchTemplateBackendError"));
      }
    },

    // Search template
    handleSearch() {
      if (this.search) {
        const searchValue = this.search.toLowerCase();
        const filteredList = this.templateList.filter((template) =>
          template.agentName.toLowerCase().includes(searchValue)
        );
        this.templateList = filteredList;
        this.total = filteredList.length;
      } else {
        this.loadTemplateList();
      }
    },

    // ModifyshowAddTemplateDialogMethod, make it jump to same page as edit page
    // Show add template popup
    showAddTemplateDialog() {
      // Jump to template quick config page, do not passtemplateIdParam means add
      this.$router.push({
        path: "/template-quick-config",
      });
    },

    // Edit template
    editTemplate(row) {
      // Jump to template quick config page and pass template ID parameter
      this.$router.push({
        path: "/template-quick-config",
        query: { templateId: row.id },
      });
    },

    // Delete template
    deleteTemplate(row) {
      this.$confirm(
        this.$t("agentTemplateManagement.confirmSingleDelete"),
        this.$t("common.warning"),
        {
          confirmButtonText: this.$t("common.confirm"),
          cancelButtonText: this.$t("common.cancel"),
          type: "warning",
        }
      )
        .then(() => {
          agentApi.deleteAgentTemplate(row.id, (res) => {
            if (res && typeof res === "object") {
              // Checkres.dataWhether exists and containscode=0
              if (res.data && res.data.code === 0) {
                this.$message.success(this.$t("agentTemplateManagement.deleteSuccess"));
                this.loadTemplateList();
              } else {
                this.$message.error(
                  res?.data?.msg || this.$t("agentTemplateManagement.deleteFailed")
                );
              }
            } else {
              this.$message.error(this.$t("agentTemplateManagement.deleteBackendError"));
            }
          });
        })
        .catch(() => {
          this.$message.info(this.$t("common.deleteCancelled"));
        });
    },

    // Batch delete templates
    batchDeleteTemplate() {
      if (this.selectedTemplates.length === 0) {
        this.$message.warning(this.$t("agentTemplateManagement.selectTemplate"));
        return;
      }

      this.$confirm(
        this.$t("agentTemplateManagement.confirmBatchDelete", {
          count: this.selectedTemplates.length,
        }),
        this.$t("common.warning"),
        {
          confirmButtonText: this.$t("common.confirm"),
          cancelButtonText: this.$t("common.cancel"),
          type: "warning",
        }
      )
        .then(() => {
          // Ensure parameter format correct - willidArray as request body
          const ids = this.selectedTemplates.map((template) => template.id);

          agentApi.batchDeleteAgentTemplate(ids, (res) => {
            if (res && typeof res === "object") {
              if (res.data && res.data.code === 0) {
                this.$message.success(
                  this.$t("agentTemplateManagement.batchDeleteSuccess")
                );
                // Reload template list
                this.loadTemplateList();
                // Clear selectedStatus
                this.selectedTemplates = [];
                this.isAllSelected = false;
              } else {
                this.$message.error(
                  res?.data?.msg || this.$t("agentTemplateManagement.batchDeleteFailed")
                );
              }
            } else {
              this.$message.error(this.$t("agentTemplateManagement.deleteBackendError"));
            }
          });
        })
        .catch(() => {
          this.$message.info(this.$t("common.deleteCancelled"));
        });
    },

    // CompletePaginationRelated methods
    handlePageChange(page) {
      this.currentPage = page;
      this.loadTemplateList();
    },

    handlePageSizeChange(size) {
      this.pageSize = size;
      this.currentPage = 1;
      this.loadTemplateList();
    },

    goFirst() {
      this.currentPage = 1;
    },
    goPrev() {
      this.currentPage--;
    },
    goNext() {
      this.currentPage++;
    },
    goToPage(page) {
      this.currentPage = page;
    },
    getVisiblePages() {
      const pages = [];
      const totalPages = this.pageCount;
      const currentPage = this.currentPage;

      if (totalPages <= 7) {
        for (let i = 1; i <= totalPages; i++) {
          pages.push(i);
        }
      } else {
        if (currentPage <= 4) {
          for (let i = 1; i <= 5; i++) {
            pages.push(i);
          }
          pages.push("...");
          pages.push(totalPages);
        } else if (currentPage >= totalPages - 3) {
          pages.push(1);
          pages.push("...");
          for (let i = totalPages - 4; i <= totalPages; i++) {
            pages.push(i);
          }
        } else {
          pages.push(1);
          pages.push("...");
          for (let i = currentPage - 1; i <= currentPage + 1; i++) {
            pages.push(i);
          }
          pages.push("...");
          pages.push(totalPages);
        }
      }

      return pages;
    },

    // ModifyhandleSelectAllMethod
    handleSelectAll() {
      this.isAllSelected = !this.isAllSelected;
      this.templateList.forEach((row) => {
        row.selected = this.isAllSelected;
      });
      // Update selected template list
      this.selectedTemplates = this.isAllSelected ? [...this.templateList] : [];
    },

    // Handle row selection change
    handleRowSelectionChange(row) {
      // Find selected template
      this.selectedTemplates = this.templateList.filter((template) => template.selected);
      // Update select allStatus
      this.isAllSelected =
        this.templateList.length > 0 &&
        this.selectedTemplates.length === this.templateList.length;
    },
  },
};
</script>

<style scoped lang="scss">
/* Base background and layout settings */
.welcome {
  min-height: 100vh;
  display: flex;
  flex-direction: column;
  position: relative;
  background: linear-gradient(to bottom right, #dce8ff, #e4eeff, #e6cbfd) center;
  background-size: cover;
  overflow: hidden;
  width: 100%;
}

/* Operation bar style */
.operation-bar {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 16px 24px;
}

.page-title {
  font-size: 24px;
  margin: 0;
}

.right-operations {
  display: flex;
  align-items: center;
  gap: 10px;
}

.search-input {
  width: 200px;
}

.btn-search {
  background: linear-gradient(135deg, #6b8cff, #a966ff);
  border: none;
  color: white;
}

/* Main container style */
.main-wrapper {
  // Top 63px Bottom 35px Query72px
  height: calc(100vh - 63px - 35px - 72px);
  margin: 0 22px;
  border-radius: 15px;
  box-shadow: 0 2px 12px rgba(0, 0, 0, 0.1);
  position: relative;
  background: rgba(237, 242, 255, 0.5);
  display: flex;
  flex-direction: column;
}

.content-panel {
  width: 100%;
  flex: 1;
  display: flex;
  overflow: hidden;
  border-radius: 15px;
  background: transparent;
  border: 1px solid #fff;
}

.content-area {
  flex: 1;
  min-width: 600px;
  overflow-x: auto;
  background-color: white;
  display: flex;
  flex-direction: column;
  position: relative;
}

/* Template card style */
.template-card {
  border: none;
  box-shadow: none;
  flex: 1;
  display: flex;
  flex-direction: column;
  overflow: hidden;

  :deep(.el-card__body) {
    padding: 15px;
    display: flex;
    flex-direction: column;
    flex: 1;
    overflow: hidden;
  }
}

/* Table style - Optimized integrated version */
.transparent-table {
  width: 100%;
  flex: 1;
  min-height: 0;
}

:deep(.el-table) {
  height: 100%;
  display: flex;
  flex-direction: column;
  --table-max-height: calc(100vh - 42vh);
  max-height: var(--table-max-height);

  /* Table header style */
  .el-table__header th {
    padding: 8px 0 !important;
    height: 40px !important;
  }

  .el-table__header th .cell {
    color: #303133 !important;
    font-weight: 600;
  }

  /* Table body style */
  .el-table__body {
    .el-table__row td {
      padding: 12px 0 !important;
      border-bottom: 1px solid #ebeef5;
    }
    .el-table__row:hover {
      background-color: #f5f7fa;
    }
  }

  /* Table button style */
  .el-button--text {
    color: #7079aa;
  }

  .el-button--text:hover {
    color: #5a64b5;
  }

  /* Cell text style */
  .cell {
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }
}

/* Table bottom action bar */
.table_bottom {
  display: flex;
  justify-content: space-between !important;
  align-items: center;
  margin-top: auto;
  padding: 0 20px !important;
  width: 100% !important;
  box-sizing: border-box !important;
}

/* Control button style */
.ctrl_btn {
  display: flex;
  gap: 8px;
  padding-left: 0 !important;
  margin-left: 0 !important;

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
  .el-button--success {
    background: #5bc98c;
    color: white;
  }
  .el-button--danger {
    background: #fd5b63;
    color: white;
  }
}
</style>
