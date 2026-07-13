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
        <el-button size="small" @click="doValidate" :loading="validating" :disabled="savingStep || rebindingSharedVisual">{{ $t('lesson.validate') }}</el-button>
        <el-button size="small" @click="doPreview" :loading="previewing" :disabled="promptDirty || savingStep || rebindingSharedVisual">{{ $t('lesson.previewManifest') }}</el-button>
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

      <section v-if="lesson" class="lesson-studio">
        <LessonStepNavigator v-model="selectedStepIndex" :steps="steps" :editable="isDraft" @add="openStepDialog" />
        <main class="lesson-studio__canvas">
          <div class="lesson-studio__toolbar">
            <div>
              <span class="eyebrow">VISUAL LESSON BUILDER</span>
              <h3>{{ selectedStep ? promptDraft : 'Choose or add a lesson step' }}</h3>
            </div>
            <el-button v-if="isDraft && selectedStep" type="primary" size="small" :loading="savingStep" :disabled="savingStep || rebindingSharedVisual || !selectedStepDirty" @click="saveSelectedStep">
              Save step
            </el-button>
          </div>
          <div v-if="selectedStep" class="lesson-studio__workbench">
            <div>
              <lesson-step-prompt-editor
                v-model="promptDraft"
                :disabled="!isDraft || savingStep || rebindingSharedVisual"
                @input="onPromptInput"
              />
              <LessonInteractionPanel v-model="selectedAuthoring" :disabled="!isDraft || savingStep || rebindingSharedVisual" />
              <SharedAssetPicker
                :assets="bundleAssets"
                :selected-key="selectedObjectKey"
                category="teachingObject"
                :disabled="savingStep || rebindingSharedVisual"
                @select-intent="reviewSharedAssetSelection"
              />
            </div>
            <RobotLessonPreview
              v-if="previewManifest"
              :manifest-preview="previewManifest"
              :step-index="selectedStepIndex"
            />
            <div v-else class="preview-empty">
              <strong>Robot preview</strong>
              <span>Generate the espTft manifest preview to inspect the exact 480×320 scene.</span>
              <el-button size="small" :disabled="promptDirty || savingStep || rebindingSharedVisual" @click="doPreview">Generate preview</el-button>
            </div>
          </div>
          <LessonSimulationPanel
            v-if="previewManifest"
            :value="simulationEvidence"
            :lesson-id="lessonId"
            :manifest-preview="previewManifest"
            :steps="steps"
            :proof-version="proofVersion"
            :disabled="promptDirty || savingStep || rebindingSharedVisual"
            @evidence="acceptSimulationEvidence"
          />
          <LessonEngagementTrack :steps="studioSteps" @select="selectedStepIndex = $event" />
          <LessonPublishReadiness :steps="studioSteps" :assets="bundleAssets" :manifest="previewManifest ? previewManifest.manifest : {}" />
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
      <LessonAssetManager
        v-if="isDraft"
        ref="assetManager"
        :lesson-id="lessonId"
        :subject-hint="lastSubject"
        :disabled="savingStep || rebindingSharedVisual"
        @assets-loaded="onAssetsLoaded"
        @impact-review-request="reviewAssetReplacement"
      />
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

    <SharedVisualImpactDialog
      v-if="sharedImpactIntent"
      :visible="sharedImpactVisible"
      :lesson-id="lessonId"
      :asset="sharedImpactIntent.asset"
      :assets="bundleAssets"
      :steps="steps"
      :current-step="selectedStep"
      :cloned-asset="sharedImpactClonedAsset"
      :rebind-pending="rebindingSharedVisual"
      :rebind-error="sharedImpactRebindError"
      :intent-type="sharedImpactIntent.intent"
      :layer="sharedImpactIntent.layer"
      :uncertain-clone-key="sharedImpactUncertainCloneKey"
      :reconciling="sharedImpactReconciling"
      @keep-shared="keepSharedVisual"
      @cloned="applyClonedVisual"
      @retry-rebind="retryClonedVisual"
      @clone-uncertain="onCloneUncertain"
      @retry-discovery="discoverUncertainClone"
      @error="onSharedImpactError"
      @close="closeSharedImpact"
    />
  </div>
</template>

<script>
import HeaderBar from '@/components/HeaderBar.vue';
import LessonAssetManager from '@/components/LessonAssetManager.vue';
import LessonEngagementTrack from '@/components/lesson/LessonEngagementTrack.vue';
import LessonInteractionPanel from '@/components/lesson/LessonInteractionPanel.vue';
import LessonPublishReadiness from '@/components/lesson/LessonPublishReadiness.vue';
import LessonSimulationPanel from '@/components/lesson/LessonSimulationPanel.vue';
import LessonStepPromptEditor from '@/components/lesson/LessonStepPromptEditor.vue';
import LessonStepNavigator from '@/components/lesson/LessonStepNavigator.vue';
import RobotLessonPreview from '@/components/lesson/RobotLessonPreview.vue';
import SharedAssetPicker from '@/components/lesson/SharedAssetPicker.vue';
import SharedVisualImpactDialog from '@/components/lesson/SharedVisualImpactDialog.vue';
import {
  bindClonedAssetToStep,
  collectAssetReferences,
  mergeAuthoringFields,
  replaceStepAssetReference,
} from '@/components/lesson/lesson-builder-logic';
import Api from '@/apis/api';

export default {
  name: 'LessonEditor',
  components: {
    HeaderBar,
    LessonAssetManager,
    LessonEngagementTrack,
    LessonInteractionPanel,
    LessonPublishReadiness,
    LessonSimulationPanel,
    LessonStepPromptEditor,
    LessonStepNavigator,
    RobotLessonPreview,
    SharedAssetPicker,
    SharedVisualImpactDialog,
  },
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
      stepForm: this.blankStepForm(),
      correctChoiceId: '',
      lastSubject: '',
      // Lifted bundle assets from LessonAssetManager (keyed by layer downstream).
      bundleAssets: [],
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
      selectedStepIndex: 0,
      promptDraft: '',
      promptDirty: false,
      promptStepKey: '',
      promptEditRevision: 0,
      promptSaveRequestId: 0,
      selectedStepDrafts: {},
      selectedAssetDrafts: {},
      dirtyStepKeys: {},
      stepEditRevisions: {},
      savingStep: false,
      previewManifest: null,
      simulationEvidence: null,
      proofVersion: 0,
      previewRequestId: 0,
      assetProofFingerprint: null,
      assetRefreshIsProofRecovery: false,
      renameVisible: false,
      titleDraft: '',
      sharedImpactVisible: false,
      sharedImpactIntent: null,
      rebindingSharedVisual: false,
      sharedImpactClonedAsset: null,
      sharedImpactRebindError: '',
      sharedImpactUncertainCloneKey: '',
      sharedImpactReconciling: false,
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
    selectedStepKey() {
      return this.selectedStep ? this.selectedStep.stepKey : '';
    },
    selectedStepDirty() {
      return Boolean(
        this.selectedStep
        && (
          this.dirtyStepKeys[this.selectedStep.stepKey]
          || (this.promptStepKey === this.selectedStep.stepKey && this.promptDirty)
        ),
      );
    },
    selectedAuthoring: {
      get() {
        if (!this.selectedStep) return mergeAuthoringFields({}, {});
        return this.selectedStepDrafts[this.selectedStep.stepKey]
          || mergeAuthoringFields(this.selectedStep.stepBody || {}, {});
      },
      set(value) {
        if (!this.selectedStep || this.savingStep || this.rebindingSharedVisual) return;
        this.$set(this.selectedStepDrafts, this.selectedStep.stepKey, value);
        this.$set(this.dirtyStepKeys, this.selectedStep.stepKey, true);
        this.bumpStepEditRevision(this.selectedStep.stepKey);
        this.invalidatePreview();
      },
    },
    selectedObjectKey() {
      if (this.selectedStep && this.selectedAssetDrafts[this.selectedStep.stepKey]) {
        return this.selectedAssetDrafts[this.selectedStep.stepKey].assetKey;
      }
      const body = this.selectedStep && this.selectedStep.stepBody;
      return body && body.teachingObject && body.teachingObject.asset
        ? body.teachingObject.asset.key
        : '';
    },
    studioSteps() {
      return this.steps.map((step) => {
        const authored = this.selectedStepDrafts[step.stepKey];
        return authored ? { ...step, stepBody: { ...(step.stepBody || {}), ...authored } } : step;
      });
    },
  },
  watch: {
    selectedStepKey() {
      this.resetPromptDraft(this.selectedStep);
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
    this.fetchAll();
  },
  methods: {
    statusType(status) {
      if (status === 'published') return 'success';
      if (status === 'archived') return 'info';
      return 'warning';
    },
    // Fresh step-form skeleton incl. scene + vocab + expression-override state.
    blankStepForm() {
      return {
        stepType: '',
        prompt: '',
        subject: '',
        helperText: '',
        l1TransferHint: '',
        choices: [],
        renderExpression: '',
        vocab: {
          word: '',
          ipa: '',
          partOfSpeech: '',
          translationVi: '',
          definition: '',
          examples: [],
        },
        scene: {
          backgroundKey: '',
          altCaption: '',
          fit: 'cover',
          objectKey: '',
          primaryWord: '',
          placementAnchor: 'center',
          supportWords: [],
          activeWindows: [],
          successUtterance: '',
          missUtterance: '',
          timeoutSec: 12,
        },
      };
    },
    onAssetsLoaded(assets) {
      const nextAssets = Array.isArray(assets) ? assets : [];
      const fingerprint = nextAssets
        .map((asset) => [asset.profile, asset.assetKey, asset.sha256, asset.version, asset.path].join(':'))
        .sort()
        .join('|');
      if (this.assetProofFingerprint !== null
        && fingerprint !== this.assetProofFingerprint
        && !this.assetRefreshIsProofRecovery) this.invalidatePreview();
      this.assetProofFingerprint = fingerprint;
      this.bundleAssets = nextAssets;
    },
    invalidatePreview() {
      this.proofVersion += 1;
      this.previewRequestId += 1;
      this.previewing = false;
      this.preview = null;
      this.previewManifest = null;
      this.simulationEvidence = null;
    },
    acceptSimulationEvidence(result, proofVersion) {
      if (proofVersion !== this.proofVersion) return;
      this.simulationEvidence = result;
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
    assetByKey(key) {
      return this.bundleAssets.find((a) => a.assetKey === key) || null;
    },
    // Build the stepBody.vocab object, dropping empty sub-fields. Returns null when
    // nothing was authored. locale = lesson locale (default 'vi').
    buildVocab(subject) {
      const v = this.stepForm.vocab;
      const word = (v.word || subject || '').trim();
      const out = {};
      if (word) out.word = word;
      if ((v.ipa || '').trim()) out.ipa = v.ipa.trim();
      if (v.partOfSpeech) out.partOfSpeech = v.partOfSpeech;
      const tr = (v.translationVi || '').trim();
      if (tr) {
        const loc = (this.lesson && this.lesson.locale) || 'vi';
        out.translation = { [loc]: tr };
      }
      if ((v.definition || '').trim()) out.definition = v.definition.trim();
      const examples = (v.examples || [])
        .filter((e) => (e.text || '').trim())
        .map((e) => {
          const ex = { text: e.text.trim() };
          if ((e.translation || '').trim()) ex.translation = e.translation.trim();
          return ex;
        });
      if (examples.length) out.examples = examples;
      // Only emit when more than the auto-mirrored word is present.
      return Object.keys(out).length > (out.word ? 1 : 0) || out.word ? out : null;
    },
    // Build the stepBody.scene object from the lifted bundle assets, matching the
    // 076 seed shape. Returns null when nothing was authored.
    buildScene(subject) {
      const s = this.stepForm.scene;
      const scene = {};
      const bg = this.assetByKey(s.backgroundKey);
      if (bg) {
        scene.backgroundScene = {
          mode: 'poster',
          poster: { key: bg.assetKey, src: bg.path || bg.url, fit: s.fit || 'cover', sha256: bg.sha256 },
          video: null,
          altCaption: (s.altCaption || '').trim(),
        };
      }
      const obj = this.assetByKey(s.objectKey);
      if (obj) {
        const teachingObject = {
          primaryWord: (s.primaryWord || subject || '').trim(),
          supportWords: Array.isArray(s.supportWords) ? s.supportWords.filter(Boolean) : [],
          placement: { anchor: s.placementAnchor || 'center', paddingTopPercent: 8 },
          asset: { key: obj.assetKey, src: obj.path || obj.url, sha256: obj.sha256 },
        };
        // focusTarget: model step only; clamp [0,1] + enforce tStart<tEnd here so a
        // bad window cannot 400 the lesson at publish.
        if (this.stepForm.stepType === 'model' && s.activeWindows.length) {
          const clamp = (n) => Math.min(1, Math.max(0, Number(n) || 0));
          const windows = s.activeWindows
            .map((w) => ({
              tStart: Math.max(0, Number(w.tStart) || 0),
              tEnd: Math.max(0, Number(w.tEnd) || 0),
              x: clamp(w.x), y: clamp(w.y), w: clamp(w.w), h: clamp(w.h),
            }))
            .filter((w) => w.tStart < w.tEnd);
          if (windows.length) {
            teachingObject.focusTarget = {
              activeWindows: windows,
              successUtterance: (s.successUtterance || '').trim(),
              missUtterance: (s.missUtterance || '').trim(),
            };
          }
        }
        scene.teachingObject = teachingObject;
      }
      if (Object.keys(scene).length) {
        const expressionByType = {
          greeting: 'teaching',
          review: 'teaching',
          focus: 'teaching',
          model: 'teaching',
          listen: 'listening',
          repeat: 'listening',
          fillBlank: 'thinking',
          feedback: 'teaching',
          celebrate: 'celebrating',
        };
        const poseByExpression = {
          teaching: 'teach',
          listening: 'listening',
          thinking: 'thinking',
          celebrating: 'celebrate',
        };
        const expression = this.stepForm.renderExpression || expressionByType[this.stepForm.stepType] || 'teaching';
        const pose = poseByExpression[expression] || 'teach';
        const overlay = this.assetByKey('robotOverlay.' + pose);
        const overlaySrc = overlay && (overlay.path || overlay.url);
        if (overlaySrc) {
          scene.robotOverlay = {
            robotState: pose === 'celebrate' ? 'celebrating' : (pose === 'listening' || pose === 'thinking' ? pose : 'talking'),
            pose,
            expression,
            anchor: 'bottomLeft',
            pivot: { x: 0.5, y: 1.0 },
            asset: { key: overlay.assetKey, src: overlaySrc, sha256: overlay.sha256 },
            atlas: { image: overlaySrc, cell: 0 },
            pointerEvents: 'none',
          };
        }
        scene.audio = { via: 'tts' };
        scene.timeoutSec = Number(s.timeoutSec) || 12;
        return scene;
      }
      return null;
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
    fetchSteps(options = {}) {
      Api.lesson.listSteps(this.lessonId, (rows) => {
        const selectedKey = this.selectedStepKey;
        this.steps = rows;
        const matchingIndex = rows.findIndex((step) => step.stepKey === selectedKey);
        this.selectedStepIndex = matchingIndex >= 0
          ? matchingIndex
          : Math.min(this.selectedStepIndex, Math.max(0, rows.length - 1));
        let promptStateApplied = true;
        if (options.promptGuard) {
          promptStateApplied = this.syncPromptDraftAfterFetch(rows[this.selectedStepIndex] || null, options.promptGuard);
        } else if (options.preservePrompt && this.promptStepKey === selectedKey) {
          const selected = rows[this.selectedStepIndex] || null;
          this.promptDirty = this.promptDraft !== (selected && selected.prompt ? selected.prompt : '');
        } else {
          this.resetPromptDraft(rows[this.selectedStepIndex] || null);
        }
        if (options.onSuccess) options.onSuccess(rows, promptStateApplied);
      }, (msg) => {
        this.$message.error(msg);
        if (options.onError) options.onError(msg);
      });
    },
    resetPromptDraft(step) {
      this.promptStepKey = step ? step.stepKey : '';
      this.promptDraft = step && typeof step.prompt === 'string' ? step.prompt : '';
      this.promptDirty = false;
      this.promptEditRevision += 1;
    },
    shouldApplySavedStepState(guard) {
      return Boolean(
        guard
        && guard.requestId === this.promptSaveRequestId
        && guard.stepKey === this.promptStepKey
        && guard.promptRevision === this.promptEditRevision
        && guard.stepRevision === (this.stepEditRevisions[guard.stepKey] || 0),
      );
    },
    clearSavedStepDraft(guard) {
      if (!guard || guard.requestId !== this.promptSaveRequestId
        || guard.stepRevision !== (this.stepEditRevisions[guard.stepKey] || 0)) return false;
      this.$delete(this.selectedStepDrafts, guard.stepKey);
      this.$delete(this.selectedAssetDrafts, guard.stepKey);
      this.$delete(this.dirtyStepKeys, guard.stepKey);
      this.$delete(this.stepEditRevisions, guard.stepKey);
      return true;
    },
    syncPromptDraftAfterFetch(step, guard) {
      if (this.shouldApplySavedStepState(guard)) {
        this.resetPromptDraft(step);
        return true;
      }
      if (step && step.stepKey === this.promptStepKey) {
        this.promptDirty = this.promptDraft !== (step.prompt || '');
      }
      return false;
    },
    bumpStepEditRevision(stepKey) {
      this.$set(this.stepEditRevisions, stepKey, (this.stepEditRevisions[stepKey] || 0) + 1);
    },
    onPromptInput(value) {
      const step = this.selectedStep;
      if (!step || this.savingStep || this.rebindingSharedVisual || this.promptStepKey !== step.stepKey) return;
      this.promptEditRevision += 1;
      this.bumpStepEditRevision(step.stepKey);
      this.promptDirty = value !== (step.prompt || '');
      this.invalidatePreview();
    },
    selectSharedAsset(asset) {
      if (!this.selectedStep || !this.isDraft || this.savingStep || this.rebindingSharedVisual) return;
      this.$set(this.selectedAssetDrafts, this.selectedStep.stepKey, asset);
      this.$set(this.dirtyStepKeys, this.selectedStep.stepKey, true);
      this.bumpStepEditRevision(this.selectedStep.stepKey);
      this.invalidatePreview();
    },
    openSharedImpact(intent, asset) {
      if (!asset || !asset.assetId || !this.selectedStep || !this.isDraft || this.savingStep || this.rebindingSharedVisual) return;
      this.sharedImpactIntent = {
        intent,
        asset,
        stepKey: this.selectedStep.stepKey,
        boundAssetKey: intent === 'select' ? this.selectedObjectKey : asset.assetKey,
        layer: asset.layer || 'teachingObject',
      };
      this.sharedImpactClonedAsset = null;
      this.sharedImpactRebindError = '';
      this.sharedImpactUncertainCloneKey = '';
      this.sharedImpactReconciling = false;
      this.sharedImpactVisible = true;
    },
    reviewSharedAssetSelection(asset) {
      this.openSharedImpact('select', asset);
    },
    reviewAssetReplacement(payload) {
      if (!payload || payload.intent !== 'replace') return;
      this.openSharedImpact('replace', payload.asset);
    },
    keepSharedVisual() {
      const intent = this.sharedImpactIntent;
      if (!this.savingStep && !this.rebindingSharedVisual && intent && intent.intent === 'select' && this.selectedStepKey === intent.stepKey) {
        this.selectSharedAsset(intent.asset);
      }
      this.closeSharedImpact();
    },
    closeSharedImpact(force = false) {
      if (!force && (this.rebindingSharedVisual || this.sharedImpactClonedAsset || this.sharedImpactUncertainCloneKey || this.sharedImpactReconciling)) return;
      this.sharedImpactVisible = false;
      this.sharedImpactIntent = null;
      this.sharedImpactClonedAsset = null;
      this.sharedImpactRebindError = '';
      this.sharedImpactUncertainCloneKey = '';
      this.sharedImpactReconciling = false;
    },
    onSharedImpactError(msg) {
      this.$message.error(msg || this.$t('lesson.sharedImpactCloneError'));
    },
    onCloneUncertain(payload) {
      const key = payload && payload.assetKey;
      if (!key) {
        this.sharedImpactRebindError = this.$t('lesson.sharedImpactInvalidCloneResponse');
        return;
      }
      this.sharedImpactUncertainCloneKey = key;
      this.discoverUncertainClone();
    },
    discoverUncertainClone() {
      const key = this.sharedImpactUncertainCloneKey;
      if (!key || this.sharedImpactReconciling) return;
      this.sharedImpactReconciling = true;
      this.sharedImpactRebindError = '';
      this.reloadAssets((assets) => {
        const clone = (Array.isArray(assets) ? assets : []).find((asset) => asset.assetKey === key);
        this.sharedImpactReconciling = false;
        if (!this.validClonedAsset(clone)) {
          this.sharedImpactRebindError = this.$t('lesson.sharedImpactCloneNotFound', { key });
          return;
        }
        this.sharedImpactUncertainCloneKey = '';
        this.applyClonedVisual(clone);
      }, (msg) => {
        this.sharedImpactReconciling = false;
        this.sharedImpactRebindError = this.$t('lesson.sharedImpactDiscoveryFailed', { reason: msg || key });
      });
    },
    reloadAssets(onSuccess, onError) {
      if (this.$refs.assetManager) this.$refs.assetManager.reload(onSuccess, onError);
      else if (onError) onError(this.$t('lesson.sharedImpactRefreshError'));
    },
    applyClonedVisual(clonedAsset) {
      if (!this.validClonedAsset(clonedAsset)) {
        this.sharedImpactRebindError = this.$t('lesson.sharedImpactInvalidCloneResponse');
        this.$message.error(this.sharedImpactRebindError);
        return;
      }
      this.sharedImpactClonedAsset = clonedAsset;
      this.sharedImpactRebindError = '';
      if (this.savingStep || this.rebindingSharedVisual) {
        this.sharedImpactRebindError = this.$t('lesson.sharedImpactBusyRetry');
        this.reloadAssets();
        return;
      }
      this.rebindClonedVisual(clonedAsset);
    },
    retryClonedVisual(clonedAsset) {
      if (!clonedAsset || clonedAsset !== this.sharedImpactClonedAsset || this.savingStep || this.rebindingSharedVisual) return;
      this.sharedImpactRebindError = '';
      this.rebindClonedVisual(clonedAsset);
    },
    validClonedAsset(asset) {
      return Boolean(asset && !Array.isArray(asset) && ['assetId', 'assetKey', 'path', 'sha256']
        .every((key) => typeof asset[key] === 'string' && asset[key].trim()));
    },
    failSharedVisualRebind(msg) {
      this.rebindingSharedVisual = false;
      this.sharedImpactRebindError = this.$t('lesson.sharedImpactRebindFailed', { reason: msg || this.$t('lesson.sharedImpactRefreshError') });
      this.reloadAssets();
      this.$message.error(this.sharedImpactRebindError);
    },
    refreshSharedVisualTruth(onSuccess, onError) {
      let remaining = 3;
      let failed = false;
      const done = () => {
        if (failed) return;
        remaining -= 1;
        if (remaining === 0) onSuccess();
      };
      const fail = (msg) => {
        if (failed) return;
        failed = true;
        this.assetRefreshIsProofRecovery = false;
        onError(msg);
      };
      this.assetRefreshIsProofRecovery = true;
      this.reloadAssets(done, fail);
      this.doValidate(done, fail);
      this.doPreview(done, fail);
    },
    rebindClonedVisual(clonedAsset) {
      if (this.savingStep || this.rebindingSharedVisual) return;
      const intent = this.sharedImpactIntent;
      const step = intent && this.steps.find((row) => row.stepKey === intent.stepKey);
      if (!intent || !step || !clonedAsset) return;
      let stepBody;
      try {
        stepBody = intent.intent === 'select'
          ? bindClonedAssetToStep(step.stepBody || {}, {
            intent: 'select', layer: intent.layer, boundAssetKey: intent.boundAssetKey,
          }, clonedAsset)
          : replaceStepAssetReference(step.stepBody || {}, intent.asset.assetKey, clonedAsset);
      } catch (error) {
        this.failSharedVisualRebind(error && error.message);
        return;
      }
      if (!collectAssetReferences([{ stepKey: step.stepKey, stepBody }], clonedAsset.assetKey).length) {
        this.failSharedVisualRebind(this.$t('lesson.sharedImpactNoRebindTarget'));
        return;
      }
      this.invalidatePreview();
      this.rebindingSharedVisual = true;
      Api.lesson.updateStep(
        this.lessonId,
        step.stepKey,
        { ...step, stepBody },
        () => {
          this.fetchSteps({
            preservePrompt: true,
            onSuccess: () => {
              this.refreshSharedVisualTruth(() => {
                this.assetRefreshIsProofRecovery = false;
                this.rebindingSharedVisual = false;
                this.$delete(this.selectedAssetDrafts, step.stepKey);
                if (!this.selectedStepDrafts[step.stepKey] && !this.promptDirty) {
                  this.$delete(this.dirtyStepKeys, step.stepKey);
                }
                const replaceAfterClone = intent.intent === 'replace';
                this.closeSharedImpact(true);
                if (replaceAfterClone) this.$nextTick(() => {
                  if (this.$refs.assetManager) this.$refs.assetManager.confirmReplace({ ...intent.asset, ...clonedAsset });
                });
                this.$message.success(this.$t('lesson.sharedImpactCloned'));
              }, (msg) => this.failSharedVisualRebind(msg));
            },
            onError: (msg) => this.failSharedVisualRebind(msg),
          });
        },
        (msg) => this.failSharedVisualRebind(msg),
      );
    },
    saveSelectedStep() {
      const step = this.selectedStep;
      if (!step || !this.isDraft || this.savingStep || this.rebindingSharedVisual) return;
      const authored = this.selectedAuthoring;
      const stepBody = { ...(step.stepBody || {}), ...authored };
      const selectedAsset = this.selectedAssetDrafts[step.stepKey];
      if (selectedAsset) {
        stepBody.teachingObject = {
          ...(stepBody.teachingObject || {}),
          primaryWord: authored.teachingWord.text || step.subject,
          asset: {
            key: selectedAsset.assetKey,
            src: selectedAsset.path || selectedAsset.url,
            sha256: selectedAsset.sha256,
            version: selectedAsset.version,
            bytes: selectedAsset.bytes,
          },
        };
      }
      const saveGuard = {
        requestId: this.promptSaveRequestId + 1,
        stepKey: step.stepKey,
        promptRevision: this.promptEditRevision,
        stepRevision: this.stepEditRevisions[step.stepKey] || 0,
      };
      this.promptSaveRequestId = saveGuard.requestId;
      this.invalidatePreview();
      this.savingStep = true;
      Api.lesson.updateStep(
        this.lessonId,
        step.stepKey,
        { ...step, prompt: this.promptDraft, stepBody },
        () => {
          if (saveGuard.requestId !== this.promptSaveRequestId) return;
          this.fetchSteps({
            promptGuard: saveGuard,
            onSuccess: () => {
              if (saveGuard.requestId !== this.promptSaveRequestId) return;
              this.savingStep = false;
              this.clearSavedStepDraft(saveGuard);
              this.$message.success(this.$t('lesson.stepSaved'));
            },
            onError: () => {
              if (saveGuard.requestId === this.promptSaveRequestId) this.savingStep = false;
            },
          });
        },
        (msg) => {
          if (saveGuard.requestId === this.promptSaveRequestId) this.savingStep = false;
          this.$message.error(msg);
        },
      );
    },
    moveStep(index, delta) {
      const target = index + delta;
      if (target < 0 || target >= this.steps.length) return;
      const order = this.steps.map((s) => s.stepKey);
      const tmp = order[index];
      order[index] = order[target];
      order[target] = tmp;
      this.invalidatePreview();
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
          this.invalidatePreview();
          Api.lesson.deleteStep(this.lessonId, row.stepKey, (rows) => { this.steps = rows; }, (msg) => this.$message.error(msg));
        })
        .catch(() => {});
    },
    openStepDialog() {
      const firstId = 'c1';
      const form = this.blankStepForm();
      form.stepType = this.stepTypes.length ? this.stepTypes[0].stepType : '';
      form.subject = this.lastSubject || '';
      form.choices = [{ id: firstId, label: '' }, { id: 'c2', label: '' }];
      // Prefill vocab.word + scene.primaryWord from the carried subject.
      form.vocab.word = this.lastSubject || '';
      form.scene.primaryWord = this.lastSubject || '';
      this.stepForm = form;
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
      // Assemble stepBody from the Scene composer + structured Vocabulary. Both are
      // optional; only send a non-empty stepBody (the server defaults to {}).
      const stepBody = {};
      const scene = this.buildScene(f.subject);
      if (scene) Object.assign(stepBody, scene);
      const vocab = this.buildVocab(f.subject);
      if (vocab) stepBody.vocab = vocab;
      Object.assign(stepBody, mergeAuthoringFields({}, {
        teachingWord: { text: (f.scene.primaryWord || f.subject || '').trim().toUpperCase() },
      }));
      if (Object.keys(stepBody).length) payload.stepBody = stepBody;
      // Per-step robot-face override (server validates against firmware-supported set).
      if (f.renderExpression) payload.renderOverride = { expression: f.renderExpression };
      this.invalidatePreview();
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
      this.invalidatePreview();
      this.renaming = true;
      Api.lesson.updateLesson(
        this.lessonId,
        { title: this.titleDraft },
        (l) => { this.renaming = false; this.renameVisible = false; this.lesson = l; this.$message.success(this.$t('lesson.renamed')); },
        (msg) => { this.renaming = false; this.$message.error(msg); },
      );
    },
    doValidate(onSuccess, onError) {
      this.validating = true;
      Api.lesson.validate(
        this.lessonId,
        (res) => {
          this.validating = false;
          if (res && res.valid) this.$message.success(this.$t('lesson.validOk', { profiles: (res.profiles || []).join(', ') }));
          else this.$message.warning(this.$t('lesson.validFail'));
          if (typeof onSuccess === 'function') onSuccess(res);
        },
        (msg) => { this.validating = false; this.$message.error(msg); if (typeof onError === 'function') onError(msg); },
      );
    },
    doPreview(onSuccess, onError) {
      const requestId = this.previewRequestId + 1;
      const proofVersion = this.proofVersion;
      this.previewRequestId = requestId;
      this.previewing = true;
      Api.lesson.manifestPreview(
        this.lessonId,
        'espTft',
        (res) => {
          if (requestId !== this.previewRequestId || proofVersion !== this.proofVersion) return;
          this.previewing = false;
          this.preview = { checksum: res.checksum, etag: res.etag };
          this.previewManifest = res && res.manifest && res.preview
            ? { manifest: res.manifest, preview: res.preview, checksum: res.checksum, etag: res.etag }
            : null;
          if (typeof onSuccess === 'function') onSuccess(res);
        },
        (msg) => {
          if (requestId !== this.previewRequestId || proofVersion !== this.proofVersion) return;
          this.previewing = false;
          this.$message.error(msg);
          if (typeof onError === 'function') onError(msg);
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
@media (max-width:760px) { .lesson-studio { grid-template-columns:1fr; }.lesson-studio__toolbar { align-items:flex-start; flex-direction:column; gap:10px; } }
</style>
