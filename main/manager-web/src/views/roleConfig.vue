<template>
  <div class="welcome">
    <HeaderBar />

    <div class="operation-bar">
      <h2 class="page-title">{{ $t("roleConfig.title") }}</h2>
    </div>

    <div class="main-wrapper">
      <div class="content-panel">
        <div class="content-area">
          <el-card class="config-card" shadow="never">
            <div class="config-header">
              <div class="header-left">
                <div class="header-icon">
                  <img loading="lazy" src="@/assets/home/setting-user.png" alt="" />
                </div>
                <span class="header-title">{{ form.agentName }}</span>
              </div>
              <div class="header-tags">
                <el-tag
                  v-for="tag in dynamicTags"
                  :key="tag.id"
                  class="custom-tag"
                  closable
                  :disable-transitions="false"
                  @close="handleClose(tag.id)">
                  {{tag.tagName}}
                </el-tag>
                <el-input
                  class="input-new-tag"
                  v-if="inputVisible"
                  v-model="inputValue"
                  ref="saveTagInput"
                  size="small"
                  maxLength="20"
                  @keyup.enter.native="handleInputConfirm"
                  @blur="handleInputConfirm"
                >
                </el-input>
                <el-button class="custom-tag-btn" v-else size="small" @click="showInput">+ {{ $t("roleConfig.addTag") }}</el-button>
              </div>
              <div class="header-actions">
                <div class="hint-text">
                  <img loading="lazy" src="@/assets/home/info.png" alt="" />
                  <span>{{ $t("roleConfig.restartNotice") }}</span>
                </div>
                <el-button type="primary" class="save-btn" @click="saveConfig">
                  {{ $t("roleConfig.saveConfig") }}
                </el-button>
                <el-button class="reset-btn" @click="resetConfig">{{
                  $t("roleConfig.reset")
                }}</el-button>
                <button class="custom-close-btn" @click="goToHome">×</button>
              </div>
            </div>
            <div class="divider"></div>

            <el-form ref="form" :model="form" label-width="72px">
              <div class="form-content">
                <div class="form-grid">
                  <div class="form-column">
                    <el-form-item>
                      <template #label>
                        <el-tooltip :content="$t('roleConfig.tooltip.agentName')" placement="top" effect="light" popper-class="custom-tooltip">
                          <span>{{ $t('roleConfig.agentName') }}:</span>
                        </el-tooltip>
                      </template>
                      <el-input
                        v-model="form.agentName"
                        class="form-input"
                        maxlength="64"
                      />
                    </el-form-item>
                    <el-form-item>
                      <template #label>
                        <el-tooltip :content="$t('roleConfig.tooltip.roleTemplate')" placement="top" effect="light" popper-class="custom-tooltip">
                          <span>{{ $t('roleConfig.roleTemplate') }}:</span>
                        </el-tooltip>
                      </template>
                      <div class="template-container">
                        <div
                          v-for="(template, index) in templates"
                          :key="`template-${index}`"
                          class="template-item"
                          :class="{ 'template-loading': loadingTemplate }"
                          @click="selectTemplate(template)"
                        >
                          {{ template.agentName }}
                        </div>
                      </div>
                    </el-form-item>
                    <el-form-item class="context-provider-item">
                      <template #label>
                        <el-tooltip :content="$t('roleConfig.tooltip.contextProvider')" placement="top" effect="light" popper-class="custom-tooltip">
                          <span>{{ $t('roleConfig.contextProvider') }}:</span>
                        </el-tooltip>
                      </template>
                      <div style="display: flex; align-items: center; justify-content: space-between;">
                        <span style="color: #606266; font-size: 13px;">
                          {{ $t('roleConfig.contextProviderSuccess', { count: currentContextProviders.length }) }}<a href="docs/context-provider-integration.md" target="_blank" class="doc-link">{{ $t('roleConfig.contextProviderDocLink') }}</a>
                        </span>
                        <el-button
                          class="edit-function-btn"
                          size="small"
                          @click="openContextProviderDialog"
                        >
                          {{ $t('roleConfig.editContextProvider') }}
                        </el-button>
                      </div>
                    </el-form-item>
                    <el-form-item>
                      <template #label>
                        <el-tooltip :content="$t('roleConfig.tooltip.roleIntroduction')" placement="top" effect="light" popper-class="custom-tooltip">
                          <span>{{ $t('roleConfig.roleIntroduction') }}:</span>
                        </el-tooltip>
                      </template>
                      <el-input
                        type="textarea"
                        rows="8"
                        resize="none"
                        :placeholder="$t('roleConfig.pleaseEnterContent')"
                        v-model="form.systemPrompt"
                        maxlength="2000"
                        show-word-limit
                        class="form-textarea"
                      />
                    </el-form-item>

                    <el-form-item>
                      <template #label>
                        <el-tooltip :content="$t('roleConfig.tooltip.memoryHis')" placement="top" effect="light" popper-class="custom-tooltip">
                          <span>{{ $t('roleConfig.memoryHis') }}:</span>
                        </el-tooltip>
                      </template>
                      <el-input
                        type="textarea"
                        rows="4"
                        resize="none"
                        v-model="form.summaryMemory"
                        maxlength="2000"
                        show-word-limit
                        class="form-textarea"
                        :disabled="form.model.memModelId !== 'Memory_mem_local_short'"
                      />
                    </el-form-item>
                    <el-form-item
                      style="display: none"
                    >
                      <template #label>
                        <el-tooltip :content="$t('roleConfig.tooltip.languageCode')" placement="top" effect="light" popper-class="custom-tooltip">
                          <span>{{ $t('roleConfig.languageCode') }}:</span>
                        </el-tooltip>
                      </template>
                      <el-input
                        v-model="form.langCode"
                        :placeholder="$t('roleConfig.pleaseEnterLangCode')"
                        maxlength="10"
                        show-word-limit
                        class="form-input"
                      />
                    </el-form-item>
                    <el-form-item
                      style="display: none"
                    >
                      <template #label>
                        <el-tooltip :content="$t('roleConfig.tooltip.interactionLanguage')" placement="top" effect="light" popper-class="custom-tooltip">
                          <span>{{ $t('roleConfig.interactionLanguage') }}:</span>
                        </el-tooltip>
                      </template>
                      <el-input
                        v-model="form.language"
                        :placeholder="$t('roleConfig.pleaseEnterLangName')"
                        maxlength="10"
                        show-word-limit
                        class="form-input"
                      />
                    </el-form-item>
                  </div>
                  <div class="form-column">
                    <div class="model-row">
                      <el-form-item 
                        v-if="featureStatus.vad" 
                        class="model-item"
                      >
                        <template #label>
                          <el-tooltip :content="$t('roleConfig.tooltip.vad')" placement="top" effect="light" popper-class="custom-tooltip">
                            <span>{{ $t('roleConfig.vad') }}</span>
                          </el-tooltip>
                        </template>
                        <div class="model-select-wrapper">
                          <el-select
                            v-model="form.model.vadModelId"
                            filterable
                            :placeholder="$t('roleConfig.pleaseSelect')"
                            class="form-select"
                            @change="handleModelChange('VAD', $event)"
                          >
                            <el-option
                              v-for="(item, optionIndex) in modelOptions['VAD']"
                              :key="`option-vad-${optionIndex}`"
                              :label="item.label"
                              :value="item.value"
                            />
                          </el-select>
                        </div>
                      </el-form-item>
                      <el-form-item 
                        v-if="featureStatus.asr" 
                        class="model-item"
                      >
                        <template #label>
                          <el-tooltip :content="$t('roleConfig.tooltip.asr')" placement="top" effect="light" popper-class="custom-tooltip">
                            <span>{{ $t('roleConfig.asr') }}</span>
                          </el-tooltip>
                        </template>
                        <div class="model-select-wrapper">
                          <el-select
                            v-model="form.model.asrModelId"
                            filterable
                            :placeholder="$t('roleConfig.pleaseSelect')"
                            class="form-select"
                            @change="handleModelChange('ASR', $event)"
                          >
                            <el-option
                              v-for="(item, optionIndex) in modelOptions['ASR']"
                              :key="`option-asr-${optionIndex}`"
                              :label="item.label"
                              :value="item.value"
                            />
                          </el-select>
                        </div>
                      </el-form-item>
                    </div>
                    <div class="model-row">
                      <el-form-item class="model-item">
                        <template #label>
                          <el-tooltip :content="$t('roleConfig.tooltip.llm')" placement="top" effect="light" popper-class="custom-tooltip">
                            <span>{{ $t('roleConfig.llm') }}</span>
                          </el-tooltip>
                        </template>
                        <div class="model-select-wrapper">
                          <el-select
                            v-model="form.model.llmModelId"
                            filterable
                            :placeholder="$t('roleConfig.pleaseSelect')"
                            class="form-select"
                            @change="handleModelChange('LLM', $event)"
                          >
                            <el-option
                              v-for="(item, optionIndex) in modelOptions['LLM']"
                              :key="`option-asr-${optionIndex}`"
                              :label="item.label"
                              :value="item.value"
                            />
                          </el-select>
                        </div>
                      </el-form-item>
                      <el-form-item class="model-item">
                        <template #label>
                          <el-tooltip :content="$t('roleConfig.tooltip.slm')" placement="top" effect="light" popper-class="custom-tooltip">
                            <span>{{ $t('roleConfig.slm') }}</span>
                          </el-tooltip>
                        </template>
                        <div class="model-select-wrapper">
                          <el-select
                            v-model="form.model.slmModelId"
                            filterable
                            :placeholder="$t('roleConfig.pleaseSelect')"
                            class="form-select"
                          >
                            <el-option
                              v-for="(item, optionIndex) in modelOptions['LLM']"
                              :key="`option-asr-${optionIndex}`"
                              :label="item.label"
                              :value="item.value"
                            />
                          </el-select>
                        </div>
                      </el-form-item>
                    </div>
                    <el-form-item
                      v-for="(model, index) in models.slice(4)"
                      :key="`model-${index}`"
                      class="model-item"
                    >
                      <template #label>
                        <el-tooltip :content="$t('roleConfig.tooltip.' + model.type.toLowerCase())" placement="top" effect="light" popper-class="custom-tooltip">
                          <span>{{ $t('roleConfig.' + model.type.toLowerCase()) }}</span>
                        </el-tooltip>
                      </template>
                      <div class="model-select-wrapper">
                        <el-select
                          v-model="form.model[model.key]"
                          filterable
                          :placeholder="$t('roleConfig.pleaseSelect')"
                          class="form-select"
                          @change="handleModelChange(model.type, $event)"
                        >
                          <el-option
                            v-for="(item, optionIndex) in modelOptions[model.type]"
                            v-if="!item.isHidden"
                            :key="`option-${index}-${optionIndex}`"
                            :label="item.label"
                            :value="item.value"
                          />
                        </el-select>
                        <div v-if="showFunctionIcons(model.type)" class="function-icons">
                          <el-tooltip
                            v-for="func in currentFunctions"
                            :key="func.name"
                            effect="light"
                            placement="top"
                          >
                            <div slot="content">
                              <div><strong>Function name:</strong> {{ func.name }}</div>
                            </div>
                            <div class="icon-dot">
                              {{ getFunctionDisplayChar(func.name) }}
                            </div>
                          </el-tooltip>
                          <el-button
                            class="edit-function-btn"
                            @click="openFunctionDialog"
                            :class="{ 'active-btn': showFunctionDialog }"
                          >
                            {{ $t("roleConfig.editFunctions") }}
                          </el-button>
                        </div>
                        <div
                          v-if="
                            model.type === 'Memory' &&
                            form.model.memModelId !== 'Memory_nomem'
                          "
                          class="chat-history-options"
                        >
                          <el-radio-group
                            v-model="form.chatHistoryConf"
                            @change="updateChatHistoryConf"
                          >
                            <el-radio-button :label="1">{{
                              $t("roleConfig.reportText")
                            }}</el-radio-button>
                            <el-radio-button :label="2">{{
                              $t("roleConfig.reportTextVoice")
                            }}</el-radio-button>
                          </el-radio-group>
                        </div>
                      </div>
                    </el-form-item>
                    <div class="model-row">
                      <el-form-item class="model-item">
                        <template #label>
                          <el-tooltip :content="$t('roleConfig.tooltip.voiceMode')" placement="top" effect="light" popper-class="custom-tooltip">
                            <span>{{ $t('roleConfig.voiceMode') }}</span>
                          </el-tooltip>
                        </template>
                        <div class="model-select-wrapper">
                          <el-select
                            v-model="form.voiceMode"
                            :placeholder="$t('roleConfig.pleaseSelect')"
                            class="form-select"
                          >
                            <el-option
                              :label="$t('roleConfig.classicPipeline')"
                              value="classic_pipeline"
                            />
                            <el-option
                              :label="$t('roleConfig.googleLiveApi')"
                              value="google_live"
                            />
                          </el-select>
                        </div>
                      </el-form-item>
                    </div>
                    <div v-if="form.voiceMode === 'google_live'" class="google-live-panel">
                      <div class="model-row">
                        <el-form-item class="model-item">
                          <template #label>
                            <span>{{ $t('roleConfig.googleLiveApiKey') }}</span>
                          </template>
                          <el-input
                            v-model="form.googleLiveConfig.api_key"
                            type="password"
                            show-password
                            class="form-input"
                            maxlength="256"
                          />
                        </el-form-item>
                      </div>
                      <div class="model-row">
                        <el-form-item class="model-item">
                          <template #label>
                            <span>{{ $t('roleConfig.googleLiveModel') }}</span>
                          </template>
                          <el-input
                            v-model="form.googleLiveConfig.model"
                            class="form-input"
                            maxlength="128"
                          />
                        </el-form-item>
                      </div>
                      <div class="model-row">
                        <el-form-item class="model-item">
                          <template #label>
                            <span>{{ $t('roleConfig.googleLiveVoiceName') }}</span>
                          </template>
                          <el-input
                            v-model="form.googleLiveConfig.voice_name"
                            class="form-input"
                            maxlength="128"
                          />
                        </el-form-item>
                      </div>
                      <div class="model-row">
                        <el-form-item class="model-item">
                          <template #label>
                            <span>{{ $t('roleConfig.googleLiveConnectTimeoutSec') }}</span>
                          </template>
                          <el-input-number
                            v-model="form.googleLiveConfig.connect_timeout_sec"
                            :min="1"
                            :max="120"
                            :controls="false"
                            class="form-input"
                            style="width: 100%;"
                          />
                        </el-form-item>
                        <el-form-item class="model-item">
                          <template #label>
                            <span>{{ $t('roleConfig.googleLiveRecvTimeoutSec') }}</span>
                          </template>
                          <el-input-number
                            v-model="form.googleLiveConfig.recv_timeout_sec"
                            :min="1"
                            :max="300"
                            :controls="false"
                            class="form-input"
                            style="width: 100%;"
                          />
                        </el-form-item>
                      </div>
                      <div class="model-row">
                        <el-form-item class="model-item">
                          <template #label>
                            <span>{{ $t('roleConfig.googleLiveInputFlushDelaySec') }}</span>
                          </template>
                          <el-input-number
                            v-model="form.googleLiveConfig.input_flush_delay_sec"
                            :min="0"
                            :max="10"
                            :step="0.1"
                            :precision="1"
                            :controls="false"
                            class="form-input"
                            style="width: 100%;"
                          />
                        </el-form-item>
                        <el-form-item class="model-item">
                          <template #label>
                            <span>{{ $t('roleConfig.googleLiveInputSampleRate') }}</span>
                          </template>
                          <el-input-number
                            v-model="form.googleLiveConfig.input_sample_rate"
                            :min="8000"
                            :max="48000"
                            :step="1000"
                            :controls="false"
                            class="form-input"
                            style="width: 100%;"
                          />
                        </el-form-item>
                      </div>
                      <div class="model-row">
                        <el-form-item class="model-item">
                          <template #label>
                            <span>{{ $t('roleConfig.googleLiveOutputSampleRate') }}</span>
                          </template>
                          <el-input-number
                            v-model="form.googleLiveConfig.output_sample_rate"
                            :min="8000"
                            :max="48000"
                            :step="1000"
                            :controls="false"
                            class="form-input"
                            style="width: 100%;"
                          />
                        </el-form-item>
                        <el-form-item class="model-item">
                          <template #label>
                            <span>{{ $t('roleConfig.googleLiveReconnectBackoffMs') }}</span>
                          </template>
                          <el-input-number
                            v-model="form.googleLiveConfig.reconnect.backoff_ms"
                            :min="0"
                            :max="10000"
                            :step="100"
                            :controls="false"
                            class="form-input"
                            style="width: 100%;"
                          />
                        </el-form-item>
                      </div>
                      <div class="google-live-switches">
                        <el-switch
                          v-model="form.googleLiveConfig.enable_audio_input"
                          :active-text="$t('roleConfig.googleLiveEnableAudioInput')"
                        />
                        <el-switch
                          v-model="form.googleLiveConfig.enable_audio_output"
                          :active-text="$t('roleConfig.googleLiveEnableAudioOutput')"
                        />
                        <el-switch
                          v-model="form.googleLiveConfig.native_voice"
                          :active-text="$t('roleConfig.googleLiveNativeVoice')"
                        />
                        <el-switch
                          v-model="form.googleLiveConfig.barge_in"
                          :active-text="$t('roleConfig.googleLiveBargeIn')"
                        />
                        <el-switch
                          v-model="form.googleLiveConfig.send_llm_state_events"
                          :active-text="$t('roleConfig.googleLiveSendLlmStateEvents')"
                        />
                        <el-switch
                          v-model="form.googleLiveConfig.send_transcript_events"
                          :active-text="$t('roleConfig.googleLiveSendTranscriptEvents')"
                        />
                        <el-switch
                          v-model="form.googleLiveConfig.reconnect.enabled"
                          :active-text="$t('roleConfig.googleLiveReconnectEnabled')"
                        />
                        <el-switch
                          v-model="form.googleLiveConfig.fallback_to_classic_on_error"
                          :active-text="$t('roleConfig.googleLiveFallback')"
                        />
                      </div>
                      <div class="model-row">
                        <el-form-item class="model-item">
                          <template #label>
                            <span>{{ $t('roleConfig.googleLiveReconnectMaxRetries') }}</span>
                          </template>
                          <el-input-number
                            v-model="form.googleLiveConfig.reconnect.max_retries"
                            :min="0"
                            :max="10"
                            :controls="false"
                            class="form-input"
                            style="width: 100%;"
                          />
                        </el-form-item>
                      </div>
                    </div>
                    <div class="model-row">
                      <!-- LanguageFilter -->
                      <el-form-item class="model-item language-select-item">
                        <template #label>
                          <el-tooltip :content="$t('roleConfig.tooltip.language')" placement="top" effect="light" popper-class="custom-tooltip">
                            <span>{{ $t('roleConfig.language') }}</span>
                          </el-tooltip>
                        </template>
                        <div class="model-select-wrapper">
                          <el-select
                            v-model="selectedLanguage"
                            :placeholder="$t('roleConfig.selectLanguage')"
                            class="form-select language-select"
                            @change="filterVoicesByLanguage"
                          >
                            <el-option
                              v-for="(lang, index) in languageOptions"
                              :key="`lang-${index}`"
                              :label="lang.label"
                              :value="lang.value"
                            />
                          </el-select>
                        </div>
                      </el-form-item>

                      <!-- Voice selector -->
                      <el-form-item class="model-item">
                        <template #label>
                          <el-tooltip :content="$t('roleConfig.tooltip.voiceType')" placement="top" effect="light" popper-class="custom-tooltip">
                            <span>{{ $t('roleConfig.voiceType') }}</span>
                          </el-tooltip>
                        </template>
                        <div class="model-select-wrapper">
                          <el-select
                            v-model="form.ttsVoiceId"
                            filterable
                            :placeholder="$t('roleConfig.pleaseSelect')"
                            class="form-select"
                          >
                            <el-option
                              v-for="(item, index) in voiceOptions"
                              :key="`voice-${index}`"
                              :label="item.label"
                              :value="item.value"
                            >
                              <div
                                style="
                                  display: flex;
                                  justify-content: space-between;
                                  align-items: center;
                                "
                              >
                                <span>{{ item.label }}</span>
                                <template v-if="hasAudioPreview(item)">
                                  <el-button
                                    type="text"
                                    :icon="
                                      playingVoice &&
                                      currentPlayingVoiceId === item.value &&
                                      !isPaused
                                        ? 'el-icon-video-pause'
                                        : 'el-icon-video-play'
                                    "
                                    size="small"
                                    @click.stop="toggleAudioPlayback(item.value)"
                                    :loading="false"
                                    class="play-button"
                                  />
                                </template>
                              </div>
                            </el-option>
                          </el-select>
                          <el-button
                            class="edit-function-btn"
                            style="margin-left: 10px;"
                            @click="openTtsAdvancedSettings"
                          >
                            {{ $t('roleConfig.advancedSettings') }}
                          </el-button>
                        </div>
                      </el-form-item>
                    </div>
                  </div>
                </div>
              </div>
            </el-form>
          </el-card>
        </div>
      </div>
    </div>
    <function-dialog
      v-model="showFunctionDialog"
      :functions="currentFunctions"
      :all-functions="allFunctions"
      :agent-id="$route.query.agentId || ''"
      @update-functions="handleUpdateFunctions"
      @dialog-closed="handleDialogClosed"
    />
    <context-provider-dialog
      :visible.sync="showContextProviderDialog"
      :providers="currentContextProviders"
      @confirm="handleUpdateContext"
    />
    <tts-advanced-settings
      :visible.sync="showTtsAdvancedDialog"
      :settings="ttsSettings"
      :checked-replacement-word-ids="checkedReplacementWordIds"
      @save="handleTtsSettingsSave"
    />
    <el-footer>
      <version-footer />
    </el-footer>
  </div>
</template>

<script>
import Api from "@/apis/api";
import { getServiceUrl } from "@/apis/api";
import RequestService from "@/apis/httpRequest";
import FunctionDialog from "@/components/FunctionDialog.vue";
import ContextProviderDialog from "@/components/ContextProviderDialog.vue";
import TtsAdvancedSettings from "@/components/TtsAdvancedSettings.vue";
import HeaderBar from "@/components/HeaderBar.vue";
import i18n from "@/i18n";
import featureManager from "@/utils/featureManager"; 
import VersionFooter from "@/components/VersionFooter.vue";

export default {
  name: "RoleConfigPage",
  components: { HeaderBar, FunctionDialog, ContextProviderDialog, TtsAdvancedSettings, VersionFooter },
  data() {
    return {
      showContextProviderDialog: false,
      showTtsAdvancedDialog: false,
      ttsSettings: {
        volume: 0,
        speed: 0,
        pitch: 0
      },
      tempSummaryMemory: "",
      form: {
        agentCode: "",
        agentName: "",
        voiceMode: "classic_pipeline",
        googleLiveConfigJson: "",
        googleLiveConfig: {
          model: "gemini-2.5-flash-native-audio-preview-12-2025",
          enable_audio_input: true,
          enable_audio_output: true,
          native_voice: true,
          fallback_to_classic_on_error: true,
        },
        ttsVoiceId: "",
        ttsVolume: null,
        ttsRate: null,
        ttsPitch: null,
        chatHistoryConf: 0,
        systemPrompt: "",
        summaryMemory: "",
        langCode: "",
        language: "",
        sort: "",
        model: {
          ttsModelId: "",
          vadModelId: "",
          asrModelId: "",
          llmModelId: "",
          slmModelId: "",
          vllmModelId: "",
          memModelId: "",
          intentModelId: "",
        },
      },
      models: [
        { label: this.$t("roleConfig.vad"), key: "vadModelId", type: "VAD" },
        { label: this.$t("roleConfig.asr"), key: "asrModelId", type: "ASR" },
        { label: this.$t("roleConfig.llm"), key: "llmModelId", type: "LLM" },
        { label: this.$t("roleConfig.slm"), key: "slmModelId", type: "SLM" },
        { label: this.$t("roleConfig.vllm"), key: "vllmModelId", type: "VLLM" },
        { label: this.$t("roleConfig.intent"), key: "intentModelId", type: "Intent" },
        { label: this.$t("roleConfig.memory"), key: "memModelId", type: "Memory" },
        { label: this.$t("roleConfig.tts"), key: "ttsModelId", type: "TTS" },
      ],
      llmModeTypeMap: new Map(),
      modelOptions: {},
      templates: [],
      loadingTemplate: false,
      voiceOptions: [],
      voiceDetails: {}, // SaveCompleteVoice info
      showFunctionDialog: false,
      currentFunctions: [],
      currentContextProviders: [],
      allFunctions: [],
      originalFunctions: [],
      playingVoice: false,
      isPaused: false,
      currentAudio: null,
      currentPlayingVoiceId: null,
      // LanguageFilter relatedStatus
      languageOptions: [], // LanguageOption list
      selectedLanguage: '', // Currently selectedLanguage
      // FunctionStatus
      featureStatus: {
        vad: false, // LanguageDetect activity featureStatus
        asr: false, // Speech recognition featureStatus
      },
      dynamicTags: [],
      inputVisible: false,
      inputValue: '',
      checkedReplacementWordIds: []
    };
  },
  methods: {
    createDefaultGoogleLiveConfig() {
      return {
        api_key: "",
        model: "gemini-2.5-flash-native-audio-preview-12-2025",
        voice_name: "",
        enable_audio_input: true,
        enable_audio_output: true,
        native_voice: true,
        connect_timeout_sec: 10,
        recv_timeout_sec: 30,
        input_flush_delay_sec: 0.8,
        input_sample_rate: 16000,
        output_sample_rate: 24000,
        interrupt_on_input_while_speaking: true,
        interrupt_rms_threshold: 600,
        interrupt_min_output_age_sec: 0.25,
        interrupt_suppress_audio_sec: 0.25,
        drop_input_while_speaking: false,
        barge_in: true,
        barge_in_rms_threshold: 600,
        barge_in_min_output_age_sec: 0.25,
        send_transcript_events: true,
        send_llm_state_events: false,
        reconnect: {
          enabled: true,
          max_retries: 2,
          backoff_ms: 500,
        },
        fallback_to_classic_on_error: true,
      };
    },
    normalizeGoogleLiveConfig(config) {
      const defaults = this.createDefaultGoogleLiveConfig();
      if (!config || typeof config !== "object" || Array.isArray(config)) {
        return defaults;
      }
      return {
        ...defaults,
        ...config,
        reconnect: {
          ...defaults.reconnect,
          ...(config.reconnect && typeof config.reconnect === "object" && !Array.isArray(config.reconnect)
            ? config.reconnect
            : {}),
        },
      };
    },
    parseGoogleLiveConfig(jsonValue) {
      if (!jsonValue) {
        return this.createDefaultGoogleLiveConfig();
      }
      try {
        return this.normalizeGoogleLiveConfig(JSON.parse(jsonValue));
      } catch (error) {
        console.warn("Invalid googleLiveConfigJson, using defaults", error);
        return this.createDefaultGoogleLiveConfig();
      }
    },
    buildGoogleLiveConfigJson() {
      return JSON.stringify(this.normalizeGoogleLiveConfig(this.form.googleLiveConfig));
    },
    goToHome() {
      this.$router.push("/home");
    },
    async saveConfig() {
      try {
        await this.handleSaveAgentTags(this.$route.query.agentId);
      } catch (error) {
        console.error('Save tag failed:', error);
        return;
      }

      const configData = {
        agentCode: this.form.agentCode,
        agentName: this.form.agentName,
        asrModelId: this.form.model.asrModelId,
        vadModelId: this.form.model.vadModelId,
        llmModelId: this.form.model.llmModelId,
        slmModelId: this.form.model.slmModelId,
        vllmModelId: this.form.model.vllmModelId,
        ttsModelId: this.form.model.ttsModelId,
        ttsVoiceId: this.form.ttsVoiceId,
        ttsLanguage: this.selectedLanguage,
        chatHistoryConf: this.form.chatHistoryConf,
        memModelId: this.form.model.memModelId,
        intentModelId: this.form.model.intentModelId,
        systemPrompt: this.form.systemPrompt,
        summaryMemory: this.form.summaryMemory,
        langCode: this.form.langCode,
        language: this.form.language,
        sort: this.form.sort,
        voiceMode: this.form.voiceMode,
        googleLiveConfigJson:
          this.form.voiceMode === "google_live"
            ? this.buildGoogleLiveConfigJson()
            : null,
        functions: this.currentFunctions.map((item) => {
          return {
            pluginId: item.id,
            paramInfo: item.params,
          };
        }),
        contextProviders: this.currentContextProviders,
        correctWordFileIds: this.checkedReplacementWordIds,
      };

      // Only when user setTTSPass only when parameter (notnull/undefined)
      if (this.form.ttsVolume !== null && this.form.ttsVolume !== undefined) {
        configData.ttsVolume = this.form.ttsVolume;
      }
      if (this.form.ttsRate !== null && this.form.ttsRate !== undefined) {
        configData.ttsRate = this.form.ttsRate;
      }
      if (this.form.ttsPitch !== null && this.form.ttsPitch !== undefined) {
        configData.ttsPitch = this.form.ttsPitch;
      }
      Api.agent.updateAgentConfig(this.$route.query.agentId, configData, ({ data }) => {
        if (data.code === 0) {
          this.$message.success({
            message: i18n.t("roleConfig.saveSuccess"),
            showClose: true,
          });
        } else {
          this.$message.error({
            message: data.msg || i18n.t("roleConfig.saveFailed"),
            showClose: true,
          });
        }
      });
      
    },
    resetConfig() {
      this.$confirm(i18n.t("roleConfig.confirmReset"), i18n.t("message.info"), {
        confirmButtonText: i18n.t("button.ok"),
        cancelButtonText: i18n.t("button.cancel"),
        type: "warning",
      })
        .then(() => {
          this.form = {
            agentCode: "",
            agentName: "",
            voiceMode: "classic_pipeline",
            googleLiveConfigJson: "",
            googleLiveConfig: this.createDefaultGoogleLiveConfig(),
            ttsVoiceId: "",
            chatHistoryConf: 0,
            systemPrompt: "",
            summaryMemory: "",
            langCode: "",
            language: "",
            sort: "",
            model: {
              ttsModelId: "",
              vadModelId: "",
              asrModelId: "",
              llmModelId: "",
              slmModelId: "",
              vllmModelId: "",
              memModelId: "",
              intentModelId: "",
            },
          };
          this.dynamicTags = [];
          this.currentFunctions = [];
          this.$message.success({
            message: i18n.t("roleConfig.resetSuccess"),
            showClose: true,
          });
        })
        .catch(() => {});
    },
    fetchTemplates() {
      Api.agent.getAgentTemplate(({ data }) => {
        if (data.code === 0) {
          this.templates = data.data;
        } else {
          this.$message.error(data.msg || i18n.t("roleConfig.fetchTemplatesFailed"));
        }
      });
    },
    selectTemplate(template) {
      if (this.loadingTemplate) return;
      this.loadingTemplate = true;
      try {
        this.applyTemplateData(template);
        this.$message.success({
          message: `${template.agentName}${i18n.t("roleConfig.templateApplied")}`,
          showClose: true,
        });
      } catch (error) {
        this.$message.error({
          message: i18n.t("roleConfig.applyTemplateFailed"),
          showClose: true,
        });
        console.error("Apply template failed:", error);
      } finally {
        this.loadingTemplate = false;
      }
    },
    applyTemplateData(templateData) {
      const nextVoiceMode = templateData.voiceMode || this.form.voiceMode;
      const nextGoogleLiveConfigJson =
        nextVoiceMode === "google_live"
          ? templateData.googleLiveConfigJson || this.form.googleLiveConfigJson
          : "";
      this.form = {
        ...this.form,
        agentName: templateData.agentName || this.form.agentName,
        voiceMode: nextVoiceMode,
        googleLiveConfigJson: nextGoogleLiveConfigJson,
        googleLiveConfig:
          nextVoiceMode === "google_live"
            ? this.parseGoogleLiveConfig(nextGoogleLiveConfigJson)
            : this.createDefaultGoogleLiveConfig(),
        ttsVoiceId: templateData.ttsVoiceId || this.form.ttsVoiceId,
        chatHistoryConf: templateData.chatHistoryConf || this.form.chatHistoryConf,
        systemPrompt: templateData.systemPrompt || this.form.systemPrompt,
        summaryMemory: templateData.summaryMemory || this.form.summaryMemory,
        langCode: templateData.langCode || this.form.langCode,
        model: {
          ttsModelId: templateData.ttsModelId || this.form.model.ttsModelId,
          vadModelId: templateData.vadModelId || this.form.model.vadModelId,
          asrModelId: templateData.asrModelId || this.form.model.asrModelId,
          llmModelId: templateData.llmModelId || this.form.model.llmModelId,
          slmModelId: templateData.llmModelId || this.form.model.slmModelId,
          vllmModelId: templateData.vllmModelId || this.form.model.vllmModelId,
          memModelId: templateData.memModelId || this.form.model.memModelId,
          intentModelId: templateData.intentModelId || this.form.model.intentModelId,
        },
      };
    },
    fetchAgentConfig(agentId) {
      Api.agent.getDeviceConfig(agentId, ({ data }) => {
        if (data.code === 0) {
          this.tempSummaryMemory = "";
          this.form = {
            ...this.form,
            ...data.data,
            voiceMode: data.data.voiceMode || "classic_pipeline",
            googleLiveConfigJson: data.data.googleLiveConfigJson || "",
            googleLiveConfig: this.parseGoogleLiveConfig(
              data.data.googleLiveConfigJson
            ),
            model: {
              ttsModelId: data.data.ttsModelId,
              vadModelId: data.data.vadModelId,
              asrModelId: data.data.asrModelId,
              llmModelId: data.data.llmModelId,
              slmModelId: data.data.slmModelId,
              vllmModelId: data.data.vllmModelId,
              memModelId: data.data.memModelId,
              intentModelId: data.data.intentModelId,
            },
          };

          // SyncTTSSet tottsSettings
          this.ttsSettings = {
            volume: this.form.ttsVolume || 0,
            speed: this.form.ttsRate || 0,
            pitch: this.form.ttsPitch || 0
          };
          // SyncReplacement wordtocheckedReplacementWordIds
          this.checkedReplacementWordIds = data.data.correctWordFileIds || [];

          // Backend only gave minimal mapping:[{ id, agentId, pluginId }, ...]
          const savedMappings = data.data.functions || [];
          
          // Load context config
          this.currentContextProviders = data.data.contextProviders || [];

          // Ensure first allFunctions Already loaded (if not, first fetchAllFunctions)
          const ensureFuncs = this.allFunctions.length
            ? Promise.resolve()
            : this.fetchAllFunctions();

          ensureFuncs.then(() => {
            // Merge: according to pluginId(id Field) put fullMetadataInfoComplete
            this.currentFunctions = savedMappings.map((mapping) => {
              const meta = this.allFunctions.find((f) => f.id === mapping.pluginId);
              if (!meta) {
                // Plugin definition not found, fallback handling
                return { id: mapping.pluginId, name: mapping.pluginId, params: {} };
              }
              return {
                id: mapping.pluginId,
                name: meta.name,
                // If backend still has paramInfo Use field mapping.paramInfoOtherwise use meta.params Default value
                params: mapping.paramInfo || { ...meta.params },
                fieldsMeta: meta.fieldsMeta, // Keep for dialog render tooltip
              };
            });
            // Back up original, for restore on cancel
            this.originalFunctions = JSON.parse(JSON.stringify(this.currentFunctions));

            // Ensure intent recognition option visibility correct
            this.updateIntentOptionsVisibility();
          });
        } else {
          this.$message.error(data.msg || i18n.t("roleConfig.fetchConfigFailed"));
        }
      });
    },
    fetchModelOptions() {
      this.models.forEach((model) => {
        if (model.type != "LLM") {
          Api.model.getModelNames(model.type, "", ({ data }) => {
            if (data.code === 0) {
              this.$set(
                this.modelOptions,
                model.type,
                data.data.map((item) => ({
                  value: item.id,
                  label: item.modelName,
                  isHidden: false,
                }))
              );

              // If intent recognition option, need based on currentLLMType update visibility
              if (model.type === "Intent") {
                this.updateIntentOptionsVisibility();
              }
            } else {
              this.$message.error(data.msg || i18n.t("roleConfig.fetchModelsFailed"));
            }
          });
        } else {
          Api.model.getLlmModelCodeList("", ({ data }) => {
            if (data.code === 0) {
              let LLMdata = [];
              data.data.forEach((item) => {
                LLMdata.push({
                  value: item.id,
                  label: item.modelName,
                  isHidden: false,
                });
                this.llmModeTypeMap.set(item.id, item.type);
              });
              this.$set(this.modelOptions, model.type, LLMdata);
            } else {
              this.$message.error(data.msg || "Failed to get LLM model list");
            }
          });
        }
      });
    },
    fetchVoiceOptions(modelId) {
      if (!modelId) {
        this.voiceOptions = [];
        this.voiceDetails = {};
        this.languageOptions = [];
        this.selectedLanguage = '';
        return;
      }
      Api.model.getModelVoices(modelId, "", ({ data }) => {
        if (data.code === 0 && data.data) {
          // SaveCompleteVoice info
          this.voiceDetails = data.data.reduce((acc, voice) => {
            acc[voice.id] = voice;
            return acc;
          }, {});
          
          // Extract allLanguageOptions and deduplicate
          const allLanguages = new Set();
          data.data.forEach(voice => {
            if (voice.languages) {
              const languagesArray = voice.languages.split(/[、；;,，]/).map(lang => lang.trim()).filter(lang => lang);
              languagesArray.forEach(lang => allLanguages.add(lang));
            }
          });

          this.languageOptions = Array.from(allLanguages).map(lang => ({
            value: lang,
            label: lang
          }));

          // Use user-selected returned by backendLanguage, if none, use first oneLanguageOption
          if (this.form.ttsLanguage && this.languageOptions.some(option => option.value === this.form.ttsLanguage)) {
            this.selectedLanguage = this.form.ttsLanguage;
          } else if (this.languageOptions.length > 0) {
            this.selectedLanguage = this.languageOptions[0].value;
          }

          // Based on selectedLanguageFilter voice
          this.filterVoicesByLanguage();
        } else {
          this.voiceOptions = [];
          this.voiceDetails = {};
          this.languageOptions = [];
          this.selectedLanguage = '';
        }
      });
    },
    
    // Based onLanguageFilter voice
    filterVoicesByLanguage() {
      if (!this.voiceDetails || Object.keys(this.voiceDetails).length === 0) {
        this.voiceOptions = [];
        return;
      }

      const allVoices = Object.values(this.voiceDetails);

      // Based on selectedLanguageFilter voice
      const filteredVoices = allVoices.filter(voice => {
        if (!voice.languages) {
          // For noneLanguageInfocloned voice, always show
          return Boolean(voice.isClone);
        }
        const languagesArray = voice.languages.split(/[、；;,，]/).map(lang => lang.trim()).filter(lang => lang);
        return languagesArray.includes(this.selectedLanguage);
      });

      this.voiceOptions = filteredVoices.map((voice) => ({
        value: voice.id,
        label: voice.name,
        voiceDemo: voice.voiceDemo,
        voice_demo: voice.voice_demo,
        isClone: Boolean(voice.isClone),
        train_status: voice.trainStatus,
      }));

      // Check whether current selected voice supports currentLanguage, if unsupported select first
      const currentVoiceSupportsLanguage = this.form.ttsVoiceId &&
        filteredVoices.some(voice => voice.id === this.form.ttsVoiceId);

      if (!currentVoiceSupportsLanguage) {
        this.form.ttsVoiceId = filteredVoices.length > 0 ? filteredVoices[0].id : '';
      }

      // Sync tottsSettings(if value isnull, use0as display default value, but notModifyformValue in)
      this.ttsSettings = {
        volume: this.form.ttsVolume !== null && this.form.ttsVolume !== undefined ? this.form.ttsVolume : 0,
        speed: this.form.ttsRate !== null && this.form.ttsRate !== undefined ? this.form.ttsRate : 0,
        pitch: this.form.ttsPitch !== null && this.form.ttsPitch !== undefined ? this.form.ttsPitch : 0
      };
    },

    getFunctionDisplayChar(name) {
      if (!name || name.length === 0) return "";

      for (let i = 0; i < name.length; i++) {
        const char = name[i];
        if (/[\u4e00-\u9fa5a-zA-Z0-9]/.test(char)) {
          return char;
        }
      }

      // If no valid character found, return first character
      return name.charAt(0);
    },
    showFunctionIcons(type) {
      return type === "Intent" && this.form.model.intentModelId !== "Intent_nointent";
    },
    handleModelChange(type, value) {
      if (type === "Intent" && value !== "Intent_nointent") {
        this.fetchAllFunctions();
      }
      if (type === "Memory") {
        if (value === "Memory_nomem") {
          // Model without memory, defaultDo not recordChat history
          this.form.chatHistoryConf = 0;
        } else {
          // Model with memory, defaultRecord textAnd voice
          this.form.chatHistoryConf = 2;
        }
        if (value === "Memory_nomem" || value === "Memory_mem_report_only") {
          this.tempSummaryMemory = this.form.summaryMemory;
          this.form.summaryMemory = "";
        } else if (this.tempSummaryMemory !== "" && this.form.summaryMemory === "") {
          this.form.summaryMemory = this.tempSummaryMemory;
          this.tempSummaryMemory = "";
        }
      }
      if (type === "LLM") {
        // whenLLMWhen type changes, update visibility of intent recognition options
        this.updateIntentOptionsVisibility();
      }
    },
    fetchAllFunctions() {
      return new Promise((resolve, reject) => {
        Api.model.getPluginFunctionList(null, ({ data }) => {
          if (data.code === 0) {
            this.allFunctions = data.data.map((item) => {
              const meta = JSON.parse(item.fields || "[]");
              const params = meta.reduce((m, f) => {
                m[f.key] = f.default;
                return m;
              }, {});
              return { ...item, fieldsMeta: meta, params };
            });
            resolve();
          } else {
            this.$message.error(data.msg || i18n.t("roleConfig.fetchPluginsFailed"));
            reject();
          }
        });
      });
    },
    openFunctionDialog() {
      // When showing edit dialog, ensure allFunctions Already loaded
      if (this.allFunctions.length === 0) {
        this.fetchAllFunctions().then(() => (this.showFunctionDialog = true));
      } else {
        this.showFunctionDialog = true;
      }
    },
    openContextProviderDialog() {
      this.showContextProviderDialog = true;
    },
    openTtsAdvancedSettings() {
      this.showTtsAdvancedDialog = true;
    },
    handleTtsSettingsSave(settings) {
      const { replacementWordIds, ...ttsSettings } = settings;
      this.checkedReplacementWordIds = replacementWordIds;
      // SaveTTSSet
      this.ttsSettings = ttsSettings;
      this.form.ttsVolume = ttsSettings.volume;
      this.form.ttsRate = ttsSettings.speed;
      this.form.ttsPitch = ttsSettings.pitch;
    },
    handleUpdateContext(providers) {
      this.currentContextProviders = providers;
    },
    handleUpdateFunctions(selected) {
      this.currentFunctions = selected;
    },
    handleDialogClosed(saved) {
      if (!saved) {
        this.currentFunctions = JSON.parse(JSON.stringify(this.originalFunctions));
      } else {
        this.originalFunctions = JSON.parse(JSON.stringify(this.currentFunctions));
      }
      this.showFunctionDialog = false;
    },
    updateIntentOptionsVisibility() {
      // Based on current selectedLLMUpdate intent recognition option visibility by type
      const currentLlmId = this.form.model.llmModelId;
      if (!currentLlmId || !this.modelOptions["Intent"]) return;

      const llmType = this.llmModeTypeMap.get(currentLlmId);
      if (!llmType) return;

      this.modelOptions["Intent"].forEach((item) => {
        if (item.value === "Intent_function_call") {
          // IfllmTypeisopenaiorollama, allow selectfunction_call
          // Otherwise hidefunction_callOption
          if (llmType === "openai" || llmType === "ollama") {
            item.isHidden = false;
          } else {
            item.isHidden = true;
          }
        } else {
          // Other intent recognition options always visible
          item.isHidden = false;
        }
      });

      // If current selected intent recognition isfunction_call, butLLMIf type unsupported, set to first optional item
      if (
        this.form.model.intentModelId === "Intent_function_call" &&
        llmType !== "openai" &&
        llmType !== "ollama"
      ) {
        // Find first visible option
        const firstVisibleOption = this.modelOptions["Intent"].find(
          (item) => !item.isHidden
        );
        if (firstVisibleOption) {
          this.form.model.intentModelId = firstVisibleOption.value;
        } else {
          // If no visible option, set toIntent_nointent
          this.form.model.intentModelId = "Intent_nointent";
        }
      }
    },
    // Check if audio preview exists
    hasAudioPreview(item) {
      // Check if cloned audio
      // Use backend actual returned isClone Field
      const isCloneAudio = Boolean(item.isClone);
      
      // Check if valid audio existsURL, only use fields actually returned by backend
      const hasValidAudioUrl = !!((item.voice_demo || item.voiceDemo)?.trim());
      
      // Clone audio always shows play button. Normal audio needs validURLShow only
      return isCloneAudio || hasValidAudioUrl;
    },

    // Play/Pause audio switch
    toggleAudioPlayback(voiceId) {
      // If clicked audio is currently playing audio, toggle pause/PlayStatus
      if (this.playingVoice && this.currentPlayingVoiceId === voiceId) {
        if (this.isPaused) {
          // From pauseStatusResume playback
          this.currentAudio.play().catch((error) => {
            console.error("Resume playback failed:", error);
            this.$message.warning(this.$t('roleConfig.cannotResumeAudio'));
          });
          this.isPaused = false;
        } else {
          // Pause playback
          this.currentAudio.pause();
          this.isPaused = true;
        }
        return;
      }

      // Otherwise start playing new audio
      this.playVoicePreview(voiceId);
    },

    // Play voice preview
    playVoicePreview(voiceId = null) {
      // If passed invoiceId, use passed-in if provided, otherwise use currently selected
      const targetVoiceId = voiceId || this.form.ttsVoiceId;

      if (!targetVoiceId) {
        this.$message.warning(this.$t('roleConfig.selectVoiceFirst'));
        return;
      }

      // Stop currently playing audio
      if (this.currentAudio) {
        this.currentAudio.pause();
        this.currentAudio = null;
      }

      // Reset playbackStatus
      this.isPaused = false;
      this.currentPlayingVoiceId = targetVoiceId;

      try {
        // fromSaveGet audio from voice detailsURL
        const voiceDetail = this.voiceDetails[targetVoiceId];

        // Add debugInfo
        console.log("Currently selected voice ID:", targetVoiceId);
        console.log("Voice details:", voiceDetail);

        // Try multiple possible audio property names
        let audioUrl = null;
        let isCloneAudio = false;

        if (voiceDetail) {
          // Use backend actual returned isClone field determines whether cloned audio
          isCloneAudio = Boolean(voiceDetail.isClone);
          console.log(
            "Cloned audio judgment result:",
            isCloneAudio,
            "Training status:",
            voiceDetail.train_status
          );

          // Get audioURL
          if (isCloneAudio && voiceDetail.id) {
            // For clone audio, use correct interface provided by backend
            // Note: need get audio through two steps hereURL
            // 1. FirstGet audio download ID
            // 2. Then use thisIDBuild playbackURL
            // Due to async operation, need request firstgetAudioId
            console.log("Cloned audio detected, preparing to get audio URL:", voiceDetail.id);

            // Create onePromiseHandle async audio fetchURLOperation of
            const getCloneAudioUrl = () => {
              return new Promise((resolve) => {
                // First callgetAudioIdAPI get temporaryUUID
                RequestService.sendRequest()
                  .url(`${getServiceUrl()}/voiceClone/audio/${voiceDetail.id}`)
                  .method("POST")
                  .success((res) => {
                    if (res.data.code === 0 && res.data.data) {
                      // Handle returned data format, atres.dataWrap another layer on top.data
                      const audioId = res.data.data;
                      console.log("Got audio ID:", audioId);
                      // Use returnedUUIDBuild playbackURL
                      const playUrl = `${getServiceUrl()}/voiceClone/play/${audioId}`;
                      console.log("Build cloned audio playback URL:", playUrl);
                      resolve(playUrl);
                    } else {
                      console.error("Failed to get audio ID:", res.msg);
                      resolve(null);
                    }
                  })
                  .networkFail((err) => {
                    console.error("Audio ID request API failed:", err);
                    resolve(null);
                  })
                  .send();
              });
            };

            // Set playbackStatus
            this.playingVoice = true;
            // CreateAudioInstance
            this.currentAudio = new Audio();
            // Set volume
            this.currentAudio.volume = 1.0;

            // Set timeout to prevent too long loading
            const timeoutId = setTimeout(() => {
              if (this.currentAudio && this.playingVoice) {
                this.$message.warning(this.$t('roleConfig.audioLoadTimeout'));
                this.playingVoice = false;
              }
            }, 10000); // 10Second timeout

            // Listen playbackError
            this.currentAudio.onerror = () => {
              clearTimeout(timeoutId);
              console.error("Clone audio playback error");
              this.$message.warning(this.$t('roleConfig.cloneAudioPlayFailed'));
              this.playingVoice = false;
            };

            // Listen playback start, clear timeout
            this.currentAudio.onplay = () => {
              clearTimeout(timeoutId);
            };

            // Listen playback end
            this.currentAudio.onended = () => {
              this.playingVoice = false;
            };

            // Handle async getURLAnd play
            getCloneAudioUrl().then((url) => {
              if (url) {
                // Set audioURLAnd play
                this.currentAudio.src = url;
                this.currentAudio.play().catch((error) => {
                  clearTimeout(timeoutId);
                  console.error("Failed to play cloned audio:", error);
                  this.$message.warning(this.$t('roleConfig.cannotPlayCloneAudio'));
                  this.playingVoice = false;
                });
              } else {
                clearTimeout(timeoutId);
                this.$message.warning(this.$t('roleConfig.getCloneAudioFailed'));
                this.playingVoice = false;
              }
            });

            // Return, avoid continuing normal audio playback logic below
            return;
          } else {
            // For normal audio, only use fields actually returned by backend
            audioUrl =
              voiceDetail.voiceDemo ||
              voiceDetail.voice_demo;
          }

          // If not found, try check whether hasURLformat field
          if (!audioUrl) {
            for (const key in voiceDetail) {
              const value = voiceDetail[key];
              if (
                typeof value === "string" &&
                (value.startsWith("http://") ||
                  value.startsWith("https://") ||
                  value.endsWith(".mp3") ||
                  value.endsWith(".wav") ||
                  value.endsWith(".ogg"))
              ) {
                audioUrl = value;
                console.log(`Found possible audioURLIn field '${key}':`, audioUrl);
                break;
              }
            }
          }
        }

        if (!audioUrl) {
          // If no audioURLShow friendlyPrompt
          this.$message.warning(this.$t('roleConfig.noPreviewAudio'));
          return;
        }

        // Processing logic for non-cloned audio
        if (!isCloneAudio) {
          // Set playbackStatus
          this.playingVoice = true;

          // Create andPlay audio
          this.currentAudio = new Audio();
          this.currentAudio.src = audioUrl;

          // Set volume
          this.currentAudio.volume = 1.0;

          // Set timeout to prevent too long loading
          const timeoutId = setTimeout(() => {
            if (this.currentAudio && this.playingVoice) {
              this.$message.warning(this.$t('roleConfig.audioLoadTimeout'));
              this.playingVoice = false;
            }
          }, 10000); // 10Second timeout

          // Listen playbackError
          this.currentAudio.onerror = () => {
            clearTimeout(timeoutId);
            console.error("Audio playback error");
            this.$message.warning(this.$t('roleConfig.audioPlayFailed'));
            this.playingVoice = false;
          };

          // Listen playback start, clear timeout
          this.currentAudio.onplay = () => {
            clearTimeout(timeoutId);
          };

          // Listen playback end
          this.currentAudio.onended = () => {
            this.playingVoice = false;
          };

          // StartPlay audio
          this.currentAudio.play().catch((error) => {
            clearTimeout(timeoutId);
            console.error("Playback failed:", error);
            this.$message.warning(this.$t('roleConfig.cannotPlayAudio'));
            this.playingVoice = false;
          });
        }
      } catch (error) {
        console.error("Error during audio playback:", error);
        this.$message.error(this.$t('roleConfig.audioPlayError'));
        this.playingVoice = false;
      }
    },
    updateChatHistoryConf() {
      if (this.form.model.memModelId === "Memory_nomem") {
        this.form.chatHistoryConf = 0;
      }
    },
    // Load functionStatus
    async loadFeatureStatus() {
      try {
        // EnsurefeatureManagerInitialization completed
        await featureManager.waitForInitialization();
        const config = featureManager.getConfig();
        this.featureStatus.voiceprintRecognition = config.voiceprintRecognition || false;
        this.featureStatus.vad = config.vad || false;
        this.featureStatus.asr = config.asr || false;
      } catch (error) {
        console.error("Failed to load feature status:", error);
      }
    },
    handleClose(id) {
      this.dynamicTags = this.dynamicTags.filter((item) => item.id !== id);
    },

    showInput() {
      this.inputVisible = true;
      this.$nextTick(_ => {
        this.$refs.saveTagInput.$refs.input.focus();
      });
    },

    handleInputConfirm() {
      let inputValue = this.inputValue;
      if (inputValue) {
        const tag = { id: new Date().getTime(), tagName: inputValue };
        this.dynamicTags.push(tag);
      }
      this.inputVisible = false;
      this.inputValue = '';
    },
    getAgentTags(agentId) {
      Api.agent.getAgentTags(agentId, ({ data }) => {
        if (data.code === 0) {
          this.dynamicTags = data.data || [];
        }
      });
    },
    handleSaveAgentTags(agentId) {
      return new Promise((resolve, reject) => {
        const tagNames = this.dynamicTags.map(tag => tag.tagName);
        Api.agent.saveAgentTags(agentId, { tagNames }, ({ data }) => {
          if (data.code === 0) {
            resolve();
          } else {
            reject(data.msg);
          }
        });
      });
    }
  },
  watch: {
    "form.model.ttsModelId": {
      handler(newVal, oldVal) {
        if (oldVal && newVal !== oldVal) {
          this.form.ttsVoiceId = "";
          this.fetchVoiceOptions(newVal);
        } else {
          this.fetchVoiceOptions(newVal);
        }
      },
      immediate: true,
    },
    voiceOptions: {
      handler(newVal) {
        if (newVal && newVal.length > 0 && !this.form.ttsVoiceId) {
          this.form.ttsVoiceId = newVal[0].value;
        }
      },
      immediate: true,
    },
  },
  async mounted() {
    const agentId = this.$route.query.agentId;
    if (agentId) {
      this.fetchAgentConfig(agentId);
      this.getAgentTags(agentId);
      this.fetchAllFunctions();
    }
    this.fetchModelOptions();
    this.fetchTemplates();
    // Load functionStatus, ensurefeatureManagerInitialized
    await this.loadFeatureStatus();
  },
};
</script>

<style lang="scss" scoped>
::v-deep .el-radio-group {
  .is-active {
    .el-radio-button__inner {
      &:hover {
        color: #fff !important;
      }
    }
  }
}
.welcome {
  min-width: 900px;
  height: 100vh;
  display: flex;
  position: relative;
  flex-direction: column;
  background: linear-gradient(to bottom right, #dce8ff, #e4eeff, #e6cbfd);
  background-size: cover;
  -webkit-background-size: cover;
  -o-background-size: cover;
  overflow: hidden;
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

.main-wrapper {
  height: calc(100vh - 63px - 35px - 60px);
  margin: 0 22px;
  border-radius: 15px;
  box-shadow: 0 2px 12px rgba(0, 0, 0, 0.1);
  position: relative;
  background: rgba(237, 242, 255, 0.5);
  display: flex;
  flex-direction: column;
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

.config-card {
  background: white;
  border: none;
  box-shadow: none;
  display: flex;
  flex-direction: column;
  flex: 1;
  overflow-y: auto;
}

.config-header {
  position: relative;
  display: flex;
  align-items: center;
  gap: 13px;
  padding: 0 0 5px 0;
  font-weight: 700;
  font-size: 19px;
  color: #3d4566;
  justify-content: space-between;
}

.header-left {
  display: flex;
  align-items: center;
  gap: 13px;
  flex-shrink: 0;
}

.header-tags {
  display: flex;
  align-items: center;
  gap: 8px;
  flex: 1;
  min-width: 0;
  overflow-x: auto;
  padding-bottom: 4px;
  &::-webkit-scrollbar {
      height: 6px;
      background: #e6ebff;
    }
    &::-webkit-scrollbar-thumb {
      background: #5778ff;
      border-radius: 8px;
    }
}

.header-tags .el-tag {
  flex-shrink: 0;
}

.more-tag {
  cursor: pointer;
  flex-shrink: 0;
}

.all-tags-popover {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
  padding: 8px;
}

.header-icon {
  width: 37px;
  height: 37px;
  background: #5778ff;
  border-radius: 50%;
  display: flex;
  align-items: center;
  justify-content: center;
}

.header-icon img {
  width: 19px;
  height: 19px;
}

.divider {
  height: 1px;
  background: #e8f0ff;
}

.form-content {
  padding: 2vh 0;
}

.form-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 20px;
}

.form-column {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.form-input {
  width: 100%;
}

.form-select {
  flex: 1;
  width: 100%;
  height: 36px;
}

.play-button {
  color: #409eff;
  transition: color 0.3s;
}

.play-button:hover {
  color: #66b1ff;
}

.play-button.is-loading {
  color: #909399;
}

.form-textarea {
  width: 100%;
}

.voice-select-wrapper {
  display: flex;
  align-items: center;
  gap: 10px;
}

.template-container {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
}

.template-item {
  height: 4vh;
  min-width: 60px;
  padding: 0 12px;
  border-radius: 8px;
  background: #e6ebff;
  line-height: 4vh;
  font-weight: 400;
  font-size: 11px;
  text-align: center;
  color: #5778ff;
  cursor: pointer;
  transition: background-color 0.3s ease;
  white-space: nowrap;
}

.template-item:hover {
  background-color: #d0d8ff;
}

.model-select-wrapper {
  display: flex;
  align-items: center;
  width: 100%;
}

.model-row {
  display: flex;
  gap: 20px;
  margin-bottom: 6px;
}

.model-row .model-item {
  flex: 1;
  margin-bottom: 0;
}

.model-row .language-select-item {
  flex: 0 0 35%;
  max-width: 35%;
}

.model-row .language-select-item .language-select {
  width: 100%;
}

.google-live-panel {
  margin-bottom: 12px;
}

.google-live-switches {
  display: flex;
  flex-wrap: wrap;
  gap: 12px 20px;
  margin: 4px 0 8px 72px;
}

.model-row .el-form-item__label {
  font-size: 12px !important;
  color: #3d4566 !important;
  font-weight: 400;
  line-height: 22px;
  padding-bottom: 2px;
}

.function-icons {
  display: flex;
  align-items: center;
  margin-left: auto;
  padding-left: 10px;
}

.icon-dot {
  width: 25px;
  height: 25px;
  border-radius: 50%;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  color: #5778ff;
  font-weight: bold;
  font-size: 12px;
  margin-right: 8px;
  position: relative;
  background-color: #e6ebff;
}

::v-deep .el-form-item__label {
  font-size: 12px !important;
  color: #3d4566 !important;
  font-weight: 400;
  line-height: 22px;
  padding-bottom: 2px;
}

::v-deep .el-textarea .el-input__count {
  color: #909399;
  background: none;
  position: absolute;
  font-size: 12px;
  right: 3%;
}

.custom-close-btn {
  position: absolute;
  top: 25%;
  right: 0;
  transform: translateY(-50%);
  width: 35px;
  height: 35px;
  border-radius: 50%;
  border: 2px solid #cfcfcf;
  background: none;
  font-size: 30px;
  font-weight: lighter;
  color: #cfcfcf;
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 1;
  padding: 0;
  outline: none;
}

.custom-close-btn:hover {
  color: #409eff;
  border-color: #409eff;
}

.edit-function-btn {
  background: #e6ebff;
  color: #5778ff;
  border: 1px solid #adbdff;
  border-radius: 18px;
  padding: 10px 20px;
  transition: all 0.3s;
}

.edit-function-btn.active-btn {
  background: #5778ff;
  color: white;
}

.chat-history-options {
  display: flex;
  gap: 10px;
  min-width: 250px;
  justify-content: flex-end;
}

.chat-history-options ::v-deep .el-radio-button {
  border-color: #5778ff;
}

.chat-history-options ::v-deep .el-radio-button .el-radio-button__inner {
  color: #5778ff;
  border-color: #5778ff;
  background-color: transparent;
}

.chat-history-options ::v-deep .el-radio-button.is-active .el-radio-button__inner {
  background-color: #5778ff;
  border-color: #5778ff;
  color: white;
}

.chat-history-options ::v-deep .el-radio-button .el-radio-button__inner:hover {
  color: #5778ff;
}

.header-actions {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-left: auto;
}

.header-actions .hint-text {
  display: flex;
  align-items: center;
  gap: 4px;
  color: #979db1;
  font-size: 12px;
  margin-right: 8px;
}

.header-actions .hint-text img {
  width: 16px;
  height: 16px;
}

.header-actions .save-btn {
  background: #5778ff;
  color: white;
  border: none;
  border-radius: 18px;
  padding: 8px 16px;
  height: 32px;
  font-size: 14px;
}

.header-actions .reset-btn {
  background: #e6ebff;
  color: #5778ff;
  border: 1px solid #adbdff;
  border-radius: 18px;
  padding: 8px 16px;
  height: 32px;
}

.header-actions .custom-close-btn {
  position: static;
  transform: none;
  width: 32px;
  height: 32px;
  margin-left: 8px;
}

.context-provider-item ::v-deep .el-form-item__label {
  line-height: 42px !important;
}

.doc-link {
  color: #5778ff;
  text-decoration: none;
  margin-left: 4px;

  &:hover {
    text-decoration: underline;
  }
}

.slider-wrapper {
  width: 100%;
  padding-right: 12px;
}

.slider-hint {
  display: block;
  font-size: 12px;
  color: #909399;
  margin-top: 4px;
  line-height: 1.5;
}

.tts-slider {
  width: 100%;
}

.tts-slider ::v-deep .el-slider__input {
  width: 80px;
}

.tts-slider ::v-deep .el-input__inner {
  text-align: center;
  padding: 0 8px;
}
.custom-tag {
  background: #e6ebff;
  color: #5778ff;
  border-radius: 8px;
  font-size: 12px;
  font-weight: normal;
  border: none;
}
.custom-tag-btn {
  background: #e6ebff;
  color: #5778ff;
  border-radius: 8px;
  font-weight: normal;
  border: 1px solid #e6ebff;
  &:hover {
    background-color: #d0d8ff;
  }
}
.input-new-tag {
  width: 90px;
  &::v-deep(.el-input__inner) {
    width: 90px !important;
  }
}

</style>

<style>
.custom-tooltip {
  max-width: 400px !important;
  word-break: break-word;
}
</style>
