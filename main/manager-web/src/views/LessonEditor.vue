<template>
  <div class="welcome">
    <HeaderBar />

    <!-- Lesson header -->
    <div class="operation-bar">
      <div class="left-title">
        <el-button type="text" icon="el-icon-arrow-left" @click="$router.back()">
          {{ $t('lesson.pageTitle') }}
        </el-button>
        <h2 class="page-title" v-if="lesson">
          {{ lesson.title }}
          <span class="muted">({{ lesson.lessonKey }})</span>
          <el-tag :type="statusType(lesson.status)" size="small" style="margin-left: 8px">
            {{ lesson.status }}
          </el-tag>
          <span class="muted">v{{ lesson.lessonVersion }}</span>
        </h2>
      </div>
      <div class="right-operations" v-if="lesson">
        <el-button v-if="isDraft" size="small" @click="openRename">{{ $t('lesson.rename') }}</el-button>
        <el-button size="small" @click="doValidate" :loading="validating">{{ $t('lesson.validate') }}</el-button>
        <el-button size="small" @click="doPreview" :loading="previewing">{{ $t('lesson.previewManifest') }}</el-button>
        <el-button v-if="isDraft" type="primary" size="small" @click="doPublish" :loading="publishing">
          {{ $t('lesson.publish') }}
        </el-button>
      </div>
    </div>

    <div class="main-wrapper" v-loading="loading">
      <!-- Publish / preview result -->
      <el-alert
        v-if="publishMessage"
        :title="publishMessage"
        type="success"
        :closable="false"
        show-icon
        style="margin-bottom: 12px"
      />
      <el-card v-if="preview" shadow="never" class="preview-card">
        <div class="kv"><span class="muted">{{ $t('lesson.checksum') }}</span><span class="mono">{{ preview.checksum }}</span></div>
        <div class="kv"><span class="muted">etag</span><span class="mono">{{ preview.etag }}</span></div>
      </el-card>

      <!-- Steps -->
      <el-card shadow="never" class="content-area">
        <div slot="header" class="card-header">{{ $t('lesson.steps') }} ({{ steps.length }})</div>
        <el-table :data="steps" stripe style="width: 100%">
          <el-table-column :label="'#'" width="60" align="center">
            <template slot-scope="scope">{{ scope.$index + 1 }}</template>
          </el-table-column>
          <el-table-column prop="stepType" :label="$t('lesson.stepType')" width="130" />
          <el-table-column prop="prompt" :label="$t('lesson.prompt')" min-width="200" />
          <el-table-column prop="subject" :label="$t('lesson.subject')" width="120" />
          <el-table-column :label="$t('lesson.renderTriple')" min-width="170">
            <template slot-scope="scope">
              <span class="muted mono small">{{ scope.row.robotState }}/{{ scope.row.pose }}/{{ scope.row.phase }}</span>
            </template>
          </el-table-column>
          <el-table-column v-if="isDraft" :label="$t('lesson.colActions')" width="180">
            <template slot-scope="scope">
              <el-button type="text" size="small" :disabled="scope.$index === 0 || reordering" @click="moveStep(scope.$index, -1)">↑</el-button>
              <el-button type="text" size="small" :disabled="scope.$index === steps.length - 1 || reordering" @click="moveStep(scope.$index, 1)">↓</el-button>
              <el-button type="text" size="small" class="danger-text" @click="deleteStep(scope.row)">{{ $t('lesson.deleteStep') }}</el-button>
            </template>
          </el-table-column>
          <template slot="empty"><span class="muted">{{ $t('lesson.noSteps') }}</span></template>
        </el-table>

        <!-- Add step (draft only) -->
        <div v-if="isDraft" class="add-row">
          <el-select v-model="stepForm.stepType" :placeholder="$t('lesson.stepType')" size="small" style="width: 160px">
            <el-option v-for="t in stepTypes" :key="t.stepType" :label="t.stepType + (t.isBuiltin ? '' : ' (author)')" :value="t.stepType" />
          </el-select>
          <el-input v-model="stepForm.prompt" :placeholder="$t('lesson.prompt')" size="small" style="width: 220px" />
          <el-input v-model="stepForm.subject" :placeholder="$t('lesson.subject')" size="small" style="width: 140px" />
          <el-button type="primary" size="small" :loading="addingStep" @click="addStep">{{ $t('lesson.addStep') }}</el-button>
        </div>
        <p v-else class="muted">{{ $t('lesson.draftOnly') }}</p>
      </el-card>

      <!-- Asset upload (draft only) — layer selectable so a playable bundle
           (critical backgroundScene + teachingObject) can be built from the UI -->
      <el-card v-if="isDraft" shadow="never" class="content-area">
        <div slot="header" class="card-header">{{ $t('lesson.assets') }}</div>
        <div class="add-row">
          <el-select v-model="uploadLayer" size="small" style="width: 180px">
            <el-option label="backgroundScene" value="backgroundScene" />
            <el-option label="teachingObject" value="teachingObject" />
            <el-option label="robotOverlay" value="robotOverlay" />
          </el-select>
          <el-upload
            ref="uploader"
            action="#"
            :auto-upload="false"
            :show-file-list="true"
            :limit="1"
            accept="image/*"
            :on-change="onFilePick"
            :on-remove="() => { pickedFile = null }"
          >
            <el-button size="small">{{ $t('lesson.pickImage') }}</el-button>
          </el-upload>
          <el-button type="primary" size="small" :loading="uploading" :disabled="!pickedFile" @click="uploadAsset">
            {{ $t('lesson.uploadAsset') }}
          </el-button>
          <span class="muted small">{{ $t('lesson.assetHint') }}</span>
        </div>
        <div v-if="lastAsset" class="kv-block">
          <div class="kv"><span class="muted">layer</span><span>{{ lastAsset.layer || uploadLayer }}</span></div>
          <div class="kv"><span class="muted">sha256 (server)</span><span class="mono">{{ lastAsset.sha256 }}</span></div>
        </div>
      </el-card>
    </div>

    <!-- Rename dialog -->
    <el-dialog :title="$t('lesson.rename')" :visible.sync="renameVisible" width="420px">
      <el-input v-model="titleDraft" :placeholder="$t('lesson.colTitle')" size="small" />
      <span slot="footer">
        <el-button size="small" @click="renameVisible = false">{{ $t('course.cancel') }}</el-button>
        <el-button type="primary" size="small" :loading="renaming" @click="doRename">{{ $t('course.save') }}</el-button>
      </span>
    </el-dialog>
  </div>
</template>

<script>
import HeaderBar from '@/components/HeaderBar.vue';
import Api from '@/apis/api';

const ROLE_BY_LAYER = { backgroundScene: 'poster', teachingObject: 'primarySubject', robotOverlay: 'pose' };

export default {
  name: 'LessonEditor',
  components: { HeaderBar },
  data() {
    return {
      lesson: null,
      steps: [],
      stepTypes: [],
      loading: false,
      reordering: false,
      addingStep: false,
      uploading: false,
      validating: false,
      previewing: false,
      publishing: false,
      renaming: false,
      stepForm: { stepType: '', prompt: '', subject: '' },
      uploadLayer: 'backgroundScene',
      pickedFile: null,
      lastAsset: null,
      preview: null,
      publishMessage: '',
      renameVisible: false,
      titleDraft: '',
    };
  },
  computed: {
    lessonId() {
      return this.$route.query.lessonId;
    },
    isDraft() {
      return this.lesson && this.lesson.status === 'draft';
    },
  },
  created() {
    if (!this.lessonId) {
      this.$router.replace('/course-management');
      return;
    }
    this.fetchAll();
  },
  methods: {
    statusType(status) {
      if (status === 'published') return 'success';
      if (status === 'archived') return 'info';
      return 'warning';
    },
    fetchAll() {
      this.loading = true;
      Api.lesson.getLesson(
        this.lessonId,
        (l) => {
          this.lesson = l;
          this.loading = false;
          this.fetchSteps();
        },
        (msg) => {
          this.loading = false;
          this.$message.error(msg);
        },
      );
      Api.lesson.listStepTypes(
        (types) => {
          this.stepTypes = types;
          if (!this.stepForm.stepType && types.length) this.stepForm.stepType = types[0].stepType;
        },
        () => {},
      );
    },
    fetchSteps() {
      Api.lesson.listSteps(this.lessonId, (rows) => { this.steps = rows; }, (msg) => this.$message.error(msg));
    },
    moveStep(index, delta) {
      const target = index + delta;
      if (target < 0 || target >= this.steps.length) return;
      const order = this.steps.map((s) => s.stepKey);
      const tmp = order[index];
      order[index] = order[target];
      order[target] = tmp;
      this.reordering = true;
      Api.lesson.reorderSteps(
        this.lessonId,
        order,
        (rows) => { this.reordering = false; this.steps = rows; },
        (msg) => { this.reordering = false; this.$message.error(msg); },
      );
    },
    deleteStep(row) {
      this.$confirm(this.$t('lesson.deleteStepConfirm', { key: row.stepKey }), this.$t('lesson.deleteStep'), { type: 'warning' })
        .then(() => {
          Api.lesson.deleteStep(this.lessonId, row.stepKey, (rows) => { this.steps = rows; }, (msg) => this.$message.error(msg));
        })
        .catch(() => {});
    },
    addStep() {
      const f = this.stepForm;
      if (!f.stepType || !f.prompt || !f.subject) {
        this.$message.warning(this.$t('lesson.stepRequired'));
        return;
      }
      this.addingStep = true;
      Api.lesson.createStep(
        this.lessonId,
        { stepType: f.stepType, prompt: f.prompt, subject: f.subject },
        () => {
          this.addingStep = false;
          this.stepForm.prompt = '';
          this.stepForm.subject = '';
          this.fetchSteps();
        },
        (msg) => { this.addingStep = false; this.$message.error(msg); },
      );
    },
    onFilePick(file) {
      this.pickedFile = file.raw || file;
    },
    uploadAsset() {
      if (!this.pickedFile) return;
      this.uploading = true;
      const layer = this.uploadLayer;
      const fields = {
        profile: 'espTft',
        layer,
        role: ROLE_BY_LAYER[layer] || 'primarySubject',
        critical: 'true',
        assetKey: layer + '.' + Date.now().toString(36),
      };
      Api.lesson.uploadAsset(
        this.lessonId,
        this.pickedFile,
        fields,
        (asset) => {
          this.uploading = false;
          const a = asset || {};
          this.lastAsset = a.asset ? { ...a.asset, layer } : { ...a, layer };
          this.pickedFile = null;
          if (this.$refs.uploader) this.$refs.uploader.clearFiles();
          this.$message.success(this.$t('lesson.uploadOk'));
        },
        (msg) => { this.uploading = false; this.$message.error(msg); },
      );
    },
    openRename() {
      this.titleDraft = this.lesson ? this.lesson.title : '';
      this.renameVisible = true;
    },
    doRename() {
      if (!this.titleDraft) return;
      this.renaming = true;
      Api.lesson.updateLesson(
        this.lessonId,
        { title: this.titleDraft },
        (l) => { this.renaming = false; this.renameVisible = false; this.lesson = l; this.$message.success(this.$t('lesson.renamed')); },
        (msg) => { this.renaming = false; this.$message.error(msg); },
      );
    },
    doValidate() {
      this.validating = true;
      Api.lesson.validate(
        this.lessonId,
        (res) => {
          this.validating = false;
          if (res && res.valid) this.$message.success(this.$t('lesson.validOk', { profiles: (res.profiles || []).join(', ') }));
          else this.$message.warning(this.$t('lesson.validFail'));
        },
        (msg) => { this.validating = false; this.$message.error(msg); },
      );
    },
    doPreview() {
      this.previewing = true;
      Api.lesson.manifestPreview(
        this.lessonId,
        'espTft',
        (res) => { this.previewing = false; this.preview = { checksum: res.checksum, etag: res.etag }; },
        (msg) => { this.previewing = false; this.$message.error(msg); },
      );
    },
    doPublish() {
      this.$confirm(this.$t('lesson.publishConfirm'), this.$t('lesson.publish'), { type: 'warning' })
        .then(() => {
          this.publishing = true;
          Api.lesson.publish(
            this.lessonId,
            (res) => {
              this.publishing = false;
              this.publishMessage = this.$t('lesson.publishedMsg', { v: res.lessonVersion, checksum: res.checksum });
              this.fetchAll(); // flip the badge to published + lock controls
            },
            (msg) => { this.publishing = false; this.$message.error(msg); },
          );
        })
        .catch(() => {});
    },
  },
};
</script>

<style lang="scss" scoped>
.operation-bar {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 16px 24px 0;
  flex-wrap: wrap;
  gap: 8px;
}
.left-title { display: flex; align-items: center; gap: 12px; }
.page-title { margin: 0; font-size: 18px; }
.right-operations { display: flex; align-items: center; gap: 8px; }
.main-wrapper { padding: 16px 24px; }
.content-area { margin-bottom: 16px; }
.card-header { font-weight: 600; }
.add-row { display: flex; align-items: center; gap: 10px; margin-top: 14px; flex-wrap: wrap; }
.kv { display: flex; gap: 10px; padding: 2px 0; }
.kv .muted { width: 130px; display: inline-block; }
.kv-block { margin-top: 12px; }
.mono { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; word-break: break-all; }
.small { font-size: 12px; }
.muted { color: #909399; }
.danger-text { color: #f56c6c; }
.preview-card { margin-bottom: 16px; }
</style>
