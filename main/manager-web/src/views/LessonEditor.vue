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
        <el-button size="small" @click="doValidate" :loading="validating" :disabled="proofActionsDisabled">{{ $t('lesson.validate') }}</el-button>
        <el-button size="small" @click="doPreview" :loading="previewing" :disabled="proofActionsDisabled">{{ $t('lesson.previewManifest') }}</el-button>
        <el-button v-if="isDraft" type="primary" size="small" @click="doPublish" :loading="publishing || publishPreparing" :disabled="!canPublishCurrentProof()">
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
              <el-button size="small" :disabled="proofActionsDisabled" @click="doPreview">Generate preview</el-button>
            </div>
          </div>
          <LessonSimulationPanel
            v-if="previewManifest"
            :value="simulationEvidence"
            :lesson-id="lessonId"
            :manifest-preview="previewManifest"
            :steps="steps"
            :proof-version="proofVersion"
            :disabled="proofActionsDisabled"
            @evidence="acceptSimulationEvidence"
          />
          <LessonEngagementTrack :steps="studioSteps" @select="selectedStepIndex = $event" />
          <LessonPublishReadiness
            :steps="studioSteps"
            :assets="bundleAssets"
            :manifest="previewManifest ? previewManifest.manifest : {}"
            :validation-result="validationResult"
            :validation-current="validationProofVersion === proofVersion"
            @ready-change="readinessReady = $event"
          />
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
        :disabled="savingStep || rebindingSharedVisual || assetMutating"
        :mutation-settler="settleAssetMutation"
        :refresh-handler="retryFailedAssetReconciliation"
        @assets-loaded="onAssetsLoaded"
        @asset-read-started="onAssetReadStarted"
        @asset-mutated="onAssetMutated"
        @asset-mutation-uncertain="onAssetMutationUncertain"
        @asset-mutation-detached="onAssetMutationDetached"
        @mutation-state="onAssetMutationState"
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
    <LessonPublishReviewDialog
      :visible.sync="publishReviewVisible"
      :snapshot="publishReviewSnapshot"
      :publishing="publishing"
      :result="publishResult"
      @publish="publishReviewedVersion"
    />
  </div>
</template>

<script>
import HeaderBar from '@/components/HeaderBar.vue';
import LessonAssetManager from '@/components/LessonAssetManager.vue';
import LessonEngagementTrack from '@/components/lesson/LessonEngagementTrack.vue';
import LessonInteractionPanel from '@/components/lesson/LessonInteractionPanel.vue';
import LessonPublishReadiness from '@/components/lesson/LessonPublishReadiness.vue';
import LessonPublishReviewDialog from '@/components/lesson/LessonPublishReviewDialog.vue';
import LessonSimulationPanel from '@/components/lesson/LessonSimulationPanel.vue';
import LessonStepPromptEditor from '@/components/lesson/LessonStepPromptEditor.vue';
import LessonStepNavigator from '@/components/lesson/LessonStepNavigator.vue';
import RobotLessonPreview from '@/components/lesson/RobotLessonPreview.vue';
import SharedAssetPicker from '@/components/lesson/SharedAssetPicker.vue';
import SharedVisualImpactDialog from '@/components/lesson/SharedVisualImpactDialog.vue';
import { reserveAssetReadEpoch } from '@/components/lesson/asset-read-epoch';
import {
  bindClonedAssetToStep,
  collectAssetReferences,
  mergeAuthoringFields,
  replaceStepAssetReference,
  validSimulationEvidence as validateSimulationEvidence,
} from '@/components/lesson/lesson-builder-logic';
import Api from '@/apis/api';
import { isUncertainNestError } from '@/apis/nestHttp';

export default {
  name: 'LessonEditor',
  components: {
    HeaderBar,
    LessonAssetManager,
    LessonEngagementTrack,
    LessonInteractionPanel,
    LessonPublishReadiness,
    LessonPublishReviewDialog,
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
      validationResult: null,
      validationProofVersion: -1,
      validationRequestId: 0,
      previewing: false,
      previewProofVersion: -1,
      publishing: false,
      publishPreparing: false,
      publishReviewVisible: false,
      publishReviewSnapshot: null,
      publishReviewRequestId: 0,
      publishRequestId: 0,
      publishResult: null,
      readinessReady: false,
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
      simulationProofVersion: -1,
      proofVersion: 0,
      previewRequestId: 0,
      assetProofFingerprint: null,
      assetRefreshIsProofRecovery: false,
      assetMutationTokens: {},
      assetReconciliationEpoch: 0,
      assetReconciliationRequests: {},
      assetAppliedReadEpoch: 0,
      assetLatestReadEpoch: 0,
      editorDestroying: false,
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
    proofActionsDisabled() {
      return this.hasUnsafeProofState();
    },
    assetMutating() {
      return Object.keys(this.assetMutationTokens).length > 0;
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
  beforeDestroy() {
    this.editorDestroying = true;
    this.publishReviewVisible = false;
    this.proofVersion += 1;
    this.previewRequestId += 1;
    this.validationRequestId += 1;
    this.publishReviewRequestId += 1;
    this.publishRequestId += 1;
    this.promptSaveRequestId += 1;
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
    onAssetsLoaded(assets, metadata = {}) {
      if (this.editorDestroying) return;
      const readEpoch = Number(metadata && metadata.readEpoch);
      if (Number.isFinite(readEpoch)
        && (readEpoch < this.assetAppliedReadEpoch || readEpoch < this.assetLatestReadEpoch)) return false;
      if (Number.isFinite(readEpoch)) {
        this.assetLatestReadEpoch = Math.max(this.assetLatestReadEpoch, readEpoch);
        this.assetAppliedReadEpoch = readEpoch;
      }
      const nextAssets = Array.isArray(assets) ? assets : [];
      const fingerprint = this.buildAssetProofFingerprint(nextAssets);
      if (this.assetProofFingerprint !== null
        && fingerprint !== this.assetProofFingerprint
        && !this.assetRefreshIsProofRecovery) this.invalidatePreview();
      this.assetProofFingerprint = fingerprint;
      this.bundleAssets = nextAssets;
      return true;
    },
    onAssetReadStarted(metadata) {
      if (this.editorDestroying) return false;
      const readEpoch = Number(metadata && metadata.readEpoch);
      if (!Number.isFinite(readEpoch)) return false;
      this.assetLatestReadEpoch = Math.max(this.assetLatestReadEpoch, readEpoch);
      return true;
    },
    buildAssetProofFingerprint(assets) {
      return (Array.isArray(assets) ? assets : [])
        .map((asset) => JSON.stringify({
          assetId: asset.assetId || '',
          profile: asset.profile || '',
          assetKey: asset.assetKey || '',
          sha256: asset.sha256 || '',
          layer: asset.layer || '',
          role: asset.role || '',
          critical: asset.critical === true,
          mediaType: asset.mediaType || '',
          bytes: asset.bytes == null ? null : Number(asset.bytes),
          width: asset.width == null ? null : Number(asset.width),
          height: asset.height == null ? null : Number(asset.height),
          path: asset.path || asset.url || '',
          version: asset.version || asset.versionId || '',
        }))
        .sort()
        .join('|');
    },
    onAssetMutated() {
      if (this.editorDestroying) return;
      this.invalidatePreview();
    },
    onAssetMutationUncertain() {
      if (this.editorDestroying) return;
      this.invalidatePreview();
    },
    onAssetMutationState(payload) {
      if (this.editorDestroying) return;
      if (!payload || typeof payload.id !== 'string' || !payload.id) return;
      if (payload.active === true) this.$set(this.assetMutationTokens, payload.id, true);
      else this.$delete(this.assetMutationTokens, payload.id);
    },
    onAssetMutationDetached(payload) {
      if (this.editorDestroying) return;
      const id = payload && payload.id;
      if (typeof id !== 'string' || !id) return;
      this.$set(this.assetMutationTokens, id, true);
    },
    settleAssetMutation(payload) {
      if (this.editorDestroying) return false;
      const id = payload && payload.id;
      const outcome = payload && payload.outcome;
      if (typeof id !== 'string' || !this.assetMutationTokens[id]) return false;
      if (this.assetMutationTokens[id] === 'settling') return false;
      if (outcome !== 'rejected' && outcome !== 'success' && outcome !== 'uncertain') return false;
      this.$set(this.assetMutationTokens, id, 'settling');
      if (outcome === 'rejected') {
        this.$delete(this.assetMutationTokens, id);
        return true;
      }
      this.invalidatePreview();
      return this.reconcileAssetMutation(id);
    },
    reconcileAssetMutation(id) {
      if (this.editorDestroying || typeof id !== 'string') return false;
      if (this.assetMutationTokens[id] !== 'settling' && this.assetMutationTokens[id] !== 'reconcile-failed') return false;
      const requestId = this.assetReconciliationEpoch + 1;
      const readEpoch = reserveAssetReadEpoch();
      this.assetReconciliationEpoch = requestId;
      this.$set(this.assetReconciliationRequests, id, { requestId, readEpoch });
      this.$set(this.assetMutationTokens, id, 'settling');
      this.onAssetReadStarted({ readEpoch });
      const currentManager = this.$refs && this.$refs.assetManager;
      if (currentManager && typeof currentManager.trackAssetRead === 'function') currentManager.trackAssetRead(readEpoch);
      Api.lesson.listAssets(
        this.lessonId,
        'espTft',
        (result) => {
          if (this.editorDestroying) return;
          const activeRequest = this.assetReconciliationRequests[id];
          if (!activeRequest || activeRequest.requestId !== requestId || activeRequest.readEpoch !== readEpoch) return;
          if (requestId !== this.assetReconciliationEpoch || readEpoch !== this.assetLatestReadEpoch) {
            this.$delete(this.assetReconciliationRequests, id);
            if (this.assetMutationTokens[id] === 'settling') this.$set(this.assetMutationTokens, id, 'reconcile-failed');
            return;
          }
          const assets = result && result.assets;
          const manager = this.$refs && this.$refs.assetManager;
          const applied = manager && typeof manager.applyServerAssets === 'function'
            ? manager.applyServerAssets(assets, readEpoch)
            : this.onAssetsLoaded(assets, { readEpoch });
          if (applied === false) {
            this.$delete(this.assetReconciliationRequests, id);
            this.$set(this.assetMutationTokens, id, 'reconcile-failed');
            return;
          }
          this.$delete(this.assetReconciliationRequests, id);
          this.$delete(this.assetMutationTokens, id);
        },
        (message) => {
          if (this.editorDestroying) return;
          const activeRequest = this.assetReconciliationRequests[id];
          if (!activeRequest || activeRequest.requestId !== requestId || activeRequest.readEpoch !== readEpoch) return;
          if (requestId !== this.assetReconciliationEpoch || readEpoch !== this.assetLatestReadEpoch) {
            this.$delete(this.assetReconciliationRequests, id);
            if (this.assetMutationTokens[id] === 'settling') this.$set(this.assetMutationTokens, id, 'reconcile-failed');
            return;
          }
          this.$delete(this.assetReconciliationRequests, id);
          this.$set(this.assetMutationTokens, id, 'reconcile-failed');
          this.$message.error(message);
        },
      );
      return true;
    },
    retryAssetReconciliation(id) {
      if (this.editorDestroying || this.assetMutationTokens[id] !== 'reconcile-failed') return false;
      return this.reconcileAssetMutation(id);
    },
    retryFailedAssetReconciliation() {
      if (Object.keys(this.assetMutationTokens).some((key) => this.assetMutationTokens[key] === 'settling')) return true;
      const id = Object.keys(this.assetMutationTokens).find((key) => this.assetMutationTokens[key] === 'reconcile-failed');
      if (id) {
        this.retryAssetReconciliation(id);
        return true;
      }
      return false;
    },
    hasUnsafeProofState() {
      return Boolean(
        this.editorDestroying
        || this.promptDirty
        || Object.keys(this.dirtyStepKeys || {}).some((key) => this.dirtyStepKeys[key])
        || Object.keys(this.selectedStepDrafts || {}).length > 0
        || Object.keys(this.selectedAssetDrafts || {}).length > 0
        || this.stepDialogVisible
        || this.renameVisible
        || this.sharedImpactVisible
        || this.sharedImpactReconciling
        || this.savingStep
        || this.validating
        || this.previewing
        || this.rebindingSharedVisual
        || this.reordering
        || this.addingStep
        || this.renaming
        || this.publishing
        || this.publishPreparing
        || this.assetMutating
      );
    },
    isUncertainMutationError(error) {
      return isUncertainNestError(error);
    },
    handleUncertainMutationError(error, reconcile) {
      if (this.editorDestroying) return false;
      if (!this.isUncertainMutationError(error)) return false;
      this.invalidatePreview();
      if (typeof reconcile === 'function') reconcile.call(this);
      return true;
    },
    invalidatePreview() {
      if (this.editorDestroying) return;
      this.proofVersion += 1;
      this.previewRequestId += 1;
      this.validationRequestId += 1;
      this.publishReviewRequestId += 1;
      this.previewing = false;
      this.validating = false;
      this.preview = null;
      this.previewManifest = null;
      this.previewProofVersion = -1;
      this.simulationEvidence = null;
      this.simulationProofVersion = -1;
      this.validationResult = null;
      this.validationProofVersion = -1;
      this.publishReviewVisible = false;
      this.publishReviewSnapshot = null;
      this.publishResult = null;
      this.publishPreparing = false;
    },
    acceptSimulationEvidence(result, proofVersion) {
      if (this.editorDestroying) return;
      if (proofVersion !== this.proofVersion) return;
      if (result === null) {
        this.simulationEvidence = null;
        this.simulationProofVersion = -1;
        this.publishReviewRequestId += 1;
        this.publishReviewVisible = false;
        this.publishReviewSnapshot = null;
        this.publishResult = null;
        return;
      }
      if (!this.previewIdentityMatches(result, this.previewManifest)) return;
      if (!this.validSimulationEvidence(result, this.previewManifest)) return;
      this.simulationEvidence = result;
      this.simulationProofVersion = proofVersion;
    },
    validSimulationEvidence(result, expectedPreview) {
      return validateSimulationEvidence(result, expectedPreview, this.steps);
    },
    previewIdentityMatches(result, preview) {
      return Boolean(
        result && preview && result.checksum === preview.checksum && result.etag === preview.etag
        && result.preview && preview.preview
        && result.preview.profile === preview.preview.profile
        && result.preview.width === preview.preview.width
        && result.preview.height === preview.preview.height
      );
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
      if (this.editorDestroying) return;
      this.$message.error(msg || this.$t('lesson.sharedImpactCloneError'));
    },
    onCloneUncertain(payload) {
      if (this.editorDestroying) return;
      const key = payload && payload.assetKey;
      if (!key) {
        this.sharedImpactRebindError = this.$t('lesson.sharedImpactInvalidCloneResponse');
        return;
      }
      this.sharedImpactUncertainCloneKey = key;
      this.discoverUncertainClone();
    },
    discoverUncertainClone() {
      if (this.editorDestroying) return;
      const key = this.sharedImpactUncertainCloneKey;
      if (!key || this.sharedImpactReconciling) return;
      this.sharedImpactReconciling = true;
      this.sharedImpactRebindError = '';
      this.reloadAssets((assets) => {
        if (this.editorDestroying) return;
        const clone = (Array.isArray(assets) ? assets : []).find((asset) => asset.assetKey === key);
        this.sharedImpactReconciling = false;
        if (!this.validClonedAsset(clone)) {
          this.sharedImpactRebindError = this.$t('lesson.sharedImpactCloneNotFound', { key });
          return;
        }
        this.sharedImpactUncertainCloneKey = '';
        this.applyClonedVisual(clone);
      }, (msg) => {
        if (this.editorDestroying) return;
        this.sharedImpactReconciling = false;
        this.sharedImpactRebindError = this.$t('lesson.sharedImpactDiscoveryFailed', { reason: msg || key });
      });
    },
    reloadAssets(onSuccess, onError) {
      if (this.editorDestroying) return false;
      if (this.$refs.assetManager) this.$refs.assetManager.reload(onSuccess, onError);
      else if (onError) onError(this.$t('lesson.sharedImpactRefreshError'));
      return true;
    },
    applyClonedVisual(clonedAsset) {
      if (this.editorDestroying) return;
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
      if (this.editorDestroying) return;
      this.rebindingSharedVisual = false;
      this.sharedImpactRebindError = this.$t('lesson.sharedImpactRebindFailed', { reason: msg || this.$t('lesson.sharedImpactRefreshError') });
      this.reloadAssets();
      this.$message.error(this.sharedImpactRebindError);
    },
    refreshSharedVisualTruth(onSuccess, onError) {
      if (this.editorDestroying) return false;
      let remaining = 3;
      let failed = false;
      const done = () => {
        if (this.editorDestroying || failed) return;
        remaining -= 1;
        if (remaining === 0) onSuccess();
      };
      const fail = (msg) => {
        if (this.editorDestroying || failed) return;
        failed = true;
        this.assetRefreshIsProofRecovery = false;
        onError(msg);
      };
      this.assetRefreshIsProofRecovery = true;
      this.reloadAssets(done, fail);
      this.doValidate(done, fail);
      this.doPreview(done, fail, { allowUnsafe: true, storeProof: !this.promptDirty && !Object.keys(this.dirtyStepKeys).some((key) => this.dirtyStepKeys[key]) });
      return true;
    },
    rebindClonedVisual(clonedAsset) {
      if (this.editorDestroying) return;
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
          if (this.editorDestroying) return;
          this.fetchSteps({
            preservePrompt: true,
            onSuccess: () => {
              if (this.editorDestroying) return;
              this.refreshSharedVisualTruth(() => {
                if (this.editorDestroying) return;
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
              }, (msg) => {
                if (this.editorDestroying) return;
                this.failSharedVisualRebind(msg);
              });
            },
            onError: (msg) => {
              if (this.editorDestroying) return;
              this.failSharedVisualRebind(msg);
            },
          });
        },
        (msg) => {
          if (this.editorDestroying) return;
          this.failSharedVisualRebind(msg);
        },
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
      this.reordering = true;
      Api.lesson.reorderSteps(
        this.lessonId,
        order,
        (rows) => { this.invalidatePreview(); this.reordering = false; this.steps = rows; },
        (msg, error) => {
          this.reordering = false;
          this.handleUncertainMutationError(error, () => this.fetchSteps({ preservePrompt: true }));
          this.$message.error(msg);
        },
      );
    },
    deleteStep(row) {
      this.$confirm(this.$t('lesson.deleteStepConfirm', { key: row.stepKey }), this.$t('lesson.deleteStep'), { type: 'warning' })
        .then(() => {
          Api.lesson.deleteStep(
            this.lessonId,
            row.stepKey,
            (rows) => { this.invalidatePreview(); this.steps = rows; },
            (msg, error) => {
              this.handleUncertainMutationError(error, () => this.fetchSteps({ preservePrompt: true }));
              this.$message.error(msg);
            },
          );
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
      this.addingStep = true;
      Api.lesson.createStep(
        this.lessonId,
        payload,
        () => {
          this.invalidatePreview();
          this.addingStep = false;
          this.stepDialogVisible = false;
          this.lastSubject = f.subject; // prefill next step + teachingObject asset key
          this.fetchSteps();
        },
        (msg, error) => {
          this.addingStep = false;
          this.handleUncertainMutationError(error, () => this.fetchSteps({ preservePrompt: true }));
          this.$message.error(msg);
        },
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
        (l) => {
          this.invalidatePreview();
          this.renaming = false;
          this.renameVisible = false;
          this.lesson = l;
          this.$message.success(this.$t('lesson.renamed'));
        },
        (msg, error) => {
          this.renaming = false;
          this.handleUncertainMutationError(error, this.fetchAll);
          this.$message.error(msg);
        },
      );
    },
    doValidate(onSuccess, onError) {
      if (this.editorDestroying || this.hasUnsafeProofState()) return false;
      const requestId = this.validationRequestId + 1;
      const proofVersion = this.proofVersion;
      this.validationRequestId = requestId;
      this.publishReviewRequestId += 1;
      this.publishReviewVisible = false;
      this.publishReviewSnapshot = null;
      this.publishResult = null;
      this.validationResult = null;
      this.validationProofVersion = -1;
      this.validating = true;
      Api.lesson.validate(
        this.lessonId,
        (res) => {
          if (this.editorDestroying) return;
          if (requestId !== this.validationRequestId || proofVersion !== this.proofVersion) return;
          this.validating = false;
          this.validationResult = res && typeof res === 'object' ? res : { valid: false, profiles: [], errors: ['Invalid validation response'], warnings: [] };
          this.validationProofVersion = proofVersion;
          if (res && res.valid) this.$message.success(this.$t('lesson.validOk', { profiles: (res.profiles || []).join(', ') }));
          else this.$message.warning(this.$t('lesson.validFail'));
          if (typeof onSuccess === 'function') onSuccess(res);
        },
        (msg) => {
          if (this.editorDestroying) return;
          if (requestId !== this.validationRequestId || proofVersion !== this.proofVersion) return;
          this.validating = false;
          this.validationResult = { valid: false, profiles: [], errors: [msg], warnings: [] };
          this.validationProofVersion = proofVersion;
          this.$message.error(msg);
          if (typeof onError === 'function') onError(msg);
        },
      );
      return true;
    },
    validManifestPreviewResponse(result) {
      return Boolean(
        result && typeof result.checksum === 'string' && result.checksum
        && typeof result.etag === 'string' && result.etag
        && result.manifest && Array.isArray(result.manifest.steps)
        && result.preview && result.preview.profile === 'espTft'
        && Number(result.preview.width) === 480 && Number(result.preview.height) === 320
      );
    },
    doPreview(onSuccess, onError, options = {}) {
      if (this.editorDestroying) return false;
      if (!options.allowUnsafe && this.hasUnsafeProofState()) return false;
      const requestId = this.previewRequestId + 1;
      const previousProofVersion = this.proofVersion;
      this.proofVersion += 1;
      const proofVersion = this.proofVersion;
      if (this.validationProofVersion === previousProofVersion) this.validationProofVersion = proofVersion;
      this.publishReviewRequestId += 1;
      this.publishReviewVisible = false;
      this.publishReviewSnapshot = null;
      this.publishResult = null;
      this.previewRequestId = requestId;
      this.simulationEvidence = null;
      this.simulationProofVersion = -1;
      this.previewing = true;
      Api.lesson.manifestPreview(
        this.lessonId,
        'espTft',
        (res) => {
          if (this.editorDestroying) return;
          if (requestId !== this.previewRequestId || proofVersion !== this.proofVersion) return;
          this.previewing = false;
          if (!this.validManifestPreviewResponse(res)) {
            const message = 'Manifest preview returned an invalid response.';
            this.$message.error(message);
            if (typeof onError === 'function') onError(message);
            return;
          }
          if (options.storeProof !== false) {
            this.preview = { checksum: res.checksum, etag: res.etag };
            this.previewManifest = { manifest: res.manifest, preview: res.preview, checksum: res.checksum, etag: res.etag };
            this.previewProofVersion = proofVersion;
          }
          if (typeof onSuccess === 'function') onSuccess(res);
        },
        (msg) => {
          if (this.editorDestroying) return;
          if (requestId !== this.previewRequestId || proofVersion !== this.proofVersion) return;
          this.previewing = false;
          this.$message.error(msg);
          if (typeof onError === 'function') onError(msg);
        },
      );
      return true;
    },
    canPublishCurrentProof() {
      return Boolean(
        !this.editorDestroying
        && this.isDraft
        && !this.publishing
        && !this.publishPreparing
        && !this.hasUnsafeProofState()
        && this.readinessReady
        && this.validationResult
        && this.validationResult.valid === true
        && this.validationProofVersion === this.proofVersion
        && this.previewManifest
        && this.previewManifest.checksum
        && this.previewProofVersion === this.proofVersion
        && this.simulationEvidence
        && this.simulationProofVersion === this.proofVersion
        && this.validSimulationEvidence(this.simulationEvidence, this.previewManifest)
      );
    },
    publishReviewIsCurrent(snapshot = this.publishReviewSnapshot) {
      return Boolean(
        snapshot
        && snapshot === this.publishReviewSnapshot
        && snapshot.requestId === this.publishReviewRequestId
        && snapshot.proofVersion === this.proofVersion
        && snapshot.previewChecksum === (this.previewManifest && this.previewManifest.checksum)
        && this.canPublishCurrentProof()
      );
    },
    normalizeEvidenceAssets(assets) {
      return (Array.isArray(assets) ? assets : []).map((asset) => ({
        assetId: asset.assetId || asset.id || '',
        profile: asset.profile || '',
        assetKey: asset.assetKey || asset.asset_key || '',
        sha256: asset.sha256 || '',
        bytes: asset.bytes == null ? null : Number(asset.bytes),
        layer: asset.layer || '',
        role: asset.role || '',
        critical: asset.critical === true,
        path: asset.path || asset.url || '',
      })).sort((a, b) => JSON.stringify(a).localeCompare(JSON.stringify(b)));
    },
    collectLessonEvidence(lesson, onSuccess, onError) {
      if (!lesson || !lesson.lessonId) { onError('Lesson evidence identity is missing.'); return false; }
      let manifestResult = null;
      let assetResult = null;
      let settled = false;
      const fail = (message) => { if (!settled) { settled = true; onError(message); } };
      const finish = () => {
        if (settled || !manifestResult || !assetResult) return;
        settled = true;
        if (!this.validManifestPreviewResponse(manifestResult)) { onError('Authoritative manifest evidence was malformed.'); return; }
        const assets = this.normalizeEvidenceAssets(assetResult.assets);
        onSuccess({
          lessonId: lesson.lessonId,
          lessonKey: lesson.lessonKey,
          lessonVersion: Number(lesson.lessonVersion),
          checksum: manifestResult.checksum,
          etag: manifestResult.etag,
          manifest: manifestResult.manifest,
          assets,
          totalBytes: assets.reduce((sum, asset) => sum + (Number(asset.bytes) || 0), 0),
        });
      };
      Api.lesson.manifestPreview(lesson.lessonId, 'espTft', (result) => { manifestResult = result; finish(); }, fail);
      Api.lesson.listAssets(lesson.lessonId, 'espTft', (result) => { assetResult = result && Array.isArray(result.assets) ? result : { assets: [] }; finish(); }, fail);
      return true;
    },
    compareOriginalEvidence(before, after) {
      const stable = (value) => {
        if (Array.isArray(value)) return `[${value.map(stable).join(',')}]`;
        if (value && typeof value === 'object') return `{${Object.keys(value).sort().map((key) => `${JSON.stringify(key)}:${stable(value[key])}`).join(',')}}`;
        return JSON.stringify(value);
      };
      const differences = [];
      if (!before || !after) return { pass: false, differences: ['Original evidence is incomplete.'] };
      if (before.lessonId !== after.lessonId || Number(before.lessonVersion) !== Number(after.lessonVersion)) differences.push('Original lesson identity/version changed.');
      if (before.checksum !== after.checksum) differences.push(`Original checksum changed: ${before.checksum || 'missing'} -> ${after.checksum || 'missing'}.`);
      if (stable(before.manifest) !== stable(after.manifest)) differences.push('Original manifest bytes changed.');
      if (stable(before.assets) !== stable(after.assets)) differences.push('Original asset pins, digests, paths, or byte counts changed.');
      if (Number(before.totalBytes || 0) !== Number(after.totalBytes || 0)) differences.push(`Original asset bytes changed: ${before.totalBytes || 0} -> ${after.totalBytes || 0}.`);
      return { pass: differences.length === 0, differences };
    },
    doPublish() {
      if (this.assetMutating || !this.canPublishCurrentProof()) return false;
      const requestId = this.publishReviewRequestId + 1;
      const proofVersion = this.proofVersion;
      this.publishReviewRequestId = requestId;
      this.publishPreparing = true;
      this.publishResult = null;
      Api.lesson.listLessons(
        this.lesson.courseId,
        (lessons) => {
          if (this.editorDestroying || requestId !== this.publishReviewRequestId || proofVersion !== this.proofVersion) return;
          const original = (Array.isArray(lessons) ? lessons : []).find((candidate) => (
            candidate.lessonKey === this.lesson.lessonKey
            && Number(candidate.lessonVersion) === Number(this.lesson.lessonVersion) - 1
            && candidate.status === 'published'
          ));
          if (!original) {
            this.publishPreparing = false;
            this.$message.error(this.$t('lesson.publishOriginalMissing'));
            return;
          }
          this.collectLessonEvidence(original, (originalEvidence) => {
            if (this.editorDestroying || requestId !== this.publishReviewRequestId || proofVersion !== this.proofVersion) return;
            this.publishPreparing = false;
            this.publishReviewSnapshot = {
              requestId,
              proofVersion,
              originalLesson: original,
              originalEvidence,
              originalLessonId: original.lessonId,
              originalVersion: original.lessonVersion,
              originalChecksum: originalEvidence.checksum,
              originalAssets: originalEvidence.assets,
              originalBytes: originalEvidence.totalBytes,
              targetLessonId: this.lessonId,
              targetVersion: this.lesson.lessonVersion,
              stepCount: this.steps.length,
              assetCount: this.bundleAssets.length,
              previewProfile: this.previewManifest.preview.profile,
              previewWidth: this.previewManifest.preview.width,
              previewHeight: this.previewManifest.preview.height,
              previewChecksum: this.previewManifest.checksum,
              previewEtag: this.previewManifest.etag,
              simulationChecksum: this.simulationEvidence.checksum,
              simulationEtag: this.simulationEvidence.etag,
              simulationTerminationReason: this.simulationEvidence.simulation.terminationReason,
              simulationCompletionEvent: this.simulationEvidence.simulation.trace[this.simulationEvidence.simulation.trace.length - 1] || null,
              validationResult: JSON.parse(JSON.stringify(this.validationResult)),
              validationProfiles: Array.isArray(this.validationResult.profiles) ? this.validationResult.profiles.map((profile) => typeof profile === 'string' ? profile : (profile.profile || profile.name || 'profile')) : [],
            };
            this.publishReviewVisible = true;
          }, (message) => {
            if (this.editorDestroying || requestId !== this.publishReviewRequestId) return;
            this.publishPreparing = false;
            this.$message.error(message);
          });
        },
        (message) => {
          if (this.editorDestroying || requestId !== this.publishReviewRequestId) return;
          this.publishPreparing = false;
          this.$message.error(message);
        },
      );
      return true;
    },
    publishReviewedVersion(snapshot) {
      if (this.publishing || !this.publishReviewIsCurrent(snapshot)) return false;
      const requestId = this.publishRequestId + 1;
      this.publishRequestId = requestId;
      this.publishing = true;
      this.publishResult = null;
      Api.lesson.publish(
        this.lessonId,
        (result) => {
          if (this.editorDestroying || requestId !== this.publishRequestId) return;
          if (!result || Number(result.lessonVersion) !== Number(snapshot.targetVersion) || typeof result.checksum !== 'string' || !result.checksum) {
            this.publishing = false;
            this.publishResult = { type: 'warning', title: this.$t('lesson.publishUncertain'), targetEvidence: result || null };
            return;
          }
          let originalAfter = null;
          let targetAfter = null;
          let evidenceError = '';
          let completed = 0;
          const finish = () => {
            completed += 1;
            if (completed < 2 || this.editorDestroying || requestId !== this.publishRequestId) return;
            this.publishing = false;
            const originalComparison = evidenceError
              ? { pass: false, differences: [evidenceError] }
              : this.compareOriginalEvidence(snapshot.originalEvidence, originalAfter);
            const targetEvidence = targetAfter ? {
              lessonVersion: targetAfter.lessonVersion,
              checksum: targetAfter.checksum,
              etag: targetAfter.etag,
              assetCount: targetAfter.assets.length,
              bytes: targetAfter.totalBytes,
              publishChecksum: result.checksum,
            } : { lessonVersion: result.lessonVersion, checksum: result.checksum, assetCount: 0 };
            this.publishResult = {
              type: originalComparison.pass ? 'success' : 'error',
              title: originalComparison.pass ? this.$t('lesson.publishVerified') : this.$t('lesson.publishVerificationFailed'),
              originalComparison,
              targetEvidence,
            };
            this.publishMessage = this.$t('lesson.publishedMsg', { v: result.lessonVersion, checksum: result.checksum });
            this.fetchAll();
          };
          this.collectLessonEvidence(snapshot.originalLesson, (evidence) => { originalAfter = evidence; finish(); }, (message) => { evidenceError = `Original verification unavailable: ${message}`; finish(); });
          this.collectLessonEvidence({ ...this.lesson, lessonVersion: result.lessonVersion }, (evidence) => { targetAfter = evidence; finish(); }, (message) => { evidenceError = evidenceError || `Target evidence unavailable: ${message}`; finish(); });
        },
        (message, error) => {
          if (this.editorDestroying || requestId !== this.publishRequestId) return;
          this.publishing = false;
          const uncertain = this.isUncertainMutationError(error);
          this.publishResult = { type: uncertain ? 'warning' : 'error', title: uncertain ? this.$t('lesson.publishUncertain') : message };
          this.$message.error(message);
        },
      );
      return true;
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
