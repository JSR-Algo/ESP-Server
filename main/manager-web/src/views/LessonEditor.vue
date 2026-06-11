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
          <el-table-column :label="$t('lesson.colChoices')" min-width="160">
            <template slot-scope="scope">
              <span v-if="scope.row.choices && scope.row.choices.length" class="small">
                <el-tag
                  v-for="c in scope.row.choices"
                  :key="c.id || c.label"
                  size="mini"
                  :type="c.isCorrect ? 'success' : 'info'"
                  effect="plain"
                  style="margin: 0 4px 2px 0"
                >{{ c.label }}</el-tag>
              </span>
              <span v-else class="muted">—</span>
            </template>
          </el-table-column>
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
          <el-button type="primary" size="small" icon="el-icon-plus" @click="openStepDialog">{{ $t('lesson.addStepTitle') }}</el-button>
        </div>
        <p v-else class="muted">{{ $t('lesson.draftOnly') }}</p>
      </el-card>

      <!-- Asset authoring (draft only): layer + role + stable assetKey + critical
           + robot pose picker + per-session preview list. -->
      <LessonAssetManager v-if="isDraft" :lesson-id="lessonId" :subject-hint="lastSubject" />
    </div>

    <!-- Step editor dialog (draft only) -->
    <el-dialog :title="$t('lesson.addStepTitle')" :visible.sync="stepDialogVisible" width="560px" :close-on-click-modal="false">
      <el-form label-position="top" size="small">
        <el-form-item :label="$t('lesson.stepType')" required>
          <el-select v-model="stepForm.stepType" :placeholder="$t('lesson.stepType')" style="width: 100%">
            <el-option-group :label="$t('lesson.stepTypePassive')">
              <el-option v-for="t in passiveStepTypes" :key="t.stepType" :label="t.stepType + (t.isBuiltin ? '' : ' (author)')" :value="t.stepType" />
            </el-option-group>
            <el-option-group :label="$t('lesson.stepTypeInteractive')">
              <el-option v-for="t in interactiveStepTypes" :key="t.stepType" :label="t.stepType + (t.isBuiltin ? '' : ' (author)')" :value="t.stepType" />
            </el-option-group>
          </el-select>
        </el-form-item>
        <el-form-item :label="$t('lesson.prompt')" required>
          <el-input v-model="stepForm.prompt" type="textarea" :rows="2" :placeholder="$t('lesson.prompt')" />
          <span v-if="isChoiceStep" class="muted small">{{ $t('lesson.blankHint') }}</span>
        </el-form-item>
        <el-form-item :label="$t('lesson.subjectLabel')" required>
          <el-input v-model="stepForm.subject" :placeholder="$t('lesson.subjectLabel')" />
        </el-form-item>
        <el-form-item :label="$t('lesson.helperText')">
          <el-input v-model="stepForm.helperText" :placeholder="$t('lesson.helperText')" />
        </el-form-item>
        <el-form-item :label="$t('lesson.l1TransferHint')">
          <el-input v-model="stepForm.l1TransferHint" :placeholder="$t('lesson.l1TransferHint')" />
        </el-form-item>

        <!-- Choices editor (fillBlank): single-correct enforced client-side -->
        <el-form-item v-if="isChoiceStep" :label="$t('lesson.choices')">
          <el-radio-group v-model="correctChoiceId" class="choice-group">
            <div v-for="(c, i) in stepForm.choices" :key="c.id" class="choice-row">
              <el-radio :label="c.id">{{ $t('lesson.choiceCorrect') }}</el-radio>
              <el-input v-model="c.label" :placeholder="$t('lesson.choiceLabel') + ' ' + (i + 1)" size="small" style="width: 240px" />
              <el-button type="text" class="danger-text" :disabled="stepForm.choices.length <= 1" @click="removeChoice(i)">{{ $t('lesson.removeChoice') }}</el-button>
            </div>
          </el-radio-group>
          <el-button type="text" icon="el-icon-plus" @click="addChoice">{{ $t('lesson.addChoice') }}</el-button>
        </el-form-item>
      </el-form>
      <span slot="footer">
        <el-button size="small" @click="stepDialogVisible = false">{{ $t('lesson.cancel') }}</el-button>
        <el-button type="primary" size="small" :loading="addingStep" @click="addStep">{{ $t('lesson.save') }}</el-button>
      </span>
    </el-dialog>

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
import LessonAssetManager from '@/components/LessonAssetManager.vue';
import Api from '@/apis/api';

export default {
  name: 'LessonEditor',
  components: { HeaderBar, LessonAssetManager },
  data() {
    return {
      lesson: null,
      steps: [],
      stepTypes: [],
      loading: false,
      reordering: false,
      addingStep: false,
      validating: false,
      previewing: false,
      publishing: false,
      renaming: false,
      stepDialogVisible: false,
      stepForm: { stepType: '', prompt: '', subject: '', helperText: '', l1TransferHint: '', choices: [] },
      correctChoiceId: '',
      lastSubject: '',
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
    passiveStepTypes() {
      return this.stepTypes.filter((t) => t.completionClass !== 'interactive');
    },
    interactiveStepTypes() {
      return this.stepTypes.filter((t) => t.completionClass === 'interactive');
    },
    // Only fillBlank carries choices today; extend if more choice types register.
    isChoiceStep() {
      return this.stepForm.stepType === 'fillBlank';
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
    openStepDialog() {
      const firstId = 'c1';
      this.stepForm = {
        stepType: this.stepTypes.length ? this.stepTypes[0].stepType : '',
        prompt: '',
        subject: this.lastSubject || '',
        helperText: '',
        l1TransferHint: '',
        choices: [{ id: firstId, label: '' }, { id: 'c2', label: '' }],
      };
      this.correctChoiceId = firstId;
      this.stepDialogVisible = true;
    },
    addChoice() {
      const id = 'c' + (this.stepForm.choices.length + 1);
      this.stepForm.choices.push({ id, label: '' });
    },
    removeChoice(index) {
      const removed = this.stepForm.choices[index];
      this.stepForm.choices.splice(index, 1);
      if (removed && removed.id === this.correctChoiceId) {
        this.correctChoiceId = this.stepForm.choices.length ? this.stepForm.choices[0].id : '';
      }
    },
    addStep() {
      const f = this.stepForm;
      if (!f.stepType || !f.prompt || !f.subject) {
        this.$message.warning(this.$t('lesson.stepRequired'));
        return;
      }
      const payload = {
        stepType: f.stepType,
        prompt: f.prompt,
        subject: f.subject,
        helperText: f.helperText,
        l1TransferHint: f.l1TransferHint,
      };
      // fillBlank: build {id,label,isCorrect} with single-correct enforced client-side
      if (this.isChoiceStep) {
        const rows = f.choices.filter((c) => (c.label || '').trim());
        if (rows.length < 2 || !rows.some((c) => c.id === this.correctChoiceId)) {
          this.$message.warning(this.$t('lesson.fillBlankNeedsChoices'));
          return;
        }
        payload.choices = rows.map((c, i) => ({
          id: c.id || ('c' + (i + 1)),
          label: c.label.trim(),
          isCorrect: c.id === this.correctChoiceId,
        }));
      }
      this.addingStep = true;
      Api.lesson.createStep(
        this.lessonId,
        payload,
        () => {
          this.addingStep = false;
          this.stepDialogVisible = false;
          this.lastSubject = f.subject; // prefill next step + teachingObject asset key
          this.fetchSteps();
        },
        (msg) => { this.addingStep = false; this.$message.error(msg); },
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
.mono { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; word-break: break-all; }
.small { font-size: 12px; }
.muted { color: #909399; }
.danger-text { color: #f56c6c; }
.preview-card { margin-bottom: 16px; }
.choice-group { display: block; }
.choice-row { display: flex; align-items: center; gap: 10px; margin-bottom: 8px; }
</style>
