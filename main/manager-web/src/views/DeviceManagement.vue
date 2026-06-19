<template>
  <div class="welcome">
    <HeaderBar />

    <div class="operation-bar">
      <h2 class="page-title">{{ $t('device.management') }}</h2>
      <div class="right-operations">
        <el-select
          v-model="selectedChildId"
          filterable
          remote
          reserve-keyword
          clearable
          :remote-method="searchChildrenByKeyword"
          :loading="childLoading"
          :placeholder="$t('device.childNamePlaceholder')"
          class="child-name-select"
        >
          <el-option
            v-for="child in childOptions"
            :key="child.childId"
            :label="child.childName"
            :value="child.childId"
          >
            <span>{{ child.childName }}</span>
            <span class="child-option-meta">
              {{ child.age ? `${child.age} ${$t('device.yearsOld')}` : (child.parentEmail || child.parentId) }}
            </span>
          </el-option>
        </el-select>
        <el-input :placeholder="$t('device.searchPlaceholder')" v-model="searchKeyword" class="search-input"
          @keyup.enter.native="handleSearch" clearable />
        <el-button class="btn-search" @click="handleSearch">{{ $t('device.search') }}</el-button>
      </div>
    </div>

    <div class="main-wrapper">
      <div class="content-panel">
        <div class="content-area">
          <el-card class="device-card" shadow="never">
            <el-table ref="deviceTable" :data="paginatedDeviceList" class="transparent-table"
              :header-cell-class-name="headerCellClassName" v-loading="loading"
              :element-loading-text="$t('deviceManagement.loading')" element-loading-spinner="el-icon-loading"
              element-loading-background="rgba(255, 255, 255, 0.7)">
              <el-table-column :label="$t('modelConfig.select')" align="center" width="120">
                <template slot-scope="scope">
                  <el-checkbox v-model="scope.row.selected"></el-checkbox>
                </template>
              </el-table-column>
              <el-table-column :label="$t('device.model')" prop="model" align="center">
                <template slot-scope="scope">
                  {{ getFirmwareTypeName(scope.row.model) }}
                </template>
              </el-table-column>
              <el-table-column :label="$t('device.firmwareVersion')" prop="firmwareVersion"
                align="center"></el-table-column>
              <el-table-column :label="$t('device.macAddress')" prop="macAddress" align="center"></el-table-column>
              <el-table-column :label="$t('device.bindTime')" prop="bindTime" align="center"></el-table-column>
              <el-table-column :label="$t('device.lastConversation')" prop="lastConversation"
                align="center"></el-table-column>
              <el-table-column v-if="mqttServiceAvailable" :label="$t('device.deviceStatus')" prop="deviceStatus" align="center">
                <template slot-scope="scope">
                  <el-tag v-if="scope.row.deviceStatus === 'online'" type="success">{{ $t('device.online') }}</el-tag>
                  <el-tag v-else type="danger">{{ $t('device.offline') }}</el-tag>
                </template>
              </el-table-column>
              <el-table-column :label="$t('device.remark')" align="center">
                <template #default="{ row }">
                  <el-input v-show="row.isEdit" v-model="row.remark" size="mini" maxlength="64" show-word-limit
                    @blur="onRemarkBlur(row)" @keyup.enter.native="onRemarkEnter(row)" />
                  <span v-show="!row.isEdit" class="remark-view">
                    <i class="el-icon-edit" @click="row.isEdit = true" style="cursor: pointer;"></i>
                    <span @click="row.isEdit = true">
                      {{ row.remark || '-' }}
                    </span>
                  </span>
                </template>
              </el-table-column>
              <el-table-column :label="$t('device.childProfile')" align="center" min-width="190">
                <template #default="{ row }">
                  <div v-if="row.isChildEdit" class="child-profile-edit">
                    <el-input
                      v-model="row.childName"
                      size="mini"
                      maxlength="64"
                      show-word-limit
                      :placeholder="$t('device.childName')"
                    />
                    <el-input-number
                      v-model="row.childAge"
                      size="mini"
                      :min="1"
                      :max="18"
                      controls-position="right"
                      :placeholder="$t('device.childAge')"
                      class="child-age-input"
                    />
                    <div class="child-profile-actions">
                      <el-button size="mini" type="primary" :loading="row._childSubmitting" @click="updateChildProfile(row)">
                        {{ $t('button.save') }}
                      </el-button>
                      <el-button size="mini" @click="cancelChildProfileEdit(row)">
                        {{ $t('button.cancel') }}
                      </el-button>
                    </div>
                  </div>
                  <div v-else class="child-profile-view">
                    <i class="el-icon-edit" @click="row.isChildEdit = true" style="cursor: pointer;"></i>
                    <span class="child-profile-name" @click="row.isChildEdit = true">{{ row.childName || '-' }}</span>
                    <span v-if="row.childAge" class="child-profile-age">
                      {{ row.childAge }} {{ $t('device.yearsOld') }}
                    </span>
                  </div>
                </template>
              </el-table-column>
              <el-table-column :label="$t('device.autoUpdate')" align="center">
                <template slot-scope="scope">
                  <el-switch v-model="scope.row.otaSwitch" size="mini" active-color="#13ce66" inactive-color="#ff4949"
                    @change="handleOtaSwitchChange(scope.row)"></el-switch>
                </template>
              </el-table-column>
              <el-table-column :label="$t('device.operation')" align="center">
                <template slot-scope="scope">
                  <el-button size="mini" type="text" @click="handleUnbind(scope.row.device_id)">
                    {{ $t('device.unbind') }}
                  </el-button>
                  <el-button size="mini" type="text" :disabled="!selectedChild" @click="applyChildNameToDevice(scope.row)">
                    {{ $t('device.applyChildProfile') }}
                  </el-button>
                  <el-button v-if="isGenerate(scope.row)" size="mini" type="text" @click="handleGenertor(scope.row)">
                    {{ $t('device.deviceThemeGeneration') }}
                  </el-button>
                </template>
              </el-table-column>
            </el-table>

            <div class="table_bottom">
              <div class="ctrl_btn">
                <el-button size="mini" type="primary" class="select-all-btn" @click="handleSelectAll">
                  {{ isCurrentPageAllSelected ? $t('common.deselectAll') : $t('common.selectAll') }}
                </el-button>
                <el-button type="success" size="mini" class="add-device-btn" @click="handleAddDevice">
                  {{ $t('device.bindWithCode') }}
                </el-button>
                <el-button type="success" size="mini" class="add-device-btn" @click="handleManualAddDevice">
                  {{ $t('device.manualAdd') }}
                </el-button>
                <el-button size="mini" type="danger" icon="el-icon-delete" @click="deleteSelected">
                  {{ $t('device.unbind') }}
                </el-button>
              </div>
              <div class="custom-pagination">
                <el-select v-model="pageSize" @change="handlePageSizeChange" class="page-size-select">
                  <el-option v-for="item in pageSizeOptions" :key="item"
                    :label="$t('dictManagement.itemsPerPage').replace('{items}', item)" :value="item">
                  </el-option>
                </el-select>
                <button class="pagination-btn" :disabled="currentPage === 1" @click="goFirst">
                  {{ $t('dictManagement.firstPage') }}
                </button>
                <button class="pagination-btn" :disabled="currentPage === 1" @click="goPrev">
                  {{ $t('dictManagement.prevPage') }}
                </button>
                <button v-for="page in visiblePages" :key="page" class="pagination-btn"
                  :class="{ active: page === currentPage }" @click="goToPage(page)">
                  {{ page }}
                </button>
                <button class="pagination-btn" :disabled="currentPage === pageCount" @click="goNext">
                  {{ $t('dictManagement.nextPage') }}
                </button>
                <span class="total-text">
                  {{ $t('dictManagement.totalRecords').replace('{total}', deviceList.length) }}
                </span>
              </div>
            </div>
          </el-card>
        </div>
      </div>
    </div>

    <AddDeviceDialog :visible.sync="addDeviceDialogVisible" :agent-id="currentAgentId"
      @refresh="fetchBindDevices(currentAgentId)" />
    <ManualAddDeviceDialog :visible.sync="manualAddDeviceDialogVisible" :agent-id="currentAgentId"
      @refresh="fetchBindDevices(currentAgentId)" />
    <el-footer>
      <version-footer />
    </el-footer>
  </div>
</template>

<script>
import Api from '@/apis/api';
import AddDeviceDialog from "@/components/AddDeviceDialog.vue";
import HeaderBar from "@/components/HeaderBar.vue";
import ManualAddDeviceDialog from "@/components/ManualAddDeviceDialog.vue";
import VersionFooter from "@/components/VersionFooter.vue";

export default {
  components: {
    HeaderBar,
    AddDeviceDialog,
    ManualAddDeviceDialog,
    VersionFooter
  },
  data() {
    return {
      addDeviceDialogVisible: false,
      manualAddDeviceDialogVisible: false,
      selectedDeviceId: '',
      searchKeyword: "",
      activeSearchKeyword: "",
      currentAgentId: this.$route.query.agentId || '',
      currentPage: 1,
      pageSize: 10,
      pageSizeOptions: [10, 20, 50, 100],
      deviceList: [],
      selectedChildId: '',
      childOptions: [],
      childLoading: false,
      loading: false,
      userApi: null,
      firmwareTypes: [],
      mqttServiceAvailable: false, // MQTTWhether service available
    };
  },
  computed: {
    filteredDeviceList() {
      const keyword = this.activeSearchKeyword.toLowerCase();
      if (!keyword) return this.deviceList;
      return this.deviceList.filter(device =>
        (device.model && device.model.toLowerCase().includes(keyword)) ||
        (device.macAddress && device.macAddress.toLowerCase().includes(keyword)) ||
        (device.remark && device.remark.toLowerCase().includes(keyword)) ||
        (device.childName && device.childName.toLowerCase().includes(keyword)) ||
        (device.childAge && String(device.childAge).includes(keyword))
      );
    },

    selectedChild() {
      return this.childOptions.find(child => child.childId === this.selectedChildId) || null;
    },

    paginatedDeviceList() {
      const start = (this.currentPage - 1) * this.pageSize;
      const end = start + this.pageSize;
      return this.filteredDeviceList.slice(start, end);
    },
    pageCount() {
      return Math.ceil(this.filteredDeviceList.length / this.pageSize);
    },
    // Calculate current page all selected
    isCurrentPageAllSelected() {
      return this.paginatedDeviceList.length > 0 &&
        this.paginatedDeviceList.every(device => device.selected);
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
  },
  mounted() {
    const agentId = this.$route.query.agentId;
    if (agentId) {
      this.fetchBindDevices(agentId);
    }
  },
  created() {
    this.getFirmwareTypes()
    this.fetchChildOptions('')
  },
  methods: {
    async getFirmwareTypes() {
      try {
        const res = await Api.dict.getDictDataByType('FIRMWARE_TYPE')
        this.firmwareTypes = res.data
      } catch (error) {
        console.error(this.$t('device.getFirmwareTypeFailed') + ':', error)
        this.$message.error(error.message || this.$t('device.getFirmwareTypeFailed'))
      }
    },
    handlePageSizeChange(val) {
      this.pageSize = val;
      this.currentPage = 1;
    },
    handleSearch() {
      this.activeSearchKeyword = this.searchKeyword;
      this.currentPage = 1;
    },
    searchChildrenByKeyword(keyword) {
      this.fetchChildOptions(keyword || '');
    },
    fetchChildOptions(keyword) {
      this.childLoading = true;
      Api.courseInsights.listLearners(
        { keyword: (keyword || '').trim(), limit: 50 },
        (rows) => {
          this.childLoading = false;
          this.childOptions = rows;
        },
        () => {
          this.childLoading = false;
          this.childOptions = [];
        },
      );
    },

    handleSelectAll() {
      const shouldSelectAll = !this.isCurrentPageAllSelected;
      this.paginatedDeviceList.forEach(row => {
        row.selected = shouldSelectAll;
      });
    },

    deleteSelected() {
      const selectedDevices = this.paginatedDeviceList.filter(device => device.selected);
      if (selectedDevices.length === 0) {
        this.$message.warning({
          message: this.$t('device.selectAtLeastOne'),
          showClose: true
        });
        return;
      }

      this.$confirm(this.$t('device.confirmBatchUnbind').replace('{count}', selectedDevices.length), this.$t('message.warning'), {
        confirmButtonText: this.$t('button.ok'),
        cancelButtonText: this.$t('button.cancel'),
        type: 'warning'
      }).then(() => {
        const deviceIds = selectedDevices.map(device => device.device_id);
        this.batchUnbindDevices(deviceIds);
      });
    },
    batchUnbindDevices(deviceIds) {
      const promises = deviceIds.map(id => {
        return new Promise((resolve, reject) => {
          Api.device.unbindDevice(id, ({ data }) => {
            if (data.code === 0) {
              resolve();
            } else {
              reject(data.msg || this.$t('device.bindFailed'));
            }
          });
        });
      });
      Promise.all(promises)
        .then(() => {
          this.$message.success({
            message: this.$t('device.batchUnbindSuccess').replace('{count}', deviceIds.length),
            showClose: true
          });
          this.fetchBindDevices(this.currentAgentId);
        })
        .catch(error => {
          this.$message.error({
            message: error || this.$t('device.batchUnbindError'),
            showClose: true
          });
        });
    },
    handleAddDevice() {
      this.addDeviceDialogVisible = true;
    },
    handleManualAddDevice() {
      this.manualAddDeviceDialogVisible = true;
    },
    submitRemark(row) {
      if (row._submitting) return;

      const text = (row.remark || '').trim();
      if (text.length > 64) {
        this.$message.warning(this.$t('device.remarkTooLong'));
        return;
      }
      if (text === row._originalRemark) {
        return;
      }

      row._submitting = true;
      this.updateDeviceInfo(row.device_id, { alias: text }, (ok, resp) => {
        if (ok) {
          row._originalRemark = text;
          this.$message.success(this.$t('device.remarkSaved'));
        } else {
          row.remark = row._originalRemark;
          this.$message.error(resp.msg || this.$t('device.remarkSaveFailed'));
        }
        row._submitting = false;
      });
    },
    applyChildNameToDevice(row) {
      const child = this.selectedChild;
      if (!child || !child.childName) {
        this.$message.warning(this.$t('device.selectChildNameFirst'));
        return;
      }
      this.updateDeviceInfo(row.device_id, { alias: child.childName, childName: child.childName, childAge: child.age }, (ok, resp) => {
        if (ok) {
          row.remark = child.childName;
          row._originalRemark = child.childName;
          row.childName = child.childName;
          row.childAge = child.age;
          row._originalChildName = child.childName;
          row._originalChildAge = child.age;
          this.$message.success(this.$t('device.childProfileApplied'));
        } else {
          this.$message.error(resp.msg || this.$t('device.remarkSaveFailed'));
        }
      });
    },
    updateChildProfile(row) {
      if (row._childSubmitting) return;
      const childName = (row.childName || '').trim();
      const childAge = row.childAge === '' || row.childAge === undefined ? null : Number(row.childAge);
      if (childName.length > 64) {
        this.$message.warning(this.$t('device.childNameTooLong'));
        return;
      }
      if (childAge !== null && (!Number.isFinite(childAge) || childAge < 1 || childAge > 18)) {
        this.$message.warning(this.$t('device.childAgeInvalid'));
        return;
      }
      row._childSubmitting = true;
      const payload = { childName, childAge };
      this.updateDeviceInfo(row.device_id, payload, (ok, resp) => {
        if (ok) {
          row.childName = childName;
          row.childAge = childAge;
          row._originalChildName = childName;
          row._originalChildAge = childAge;
          row.isChildEdit = false;
          this.$message.success(this.$t('device.childProfileSaved'));
        } else {
          row.childName = row._originalChildName;
          row.childAge = row._originalChildAge;
          this.$message.error(resp.msg || this.$t('device.childProfileSaveFailed'));
        }
        row._childSubmitting = false;
      });
    },
    cancelChildProfileEdit(row) {
      row.childName = row._originalChildName;
      row.childAge = row._originalChildAge;
      row.isChildEdit = false;
    },
    // Remark input: submit on blur
    onRemarkBlur(row) {
      row.isEdit = false;
      setTimeout(() => {
        this.submitRemark(row);
      }, 100); // Delay 100ms, avoid enter+blur Windows triggered at same time
    },
    // Remark input box: submit on Enter
    onRemarkEnter(row) {
      row.isEdit = false;
      this.submitRemark(row);
    },
    handleUnbind(device_id) {
      this.$confirm(this.$t('device.confirmUnbind'), this.$t('message.warning'), {
        confirmButtonText: this.$t('button.ok'),
        cancelButtonText: this.$t('button.cancel'),
        type: 'warning'
      }).then(() => {
        Api.device.unbindDevice(device_id, ({ data }) => {
          if (data.code === 0) {
            this.$message.success({
              message: this.$t('device.unbindSuccess'),
              showClose: true
            });
            this.fetchBindDevices(this.$route.query.agentId);
          } else {
            this.$message.error({
              message: data.msg || this.$t('device.unbindFailed'),
              showClose: true
            });
          }
        });
      });
    },
    handleGenertor(row) {
      const pathname = window.location.pathname;
      const basePath = pathname.split('/').slice(0, -1).join('/');
      const url = `${window.location.origin}${basePath}/generator/?deviceId=${row.device_id}`;
      sessionStorage.setItem('devicePath', window.location.href);
      window.location.href = url;
    },
    goFirst() {
      this.currentPage = 1;
    },
    goPrev() {
      if (this.currentPage > 1) this.currentPage--;
    },
    goNext() {
      if (this.currentPage < this.pageCount) this.currentPage++;
    },
    goToPage(page) {
      this.currentPage = page;
    },

    fetchBindDevices(agentId) {
      this.loading = true;
      Api.device.getAgentBindDevices(agentId, ({ data }) => {
        this.loading = false;
        if (data.code === 0) {
          this.deviceList = data.data.map(device => {
            return {
              device_id: device.id,
              model: device.board,
              firmwareVersion: device.appVersion,
              macAddress: device.macAddress,
              bindTime: device.createDate,
              lastConversation: device.lastConnectedAt,
              remark: device.alias,
              _originalRemark: device.alias,
              childName: device.childName,
              childAge: device.childAge,
              _originalChildName: device.childName,
              _originalChildAge: device.childAge,
              isEdit: false,
              isChildEdit: false,
              _submitting: false,
              _childSubmitting: false,
              otaSwitch: device.autoUpdate === 1,
              rawBindTime: new Date(device.createDate).getTime(),
              selected: false,
              // Initial set to offline state
              deviceStatus: 'offline'
            };
          })
            .sort((a, b) => a.rawBindTime - b.rawBindTime);
          this.activeSearchKeyword = "";
          this.searchKeyword = "";

          // After getting device list, immediately get device status
          this.fetchDeviceStatus(agentId);
        } else {
          this.$message.error(data.msg || this.$t('device.getListFailed'));
        }
      });
    },

    // Get device status
    fetchDeviceStatus(agentId) {
      // Enable table waiting state, handle dynamically loaded table headers causing mouseof current linehoverEvent cannot be removed issue
      this.loading = true;
      Api.device.getDeviceStatus(agentId, ({ data }) => {
        this.loading = false;
        if (data.code === 0) {
          try {
            // Parse device status returned by backendJSON
            const statusData = JSON.parse(data.data);

            // Directly use parsed data as device status mapping (no needdevicesfield wrapper)
            if (statusData && typeof statusData === 'object') {
              // Got device status successfully
              this.mqttServiceAvailable = true;
              // Update device status
              this.updateDeviceStatusFromResponse(statusData);
            } else {
              // Data format incorrect,MQTTService unavailable
              this.mqttServiceAvailable = false;
            }
          } catch (error) {
            // JSONParse failed,MQTTService unavailable
            this.mqttServiceAvailable = false;
          }
        } else {
          // API call failed,MQTTService unavailable
          this.mqttServiceAvailable = false;
        }
      });
    },

    // Based onAPIUpdate device status in response
    updateDeviceStatusFromResponse(deviceStatusMap) {
      this.deviceList.forEach(device => {
        // Build deviceMQTTClientID
        const macAddress = device.macAddress ? device.macAddress.replace(/:/g, '_') : 'unknown';
        const groupId = device.model ? device.model.replace(/:/g, '_') : 'GID_default';
        const mqttClientId = `${groupId}@@@${macAddress}@@@${macAddress}`;

        // Get device status from status map
        if (deviceStatusMap[mqttClientId]) {
          const statusInfo = deviceStatusMap[mqttClientId];

          let isOnline = false;
          if (statusInfo.isAlive === true) {
            isOnline = true;
          } else if (statusInfo.isAlive === false) {
            isOnline = false;
          } else if (statusInfo.isAlive === null && statusInfo.exists === true) {
            isOnline = true;
          } else {
            isOnline = false;
          }

          device.deviceStatus = isOnline ? 'online' : 'offline';
        } else {
          // If no matching status info found, default offline
          device.deviceStatus = 'offline';
        }
      });
    },
    headerCellClassName({ columnIndex }) {
      if (columnIndex === 0) {
        return "custom-selection-header";
      }
      return "";
    },
    getFirmwareTypeName(type) {
      const firmwareType = this.firmwareTypes.find(item => item.key === type)
      return firmwareType ? firmwareType.name : type
    },
    updateDeviceInfo(device_id, payload, callback) {
      return Api.device.updateDeviceInfo(device_id, payload, ({ data }) => {
        callback(data.code === 0, data);
      })
    },
    handleOtaSwitchChange(row) {
      this.updateDeviceInfo(row.device_id, { autoUpdate: row.otaSwitch ? 1 : 0 }, (result, { msg }) => {
        if (result) {
          this.$message.success(row.otaSwitch ? this.$t('device.autoUpdateEnabled') : this.$t('device.autoUpdateDisabled'));
          return;
        }
        row.otaSwitch = !row.otaSwitch
        this.$message.error(msg || this.$t('message.error'))
      })
    },
    // Determine whether emoji, theme, font can be generatedbinFile
    isGenerate(row) {
      // Guard null/undefined firmwareVersion: .replace on undefined threw inside the
      // table render -> the whole device table went blank (deep-audit). Treat unknown
      // firmware as not-generatable.
      const version = (row.firmwareVersion || '').replace(/\./g, '');
      return Number(version) >= 200;
    },
  }
};
</script>

<style scoped>
.welcome {
  min-width: 900px;
  min-height: 506px;
  height: 100vh;
  display: flex;
  position: relative;
  flex-direction: column;
  background: linear-gradient(to bottom right, #dce8ff, #e4eeff, #e6cbfd);
  background-size: cover;
  -webkit-background-size: cover;
  -o-background-size: cover;
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
  padding: 16px 24px;
}

.page-title {
  font-size: 24px;
  margin: 0;
  color: #2c3e50;
}

.right-operations {
  display: flex;
  gap: 10px;
  margin-left: auto;
}

.child-name-select {
  width: 230px;
}

.child-option-meta {
  float: right;
  color: #909399;
  font-size: 12px;
  margin-left: 12px;
}

.child-profile-view {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 4px;
  min-height: 42px;
  justify-content: center;
}

.child-profile-name {
  color: #2c3e50;
  font-weight: 600;
  cursor: pointer;
  word-break: break-word;
}

.child-profile-age {
  color: #909399;
  font-size: 12px;
}

.child-profile-edit {
  display: flex;
  flex-direction: column;
  gap: 6px;
  align-items: stretch;
}

.child-age-input {
  width: 100%;
}

.child-profile-actions {
  display: flex;
  justify-content: center;
  gap: 6px;
  flex-wrap: wrap;
}

.search-input {
  width: 280px;
  border-radius: 4px;
}

.btn-search {
  background: linear-gradient(135deg, #6b8cff, #a966ff);
  border: none;
  color: white;
}

::v-deep .search-input .el-input__inner {
  border-radius: 4px;
  border: 1px solid #DCDFE6;
  background-color: white;
  transition: border-color 0.2s;
}

::v-deep .page-size-select {
  width: 100px;
  margin-right: 8px;
}

::v-deep .page-size-select .el-input__inner {
  height: 32px;
  line-height: 32px;
  border-radius: 4px;
  border: 1px solid #e4e7ed;
  background: #dee7ff;
  color: #606266;
  font-size: 14px;
}

::v-deep .page-size-select .el-input__suffix {
  right: 6px;
  width: 15px;
  height: 20px;
  display: flex;
  justify-content: center;
  align-items: center;
  top: 6px;
  border-radius: 4px;
}

::v-deep .page-size-select .el-input__suffix-inner {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 100%;
}

::v-deep .page-size-select .el-icon-arrow-up:before {
  content: "";
  display: inline-block;
  border-left: 6px solid transparent;
  border-right: 6px solid transparent;
  border-top: 9px solid #606266;
  position: relative;
  transform: rotate(0deg);
  transition: transform 0.3s;
}

::v-deep .search-input .el-input__inner:focus {
  border-color: #6b8cff;
  outline: none;
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

.device-card {
  background: white;
  border: none;
  box-shadow: none;
  display: flex;
  flex-direction: column;
  flex: 1;
  overflow: hidden;
}

::v-deep .el-card__body {
  padding: 15px;
  display: flex;
  flex-direction: column;
  flex: 1;
  overflow: hidden;
}

.table_bottom {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-top: 10px;
  /* padding-bottom: 10px; */
}


.ctrl_btn {
  display: flex;
  gap: 8px;
  padding-left: 26px;
}

.ctrl_btn .el-button {
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
}

.ctrl_btn .el-button:hover {
  transform: translateY(-1px);
  box-shadow: 0 4px 8px rgba(0, 0, 0, 0.15);
}

.ctrl_btn .el-button--primary {
  background: #5f70f3;
  color: white;
}

.ctrl_btn .el-button--success {
  background: #5bc98c;
  color: white;
}

.ctrl_btn .el-button--danger {
  background: #fd5b63;
  color: white;
}

.custom-pagination {
  display: flex;
  align-items: center;
  gap: 10px;
}

.custom-pagination .el-select {
  margin-right: 8px;
}

.custom-pagination .pagination-btn:first-child,
.custom-pagination .pagination-btn:nth-child(2),
.custom-pagination .pagination-btn:nth-last-child(2),
.custom-pagination .pagination-btn:nth-child(3) {
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
}

.custom-pagination .pagination-btn:first-child:hover,
.custom-pagination .pagination-btn:nth-child(2):hover,
.custom-pagination .pagination-btn:nth-last-child(2):hover,
.custom-pagination .pagination-btn:nth-child(3):hover {
  background: #d7dce6;
}

.custom-pagination .pagination-btn:disabled {
  opacity: 0.6;
  cursor: not-allowed;
}

.custom-pagination .pagination-btn:not(:first-child):not(:nth-child(3)):not(:nth-child(2)):not(:nth-last-child(2)) {
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
}

.custom-pagination .pagination-btn:not(:first-child):not(:nth-child(3)):not(:nth-child(2)):not(:nth-last-child(2)):hover {
  background: rgba(245, 247, 250, 0.3);
}

.custom-pagination .pagination-btn.active {
  background: #5f70f3 !important;
  color: #ffffff !important;
  border-color: #5f70f3 !important;
}

.custom-pagination .pagination-btn.active:hover {
  background: #6d7cf5 !important;
}

.custom-pagination .total-text {
  color: #909399;
  font-size: 14px;
  margin-left: 10px;
}

:deep(.transparent-table) {
  background: white;
  border: none;
}

:deep(.transparent-table .el-table__header th) {
  background: white !important;
  color: black;
  border-right: none !important;
}

:deep(.transparent-table .el-table__body tr td) {
  border-top: 1px solid rgba(0, 0, 0, 0.04);
  border-bottom: 1px solid rgba(0, 0, 0, 0.04);
  border-right: none !important;
}

:deep(.transparent-table .el-table__header tr th:first-child .cell),
:deep(.transparent-table .el-table__body tr td:first-child .cell) {
  padding-left: 10px;
}

:deep(.el-icon-edit) {
  color: #7079aa;
  cursor: pointer;
}

:deep(.el-icon-edit:hover) {
  color: #5a64b5;
}

:deep(.custom-selection-header .el-checkbox) {
  display: none !important;
}


:deep(.el-table .el-button--text) {
  color: #7079aa;
}

:deep(.el-table .el-button--text:hover) {
  color: #5a64b5;
}

@media (max-width: 900px) {
  .welcome {
    min-width: 0;
  }

  .operation-bar {
    align-items: flex-start;
    flex-direction: column;
    gap: 12px;
  }

  .right-operations {
    width: 100%;
    margin-left: 0;
    flex-wrap: wrap;
  }

  .child-name-select,
  .search-input {
    width: 100%;
  }

  .btn-search {
    width: 100%;
  }

  .main-wrapper {
    margin: 0 12px;
  }
}

:deep(.transparent-table) {
  flex: 1;
  display: flex;
  flex-direction: column;
  /* max-height: calc(100vh - 40vh); */
}

:deep(.el-table__body-wrapper) {
  flex: 1;
  overflow-y: auto;
  max-height: none !important;
}

:deep(.el-table__header-wrapper) {
  flex-shrink: 0;
}

@media (min-width: 1144px) {
  .table_bottom {
    margin-top: 40px;
  }

  :deep(.transparent-table) .el-table__body tr td {
    padding-top: 16px;
    padding-bottom: 16px;
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

::v-deep .el-table--border::after,
::v-deep .el-table--group::after,
::v-deep .el-table::before {
  display: none !important;
}
</style>
