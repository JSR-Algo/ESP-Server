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
        </h2>
      </div>
      <div class="right-operations" v-if="lesson">
        <el-button v-if="isDraft" size="small" @click="openRename">{{ $t('lesson.rename') }}</el-button>
        <el-button size="small" @click="doValidate" :loading="validating" :disabled="proofActionsDisabled">{{ $t('lesson.validate') }}</el-button>
        <el-button v-if="lessonCapabilities.exactEspTftPreview" size="small" @click="doPreview" :loading="previewing" :disabled="proofActionsDisabled">{{ $t('lesson.previewManifest') }}</el-button>
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
      <LessonSdSyncStatus
        v-if="lesson && lesson.status === 'published' && lessonAssetGenerationStatus"
        :status="lessonAssetGenerationStatus"
        :loading="lessonAssetGenerationLoading"
        :retrying="lessonAssetGenerationRetrying"
        @retry="retryLessonSdSync"
      />
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
        <LessonStepNavigator v-model="selectedStepIndex" :steps="steps" :editable="isDraft && !lessonVisualStepMutationBlocked && !addingStep && !reordering && !deletingStepKey" @add="openStepDialog" />
        <main class="lesson-studio__canvas">
          <div class="lesson-studio__toolbar">
            <div>
              <span class="eyebrow">VISUAL LESSON BUILDER</span>
              <h3>{{ selectedStep ? promptDraft : 'Choose or add a lesson step' }}</h3>
            </div>
            <el-button v-if="isDraft && selectedStep" type="primary" size="small" :loading="savingStep" :disabled="savingStep || lessonVisualStepMutationBlocked || rebindingSharedVisual || !selectedStepDirty" @click="saveSelectedStep">
              Save step
            </el-button>
          </div>
          <section class="lesson-visual-pair" v-loading="savingLessonVisuals" :aria-busy="savingLessonVisuals ? 'true' : 'false'">
            <div class="lesson-visual-pair__heading">
              <div>
                <h4>{{ $t('lesson.visualPairTitle') }}</h4>
                <p>{{ $t('lesson.visualPairWholeLesson') }}</p>
              </div>
              <span v-if="savingLessonVisuals" role="status" aria-live="polite">{{ $t('common.loading') }}</span>
            </div>
            <p v-if="!steps.length" class="lesson-visual-pair__notice" role="status">{{ $t('lesson.visualPairNoSteps') }}</p>
            <p
              v-else-if="lessonVisualReconciliationRequired"
              class="lesson-visual-pair__notice"
              role="status"
              aria-live="polite"
            >
              {{ $t('lesson.visualPairReloadFailed') }}
            </p>
            <p
              v-else-if="pendingLessonVisualPair && (!lessonVisualPair.backgroundAssetVersionId || !lessonVisualPair.objectAssetVersionId)"
              class="lesson-visual-pair__notice"
              role="status"
              aria-live="polite"
            >
              {{ $t('lesson.visualPairRequired') }}
            </p>
            <div v-if="lessonCapabilities.sharedVisualAuthoring || lessonCapabilities.exactEspTftPreview" class="cinematic-pickers">
              <div v-if="!isDraft" class="immutable-version-message" data-testid="immutable-version-message">
                Background and teaching object update this lesson immediately. Robot overlay remains read-only for published lessons.
              </div>
              <div data-testid="lesson-background-selector">
                <CinematicLayerPicker
                  layer-slot="backgroundScene"
                  title="Background scene"
                  :assets="cinematicLibraries.backgroundScene"
                  :selected-version-id="selectedVisualVersionId('backgroundScene')"
                  :loading="cinematicLibraryLoading.backgroundScene"
                  :error="cinematicLibraryErrors.backgroundScene"
                  :disabled="lessonVisualSelectionDisabled"
                  @select="selectCinematicLayer"
                />
              </div>
              <div data-testid="lesson-object-selector">
                <CinematicLayerPicker
                  layer-slot="teachingObject"
                  title="Teaching object"
                  :assets="cinematicLibraries.teachingObject"
                  :selected-version-id="selectedVisualVersionId('teachingObject')"
                  :loading="cinematicLibraryLoading.teachingObject"
                  :error="cinematicLibraryErrors.teachingObject"
                  :disabled="lessonVisualSelectionDisabled"
                  @select="selectCinematicLayer"
                />
              </div>
              <CinematicLayerPicker
                layer-slot="robotOverlay"
                title="Robot overlay"
                :assets="cinematicLibraries.robotOverlay"
                :selected-version-id="selectedVisualVersionId('robotOverlay')"
                :loading="cinematicLibraryLoading.robotOverlay"
                :error="cinematicLibraryErrors.robotOverlay"
                :disabled="!isDraft || cinematicRefSaving || lessonVisualSelectionDisabled"
                @select="selectCinematicLayer"
              />
            </div>
          </section>
          <div v-if="selectedStep" class="lesson-studio__workbench">
            <div>
              <TvideoTemplatePanel
                v-if="selectedStepIndex === 0"
                v-model="selectedTemplateAuthoring"
                :assets="templateAssets"
                :background="selectedTemplateBackground"
              />
              <TvideoVariantBatchPanel
                v-if="isDraft && selectedStepIndex === 0 && selectedTemplateAuthoring"
                :background="selectedTemplateBackground"
                :template-authoring="selectedTemplateAuthoring"
                :generation-result="variantGenerationResult"
                :readiness="variantBatchReadiness"
                :generating="generatingVariants"
                :checking="checkingVariantReadiness"
                @generate="generateTvideoVariants"
                @readiness="runTvideoBatchReadiness"
              />
              <lesson-step-prompt-editor
                v-model="promptDraft"
                :disabled="!isDraft || savingStep || lessonVisualStepMutationBlocked || rebindingSharedVisual"
                @input="onPromptInput"
              />
              <el-card class="content-card" shadow="never">
                <div slot="header"><strong>Step details</strong></div>
                <el-form label-position="top" size="small">
                  <div class="grid-2">
                    <el-form-item :label="$t('lesson.subjectLabel')" required>
                      <el-input :value="selectedContent.subject" data-testid="lesson-step-subject" :disabled="!isDraft || savingStep || lessonVisualStepMutationBlocked || rebindingSharedVisual" @input="updateSelectedContent('subject', $event)" />
                    </el-form-item>
                    <el-form-item :label="$t('lesson.helperText')">
                      <el-input :value="selectedContent.helperText" data-testid="lesson-step-helper" :disabled="!isDraft || savingStep || lessonVisualStepMutationBlocked || rebindingSharedVisual" @input="updateSelectedContent('helperText', $event)" />
                    </el-form-item>
                  </div>
                  <el-form-item :label="$t('lesson.l1TransferHint')">
                    <el-input :value="selectedContent.l1TransferHint" data-testid="lesson-step-l1-hint" :disabled="!isDraft || savingStep || lessonVisualStepMutationBlocked || rebindingSharedVisual" @input="updateSelectedContent('l1TransferHint', $event)" />
                  </el-form-item>
                </el-form>
              </el-card>
              <LessonInteractionPanel v-model="selectedAuthoring" :disabled="!isDraft || savingStep || lessonVisualStepMutationBlocked || rebindingSharedVisual" />
            </div>
            <div class="preview-stack">
              <section v-if="cinematicDemoUrl" class="cinematic-effect preview-surface" data-testid="cinematic-design-reference">
                <div class="preview-heading">
                  <span class="eyebrow">CINEMATIC DESIGN REFERENCE</span>
                  <span class="preview-heading__hint">Hiệu ứng gốc của bài — background video + robot bay vào / đi tới / chào</span>
                  <button type="button" class="replay-btn" @click="replayCinematicFlyIn">↺ Xem lại bay vào</button>
                </div>
                <div class="cinematic-frame">
                  <iframe ref="cinematicFrame" :src="cinematicDemoUrl" title="Hiệu ứng bài học (bản thiết kế)" loading="lazy" allow="autoplay" @load="pushCinematicStep" />
                </div>
                <p class="cinematic-note">Mẫu (template): giữ nguyên <strong>bố cục + hiệu ứng</strong> (robot bay vào / đi tới / chào). Click từng step bên trái để đổi <strong>từ, prompt và vật thể</strong> tương ứng trên video.</p>
              </section>
              <section v-if="previewManifest" class="preview-surface exact-renderer-surface" data-testid="exact-robot-renderer">
                <div class="preview-heading">
                  <span class="eyebrow">EXACT ROBOT RENDERER</span>
                  <span class="preview-heading__hint">TFT 480×320 — lớp ảnh tĩnh, visual state và fallback theo manifest đã chuẩn hóa</span>
                </div>
                <RobotLessonPreview
                  :manifest="previewManifest.manifest"
                  :renderer-metadata="previewManifest"
                  :step-index="selectedStepIndex"
                  @path-change="previewPath = $event"
                />
              </section>
              <div v-else class="preview-empty">
                <strong>Robot preview</strong>
                <span>Generate the espTft manifest preview to inspect the exact 480×320 scene.</span>
                <el-button size="small" :disabled="proofActionsDisabled" @click="doPreview">Generate preview</el-button>
              </div>
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
            :validation="validationResult"
            :validation-result="validationResult"
            :validation-current="validationProofVersion === proofVersion"
            @ready-change="readinessReady = $event"
          />
        </main>
      </section>

      <!-- Steps -->
      <el-card shadow="never" class="content-area">
        <div slot="header" class="card-header">Advanced step structure · {{ steps.length }} steps</div>
        <div class="advanced-steps-scroll">
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
                <el-button type="text" size="small" :disabled="lessonVisualStepMutationBlocked || addingStep || reordering || deletingStepKey || scope.$index === 0" @click="moveStep(scope.$index, -1)">↑</el-button>
                <el-button type="text" size="small" :disabled="lessonVisualStepMutationBlocked || addingStep || reordering || deletingStepKey || scope.$index === steps.length - 1" @click="moveStep(scope.$index, 1)">↓</el-button>
                <el-button type="text" size="small" class="danger-text" :loading="deletingStepKey === scope.row.stepKey" :disabled="lessonVisualStepMutationBlocked || addingStep || reordering || deletingStepKey" @click="deleteStep(scope.row)">{{ $t('lesson.deleteStep') }}</el-button>
              </template>
            </el-table-column>
            <template slot="empty"><span class="muted">{{ $t('lesson.noSteps') }}</span></template>
          </el-table>
        </div>

        <!-- Add step (draft only) -->
        <div v-if="isDraft" class="add-row">
          <el-button type="primary" size="small" icon="el-icon-plus" :disabled="lessonVisualStepMutationBlocked || addingStep || reordering || deletingStepKey" @click="openStepDialog">{{ $t('lesson.addStepTitle') }}</el-button>
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
        :disabled="savingStep || lessonVisualStepMutationBlocked || rebindingSharedVisual || assetMutating"
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
        <el-button type="primary" size="small" :loading="addingStep" :disabled="lessonVisualStepMutationBlocked || addingStep || reordering || deletingStepKey" @click="addStep">{{ $t('lesson.save') }}</el-button>
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
      :locked="!!publishUncertainState || publishReconciling"
      :reconciling="publishReconciling"
      :result="publishResult"
      @publish="publishReviewedVersion"
      @reconcile="reconcileUncertainPublish"
    />
  </div>
</template>

<script>
import HeaderBar from '@/components/HeaderBar.vue';
import LessonAssetManager from '@/components/LessonAssetManager.vue';
import CinematicLayerPicker, { SLOT_CATEGORY as CINEMATIC_SLOT_CATEGORY } from '@/components/lesson/CinematicLayerPicker.vue';
import LessonEngagementTrack from '@/components/lesson/LessonEngagementTrack.vue';
import LessonInteractionPanel from '@/components/lesson/LessonInteractionPanel.vue';
import LessonPublishReadiness from '@/components/lesson/LessonPublishReadiness.vue';
import LessonPublishReviewDialog from '@/components/lesson/LessonPublishReviewDialog.vue';
import LessonSdSyncStatus from '@/components/lesson/LessonSdSyncStatus.vue';
import LessonSimulationPanel from '@/components/lesson/LessonSimulationPanel.vue';
import LessonStepPromptEditor from '@/components/lesson/LessonStepPromptEditor.vue';
import LessonStepNavigator from '@/components/lesson/LessonStepNavigator.vue';
import RobotLessonPreview from '@/components/lesson/RobotLessonPreview.vue';
import SharedVisualImpactDialog from '@/components/lesson/SharedVisualImpactDialog.vue';
import TvideoTemplatePanel from '@/components/lesson/TvideoTemplatePanel.vue';
import TvideoVariantBatchPanel from '@/components/lesson/TvideoVariantBatchPanel.vue';
import { reserveAssetReadEpoch } from '@/components/lesson/asset-read-epoch';
import { canonicalLessonVisualPair, buildLessonVisualRequest } from '@/components/lesson/lesson-visual-selection';
import {
  bindClonedAssetToStep,
  collectAssetReferences,
  mergeAuthoringFields,
  replaceStepAssetReference,
  validSimulationEvidence as validateSimulationEvidence,
} from '@/components/lesson/lesson-builder-logic';
import {
  normalizeBatchReadiness,
  sharedBackgroundOption,
  mergeStepBodyForSave,
} from '@/components/lesson/tvideo-template-logic';
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
import { isUncertainNestError } from '@/apis/nestHttp';
import { loadLessonRolloutCapabilities, NO_LESSON_ROLLOUT_CAPABILITIES } from '@/utils/lessonRolloutCapabilities';
import { loadCanonicalDemoContext } from '@/utils/canonicalDemoContext.mjs';
import { createAuthoringDirtyHandle } from '@/utils/serviceWorkerUpdateSafety.mjs';

export default {
  name: 'LessonEditor',
  components: {
    CinematicLayerPicker,
    HeaderBar,
    LessonAssetManager,
    LessonEngagementTrack,
    LessonInteractionPanel,
    LessonPublishReadiness,
    LessonPublishReviewDialog,
    LessonSdSyncStatus,
    LessonSimulationPanel,
    LessonStepPromptEditor,
    LessonStepNavigator,
    RobotLessonPreview,
    SharedVisualImpactDialog,
    TvideoTemplatePanel,
    TvideoVariantBatchPanel,
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
      publishUncertainState: null,
      publishReconciling: false,
      publishReconcileRequestId: 0,
      readinessReady: false,
      renaming: false,
      lastSubject: '',
      // Lifted bundle assets from LessonAssetManager (keyed by layer downstream).
      bundleAssets: [],
      sharedBackgrounds: [],
      cinematicLibraries: { backgroundScene: [], teachingObject: [], robotOverlay: [] },
      cinematicLibraryLoading: { backgroundScene: false, teachingObject: false, robotOverlay: false },
      cinematicLibraryErrors: { backgroundScene: '', teachingObject: '', robotOverlay: '' },
      cinematicRefSaving: false,
      savingLessonVisuals: false,
      pendingLessonVisualPair: null,
      lessonVisualReconciliationRequired: false,
      sharedVisualAssets: [],
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
      promptDraft: '',
      promptDirty: false,
      promptStepKey: '',
      promptEditRevision: 0,
      promptSaveRequestId: 0,
      selectedStepDrafts: {},
      selectedAssetDrafts: {},
      selectedContentDrafts: {},
      dirtyStepKeys: {},
      savingStepKeys: {},
      stepDraftRevisions: {},
      addingStep: false,
      reordering: false,
      stepEditRevisions: {},
      savingStep: false,
      deletingStepKey: '',
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
      studioRevision: 0,
      previewPath: null,
      renameVisible: false,
      titleDraft: '',
      generatingVariants: false,
      checkingVariantReadiness: false,
      variantGenerationResult: null,
      variantBatchReadiness: null,
      sharedImpactVisible: false,
      sharedImpactIntent: null,
      rebindingSharedVisual: false,
      sharedImpactClonedAsset: null,
      sharedImpactRebindError: '',
      sharedImpactUncertainCloneKey: '',
      sharedImpactReconciling: false,
      lessonLoadRequestId: 0,
      lessonStepsRequestId: 0,
      lessonVisualSaveRequestId: 0,
      lessonAssetGenerationStatus: null,
      lessonAssetGenerationLoading: false,
      lessonAssetGenerationRequestId: 0,
      lessonAssetGenerationRetrying: false,
      lessonAssetGenerationRetryRequestId: 0,
      lessonAssetGenerationPollTimer: null,
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
    cinematicDemoUrl() {
      // EVERY lesson uses the same cinematic template (background video + robot
      // fly-in / walk / greet). It is fully parameterised, so each lesson drives it
      // with its own background, teaching object, word and per-step motion.
      // embed=1 hides the demo's marketing panel — show only the tablet cinematic.
      return this.lesson ? '/tvideo-demo/index.html?embed=1' : '';
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
    templateAssets() {
      return [...this.bundleAssets, ...this.sharedBackgrounds];
    },
    selectedStep() {
      return this.steps[this.selectedStepIndex] || null;
    },
    lessonVisualPair() {
      return this.pendingLessonVisualPair || canonicalLessonVisualPair(this.steps);
    },
    selectedTemplateBackground() {
      return this.sharedBackgrounds.find((asset) => asset.assetVersionId === this.lessonVisualPair.backgroundAssetVersionId) || null;
    },
    selectedBackgroundKey() {
      return this.lessonVisualPair.backgroundAssetKey;
    },
    pickedObjectKey() {
      return this.lessonVisualPair.objectAssetKey;
    },
    lessonVisualSelectionDisabled() {
      return this.savingLessonVisuals
        || this.savingStep
        || this.rebindingSharedVisual
        || this.assetMutating
        || this.sharedImpactReconciling
        || Object.keys(this.savingStepKeys).some((key) => this.savingStepKeys[key])
        || this.addingStep
        || this.reordering
        || Boolean(this.deletingStepKey)
        || !this.steps.length;
    },
    lessonVisualStepMutationBlocked() {
      return this.savingLessonVisuals
        || Boolean(this.pendingLessonVisualPair)
        || this.lessonVisualReconciliationRequired;
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
    savingSelectedStep() {
      return Boolean(this.selectedStep && this.savingStepKeys[this.selectedStep.stepKey]);
    },
    hasUnsavedDrafts() {
      return Object.keys(this.dirtyStepKeys).some((key) => this.dirtyStepKeys[key]);
    },
    hasPendingAuthoringChanges() {
      return this.hasUnsavedDrafts
        || Boolean(this.pendingLessonVisualPair)
        || this.stepDialogVisible
        || this.renameVisible
        || this.addingStep
        || this.reordering
        || Boolean(this.deletingStepKey)
        || this.renaming
        || this.publishing
        || this.savingLessonVisuals
        || Object.keys(this.savingStepKeys).some((key) => this.savingStepKeys[key]);
    },
    selectedAuthoring: {
      get() {
        if (!this.selectedStep) return mergeAuthoringFields({}, {});
        return this.selectedStepDrafts[this.selectedStep.stepKey]
          || mergeAuthoringFields(this.selectedStep.stepBody || {}, {});
      },
      set(value) {
        if (!this.selectedStep || this.savingStep || this.lessonVisualStepMutationBlocked || this.rebindingSharedVisual) return;
        this.$set(this.selectedStepDrafts, this.selectedStep.stepKey, value);
        this.$set(this.dirtyStepKeys, this.selectedStep.stepKey, true);
        this.bumpStepEditRevision(this.selectedStep.stepKey);
        this.markStudioChanged(this.selectedStep.stepKey);
        this.invalidatePreview();
      },
    },
    selectedTemplateAuthoring: {
      get() {
        return this.selectedAuthoring.templateAuthoring || null;
      },
      set(value) {
        if (!this.selectedStep || this.lessonVisualStepMutationBlocked) return;
        const next = { ...this.selectedAuthoring };
        if (value) next.templateAuthoring = value;
        else delete next.templateAuthoring;
        this.selectedAuthoring = next;
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
        if (!this.selectedStep || this.lessonVisualStepMutationBlocked) return;
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
    selectedStepKey() {
      this.resetPromptDraft(this.selectedStep);
    },
    '$route.query.demoSource'() {
      this.loadCanonicalDemo();
    },
    selectedStepIndex() {
      this.pushCinematicStep(true);
    },
    previewManifest(next, prev) {
      this.syncCinematicSoon();
      // Show the fly-in intro when the preview first appears (assets are loaded by now).
      if (next && !prev) this.$nextTick(() => setTimeout(() => this.replayCinematicFlyIn(), 900));
    },
    '$route.query.lessonId'(value, previous) {
      if (value === previous) return;
      if (!value) {
        this.$router.replace('/course-management');
        return;
      }
      this.savingLessonVisuals = false;
      this.pendingLessonVisualPair = null;
      this.lessonVisualReconciliationRequired = false;
      this.lessonStepsRequestId += 1;
      this.lessonVisualSaveRequestId += 1;
      this.deletingStepKey = '';
      this.addingStep = false;
      this.reordering = false;
      this.resetLessonAssetGenerationStatus();
      this.fetchAll();
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
    // A cinematic iframe announces 'tvideo-ready' once its message listener is
    // attached; push the current step then so the sync never loses the load race.
    this._tvideoReadyHandler = (ev) => {
      if (ev && ev.data && ev.data.type === 'tvideo-ready') this.syncCinematicSoon();
    };
    window.addEventListener('message', this._tvideoReadyHandler);
  },
  beforeDestroy() {
    this.editorDestroying = true;
    if (this._tvideoReadyHandler) window.removeEventListener('message', this._tvideoReadyHandler);
    this.publishReviewVisible = false;
    this.proofVersion += 1;
    this.previewRequestId += 1;
    this.validationRequestId += 1;
    this.publishReviewRequestId += 1;
    this.publishRequestId += 1;
    this.publishReconcileRequestId += 1;
    this.promptSaveRequestId += 1;
    this.lessonLoadRequestId += 1;
    this.lessonStepsRequestId += 1;
    this.lessonVisualSaveRequestId += 1;
    this.lessonAssetGenerationRequestId += 1;
    this.lessonAssetGenerationRetryRequestId += 1;
    this.lessonAssetGenerationRetrying = false;
    this.clearLessonAssetGenerationPoll();
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
      if (this.lessonCapabilities.sharedVisualAuthoring || this.lessonCapabilities.exactEspTftPreview) this.loadCinematicLibraries();
    },
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
        || this.savingLessonVisuals
        || Boolean(this.pendingLessonVisualPair)
        || this.validating
        || this.previewing
        || this.rebindingSharedVisual
        || this.reordering
        || this.addingStep
        || Boolean(this.deletingStepKey)
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
      if (this.publishUncertainState || this.publishReconciling) return false;
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
    generateTvideoVariants(payload) {
      this.generatingVariants = true;
      this.variantGenerationResult = null;
      this.variantBatchReadiness = null;
      Api.lesson.generateVariants(
        this.lessonId,
        payload,
        (result) => {
          this.generatingVariants = false;
          this.variantGenerationResult = result || { created: [], failed: [] };
          const created = Array.isArray(this.variantGenerationResult.created) ? this.variantGenerationResult.created.length : 0;
          const failed = Array.isArray(this.variantGenerationResult.failed) ? this.variantGenerationResult.failed.length : 0;
          this.$message[created ? 'success' : 'warning'](`${created} variants created${failed ? `; ${failed} failed` : ''}.`);
        },
        (msg) => { this.generatingVariants = false; this.$message.error(msg); },
      );
    },
    runTvideoBatchReadiness(lessonIds) {
      if (!Array.isArray(lessonIds) || !lessonIds.length) return;
      this.checkingVariantReadiness = true;
      Api.lesson.assessBatchReadiness(
        lessonIds,
        (result) => {
          this.checkingVariantReadiness = false;
          this.variantBatchReadiness = normalizeBatchReadiness(result);
        },
        (msg) => { this.checkingVariantReadiness = false; this.$message.error(msg); },
      );
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
      const requestId = this.lessonLoadRequestId + 1;
      const lessonId = this.lessonId;
      this.lessonLoadRequestId = requestId;
      this.resetLessonAssetGenerationStatus();
      this.loading = true;
      Api.lesson.getLesson(
        lessonId,
        (l) => {
          if (this.editorDestroying || requestId !== this.lessonLoadRequestId || lessonId !== this.lessonId) return;
          this.lesson = l;
          this.loading = false;
          this.loadLessonAssetGenerationStatus();
          this.fetchSteps();
        },
        (msg) => {
          if (this.editorDestroying || requestId !== this.lessonLoadRequestId || lessonId !== this.lessonId) return;
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
      Api.lesson.listSharedBackgrounds(
        (rows) => {
          this.sharedBackgrounds = rows
            .filter((row) => row.publication_state === 'published' && row.compatibility_metadata)
            .map(sharedBackgroundOption);
        },
        () => { this.sharedBackgrounds = []; },
      );
    },
    fetchSteps(options = {}) {
      const requestId = this.lessonStepsRequestId + 1;
      const lessonLoadRequestId = this.lessonLoadRequestId;
      const lessonId = this.lessonId;
      this.lessonStepsRequestId = requestId;
      const requestIsCurrent = () => !this.editorDestroying
        && requestId === this.lessonStepsRequestId
        && lessonLoadRequestId === this.lessonLoadRequestId
        && lessonId === this.lessonId;
      Api.lesson.listSteps(lessonId, (rows) => {
        if (!requestIsCurrent()) return;
        const selectedKey = this.selectedStepKey;
        this.steps = rows;
        if (this.lessonVisualReconciliationRequired) {
          this.pendingLessonVisualPair = null;
          this.lessonVisualReconciliationRequired = false;
        }
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
        // Auto-generate the espTft preview for the canonical cinematic lesson so the
        // fly-in + step→video sync work without the author clicking "Preview" first.
        if (this.cinematicDemoUrl && this.lessonCapabilities.exactEspTftPreview
          && !this.previewManifest && !this.previewing) {
          this.$nextTick(() => this.doPreview());
        }
      }, (msg) => {
        if (!requestIsCurrent()) return;
        this.$message.error(msg);
        if (options.onError) options.onError(msg);
      });
    },
    resetLessonAssetGenerationStatus() {
      this.clearLessonAssetGenerationPoll();
      this.lessonAssetGenerationStatus = null;
      this.lessonAssetGenerationLoading = false;
      this.lessonAssetGenerationRequestId += 1;
      this.lessonAssetGenerationRetrying = false;
      this.lessonAssetGenerationRetryRequestId += 1;
    },
    clearLessonAssetGenerationPoll() {
      if (this.lessonAssetGenerationPollTimer) {
        clearTimeout(this.lessonAssetGenerationPollTimer);
        this.lessonAssetGenerationPollTimer = null;
      }
    },
    lessonAssetGenerationIsIncomplete(status) {
      return !status || !status.allConnectedCurrent;
    },
    scheduleLessonAssetGenerationPoll(status = this.lessonAssetGenerationStatus) {
      this.clearLessonAssetGenerationPoll();
      if (this.editorDestroying || !this.lesson || this.lesson.status !== 'published'
        || !this.lessonAssetGenerationIsIncomplete(status)) return false;
      const lessonId = this.lessonId;
      this.lessonAssetGenerationPollTimer = setTimeout(() => {
        this.lessonAssetGenerationPollTimer = null;
        if (!this.editorDestroying && lessonId === this.lessonId) this.loadLessonAssetGenerationStatus({ silent: true });
      }, 12000);
      return true;
    },
    loadLessonAssetGenerationStatus(options = {}) {
      if (this.editorDestroying || !this.lesson || this.lesson.status !== 'published') {
        this.resetLessonAssetGenerationStatus();
        return false;
      }
      const requestId = this.lessonAssetGenerationRequestId + 1;
      const lessonId = this.lessonId;
      this.lessonAssetGenerationRequestId = requestId;
      this.clearLessonAssetGenerationPoll();
      if (!options.silent) this.lessonAssetGenerationLoading = true;
      Api.lesson.getLessonAssetGenerationStatus(
        (status) => {
          if (this.editorDestroying || requestId !== this.lessonAssetGenerationRequestId || lessonId !== this.lessonId) return;
          this.lessonAssetGenerationLoading = false;
          this.lessonAssetGenerationStatus = status;
          this.scheduleLessonAssetGenerationPoll(status);
        },
        (msg) => {
          if (this.editorDestroying || requestId !== this.lessonAssetGenerationRequestId || lessonId !== this.lessonId) return;
          this.lessonAssetGenerationLoading = false;
          if (!options.silent) this.lessonAssetGenerationStatus = null;
          this.scheduleLessonAssetGenerationPoll(this.lessonAssetGenerationStatus);
          if (!options.silent) this.$message.error(msg);
        },
      );
      return true;
    },
    retryLessonSdSync() {
      if (this.editorDestroying || this.lessonAssetGenerationRetrying
        || !this.lesson || this.lesson.status !== 'published') return false;
      const lessonId = this.lessonId;
      const lessonLoadRequestId = this.lessonLoadRequestId;
      const retryRequestId = this.lessonAssetGenerationRetryRequestId + 1;
      this.lessonAssetGenerationRetryRequestId = retryRequestId;
      this.clearLessonAssetGenerationPoll();
      this.lessonAssetGenerationRetrying = true;
      Api.lesson.retryLessonAssetGeneration(
        (result) => {
          if (this.editorDestroying || retryRequestId !== this.lessonAssetGenerationRetryRequestId
            || lessonLoadRequestId !== this.lessonLoadRequestId || lessonId !== this.lessonId) return;
          this.lessonAssetGenerationRetrying = false;
          if (result && result.generationQueued === true
            && ['accepted', 'not_modified'].includes(result.espRetry)) {
            this.$message.success(this.$t('lesson.sdSyncRetryQueued'));
          } else if (result && result.generationQueued === true && result.espRetry === 'deferred') {
            this.$message.warning(this.$t('lesson.sdSyncRetryQueuedDeferred'));
          } else if (result && result.generationQueued === true && result.espRetry === 'unavailable') {
            this.$message.warning(this.$t('lesson.sdSyncRetryQueuedUnavailable'));
          } else {
            this.$message.error(this.$t('lesson.sdSyncRetryNotQueued'));
          }
          this.loadLessonAssetGenerationStatus({ silent: true });
        },
        (message) => {
          if (this.editorDestroying || retryRequestId !== this.lessonAssetGenerationRetryRequestId
            || lessonLoadRequestId !== this.lessonLoadRequestId || lessonId !== this.lessonId) return;
          this.lessonAssetGenerationRetrying = false;
          this.scheduleLessonAssetGenerationPoll(this.lessonAssetGenerationStatus);
          this.$message.error(message);
        },
      );
      return true;
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
      this.$delete(this.selectedContentDrafts, guard.stepKey);
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
      if (!step || this.savingStep || this.lessonVisualStepMutationBlocked || this.rebindingSharedVisual || this.promptStepKey !== step.stepKey) return;
      this.promptEditRevision += 1;
      this.bumpStepEditRevision(step.stepKey);
      this.promptDirty = value !== (step.prompt || '');
      this.invalidatePreview();
    },
    selectSharedAsset(asset) {
      if (!this.selectedStep || !this.isDraft || this.savingStep || this.lessonVisualStepMutationBlocked || this.rebindingSharedVisual) return;
      this.$set(this.selectedAssetDrafts, this.selectedStep.stepKey, asset);
      this.$set(this.dirtyStepKeys, this.selectedStep.stepKey, true);
      this.bumpStepEditRevision(this.selectedStep.stepKey);
      this.invalidatePreview();
    },
    openSharedImpact(intent, asset) {
      if (!asset || !asset.assetId || !this.selectedStep || !this.isDraft || this.savingStep || this.lessonVisualStepMutationBlocked || this.rebindingSharedVisual) return;
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
    reviewAssetReplacement(payload) {
      if (!payload || payload.intent !== 'replace') return;
      this.openSharedImpact('replace', payload.asset);
    },
    keepSharedVisual() {
      const intent = this.sharedImpactIntent;
      if (!this.savingStep && !this.lessonVisualStepMutationBlocked && !this.rebindingSharedVisual && intent && intent.intent === 'select' && this.selectedStepKey === intent.stepKey) {
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
        if (remaining === 0) {
          this.assetRefreshIsProofRecovery = false;
          onSuccess();
        }
      };
      const fail = (msg) => {
        if (this.editorDestroying || failed) return;
        failed = true;
        this.assetRefreshIsProofRecovery = false;
        onError(msg);
      };
      this.assetRefreshIsProofRecovery = true;
      this.reloadAssets(done, fail);
      this.doValidate(done, fail, { allowUnsafe: true, requireValid: true });
      this.doPreview(done, fail, { allowUnsafe: true, reuseProofVersion: true, storeProof: !this.promptDirty && !Object.keys(this.dirtyStepKeys).some((key) => this.dirtyStepKeys[key]) });
      return true;
    },
    rebindClonedVisual(clonedAsset) {
      if (this.editorDestroying) return;
      if (this.savingStep || this.lessonVisualStepMutationBlocked || this.rebindingSharedVisual) return;
      const intent = this.sharedImpactIntent;
      const step = intent && this.steps.find((row) => row.stepKey === intent.stepKey);
      if (!intent || !step || !clonedAsset) return;
      let stepBody;
      try {
        const sourceBody = JSON.parse(JSON.stringify(step.stepBody || {}));
        stepBody = intent.intent === 'select'
          ? bindClonedAssetToStep(sourceBody, {
            intent: 'select', layer: intent.layer, boundAssetKey: intent.boundAssetKey,
          }, clonedAsset)
          : replaceStepAssetReference(sourceBody, intent.asset.assetKey, clonedAsset);
      } catch (error) {
        this.failSharedVisualRebind(error && error.message);
        return;
      }
      if (!collectAssetReferences([{ stepKey: step.stepKey, stepBody }], clonedAsset.assetKey).length) {
        this.failSharedVisualRebind(this.$t('lesson.sharedImpactNoRebindTarget'));
        return;
      }
      const rebindPayload = this.stepPayloadWithoutVisualRefs({ ...step, stepBody });
      this.invalidatePreview();
      this.rebindingSharedVisual = true;
      Api.lesson.updateStep(
        this.lessonId,
        step.stepKey,
        rebindPayload,
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
    stepPayloadWithoutVisualRefs(payload) {
      const sanitized = { ...(payload || {}) };
      delete sanitized.visualRefs;
      return sanitized;
    },
    saveSelectedStep() {
      const step = this.selectedStep;
      if (!step || !this.isDraft || this.savingStep || this.lessonVisualStepMutationBlocked || this.rebindingSharedVisual) return;
      const authored = this.selectedAuthoring;
      const stepBody = mergeStepBodyForSave(step.stepBody || {}, authored);
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
      const stepPayload = this.stepPayloadWithoutVisualRefs({
        ...step,
        ...this.selectedContent,
        prompt: this.promptDraft,
        stepBody,
      });
      Api.lesson.updateStep(
        this.lessonId,
        step.stepKey,
        stepPayload,
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
    loadCinematicLibraries() {
      Object.entries(CINEMATIC_SLOT_CATEGORY).forEach(([slot, category]) => {
        this.$set(this.cinematicLibraryLoading, slot, true);
        this.$set(this.cinematicLibraryErrors, slot, '');
        Api.lesson.listVisualAssets(
          { category, profile: 'espTft' },
          (rows) => {
            this.$set(this.cinematicLibraries, slot, Array.isArray(rows) ? rows : []);
            this.$set(this.cinematicLibraryLoading, slot, false);
          },
          (msg) => {
            this.$set(this.cinematicLibraries, slot, []);
            this.$set(this.cinematicLibraryLoading, slot, false);
            this.$set(this.cinematicLibraryErrors, slot, msg || `Could not load ${category} assets.`);
          },
        );
      });
    },
    selectedVisualVersionId(slot) {
      if (slot === 'backgroundScene') return this.lessonVisualPair.backgroundAssetVersionId || '';
      if (slot === 'teachingObject') return this.lessonVisualPair.objectAssetVersionId || '';
      const refs = this.selectedStep && Array.isArray(this.selectedStep.visualRefs) ? this.selectedStep.visualRefs : [];
      const ref = refs.find((row) => row && row.slot === slot);
      return ref ? (ref.assetVersionId || ref.asset_version_id || ref.versionId || ref.version_id || '') : '';
    },
    selectedCinematicAsset(slot) {
      const versionId = this.selectedVisualVersionId(slot);
      return (this.cinematicLibraries[slot] || []).find((asset) => asset.versionId === versionId) || null;
    },
    selectCinematicLayer(selection) {
      if (!selection || !this.selectedStep || !selection.assetVersionId) return;
      const asset = selection.asset || {};
      if (selection.slot === 'backgroundScene') {
        return this.selectBackground({ assetKey: asset.assetKey, versionId: selection.assetVersionId });
      }
      if (selection.slot === 'teachingObject') {
        return this.selectTeachObject({ assetKey: asset.assetKey, versionId: selection.assetVersionId });
      }
      if (!this.isDraft) {
        this.$message.warning('Robot overlay changes require a draft lesson.');
        return;
      }
      if (this.cinematicRefSaving) return;
      this.cinematicRefSaving = true;
      Api.lesson.setVisualRef(
        this.lessonId,
        this.selectedStep.stepKey,
        selection.slot,
        selection.assetVersionId,
        () => {
          this.cinematicRefSaving = false;
          this.previewManifest = null;
          this.validationResult = null;
          this.fetchSteps({
            preservePrompt: true,
            onSuccess: () => {
              this.pushCinematicStep();
              this.$message.success(`${selection.asset.title || selection.asset.assetKey} pinned to ${selection.slot}.`);
            },
          });
        },
        (msg) => {
          this.cinematicRefSaving = false;
          this.$message.error(msg);
        },
      );
      return true;
    },
    selectTeachObject(obj) {
      if (!obj) return false;
      return this.applyLessonVisualSelection({
        objectAssetVersionId: obj.versionId,
        objectAssetKey: obj.assetKey,
      });
    },
    selectBackground(bg) {
      if (!bg) return false;
      return this.applyLessonVisualSelection({
        backgroundAssetVersionId: bg.versionId,
        backgroundAssetKey: bg.assetKey,
      });
    },
    applyLessonVisualSelection(patch) {
      if (this.savingLessonVisuals || this.savingStep || this.rebindingSharedVisual
        || this.assetMutating || this.sharedImpactReconciling
        || Object.keys(this.savingStepKeys).some((key) => this.savingStepKeys[key])
        || this.addingStep || this.reordering || this.deletingStepKey
        || !this.steps.length) return false;
      const confirmedPair = this.lessonVisualReconciliationRequired ? this.pendingLessonVisualPair : null;
      const nextPair = { ...this.lessonVisualPair, ...(patch || {}) };
      this.lessonVisualReconciliationRequired = false;
      this.pendingLessonVisualPair = nextPair;
      this.$nextTick(() => this.pushCinematicStep());
      if (!nextPair.backgroundAssetVersionId || !nextPair.objectAssetVersionId) {
        this.$message.warning(this.$t('lesson.visualPairRequired'));
        return false;
      }

      const request = buildLessonVisualRequest(this.lessonVisualPair, patch);
      const lessonId = this.lessonId;
      const lessonLoadRequestId = this.lessonLoadRequestId;
      const saveRequestId = this.lessonVisualSaveRequestId + 1;
      this.lessonVisualSaveRequestId = saveRequestId;
      const saveIsCurrent = () => !this.editorDestroying
        && saveRequestId === this.lessonVisualSaveRequestId
        && lessonLoadRequestId === this.lessonLoadRequestId
        && lessonId === this.lessonId;
      this.savingLessonVisuals = true;
      Api.lesson.applyLessonVisuals(
        lessonId,
        request,
        () => {
          if (!saveIsCurrent()) return;
          this.invalidatePreview();
          this.fetchSteps({
            onSuccess: () => {
              if (!saveIsCurrent()) return;
              this.pendingLessonVisualPair = null;
              this.savingLessonVisuals = false;
              this.lessonVisualReconciliationRequired = false;
              this.loadLessonAssetGenerationStatus();
              this.$message.success(this.$t('lesson.visualPairSaved'));
              this.syncCinematicSoon();
            },
            onError: () => {
              if (!saveIsCurrent()) return;
              this.savingLessonVisuals = false;
              this.lessonVisualReconciliationRequired = true;
              this.syncCinematicSoon();
              this.$message.warning(this.$t('lesson.visualPairReloadFailed'));
            },
          });
        },
        (msg) => {
          if (!saveIsCurrent()) return;
          this.savingLessonVisuals = false;
          if (confirmedPair) {
            this.pendingLessonVisualPair = confirmedPair;
            this.lessonVisualReconciliationRequired = true;
            this.syncCinematicSoon();
          } else {
            this.pendingLessonVisualPair = null;
            this.lessonVisualReconciliationRequired = false;
            this.$nextTick(() => this.pushCinematicStep());
          }
          this.$message.error(msg);
        },
      );
      return true;
    },
    // Push the current step to the cinematic iframes now and again shortly after,
    // so the sync lands regardless of the iframe-load / manifest-fetch race order.
    syncCinematicSoon() {
      this.pushCinematicStep();
      [250, 800].forEach((ms) => setTimeout(() => this.pushCinematicStep(), ms));
    },
    // Replay the robot fly-in → walk → greet in both cinematic previews on demand
    // (embed mode hides the demo's own replay button).
    replayCinematicFlyIn() {
      if (!this.cinematicDemoUrl) return;
      [this.$refs.cinematicFrame]
        .map((fr) => fr && fr.contentWindow)
        .filter(Boolean)
        .forEach((win) => {
          try {
            win.postMessage({ type: 'tvideo-params', payload: { replay: true } }, window.location.origin);
          } catch (e) { /* iframe not ready */ }
        });
    },
    pushCinematicStep(replayIntro = false) {
      if (!this.cinematicDemoUrl) return;
      const wins = [this.$refs.cinematicFrame]
        .map((fr) => fr && fr.contentWindow)
        .filter(Boolean);
      if (!wins.length) return;
      const manifest = this.previewManifest && this.previewManifest.manifest;
      const steps = manifest && Array.isArray(manifest.steps) ? manifest.steps : [];
      if (!steps.length) return;
      const idx = Math.max(0, Math.min(Number(this.selectedStepIndex) || 0, steps.length - 1));
      const step = steps[idx] || {};
      const scene = step.scene || {};
      const teachingObject = scene.teachingObject || step.teachingObject || {};
      const canonicalStep = steps[0] || {};
      const canonicalScene = canonicalStep.scene || {};
      const canonicalTeachingObject = canonicalScene.teachingObject || canonicalStep.teachingObject || {};
      const teachingWord = step.teachingWord || {};
      const assetUrl = (a) => (a && (a.url || a.src || (typeof a === 'string' ? a : ''))) || '';
      // The reference iframe mirrors the canonical lesson pair returned by the backend.
      const selectedBg = this.selectedCinematicAsset('backgroundScene');
      const pickedObj = this.selectedCinematicAsset('teachingObject');
      const stepMotion = (step.motion && step.motion.present) || '';
      const payload = {
        word: teachingWord.displayText || teachingWord.text || teachingObject.primaryWord || step.subject || '',
        prompt: step.prompt || '',
        stepId: `S${idx + 1}`,
        stepText: step.type || '',
        obj: pickedObj ? pickedObj.url : assetUrl(canonicalTeachingObject.asset || teachingObject.asset),
        active: idx + 1,
        total: steps.length,
        // Keep the author's chosen background applied across step changes.
        ...(selectedBg ? { bg: selectedBg.url } : {}),
        // Step 1 is the intro — replay the robot fly-in when it's (re)selected.
        replay: replayIntro && idx === 0,
        // Drives the robot's up-close clip (celebrate vs greet-loop).
        motion: stepMotion,
      };
      wins.forEach((win) => {
        try {
          win.postMessage({ type: 'tvideo-params', payload }, window.location.origin);
        } catch (e) { /* iframe not ready — the next @load / watcher will retry */ }
      });
    },
    saveSelectedStepStudio() {
      const step = this.selectedStep;
      if (!step || !this.isDraft || this.savingStep || this.lessonVisualStepMutationBlocked
        || this.rebindingSharedVisual || this.savingSelectedStep) return;
      const savedRevision = Number(this.stepDraftRevisions[step.stepKey] || 0);
      const request = buildSaveStepRequest({
        step,
        authoring: this.selectedAuthoring,
        content: this.selectedContent,
        selectedAsset: this.selectedAssetDrafts[step.stepKey],
        savedRevision,
      });
      const requestPayload = this.stepPayloadWithoutVisualRefs(request.payload);
      const lessonId = this.lessonId;
      const lessonLoadRequestId = this.lessonLoadRequestId;
      this.$set(this.savingStepKeys, step.stepKey, true);
      // A commit changes persisted lesson truth even when it clears the last dirty
      // draft, so every validation/preview launched before this point is stale.
      this.studioRevision += 1;
      this.validationResult = null;
      this.previewManifest = null;
      Api.lesson.updateStep(
        lessonId, request.stepKey, requestPayload,
        (updated) => {
          if (this.editorDestroying || lessonId !== this.lessonId
            || lessonLoadRequestId !== this.lessonLoadRequestId) return;
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
        (msg) => {
          if (this.editorDestroying || lessonId !== this.lessonId
            || lessonLoadRequestId !== this.lessonLoadRequestId) return;
          this.$delete(this.savingStepKeys, step.stepKey);
          this.validationResult = null;
          this.previewManifest = null;
          this.$message.error(msg);
        },
      );
    },
    moveStep(index, delta) {
      if (this.lessonVisualStepMutationBlocked || this.addingStep || this.reordering || this.deletingStepKey) return;
      const target = index + delta;
      if (target < 0 || target >= this.steps.length) return;
      const lessonId = this.lessonId;
      const lessonLoadRequestId = this.lessonLoadRequestId;
      const order = this.steps.map((s) => s.stepKey);
      const tmp = order[index];
      order[index] = order[target];
      order[target] = tmp;
      this.reordering = true;
      Api.lesson.reorderSteps(
        lessonId,
        order,
        (rows) => {
          if (this.editorDestroying || lessonId !== this.lessonId
            || lessonLoadRequestId !== this.lessonLoadRequestId) return;
          this.invalidatePreview();
          this.markStudioChanged();
          this.reordering = false;
          this.steps = rows;
        },
        (msg, error) => {
          if (this.editorDestroying || lessonId !== this.lessonId
            || lessonLoadRequestId !== this.lessonLoadRequestId) return;
          this.reordering = false;
          this.handleUncertainMutationError(error, () => this.fetchSteps({ preservePrompt: true }));
          this.$message.error(msg);
        },
      );
    },
    deleteStep(row) {
      if (this.lessonVisualStepMutationBlocked || this.addingStep || this.reordering || this.deletingStepKey) return;
      const lessonId = this.lessonId;
      const lessonLoadRequestId = this.lessonLoadRequestId;
      this.$confirm(this.$t('lesson.deleteStepConfirm', { key: row.stepKey }), this.$t('lesson.deleteStep'), { type: 'warning' })
        .then(() => {
          if (this.editorDestroying || lessonId !== this.lessonId
            || lessonLoadRequestId !== this.lessonLoadRequestId
            || this.lessonVisualStepMutationBlocked || this.addingStep || this.reordering || this.deletingStepKey) return;
          this.deletingStepKey = row.stepKey;
          Api.lesson.deleteStep(
            lessonId,
            row.stepKey,
            (rows) => {
              if (this.editorDestroying || lessonId !== this.lessonId
                || lessonLoadRequestId !== this.lessonLoadRequestId) return;
              this.deletingStepKey = '';
              this.invalidatePreview();
              this.markStudioChanged();
              this.steps = rows;
            },
            (msg, error) => {
              if (this.editorDestroying || lessonId !== this.lessonId
                || lessonLoadRequestId !== this.lessonLoadRequestId) return;
              this.deletingStepKey = '';
              this.handleUncertainMutationError(error, () => this.fetchSteps({ preservePrompt: true }));
              this.$message.error(msg);
            },
          );
        })
        .catch(() => {});
    },
    openStepDialog() {
      if (this.lessonVisualStepMutationBlocked || this.addingStep || this.reordering || this.deletingStepKey) return;
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
      if (this.lessonVisualStepMutationBlocked || this.addingStep || this.reordering || this.deletingStepKey) return;
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
      const lessonId = this.lessonId;
      const lessonLoadRequestId = this.lessonLoadRequestId;
      this.addingStep = true;
      Api.lesson.createStep(
        lessonId,
        result.payload,
        () => {
          if (this.editorDestroying || lessonId !== this.lessonId
            || lessonLoadRequestId !== this.lessonLoadRequestId) return;
          this.invalidatePreview();
          this.addingStep = false;
          this.stepDialogVisible = false;
          this.lastSubject = f.subject; // prefill next step + teachingObject asset key
          this.markStudioChanged();
          this.fetchSteps();
        },
        (msg, error) => {
          if (this.editorDestroying || lessonId !== this.lessonId
            || lessonLoadRequestId !== this.lessonLoadRequestId) return;
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
    doValidate(onSuccess, onError, options = {}) {
      if (this.editorDestroying || (!options.allowUnsafe && this.hasUnsafeProofState())) return false;
      const requestId = this.validationRequestId + 1;
      const proofVersion = this.proofVersion;
      let settled = false;
      const succeed = (result) => {
        if (settled) return;
        settled = true;
        if (typeof onSuccess === 'function') onSuccess(result);
      };
      const fail = (message) => {
        if (settled) return;
        settled = true;
        if (typeof onError === 'function') onError(message);
      };
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
          const parsed = this.parseValidationResult(res, 'espTft');
          this.validationResult = parsed || { valid: false, profiles: [], errors: [this.$t('lesson.validationResponseMalformed')], warnings: [], findings: [] };
          this.validationProofVersion = proofVersion;
          if (parsed && parsed.valid) this.$message.success(this.$t('lesson.validOk', { profiles: parsed.profiles.join(', ') }));
          else this.$message.warning(this.$t('lesson.validFail'));
          if (!parsed) fail(this.$t('lesson.validationResponseMalformed'));
          else if (!parsed.valid) fail(this.$t('lesson.validFail'));
          else succeed(parsed);
        },
        (msg) => {
          if (this.editorDestroying) return;
          if (requestId !== this.validationRequestId || proofVersion !== this.proofVersion) return;
          this.validating = false;
          this.validationResult = { valid: false, profiles: [], errors: [msg], warnings: [], findings: [] };
          this.validationProofVersion = proofVersion;
          this.$message.error(msg);
          fail(msg);
        },
      );
      return true;
    },
    validManifestPreviewResponse(result) {
      const preview = result && (result.preview || (
        result.manifest && result.manifest.profile === 'espTft'
          ? { profile: 'espTft', width: 480, height: 320 }
          : null
      ));
      return Boolean(
        result && typeof result.checksum === 'string' && result.checksum
        && typeof result.etag === 'string' && result.etag
        && result.manifest && Array.isArray(result.manifest.steps)
        && preview && preview.profile === 'espTft'
        && Number(preview.width) === 480 && Number(preview.height) === 320
      );
    },
    doPreview(onSuccess, onError, options = {}) {
      if (this.editorDestroying || !this.lessonCapabilities.exactEspTftPreview) return false;
      if (!options.allowUnsafe && this.hasUnsafeProofState()) return false;
      const requestId = this.previewRequestId + 1;
      const previousProofVersion = this.proofVersion;
      if (!options.reuseProofVersion) this.proofVersion += 1;
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
          const normalized = res && !res.preview && res.manifest && res.manifest.profile === 'espTft'
            ? { ...res, preview: { profile: 'espTft', width: 480, height: 320 } }
            : res;
          if (!this.validManifestPreviewResponse(normalized)) {
            const message = 'Manifest preview returned an invalid response.';
            this.$message.error(message);
            if (typeof onError === 'function') onError(message);
            return;
          }
          if (options.storeProof !== false) {
            this.preview = { checksum: normalized.checksum, etag: normalized.etag };
            this.previewManifest = normalized;
            this.previewProofVersion = proofVersion;
          }
          if (typeof onSuccess === 'function') onSuccess(normalized);
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
        && !this.publishUncertainState
        && !this.publishReconciling
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
    parseAssetEvidenceResponse(result) {
      if (!result || Array.isArray(result) || !Array.isArray(result.profiles) || !Array.isArray(result.assets)) return null;
      const profiles = result.profiles.slice();
      if (!profiles.length && !result.assets.length) return { profiles: [], assets: [] };
      if (!profiles.length || !result.assets.length
        || profiles.some((profile) => typeof profile !== 'string' || !profile.trim())
        || new Set(profiles).size !== profiles.length) return null;
      const seen = new Set();
      const assets = [];
      for (const raw of result.assets) {
        if (!raw || Array.isArray(raw) || typeof raw !== 'object') return null;
        const profile = raw.profile;
        const assetKey = raw.assetKey;
        const mediaType = raw.mediaType;
        const location = raw.path || raw.url;
        const bytes = Number(raw.bytes);
        const width = raw.width == null ? null : Number(raw.width);
        const height = raw.height == null ? null : Number(raw.height);
        if (![profile, assetKey, raw.layer, raw.role, mediaType, raw.sha256, location].every((value) => typeof value === 'string' && value.trim())) return null;
        if (!profiles.includes(profile) || !/^[a-f0-9]{64}$/i.test(raw.sha256)) return null;
        if (!Number.isSafeInteger(bytes) || bytes < 0 || (width !== null && (!Number.isFinite(width) || width < 0)) || (height !== null && (!Number.isFinite(height) || height < 0))) return null;
        if (typeof raw.critical !== 'boolean') return null;
        const identity = `${profile}\u0000${assetKey}`;
        if (seen.has(identity)) return null;
        seen.add(identity);
        assets.push({ profile, assetKey, layer: raw.layer, role: raw.role, mediaType, bytes, width, height, sha256: raw.sha256, critical: raw.critical, path: location });
      }
      const assetProfiles = [...new Set(assets.map((asset) => asset.profile))].sort();
      if (assetProfiles.join('|') !== profiles.slice().sort().join('|')) return null;
      return { profiles: profiles.slice().sort(), assets: assets.sort((a, b) => JSON.stringify(a).localeCompare(JSON.stringify(b))) };
    },
    parseValidationResult(result, expectedProfile = 'espTft') {
      if (!result || Array.isArray(result) || typeof result.valid !== 'boolean' || !Array.isArray(result.profiles)) return null;
      const profiles = result.profiles.slice();
      if (!profiles.length || profiles.some((profile) => typeof profile !== 'string' || !profile.trim()) || new Set(profiles).size !== profiles.length || !profiles.includes(expectedProfile)) return null;
      const validFinding = (finding) => typeof finding === 'string'
        ? Boolean(finding.trim())
        : Boolean(finding && !Array.isArray(finding) && typeof finding === 'object'
          && ['message', 'reason', 'code'].some((key) => typeof finding[key] === 'string' && finding[key].trim()));
      for (const key of ['errors', 'warnings', 'findings']) {
        if (result[key] !== undefined && (!Array.isArray(result[key]) || result[key].some((finding) => !validFinding(finding)))) return null;
      }
      if (result.valid && Array.isArray(result.errors) && result.errors.length) return null;
      return { ...result, valid: result.valid, profiles, errors: result.errors || [], warnings: result.warnings || [], findings: result.findings || [] };
    },
    derivePublishSource(rows, currentLesson) {
      if (!currentLesson || !Array.isArray(rows)) return null;
      const authoritativeDraft = rows.find((row) => row.lessonId === currentLesson.lessonId
        && row.lessonKey === currentLesson.lessonKey
        && Number(row.lessonVersion) === Number(currentLesson.lessonVersion)
        && row.status === 'draft');
      if (!authoritativeDraft) return null;
      const priorPublished = rows.filter((row) => row.lessonKey === authoritativeDraft.lessonKey
        && row.status === 'published'
        && Number(row.lessonVersion) < Number(authoritativeDraft.lessonVersion))
        .sort((a, b) => Number(b.lessonVersion) - Number(a.lessonVersion))[0];
      return { mode: priorPublished ? 'prior-published' : 'first-publish', authoritativeDraft, evidenceLesson: priorPublished || authoritativeDraft };
    },
    collectLessonEvidence(lesson, onSuccess, onError) {
      if (!lesson || !lesson.lessonId) { onError(this.$t('lesson.publishEvidenceIdentityMissing')); return false; }
      let manifestResult = null;
      let assetResult = null;
      let settled = false;
      const fail = (message) => { if (!settled) { settled = true; onError(message); } };
      const finish = () => {
        if (settled || !manifestResult || !assetResult) return;
        settled = true;
        if (!this.validManifestPreviewResponse(manifestResult)) { onError(this.$t('lesson.publishManifestEvidenceMalformed')); return; }
        const parsedAssets = this.parseAssetEvidenceResponse(assetResult);
        if (!parsedAssets) { onError(this.$t('lesson.publishAssetEvidenceMalformed')); return; }
        const assets = parsedAssets.assets;
        onSuccess({
          lessonId: lesson.lessonId,
          lessonKey: lesson.lessonKey,
          lessonVersion: Number(lesson.lessonVersion),
          status: lesson.status || '',
          rowManifestChecksum: lesson.manifestChecksum || '',
          checksum: manifestResult.checksum,
          etag: manifestResult.etag,
          manifest: manifestResult.manifest,
          assets,
          profiles: parsedAssets.profiles,
          totalBytes: assets.reduce((sum, asset) => sum + (Number(asset.bytes) || 0), 0),
        });
      };
      Api.lesson.manifestPreview(lesson.lessonId, 'espTft', (result) => { manifestResult = result; finish(); }, fail);
      Api.lesson.listAssets(lesson.lessonId, undefined, (result) => { assetResult = result; finish(); }, fail);
      return true;
    },
    compareOriginalEvidence(before, after, options = {}) {
      const stable = (value) => {
        if (Array.isArray(value)) return `[${value.map(stable).join(',')}]`;
        if (value && typeof value === 'object') return `{${Object.keys(value).sort().map((key) => `${JSON.stringify(key)}:${stable(value[key])}`).join(',')}}`;
        return JSON.stringify(value);
      };
      const differences = [];
      if (!before || !after) return { pass: false, differences: [{ key: 'lesson.publishDiffEvidenceIncomplete' }] };
      if (before.lessonId !== after.lessonId || Number(before.lessonVersion) !== Number(after.lessonVersion)) differences.push({ key: 'lesson.publishDiffOriginalIdentity' });
      if (before.checksum !== after.checksum) differences.push({ key: 'lesson.publishDiffOriginalChecksum', params: { before: before.checksum || '—', after: after.checksum || '—' } });
      if (!options.allowPublishChecksumTransition && (before.rowManifestChecksum || after.rowManifestChecksum)
        && (before.rowManifestChecksum !== after.rowManifestChecksum
          || (!options.allowPendingRowChecksum && after.rowManifestChecksum !== after.checksum))) differences.push({ key: 'lesson.publishDiffOriginalRowChecksum' });
      if (stable(before.manifest) !== stable(after.manifest)) differences.push({ key: 'lesson.publishDiffOriginalManifest' });
      if (stable(before.assets) !== stable(after.assets)) differences.push({ key: 'lesson.publishDiffOriginalAssets' });
      if (Number(before.totalBytes || 0) !== Number(after.totalBytes || 0)) differences.push({ key: 'lesson.publishDiffOriginalBytes', params: { before: before.totalBytes || 0, after: after.totalBytes || 0 } });
      return { pass: differences.length === 0, differences };
    },
    compareTargetEvidence(snapshot, publishResponse, fetchedTarget) {
      const differences = [];
      if (!snapshot || !publishResponse || !fetchedTarget) return { pass: false, differences: [{ key: 'lesson.publishDiffTargetMissing' }] };
      if (fetchedTarget.lessonId !== snapshot.targetLessonId) differences.push({ key: 'lesson.publishDiffTargetIdentity' });
      if (fetchedTarget.status !== 'published') differences.push({ key: 'lesson.publishDiffTargetStatus' });
      if (Number(fetchedTarget.lessonVersion) !== Number(snapshot.targetVersion)
        || Number(fetchedTarget.lessonVersion) !== Number(publishResponse.lessonVersion)) differences.push({ key: 'lesson.publishDiffTargetVersion' });
      if (!fetchedTarget.rowManifestChecksum) differences.push({ key: 'lesson.publishDiffTargetRowChecksumMissing' });
      if (fetchedTarget.checksum !== publishResponse.checksum
        || fetchedTarget.checksum !== snapshot.previewChecksum
        || fetchedTarget.rowManifestChecksum !== fetchedTarget.checksum) differences.push({ key: 'lesson.publishDiffTargetChecksum' });
      return { pass: differences.length === 0, differences };
    },
    parsePublishResponse(result, snapshot) {
      if (!result || Array.isArray(result) || !snapshot) return null;
      if (result.lessonId !== snapshot.targetLessonId || result.status !== 'published'
        || Number(result.lessonVersion) !== Number(snapshot.targetVersion)
        || typeof result.lessonKey !== 'string' || !result.lessonKey
        || typeof result.checksum !== 'string' || !/^[a-f0-9]{64}$/i.test(result.checksum)
        || !result.profileChecksums || Array.isArray(result.profileChecksums) || typeof result.profileChecksums !== 'object'
        || result.profileChecksums.espTft !== result.checksum) return null;
      return result;
    },
    collectPublishedTargetEvidence(snapshot, publishResponse, onSuccess, onError) {
      Api.lesson.getLesson(snapshot.targetLessonId, (lesson) => {
        if (!lesson || !lesson.lessonId) { onError(this.$t('lesson.publishTargetIdentityMalformed')); return; }
        this.collectLessonEvidence(lesson, onSuccess, onError);
      }, onError);
    },
    collectRefetchedLessonEvidence(lessonId, onSuccess, onError) {
      Api.lesson.getLesson(lessonId, (lesson) => {
        if (!lesson || !lesson.lessonId) { onError(this.$t('lesson.publishEvidenceIdentityMissing')); return; }
        this.collectLessonEvidence(lesson, onSuccess, onError);
      }, onError);
    },
    verifyPublishedEvidence(snapshot, result, requestId) {
      let originalAfter = null;
      let targetAfter = null;
      let originalError = '';
      let targetError = '';
      let completed = 0;
      const finish = () => {
        completed += 1;
        if (completed < 2 || this.editorDestroying || requestId !== this.publishRequestId) return;
        this.publishing = false;
        this.publishReconciling = false;
        this.publishUncertainState = null;
        const originalComparison = originalError
          ? { pass: false, differences: [{ key: 'lesson.publishDiffOriginalUnavailable', params: { reason: originalError } }] }
          : this.compareOriginalEvidence(snapshot.originalEvidence, originalAfter, { allowPublishChecksumTransition: snapshot.sourceMode === 'first-publish' });
        const targetComparison = targetError
          ? { pass: false, differences: [{ key: 'lesson.publishDiffTargetUnavailable', params: { reason: targetError } }] }
          : this.compareTargetEvidence(snapshot, result, targetAfter);
        const targetEvidence = targetAfter ? {
          lessonVersion: targetAfter.lessonVersion,
          checksum: targetAfter.checksum,
          etag: targetAfter.etag,
          assetCount: targetAfter.assets.length,
          bytes: targetAfter.totalBytes,
          publishChecksum: result.checksum,
        } : null;
        const verified = originalComparison.pass && targetComparison.pass;
        this.publishResult = {
          type: verified ? 'success' : 'error',
          title: verified
            ? this.$t(snapshot.sourceMode === 'first-publish' ? 'lesson.publishFirstVerified' : 'lesson.publishVerified')
            : this.$t(snapshot.sourceMode === 'first-publish' ? 'lesson.publishFirstVerificationFailed' : 'lesson.publishVerificationFailed'),
          originalComparison,
          targetComparison,
          targetEvidence,
        };
        if (verified) {
          this.publishMessage = this.$t('lesson.publishedOfflineSyncContinues', { v: result.lessonVersion, checksum: result.checksum });
          this.fetchAll();
        }
      };
      this.collectRefetchedLessonEvidence(snapshot.originalLessonId, (evidence) => { originalAfter = evidence; finish(); }, (message) => { originalError = message; finish(); });
      this.collectPublishedTargetEvidence(snapshot, result, (evidence) => { targetAfter = evidence; finish(); }, (message) => { targetError = message; finish(); });
      return true;
    },
    latchUncertainPublish(snapshot, requestId, reason) {
      this.publishing = false;
      this.publishUncertainState = {
        requestId,
        lessonId: snapshot.targetLessonId,
        lessonVersion: snapshot.targetVersion,
        checksum: snapshot.previewChecksum,
        proofVersion: snapshot.proofVersion,
        reason,
      };
      this.publishResult = { type: 'warning', title: this.$t('lesson.publishUncertain'), uncertain: true };
      this.reconcileUncertainPublish();
      return true;
    },
    reconcileUncertainPublish() {
      const uncertain = this.publishUncertainState;
      const snapshot = this.publishReviewSnapshot;
      if (this.editorDestroying || this.publishReconciling || !uncertain || !snapshot
        || uncertain.requestId !== this.publishRequestId
        || uncertain.lessonId !== snapshot.targetLessonId
        || uncertain.lessonVersion !== snapshot.targetVersion
        || uncertain.checksum !== snapshot.previewChecksum
        || uncertain.proofVersion !== snapshot.proofVersion
        || uncertain.proofVersion !== this.proofVersion) return false;
      const reconcileRequestId = this.publishReconcileRequestId + 1;
      this.publishReconcileRequestId = reconcileRequestId;
      this.publishReconciling = true;
      this.publishResult = { type: 'warning', title: this.$t('lesson.publishReconciling'), uncertain: true };
      Api.lesson.getLesson(snapshot.targetLessonId, (lesson) => {
        if (this.editorDestroying || reconcileRequestId !== this.publishReconcileRequestId || uncertain !== this.publishUncertainState) return;
        if (!lesson || lesson.lessonId !== snapshot.targetLessonId || Number(lesson.lessonVersion) !== Number(snapshot.targetVersion)) {
          this.publishReconciling = false;
          this.publishResult = { type: 'warning', title: this.$t('lesson.publishReconcileStillUncertain'), uncertain: true };
          return;
        }
        if (lesson.status === 'published' && typeof lesson.manifestChecksum === 'string' && lesson.manifestChecksum) {
          const recovered = this.parsePublishResponse({
            lessonId: lesson.lessonId,
            lessonKey: lesson.lessonKey,
            lessonVersion: lesson.lessonVersion,
            checksum: lesson.manifestChecksum,
            status: lesson.status,
            profileChecksums: { espTft: lesson.manifestChecksum },
          }, snapshot);
          if (!recovered) {
            this.publishReconciling = false;
            this.publishResult = { type: 'error', title: this.$t('lesson.publishVerificationFailed'), uncertain: true };
            return;
          }
          this.verifyPublishedEvidence(snapshot, recovered, uncertain.requestId);
          return;
        }
        if (lesson.status === 'draft') {
          this.collectLessonEvidence(lesson, (evidence) => {
            if (this.editorDestroying || reconcileRequestId !== this.publishReconcileRequestId || uncertain !== this.publishUncertainState) return;
            this.publishReconciling = false;
            const comparison = this.compareOriginalEvidence(snapshot.targetDraftEvidence, evidence, { allowPendingRowChecksum: true });
            this.publishResult = {
              type: 'warning',
              title: this.$t(comparison.pass ? 'lesson.publishNotYetObserved' : 'lesson.publishReconcileStillUncertain'),
              uncertain: true,
            };
          }, () => {
            if (reconcileRequestId !== this.publishReconcileRequestId) return;
            this.publishReconciling = false;
            this.publishResult = { type: 'warning', title: this.$t('lesson.publishReconcileFailed'), uncertain: true };
          });
          return;
        }
        this.publishReconciling = false;
        this.publishResult = { type: 'warning', title: this.$t('lesson.publishReconcileStillUncertain'), uncertain: true };
      }, () => {
        if (this.editorDestroying || reconcileRequestId !== this.publishReconcileRequestId) return;
        this.publishReconciling = false;
        this.publishResult = { type: 'warning', title: this.$t('lesson.publishReconcileFailed'), uncertain: true };
      });
      return true;
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
          const source = this.derivePublishSource(lessons, this.lesson);
          if (!source) {
            this.publishPreparing = false;
            this.$message.error(this.$t('lesson.publishOriginalMissing'));
            return;
          }
          this.collectLessonEvidence(source.evidenceLesson, (originalEvidence) => {
            if (this.editorDestroying || requestId !== this.publishReviewRequestId || proofVersion !== this.proofVersion) return;
            const openReview = (targetDraftEvidence) => {
              if (this.editorDestroying || requestId !== this.publishReviewRequestId || proofVersion !== this.proofVersion) return;
              this.publishPreparing = false;
              this.publishReviewSnapshot = {
                requestId,
                proofVersion,
                sourceMode: source.mode,
                originalLesson: source.evidenceLesson,
                originalEvidence,
                targetDraftEvidence,
                originalLessonId: source.evidenceLesson.lessonId,
                originalVersion: source.evidenceLesson.lessonVersion,
                originalChecksum: originalEvidence.checksum,
                originalAssets: originalEvidence.assets,
                originalBytes: originalEvidence.totalBytes,
                targetLessonId: source.authoritativeDraft.lessonId,
                targetVersion: source.authoritativeDraft.lessonVersion,
                stepCount: this.steps.length,
                assetCount: targetDraftEvidence.assets.length,
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
                validationProfiles: this.validationResult.profiles.slice(),
              };
              this.publishReviewVisible = true;
            };
            if (source.evidenceLesson.lessonId === source.authoritativeDraft.lessonId) openReview(originalEvidence);
            else this.collectLessonEvidence(source.authoritativeDraft, openReview, (message) => {
              if (requestId !== this.publishReviewRequestId) return;
              this.publishPreparing = false;
              this.$message.error(message);
            });
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
      if (this.publishing || this.publishUncertainState || this.publishReconciling || !this.publishReviewIsCurrent(snapshot)) return false;
      const requestId = this.publishRequestId + 1;
      this.publishRequestId = requestId;
      this.publishing = true;
      this.publishResult = null;
      Api.lesson.publish(
        snapshot.targetLessonId,
        (result) => {
          if (this.editorDestroying || requestId !== this.publishRequestId) return;
          const parsed = this.parsePublishResponse(result, snapshot);
          if (!parsed) { this.latchUncertainPublish(snapshot, requestId, this.$t('lesson.publishResponseMalformed')); return; }
          this.verifyPublishedEvidence(snapshot, parsed, requestId);
        },
        (message, error) => {
          if (this.editorDestroying || requestId !== this.publishRequestId) return;
          this.publishing = false;
          const uncertain = this.isUncertainMutationError(error);
          if (uncertain) this.latchUncertainPublish(snapshot, requestId, message);
          else this.publishResult = { type: 'error', title: this.$t('lesson.publishFailed', { reason: message }) };
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
  max-width: 100%;
  min-width: 0;
  box-sizing: border-box;
}
.left-title { display: flex; align-items: center; gap: 12px; flex:1 1 320px; flex-wrap:wrap; min-width:0; }
.page-title { margin: 0; font-size: 18px; min-width:0; overflow-wrap:anywhere; }
.right-operations { display: flex; align-items: center; gap: 8px; flex:0 1 auto; flex-wrap:wrap; justify-content:flex-end; min-width:0; }
.main-wrapper { box-sizing:border-box; max-width:100%; min-width:0; padding:16px 24px; width:100%; }
.content-area { box-sizing:border-box; margin-bottom:16px; max-width:100%; min-width:0; }
.content-area ::v-deep .el-card__body { box-sizing:border-box; max-width:100%; min-width:0; }
.card-header { font-weight: 600; }
.add-row { display: flex; align-items: center; gap: 10px; margin-top: 14px; flex-wrap: wrap; }
.kv { display: flex; gap: 10px; min-width:0; padding: 2px 0; }
.kv .muted { flex:0 0 130px; display: inline-block; }
.kv span:last-child { min-width:0; overflow-wrap:anywhere; }
.mono { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; word-break: break-all; }
.small { font-size: 12px; }
.muted { color: #909399; }
.danger-text { color: #f56c6c; }
.preview-card { margin-bottom: 16px; }
.canonical-demo { align-items:center; background:#17312d; border-radius:20px; box-sizing:border-box; color:#fff8df; display:grid; gap:20px; grid-template-columns:minmax(220px,.75fr) minmax(320px,1.25fr); margin-bottom:18px; max-width:100%; min-width:0; overflow:hidden; padding:18px; }
.canonical-demo > * { min-width:0; }
.canonical-demo__copy h3 { font-family:Georgia,serif; font-size:25px; margin:5px 0 8px; }
.canonical-demo__copy p { color:#c7d7d1; line-height:1.5; margin:0 0 12px; max-width:480px; }
.canonical-demo__sources { display:flex; flex-wrap:wrap; gap:8px; margin-top:14px; min-width:0; }
.canonical-demo__sources img { background:#f6ecd1; border:1px solid rgba(255,255,255,.2); border-radius:9px; height:58px; object-fit:contain; width:76px; }
.canonical-demo video { background:#0c1c19; border-radius:14px; display:block; max-height:360px; max-width:100%; min-width:0; object-fit:cover; width:100%; }
.choice-group { display: block; }
.choice-row { display: flex; align-items: center; gap: 10px; margin-bottom: 8px; }
.grid-2 { display: grid; grid-template-columns: minmax(0, 1fr) minmax(0, 1fr); gap: 0 14px; min-width:0; }
.focus-title { margin: 6px 0; font-weight: 600; }
.focus-row { display: flex; align-items: center; gap: 6px; margin-bottom: 8px; flex-wrap: wrap; }
.focus-row .el-input-number { width: 110px; }
.lesson-studio { align-items:start; background:linear-gradient(135deg,#f3ead5 0%,#edf3ea 55%,#f8dfb5 100%); border:1px solid #e5d7bd; border-radius:24px; box-sizing:border-box; display:grid; gap:16px; grid-template-columns:210px minmax(0,1fr); margin-bottom:18px; max-width:100%; min-width:0; padding:16px; }
.lesson-studio__canvas { display:grid; gap:14px; grid-template-columns:minmax(0,1fr); max-width:100%; min-width:0; }
.lesson-studio__canvas > * { box-sizing:border-box; max-width:100%; min-width:0; }
.lesson-studio__toolbar { align-items:center; display:flex; gap:12px; justify-content:space-between; max-width:100%; min-width:0; }
.lesson-studio__toolbar > div { min-width:0; }
.lesson-studio__toolbar h3 { color:#17312d; font-family:Georgia,serif; font-size:24px; margin:3px 0 0; overflow-wrap:anywhere; }
.lesson-studio__toolbar .el-button { flex:0 0 auto; }
.lesson-visual-pair { background:rgba(255,255,255,.72); border:1px solid #d8c9ac; border-radius:18px; box-sizing:border-box; max-width:100%; min-width:0; padding:14px; }
.lesson-visual-pair__heading { align-items:flex-start; display:flex; gap:16px; justify-content:space-between; }
.lesson-visual-pair__heading > div { min-width:0; }
.lesson-visual-pair__heading h4 { color:#17312d; font-family:Georgia,serif; font-size:20px; margin:0 0 3px; }
.lesson-visual-pair__heading p { color:#5f6f63; font-size:13px; margin:0; }
.lesson-visual-pair__heading [role="status"] { color:#9a6820; font-size:12px; font-weight:800; }
.lesson-visual-pair__notice { background:#fff4d6; border-radius:9px; color:#7a531c; font-size:12px; margin:10px 0 0; padding:8px 10px; }
.eyebrow { color:#9a6820; font-size:10px; font-weight:800; letter-spacing:.16em; }
.cinematic-pickers { display:grid; gap:12px; grid-template-columns:minmax(0,1fr); max-width:100%; min-width:0; }
.cinematic-pickers > * { max-width:100%; min-width:0; }
.lesson-studio__workbench { display:grid; gap:14px; grid-template-columns:minmax(330px,1fr) minmax(360px,1fr); max-width:100%; min-width:0; }
.lesson-studio__workbench > * { max-width:100%; min-width:0; }
.lesson-studio__workbench .content-card,
.lesson-studio__workbench .el-form,
.lesson-studio__workbench .el-form-item,
.lesson-studio__workbench .el-input,
.lesson-studio__workbench .el-select { max-width:100%; min-width:0; }
.preview-stack { max-width:100%; min-width:0; }
.lesson-studio__canvas ::v-deep .readiness,
.lesson-studio__canvas ::v-deep .validation-result,
.lesson-studio__canvas ::v-deep .validation-profiles,
.lesson-studio__canvas ::v-deep .validation-list,
.lesson-studio__canvas ::v-deep .readiness__issues,
.lesson-studio__canvas ::v-deep .readiness__issue { box-sizing:border-box; max-width:100%; min-width:0; overflow-wrap:anywhere; }
.lesson-studio__canvas ::v-deep .readiness__grid { grid-template-columns:repeat(auto-fit,minmax(120px,1fr)); max-width:100%; min-width:0; }
.lesson-studio__canvas ::v-deep .readiness__grid > div { min-width:0; }
.preview-heading { align-items:baseline; display:flex; flex-wrap:wrap; gap:10px; margin:6px 0 8px; }
.preview-heading .eyebrow { font-weight:800; letter-spacing:.08em; }
.preview-heading__hint { color:#7c8a7f; flex:1 1 220px; font-size:12px; min-width:0; overflow-wrap:anywhere; }
.replay-btn { margin-left:auto; padding:5px 12px; border:1px solid #16251c; border-radius:999px; background:#b9ec45; color:#16251c; cursor:pointer; font:inherit; font-size:12px; font-weight:700; }
.replay-btn:active { transform:translateY(1px); }
.bg-picker { margin-bottom:16px; }
.bg-picker__grid { display:grid; grid-template-columns:repeat(auto-fill,minmax(104px,1fr)); gap:8px; max-height:236px; overflow-y:auto; padding:4px; }
.bg-chip { display:flex; flex-direction:column; gap:4px; padding:4px; border:2px solid transparent; border-radius:10px; background:#fff; cursor:pointer; font:inherit; text-align:left; }
.bg-chip img { width:100%; aspect-ratio:16/9; object-fit:cover; border-radius:6px; display:block; background:#dce8c2; }
.bg-chip span { font-size:11.5px; color:#3a4a3c; line-height:1.2; }
.bg-chip:hover { border-color:#cbd9bb; }
.bg-chip.selected { border-color:#16251c; background:#eef7d9; box-shadow:0 2px 0 #16251c; }
.bg-chip.selected span { font-weight:800; }
.bg-chip:disabled { cursor:not-allowed; opacity:.5; }
.bg-chip:disabled:hover { border-color:transparent; }
/* Objects are transparent PNGs — show them on a light plate, not cropped. */
.obj-chip img { aspect-ratio:1/1; object-fit:contain; background:#f4f7ec; padding:4px; }
.cinematic-effect { margin-bottom:18px; }
.preview-surface { border:1px solid #d8e2dd; border-radius:18px; background:#fff; box-sizing:border-box; max-width:100%; min-width:0; padding:14px; }
.exact-renderer-surface { margin-top:18px; }
.cinematic-frame { aspect-ratio:16/10; background:#0c1c19; border:2px solid #17312d; border-radius:16px; box-sizing:border-box; max-width:100%; min-width:0; overflow:hidden; width:100%; }
.cinematic-frame iframe { border:0; display:block; height:100%; max-width:100%; min-width:0; width:100%; }
.cinematic-note { color:#5f6f63; font-size:12.5px; line-height:1.45; margin:8px 2px 0; }
.preview-empty { align-items:center; background:#17312d; border-radius:18px; box-sizing:border-box; color:#fff8df; display:flex; flex-direction:column; gap:12px; justify-content:center; max-width:100%; min-height:320px; min-width:0; padding:30px; text-align:center; }
.preview-empty span { color:#b9cbc5; max-width:320px; }
.advanced-steps-scroll { max-width:100%; min-width:0; overflow-x:auto; }
.advanced-steps-scroll .el-table { min-width:870px; }
.advanced-steps-scroll ::v-deep .el-table__header-wrapper,
.advanced-steps-scroll ::v-deep .el-table__body-wrapper,
.advanced-steps-scroll ::v-deep .el-table__footer-wrapper { overflow-x:hidden; }

@media (max-width:1100px) {
  .lesson-studio__workbench { grid-template-columns:minmax(0,1fr); }
}

@media (max-width:760px) {
  .operation-bar { align-items:flex-start; padding:12px 12px 0; }
  .left-title,
  .right-operations { flex-basis:100%; justify-content:flex-start; }
  .page-title { font-size:16px; }
  .main-wrapper { padding:12px; }
  .canonical-demo,
  .lesson-studio { border-radius:14px; grid-template-columns:minmax(0,1fr); padding:12px; }
  .lesson-studio__toolbar,
  .lesson-visual-pair__heading { align-items:flex-start; flex-direction:column; gap:10px; }
  .grid-2 { grid-template-columns:minmax(0,1fr); }
  .preview-surface,
  .preview-empty { padding:12px; }
  .advanced-steps-scroll .el-table { min-width:760px; }
}
</style>
