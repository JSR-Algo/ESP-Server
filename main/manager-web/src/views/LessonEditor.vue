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
        <el-button v-if="lessonCapabilities.exactEspTftPreview" size="small" @click="doPreview" :loading="previewing">{{ $t('lesson.previewManifest') }}</el-button>
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
      <section v-if="canonicalDemo && canonicalDemo.adminPreview" class="canonical-demo" data-testid="canonical-source-demo">
        <div class="canonical-demo__copy">
          <span class="eyebrow">SOURCE / ADMIN DEMO</span>
          <h3>Original farm scene</h3>
          <p>The video is an author reference only. The exact robot preview uses the pinned static poster and overlays.</p>
          <span class="mono small">{{ canonicalDemo.sourceFolder }}/{{ canonicalDemo.adminPreview.sourcePath }}</span>
          <div class="canonical-demo__sources" aria-label="Canonical source assets">
            <img v-for="asset in canonicalDemo.sourceAssets" :key="asset.sourcePath" data-testid="canonical-source-asset" :src="asset.url" :alt="asset.sourcePath" />
          </div>
        </div>
        <video
          data-testid="canonical-source-video"
          muted
          controls
          playsinline
          preload="metadata"
          :poster="canonicalDemo.adminPreview.posterUrl"
          :src="canonicalDemo.adminPreview.url"
        />
      </section>

      <section v-if="lesson" class="lesson-studio">
        <LessonStepNavigator v-model="selectedStepIndex" :steps="steps" :editable="isDraft" @add="openStepDialog" />
        <main class="lesson-studio__canvas">
          <div class="lesson-studio__toolbar">
            <div>
              <span class="eyebrow">VISUAL LESSON BUILDER</span>
              <h3>{{ selectedStep ? selectedStep.prompt : 'Choose or add a lesson step' }}</h3>
            </div>
            <el-button v-if="isDraft && selectedStep" type="primary" size="small" :loading="savingSelectedStep" :disabled="!selectedStepDirty" @click="saveSelectedStep">
              Save step
            </el-button>
          </div>
          <div v-if="selectedStep" class="lesson-studio__workbench">
            <div>
              <el-card shadow="never" class="step-content-panel">
                <div slot="header"><strong>Step content</strong></div>
                <el-form label-position="top" size="small">
                  <el-form-item :label="$t('lesson.prompt')" required>
                    <el-input :value="selectedContent.prompt" data-testid="lesson-step-prompt" type="textarea" :rows="2" :disabled="!isDraft" @input="updateSelectedContent('prompt', $event)" />
                  </el-form-item>
                  <div class="grid-2">
                    <el-form-item :label="$t('lesson.subjectLabel')" required>
                      <el-input :value="selectedContent.subject" data-testid="lesson-step-subject" :disabled="!isDraft" @input="updateSelectedContent('subject', $event)" />
                    </el-form-item>
                    <el-form-item :label="$t('lesson.helperText')">
                      <el-input :value="selectedContent.helperText" data-testid="lesson-step-helper" :disabled="!isDraft" @input="updateSelectedContent('helperText', $event)" />
                    </el-form-item>
                  </div>
                  <el-form-item :label="$t('lesson.l1TransferHint')">
                    <el-input :value="selectedContent.l1TransferHint" data-testid="lesson-step-l1-hint" :disabled="!isDraft" @input="updateSelectedContent('l1TransferHint', $event)" />
                  </el-form-item>
                </el-form>
              </el-card>
              <LessonInteractionPanel v-model="selectedAuthoring" :disabled="!isDraft" />
              <SharedAssetPicker
                v-if="lessonCapabilities.sharedVisualAuthoring"
                :assets="sharedVisualAssets"
                :selected-key="selectedObjectKey"
                category="teachingObject"
                @select="selectSharedAsset"
                @inspect="inspectSharedAsset"
                @clone="cloneSharedAsset"
              />
            </div>
            <RobotLessonPreview
              v-if="lessonCapabilities.exactEspTftPreview && previewManifest"
              :manifest="previewManifest"
              :step-index="selectedStepIndex"
              initial-path="correct"
              @path-change="onPreviewPathChange"
            />
            <div v-else-if="lessonCapabilities.exactEspTftPreview" class="preview-empty">
              <strong>Robot preview</strong>
              <span>Generate the espTft manifest preview to inspect the exact 480×320 scene.</span>
              <el-button size="small" @click="doPreview">Generate preview</el-button>
            </div>
          </div>
          <LessonEngagementTrack :steps="studioSteps" @select="selectedStepIndex = $event" />
          <LessonPublishReadiness :steps="studioSteps" :assets="sharedVisualAssets" :manifest="previewManifest || {}" :validation="validationResult" />
        </main>
      </section>

      <!-- Steps -->
      <el-card shadow="never" class="content-area">
        <div slot="header" class="card-header">Advanced step structure · {{ steps.length }} steps</div>
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
          <el-table-column :label="$t('lesson.renderTriple')" min-width="200">
            <template slot-scope="scope">
              <span class="muted mono small">{{ scope.row.robotState }}/{{ scope.row.pose }}/{{ scope.row.phase }}</span>
              <el-tag v-if="isExpressionOverride(scope.row)" size="mini" type="warning" effect="plain" style="margin-left: 6px">
                {{ scope.row.expression }}
              </el-tag>
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
      <LessonAssetManager v-if="isDraft" :lesson-id="lessonId" :subject-hint="lastSubject" @assets-loaded="onAssetsLoaded" />
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

        <!-- Robot expression override (face only; default empty = auto from stepType) -->
        <el-form-item :label="$t('lesson.renderExpression')">
          <el-select v-model="stepForm.renderExpression" clearable :placeholder="$t('lesson.renderExpressionAuto')" style="width: 100%">
            <el-option v-for="e in expressionOptions" :key="e.value" :label="e.label" :value="e.value" />
          </el-select>
          <span class="muted small">{{ $t('lesson.renderExpressionHint') }}</span>
        </el-form-item>

        <!-- Vocabulary (stored under stepBody.vocab; author-metadata, firmware-inert) -->
        <el-divider content-position="left">{{ $t('lesson.vocabSection') }}</el-divider>
        <div class="grid-2">
          <el-form-item :label="$t('lesson.vocabWord')">
            <el-input v-model="stepForm.vocab.word" :placeholder="$t('lesson.vocabWord')" size="small" />
          </el-form-item>
          <el-form-item :label="$t('lesson.vocabIpa')">
            <el-input v-model="stepForm.vocab.ipa" placeholder="/bɑːrn/" size="small" />
          </el-form-item>
          <el-form-item :label="$t('lesson.vocabPos')">
            <el-select v-model="stepForm.vocab.partOfSpeech" clearable :placeholder="$t('lesson.vocabPos')" size="small" style="width: 100%">
              <el-option v-for="p in partsOfSpeech" :key="p" :label="$t('lesson.pos_' + p)" :value="p" />
            </el-select>
          </el-form-item>
          <el-form-item :label="$t('lesson.vocabTranslation')">
            <el-input v-model="stepForm.vocab.translationVi" :placeholder="$t('lesson.vocabTranslation')" size="small" />
          </el-form-item>
        </div>
        <el-form-item :label="$t('lesson.vocabDefinition')">
          <el-input v-model="stepForm.vocab.definition" type="textarea" :rows="2" :placeholder="$t('lesson.vocabDefinition')" />
        </el-form-item>
        <el-form-item :label="$t('lesson.vocabExamples')">
          <div v-for="(ex, i) in stepForm.vocab.examples" :key="'ex' + i" class="choice-row">
            <el-input v-model="ex.text" :placeholder="$t('lesson.vocabExampleText')" size="small" style="flex: 1" />
            <el-input v-model="ex.translation" :placeholder="$t('lesson.vocabExampleTranslation')" size="small" style="flex: 1" />
            <el-button type="text" class="danger-text" @click="removeExample(i)">{{ $t('lesson.removeExample') }}</el-button>
          </div>
          <el-button type="text" icon="el-icon-plus" @click="addExample">{{ $t('lesson.addExample') }}</el-button>
        </el-form-item>

        <!-- Scene composer (builds stepBody.scene from lifted bundle assets) -->
        <el-divider content-position="left">{{ $t('lesson.sceneSection') }}</el-divider>
        <span v-if="!hasBundleAssets" class="muted small">{{ $t('lesson.sceneNoAssets') }}</span>

        <el-form-item :label="$t('lesson.backgroundScene')">
          <el-select v-model="stepForm.scene.backgroundKey" clearable :placeholder="$t('lesson.sceneNone')" style="width: 100%">
            <el-option v-for="a in backgroundAssets" :key="a.assetKey" :label="a.assetKey" :value="a.assetKey" />
          </el-select>
        </el-form-item>
        <div class="grid-2" v-if="stepForm.scene.backgroundKey">
          <el-form-item :label="$t('lesson.altCaption')">
            <el-input v-model="stepForm.scene.altCaption" :placeholder="$t('lesson.altCaption')" size="small" />
          </el-form-item>
          <el-form-item :label="$t('lesson.fit')">
            <el-select v-model="stepForm.scene.fit" size="small" style="width: 100%">
              <el-option label="cover" value="cover" />
              <el-option label="contain" value="contain" />
            </el-select>
          </el-form-item>
        </div>

        <el-form-item :label="$t('lesson.teachingObject')">
          <el-select v-model="stepForm.scene.objectKey" clearable :placeholder="$t('lesson.sceneNone')" style="width: 100%">
            <el-option v-for="a in teachingObjectAssets" :key="a.assetKey" :label="a.assetKey" :value="a.assetKey" />
          </el-select>
        </el-form-item>
        <div class="grid-2" v-if="stepForm.scene.objectKey">
          <el-form-item :label="$t('lesson.primaryWord')">
            <el-input v-model="stepForm.scene.primaryWord" :placeholder="$t('lesson.primaryWord')" size="small" />
          </el-form-item>
          <el-form-item :label="$t('lesson.placement')">
            <el-select v-model="stepForm.scene.placementAnchor" size="small" style="width: 100%">
              <el-option label="center" value="center" />
              <el-option label="top" value="top" />
              <el-option label="bottom" value="bottom" />
            </el-select>
          </el-form-item>
        </div>
        <el-form-item v-if="stepForm.scene.objectKey" :label="$t('lesson.supportWords')">
          <el-select v-model="stepForm.scene.supportWords" multiple filterable allow-create default-first-option :placeholder="$t('lesson.supportWords')" style="width: 100%">
            <el-option v-for="w in stepForm.scene.supportWords" :key="w" :label="w" :value="w" />
          </el-select>
        </el-form-item>

        <!-- focusTarget: model step only (matches seed s4); clamped [0,1] + tStart<tEnd guard -->
        <template v-if="stepForm.stepType === 'model'">
          <div class="muted small focus-title">{{ $t('lesson.focusTarget') }}</div>
          <div v-for="(w, i) in stepForm.scene.activeWindows" :key="'win' + i" class="focus-row">
            <el-input-number v-model="w.tStart" :min="0" :step="0.1" size="mini" controls-position="right" :placeholder="'tStart'" />
            <el-input-number v-model="w.tEnd" :min="0" :step="0.1" size="mini" controls-position="right" :placeholder="'tEnd'" />
            <el-input-number v-model="w.x" :min="0" :max="1" :step="0.01" size="mini" controls-position="right" :placeholder="'x'" />
            <el-input-number v-model="w.y" :min="0" :max="1" :step="0.01" size="mini" controls-position="right" :placeholder="'y'" />
            <el-input-number v-model="w.w" :min="0" :max="1" :step="0.01" size="mini" controls-position="right" :placeholder="'w'" />
            <el-input-number v-model="w.h" :min="0" :max="1" :step="0.01" size="mini" controls-position="right" :placeholder="'h'" />
            <el-button type="text" class="danger-text" @click="removeWindow(i)">{{ $t('lesson.removeChoice') }}</el-button>
          </div>
          <el-button type="text" icon="el-icon-plus" @click="addWindow">{{ $t('lesson.addWindow') }}</el-button>
          <div class="grid-2">
            <el-form-item :label="$t('lesson.successUtterance')">
              <el-input v-model="stepForm.scene.successUtterance" size="small" />
            </el-form-item>
            <el-form-item :label="$t('lesson.missUtterance')">
              <el-input v-model="stepForm.scene.missUtterance" size="small" />
            </el-form-item>
          </div>
          <span class="muted small">{{ $t('lesson.focusHint') }}</span>
        </template>

        <el-form-item :label="$t('lesson.timeoutSec')">
          <el-input-number v-model="stepForm.scene.timeoutSec" :min="1" :max="120" size="small" />
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
import LessonEngagementTrack from '@/components/lesson/LessonEngagementTrack.vue';
import LessonInteractionPanel from '@/components/lesson/LessonInteractionPanel.vue';
import LessonPublishReadiness from '@/components/lesson/LessonPublishReadiness.vue';
import LessonStepNavigator from '@/components/lesson/LessonStepNavigator.vue';
import RobotLessonPreview from '@/components/lesson/RobotLessonPreview.vue';
import SharedAssetPicker from '@/components/lesson/SharedAssetPicker.vue';
import { mergeAuthoringFields } from '@/components/lesson/lesson-builder-logic';
import {
  addChoice as appendStepChoice,
  buildCreateStepPayload,
  buildSaveStepRequest,
  createLessonStepEditorState,
  createStepDialogState,
  removeChoice as removeStepChoice,
  resolveSaveSuccess,
} from '@/components/lesson/lesson-step-editor-state';
import Api from '@/apis/api';
import { loadLessonRolloutCapabilities, NO_LESSON_ROLLOUT_CAPABILITIES } from '@/utils/lessonRolloutCapabilities';
import { loadCanonicalDemoContext } from '@/utils/canonicalDemoContext.mjs';
import { createAuthoringDirtyHandle } from '@/utils/serviceWorkerUpdateSafety.mjs';

export default {
  name: 'LessonEditor',
  components: {
    HeaderBar,
    LessonAssetManager,
    LessonEngagementTrack,
    LessonInteractionPanel,
    LessonPublishReadiness,
    LessonStepNavigator,
    RobotLessonPreview,
    SharedAssetPicker,
  },
  data() {
    const stepEditor = createLessonStepEditorState();
    const lessonUpdateSafety = createAuthoringDirtyHandle();
    return {
      lesson: null,
      lessonCapabilities: { ...NO_LESSON_ROLLOUT_CAPABILITIES },
      canonicalDemo: null,
      canonicalDemoLoadSequence: 0,
      steps: [],
      stepTypes: [],
      loading: false,
      stepEditor,
      lessonUpdateSafety,
      validating: false,
      previewing: false,
      publishing: false,
      renaming: false,
      lastSubject: '',
      // Lifted bundle assets from LessonAssetManager (keyed by layer downstream).
      bundleAssets: [],
      sharedVisualAssets: [],
      validationResult: null,
      // Part-of-speech enum + firmware-supported expression overrides (with REAL
      // on-device emoji so the author is not misled: listening ≡ thinking face).
      partsOfSpeech: ['noun', 'verb', 'adjective', 'adverb', 'pronoun', 'preposition', 'conjunction', 'interjection', 'determiner'],
      expressionOptions: [
        { value: 'teaching', label: 'teaching · happy 😊' },
        { value: 'listening', label: 'listening · thinking 🤔' },
        { value: 'thinking', label: 'thinking · thinking 🤔' },
        { value: 'celebrating', label: 'celebrating · laughing 😄' },
      ],
      preview: null,
      publishMessage: '',
      studioRevision: 0,
      previewManifest: null,
      previewPath: null,
      renameVisible: false,
      titleDraft: '',
    };
  },
  computed: {
    selectedStepIndex: {
      get() { return this.stepEditor.selectedStepIndex; },
      set(value) { this.stepEditor.selectedStepIndex = value; },
    },
    stepDialogVisible: {
      get() { return this.stepEditor.dialogVisible; },
      set(value) { this.stepEditor.dialogVisible = value; },
    },
    stepForm: {
      get() { return this.stepEditor.form; },
      set(value) { this.stepEditor.form = value; },
    },
    correctChoiceId: {
      get() { return this.stepEditor.correctChoiceId; },
      set(value) { this.stepEditor.correctChoiceId = value; },
    },
    addingStep: {
      get() { return this.stepEditor.adding; },
      set(value) { this.stepEditor.adding = value; },
    },
    reordering: {
      get() { return this.stepEditor.reordering; },
      set(value) { this.stepEditor.reordering = value; },
    },
    selectedStepDrafts() { return this.stepEditor.authoringDrafts; },
    selectedContentDrafts() { return this.stepEditor.contentDrafts; },
    selectedAssetDrafts() { return this.stepEditor.assetDrafts; },
    dirtyStepKeys() { return this.stepEditor.dirtyKeys; },
    savingStepKeys() { return this.stepEditor.savingKeys; },
    stepDraftRevisions() { return this.stepEditor.draftRevisions; },
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
    backgroundAssets() {
      return this.bundleAssets.filter((a) => a.layer === 'backgroundScene');
    },
    teachingObjectAssets() {
      return this.bundleAssets.filter((a) => a.layer === 'teachingObject');
    },
    hasBundleAssets() {
      return this.backgroundAssets.length > 0 || this.teachingObjectAssets.length > 0;
    },
    selectedStep() {
      return this.steps[this.selectedStepIndex] || null;
    },
    selectedStepDirty() {
      return Boolean(this.selectedStep && this.dirtyStepKeys[this.selectedStep.stepKey]);
    },
    savingSelectedStep() {
      return Boolean(this.selectedStep && this.savingStepKeys[this.selectedStep.stepKey]);
    },
    hasUnsavedDrafts() {
      return Object.keys(this.dirtyStepKeys).some((key) => this.dirtyStepKeys[key]);
    },
    hasPendingAuthoringChanges() {
      return this.hasUnsavedDrafts
        || this.stepDialogVisible
        || this.renameVisible
        || this.addingStep
        || this.reordering
        || this.renaming
        || this.publishing
        || Object.keys(this.savingStepKeys).some((key) => this.savingStepKeys[key]);
    },
    selectedAuthoring: {
      get() {
        if (!this.selectedStep) return mergeAuthoringFields({}, {});
        return this.selectedStepDrafts[this.selectedStep.stepKey]
          || mergeAuthoringFields(this.selectedStep.stepBody || {}, {});
      },
      set(value) {
        if (!this.selectedStep) return;
        this.$set(this.selectedStepDrafts, this.selectedStep.stepKey, value);
        this.$set(this.dirtyStepKeys, this.selectedStep.stepKey, true);
        this.markStudioChanged(this.selectedStep.stepKey);
      },
    },
    selectedContent: {
      get() {
        if (!this.selectedStep) return { prompt: '', subject: '', helperText: '', l1TransferHint: '' };
        return this.selectedContentDrafts[this.selectedStep.stepKey] || {
          prompt: this.selectedStep.prompt || '',
          subject: this.selectedStep.subject || '',
          helperText: this.selectedStep.helperText || '',
          l1TransferHint: this.selectedStep.l1TransferHint || '',
        };
      },
      set(value) {
        if (!this.selectedStep) return;
        this.$set(this.selectedContentDrafts, this.selectedStep.stepKey, value);
        this.$set(this.dirtyStepKeys, this.selectedStep.stepKey, true);
        this.markStudioChanged(this.selectedStep.stepKey);
      },
    },
    selectedObjectKey() {
      if (this.selectedStep && this.selectedAssetDrafts[this.selectedStep.stepKey]) {
        return this.selectedAssetDrafts[this.selectedStep.stepKey].assetKey;
      }
      const visualRef = this.selectedStep && Array.isArray(this.selectedStep.visualRefs)
        ? this.selectedStep.visualRefs.find((ref) => ref.slot === 'teachingObject')
        : null;
      if (visualRef) return visualRef.assetKey || visualRef.asset_key || '';
      const body = this.selectedStep && this.selectedStep.stepBody;
      return body && body.teachingObject && body.teachingObject.asset
        ? body.teachingObject.asset.key
        : '';
    },
    studioSteps() {
      return this.steps.map((step) => {
        const authored = this.selectedStepDrafts[step.stepKey];
        const content = this.selectedContentDrafts[step.stepKey];
        return authored || content
          ? { ...step, ...(content || {}), stepBody: { ...(step.stepBody || {}), ...(authored || {}) } }
          : step;
      });
    },
  },
  watch: {
    '$route.query.demoSource'() {
      this.loadCanonicalDemo();
    },
    hasPendingAuthoringChanges: {
      immediate: true,
      handler(value) {
        this.lessonUpdateSafety.setDirty(value);
      },
    },
    // Mirror subject into vocab.word + scene.primaryWord while they track it
    // (don't clobber an author-edited value).
    'stepForm.subject'(val, old) {
      const v = this.stepForm.vocab;
      if (v && (!v.word || v.word === old)) v.word = val;
      const sc = this.stepForm.scene;
      if (sc && (!sc.primaryWord || sc.primaryWord === old)) sc.primaryWord = val;
    },
  },
  created() {
    if (!this.lessonId) {
      this.$router.replace('/course-management');
      return;
    }
    this.loadLessonCapabilities();
    this.loadCanonicalDemo();
    this.fetchAll();
  },
  beforeDestroy() {
    this.canonicalDemoLoadSequence += 1;
    this.lessonUpdateSafety.release();
  },
  methods: {
    async loadCanonicalDemo() {
      const sequence = ++this.canonicalDemoLoadSequence;
      try {
        const demo = await loadCanonicalDemoContext(this.$route.query.demoSource);
        if (sequence === this.canonicalDemoLoadSequence && !this._isBeingDestroyed) this.canonicalDemo = demo;
      } catch (error) {
        if (sequence !== this.canonicalDemoLoadSequence || this._isBeingDestroyed) return;
        this.canonicalDemo = null;
        this.$message.warning(error instanceof Error ? error.message : 'Canonical demo could not be loaded');
      }
    },
    async loadLessonCapabilities() {
      this.lessonCapabilities = await loadLessonRolloutCapabilities();
      if (this.lessonCapabilities.sharedVisualAuthoring) this.fetchSharedVisualAssets();
    },
    statusType(status) {
      if (status === 'published') return 'success';
      if (status === 'archived') return 'info';
      return 'warning';
    },
    onAssetsLoaded(assets) {
      this.bundleAssets = Array.isArray(assets) ? assets : [];
      this.markStudioChanged();
    },
    markStudioChanged(stepKey) {
      this.studioRevision += 1;
      if (stepKey) this.$set(this.stepDraftRevisions, stepKey, Number(this.stepDraftRevisions[stepKey] || 0) + 1);
      this.validationResult = null;
      this.previewManifest = null;
    },
    updateSelectedContent(field, value) {
      if (!this.selectedStep || !this.isDraft) return;
      this.selectedContent = { ...this.selectedContent, [field]: value };
    },
    // A step carries an expression override when its persisted expression differs
    // from the stepType-derived default. Server-derived steps look "auto"; we flag
    // the divergence so authors see which rows were overridden.
    isExpressionOverride(row) {
      const expected = {
        greeting: 'teaching', review: 'teaching', focus: 'teaching', model: 'teaching',
        listen: 'listening', repeat: 'listening', fillBlank: 'thinking', feedback: 'teaching',
        celebrate: 'celebrating',
      };
      const def = expected[row.stepType];
      return !!def && !!row.expression && row.expression !== def;
    },
    addExample() {
      this.stepForm.vocab.examples.push({ text: '', translation: '' });
    },
    removeExample(i) {
      this.stepForm.vocab.examples.splice(i, 1);
    },
    addWindow() {
      this.stepForm.scene.activeWindows.push({ tStart: 0, tEnd: 1, x: 0.5, y: 0.5, w: 0.4, h: 0.4 });
    },
    removeWindow(i) {
      this.stepForm.scene.activeWindows.splice(i, 1);
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
    fetchSharedVisualAssets() {
      Api.lesson.listVisualAssets(
        { category: 'teachingObject', profile: 'espTft' },
        (assets) => { this.sharedVisualAssets = assets; },
        (msg) => this.$message.error(msg),
      );
    },
    fetchSteps() {
      Api.lesson.listSteps(this.lessonId, (rows) => {
        this.steps = rows;
        if (this.selectedStepIndex >= rows.length) this.selectedStepIndex = Math.max(0, rows.length - 1);
      }, (msg) => this.$message.error(msg));
    },
    selectSharedAsset(asset) {
      if (!this.lessonCapabilities.sharedVisualAuthoring) return;
      if (!this.selectedStep || !this.isDraft) return;
      this.$set(this.selectedAssetDrafts, this.selectedStep.stepKey, asset);
      this.$set(this.dirtyStepKeys, this.selectedStep.stepKey, true);
      this.markStudioChanged(this.selectedStep.stepKey);
    },
    inspectSharedAsset(asset) {
      if (!this.lessonCapabilities.sharedVisualAuthoring) return;
      this.$router.push({ name: 'LessonVisualAssetDetail', params: { assetKey: asset.assetKey } });
    },
    cloneSharedAsset(asset) {
      if (!this.lessonCapabilities.sharedVisualAuthoring) return;
      this.$router.push({ name: 'LessonVisualAssetDetail', params: { assetKey: asset.assetKey }, query: { mode: 'cloneForLesson', lessonId: this.lessonId } });
    },
    onPreviewPathChange(payload) {
      this.previewPath = payload;
    },
    saveSelectedStep() {
      const step = this.selectedStep;
      if (!step || !this.isDraft) return;
      const savedRevision = Number(this.stepDraftRevisions[step.stepKey] || 0);
      const request = buildSaveStepRequest({
        step,
        authoring: this.selectedAuthoring,
        content: this.selectedContent,
        selectedAsset: this.selectedAssetDrafts[step.stepKey],
        savedRevision,
      });
      this.$set(this.savingStepKeys, step.stepKey, true);
      // A commit changes persisted lesson truth even when it clears the last dirty
      // draft, so every validation/preview launched before this point is stale.
      this.studioRevision += 1;
      this.validationResult = null;
      this.previewManifest = null;
      Api.lesson.updateStep(
        this.lessonId, request.stepKey, request.payload,
        (updated) => {
          this.$delete(this.savingStepKeys, step.stepKey);
          if (resolveSaveSuccess({
            currentRevision: this.stepDraftRevisions[step.stepKey],
            savedRevision: request.savedRevision,
          }).clearDraft) {
            this.$delete(this.selectedStepDrafts, step.stepKey);
            this.$delete(this.selectedContentDrafts, step.stepKey);
            this.$delete(this.selectedAssetDrafts, step.stepKey);
            this.$delete(this.dirtyStepKeys, step.stepKey);
          }
          this.previewManifest = null;
          this.validationResult = null;
          this.fetchSteps();
          this.$message.success('Step saved to the lesson draft.');
        },
        (msg) => { this.$delete(this.savingStepKeys, step.stepKey); this.validationResult = null; this.previewManifest = null; this.$message.error(msg); },
      );
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
        (rows) => { this.reordering = false; this.steps = rows; this.markStudioChanged(); },
        (msg) => { this.reordering = false; this.$message.error(msg); },
      );
    },
    deleteStep(row) {
      this.$confirm(this.$t('lesson.deleteStepConfirm', { key: row.stepKey }), this.$t('lesson.deleteStep'), { type: 'warning' })
        .then(() => {
          Api.lesson.deleteStep(this.lessonId, row.stepKey, (rows) => { this.steps = rows; this.markStudioChanged(); }, (msg) => this.$message.error(msg));
        })
        .catch(() => {});
    },
    openStepDialog() {
      const dialog = createStepDialogState({ stepTypes: this.stepTypes, lastSubject: this.lastSubject });
      this.stepForm = dialog.form;
      this.correctChoiceId = dialog.correctChoiceId;
      this.stepDialogVisible = dialog.visible;
    },
    addChoice() {
      this.stepForm = appendStepChoice(this.stepForm);
    },
    removeChoice(index) {
      const result = removeStepChoice(this.stepForm, index, this.correctChoiceId);
      this.stepForm = result.form;
      this.correctChoiceId = result.correctChoiceId;
    },
    addStep() {
      const f = this.stepForm;
      const result = buildCreateStepPayload({
        form: f,
        correctChoiceId: this.correctChoiceId,
        assets: this.bundleAssets,
        locale: (this.lesson && this.lesson.locale) || 'vi',
      });
      if (!result.ok) {
        this.$message.warning(this.$t(`lesson.${result.reason}`));
        return;
      }
      this.addingStep = true;
      Api.lesson.createStep(
        this.lessonId,
        result.payload,
        () => {
          this.addingStep = false;
          this.stepDialogVisible = false;
          this.lastSubject = f.subject; // prefill next step + teachingObject asset key
          this.markStudioChanged();
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
      const requestedRevision = this.studioRevision;
      this.validating = true;
      Api.lesson.validate(
        this.lessonId,
        (res) => {
          this.validating = false;
          if (requestedRevision !== this.studioRevision || this.hasUnsavedDrafts) return;
          this.validationResult = res || null;
          if (res && res.valid) this.$message.success(this.$t('lesson.validOk', { profiles: (res.profiles || []).join(', ') }));
          else this.$message.warning(this.$t('lesson.validFail'));
        },
        (msg) => {
          this.validating = false;
          if (requestedRevision !== this.studioRevision || this.hasUnsavedDrafts) return;
          this.validationResult = null;
          this.$message.error(msg);
        },
      );
    },
    doPreview() {
      if (!this.lessonCapabilities.exactEspTftPreview) return;
      const requestedRevision = this.studioRevision;
      this.previewing = true;
      Api.lesson.manifestPreview(
        this.lessonId,
        'espTft',
        (res) => {
          this.previewing = false;
          if (requestedRevision !== this.studioRevision || this.hasUnsavedDrafts) return;
          this.preview = { checksum: res.checksum, etag: res.etag };
          this.previewManifest = res.manifest || null;
        },
        (msg) => {
          this.previewing = false;
          if (requestedRevision !== this.studioRevision || this.hasUnsavedDrafts) return;
          this.$message.error(msg);
        },
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
.canonical-demo { align-items:center; background:#17312d; border-radius:20px; color:#fff8df; display:grid; gap:20px; grid-template-columns:minmax(220px,.75fr) minmax(320px,1.25fr); margin-bottom:18px; overflow:hidden; padding:18px; }
.canonical-demo__copy h3 { font-family:Georgia,serif; font-size:25px; margin:5px 0 8px; }
.canonical-demo__copy p { color:#c7d7d1; line-height:1.5; margin:0 0 12px; max-width:480px; }
.canonical-demo__sources { display:flex; gap:8px; margin-top:14px; }
.canonical-demo__sources img { background:#f6ecd1; border:1px solid rgba(255,255,255,.2); border-radius:9px; height:58px; object-fit:contain; width:76px; }
.canonical-demo video { background:#0c1c19; border-radius:14px; display:block; max-height:360px; object-fit:cover; width:100%; }
.choice-group { display: block; }
.choice-row { display: flex; align-items: center; gap: 10px; margin-bottom: 8px; }
.grid-2 { display: grid; grid-template-columns: 1fr 1fr; gap: 0 14px; }
.focus-title { margin: 6px 0; font-weight: 600; }
.focus-row { display: flex; align-items: center; gap: 6px; margin-bottom: 8px; flex-wrap: wrap; }
.focus-row .el-input-number { width: 110px; }
.lesson-studio { align-items:start; background:linear-gradient(135deg,#f3ead5 0%,#edf3ea 55%,#f8dfb5 100%); border:1px solid #e5d7bd; border-radius:24px; display:grid; gap:16px; grid-template-columns:210px minmax(0,1fr); margin-bottom:18px; padding:16px; }
.lesson-studio__canvas { display:grid; gap:14px; min-width:0; }
.lesson-studio__toolbar { align-items:center; display:flex; justify-content:space-between; }
.lesson-studio__toolbar h3 { color:#17312d; font-family:Georgia,serif; font-size:24px; margin:3px 0 0; }
.eyebrow { color:#9a6820; font-size:10px; font-weight:800; letter-spacing:.16em; }
.lesson-studio__workbench { display:grid; gap:14px; grid-template-columns:minmax(330px,1fr) minmax(360px,1fr); }
.preview-empty { align-items:center; background:#17312d; border-radius:18px; color:#fff8df; display:flex; flex-direction:column; gap:12px; justify-content:center; min-height:320px; padding:30px; text-align:center; }
.preview-empty span { color:#b9cbc5; max-width:320px; }
@media (max-width:1100px) { .lesson-studio__workbench { grid-template-columns:1fr; } }
@media (max-width:760px) { .canonical-demo,.lesson-studio { grid-template-columns:1fr; }.lesson-studio__toolbar { align-items:flex-start; flex-direction:column; gap:10px; } }
</style>
