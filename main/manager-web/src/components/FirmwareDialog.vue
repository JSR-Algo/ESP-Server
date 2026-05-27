<template>
  <el-dialog :title="title" :visible.sync="dialogVisible" :close-on-click-modal="false" @close="handleClose"
    @open="handleOpen">
    <el-form ref="form" :model="form" :rules="rules" label-width="auto">
      <el-form-item :label="$t('firmwareDialog.firmwareName')" prop="firmwareName">
        <el-input v-model="form.firmwareName" :placeholder="$t('firmwareDialog.firmwareNamePlaceholder')"></el-input>
      </el-form-item>
      <el-form-item :label="$t('firmwareDialog.firmwareType')" prop="type">
        <el-select v-model="form.type" :placeholder="$t('firmwareDialog.firmwareTypePlaceholder')" style="width: 100%;"
          filterable :disabled="isTypeDisabled">
          <el-option v-for="item in firmwareTypes" :key="item.key" :label="item.name" :value="item.key"></el-option>
        </el-select>
      </el-form-item>
      <el-form-item :label="$t('firmwareDialog.version')" prop="version">
        <el-input v-model="form.version" :placeholder="$t('firmwareDialog.versionPlaceholder')"></el-input>
      </el-form-item>
      <el-form-item :label="$t('firmwareDialog.firmwareFile')" prop="firmwarePath">
        <el-upload ref="upload" class="upload-demo" action="#" :http-request="handleUpload"
          :before-upload="beforeUpload" :accept="'.bin,.apk,.wav'" :limit="1" :multiple="false" :auto-upload="true"
          :on-remove="handleRemove">
          <el-button size="small" type="primary">{{ $t('firmwareDialog.clickUpload') }}</el-button>
          <div slot="tip" class="el-upload__tip">{{ $t('firmwareDialog.uploadTip') }}</div>
        </el-upload>
        <el-progress v-if="isUploading || uploadStatus === 'success'" :percentage="uploadProgress"
          :status="uploadStatus"></el-progress>
        <div class="hint-text">
          <span>{{ $t('firmwareDialog.uploadHint') }}</span>
        </div>
      </el-form-item>
      <el-form-item :label="$t('firmwareDialog.remark')" prop="remark">
        <el-input type="textarea" v-model="form.remark"
          :placeholder="$t('firmwareDialog.remarkPlaceholder')"></el-input>
      </el-form-item>
    </el-form>
    <div slot="footer" class="dialog-footer">
      <el-button @click="handleCancel">{{ $t('button.cancel') }}</el-button>
      <el-button type="primary" @click="handleSubmit">{{ $t('button.save') }}</el-button>
    </div>
  </el-dialog>
</template>

<script>
import Api from '@/apis/api';

export default {
  name: 'FirmwareDialog',
  props: {
    visible: {
      type: Boolean,
      default: false
    },
    title: {
      type: String,
      default: ''
    },
    form: {
      type: Object,
      default: () => ({})
    },
    firmwareTypes: {
      type: Array,
      default: () => []
    }
  },

  data() {
    return {
      uploadProgress: 0,
      uploadStatus: '',
      isUploading: false,
      dialogVisible: this.visible,
      rules: {
        firmwareName: [
          { required: true, message: this.$t('firmwareDialog.requiredFirmwareName'), trigger: 'blur' }
        ],
        type: [
          { required: true, message: this.$t('firmwareDialog.requiredFirmwareType'), trigger: 'change' }
        ],
        version: [
          { required: true, message: this.$t('firmwareDialog.requiredVersion'), trigger: 'blur' },
          { pattern: /^\d+\.\d+\.\d+$/, message: this.$t('firmwareDialog.versionFormatError'), trigger: 'blur' }
        ],
        firmwarePath: [
          { required: false, message: this.$t('firmwareDialog.requiredFirmwareFile'), trigger: 'change' }
        ]
      }
    }
  },
  computed: {
    isTypeDisabled() {
      // If hasidIndicates edit mode, disable type selection
      return !!this.form.id
    }
  },
  created() {
    // Remove getDictDataByType Call
  },
  watch: {
    visible(val) {
      this.dialogVisible = val;
    },
    dialogVisible(val) {
      this.$emit('update:visible', val);
    },
  },
  methods: {
    // Remove getFirmwareTypes Method
    handleClose() {
      this.dialogVisible = false;
      this.$emit('cancel');
    },
    handleCancel() {
      this.$refs.form.clearValidate();
      this.$emit('cancel');
    },
    handleSubmit() {
      this.$refs.form.validate(valid => {
        if (valid) {
          // If add mode and no file uploaded, show error
          if (!this.form.id && !this.form.firmwarePath) {
            this.$message.error(this.$t('firmwareDialog.requiredFirmwareFile'))
            return
          }
          // After submit succeeds, leave dialog close logic to parent component
          this.$emit('submit', this.form)
        }
      })
    },
    beforeUpload(file) {
      const isValidSize = file.size / 1024 / 1024 < 100
      const isValidType = ['.bin', '.apk'].some(ext => file.name.toLowerCase().endsWith(ext))

      if (!isValidType) {
        this.$message.error(this.$t('firmwareDialog.invalidFileType'))
        return false
      }
      if (!isValidSize) {
        this.$message.error(this.$t('firmwareDialog.invalidFileSize'))
        return false
      }
      return true
    },
    handleUpload(options) {
      const { file } = options
      this.uploadProgress = 0
      this.uploadStatus = ''
      this.isUploading = true

      // UsesetTimeoutImplement simple0-50%Transition
      const timer = setTimeout(() => {
        if (this.uploadProgress < 50) {  // Only when progress less than50%Set only when
          this.uploadProgress = 50
        }
      }, 1000)

      Api.ota.uploadFirmware(file, (res) => {
        clearTimeout(timer)  // Clear timer
        res = res.data
        if (res.code === 0) {
          this.form.firmwarePath = res.data
          this.form.size = file.size
          this.uploadProgress = 100
          this.uploadStatus = 'success'
          this.$message.success(this.$t('firmwareDialog.uploadSuccess'))
          // Delay2Hide progress bar after seconds
          setTimeout(() => {
            this.isUploading = false
          }, 2000)
        } else {
          this.uploadStatus = 'exception'
          this.$message.error(res.msg || this.$t('firmwareDialog.uploadFailed'))
          this.isUploading = false
        }
      }, (progressEvent) => {
        if (progressEvent.total) {
          const progress = Math.round((progressEvent.loaded * 100) / progressEvent.total)
          // Only when progress greater than50%Update only when
          if (progress > 50) {
            this.uploadProgress = progress
          }
          // If upload complete but success response not received, keep progress bar displayed
          if (progress === 100) {
            this.uploadStatus = ''
          }
        }
      })
    },
    handleRemove() {
      this.form.firmwarePath = ''
      this.form.size = 0
      this.uploadProgress = 0
      this.uploadStatus = ''
      this.isUploading = false
    },
    handleOpen() {
      // Reset upload-related state
      this.uploadProgress = 0
      this.uploadStatus = ''
      this.isUploading = false
      // Reset file-related fields in form
      if (!this.form.id) {  // Reset only on add
        this.form.firmwarePath = ''
        this.form.size = 0
      }
      // Reset upload component regardless of edit mode
      this.$nextTick(() => {
        if (this.$refs.upload) {
          this.$refs.upload.clearFiles()
        }
      })
    }
  }
}
</script>

<style lang="scss" scoped>
::v-deep .el-dialog {
  border-radius: 20px;
}

.upload-demo {
  text-align: left;
}

.el-upload__tip {
  line-height: 1.2;
  padding-top: 2%;
  color: #909399;
}

.hint-text {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 14px;
}
</style>