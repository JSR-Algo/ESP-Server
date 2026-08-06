<template>
  <el-card shadow="never" class="content-area">
    <div slot="header" class="card-header">{{ $t('lesson.assets') }}</div>

    <!-- Upload controls -->
    <div class="asset-form">
      <div class="field">
        <label class="field-label">{{ $t('lesson.layer') }}</label>
        <el-select v-model="layer" size="small" style="width: 180px" @change="onLayerChange">
          <el-option :label="$t('lesson.layerBackground')" value="backgroundScene" />
          <el-option :label="$t('lesson.layerTeachingObject')" value="teachingObject" />
          <el-option :label="$t('lesson.layerRobotOverlay')" value="robotOverlay" />
        </el-select>
      </div>

      <!-- robotOverlay pose picker → drives assetKey robotOverlay.<pose> -->
      <div class="field" v-if="layer === 'robotOverlay'">
        <label class="field-label">{{ $t('lesson.pose') }}</label>
        <el-select v-model="pose" size="small" style="width: 150px" @change="onPoseChange">
          <el-option :label="$t('lesson.poseTeach')" value="teach" />
          <el-option :label="$t('lesson.poseListening')" value="listening" />
          <el-option :label="$t('lesson.poseThinking')" value="thinking" />
          <el-option :label="$t('lesson.poseCelebrate')" value="celebrate" />
        </el-select>
      </div>

      <div class="field">
        <label class="field-label">{{ $t('lesson.role') }}</label>
        <el-input v-model="role" size="small" style="width: 150px" />
      </div>

      <div class="field">
        <label class="field-label">{{ $t('lesson.assetKey') }}</label>
        <el-input v-model="assetKey" size="small" style="width: 220px" />
      </div>

      <div class="field">
        <label class="field-label">{{ $t('lesson.critical') }}</label>
        <el-switch v-model="critical" />
      </div>
    </div>

    <div class="add-row">
      <el-upload
        ref="uploader"
        action="#"
        :auto-upload="false"
        :show-file-list="true"
        :limit="1"
        accept="image/png,image/jpeg"
        :on-change="onFilePick"
        :on-remove="() => { pickedFile = null }"
      >
        <el-button size="small">{{ $t('lesson.pickImage') }}</el-button>
      </el-upload>
      <el-button type="primary" size="small" :loading="uploading" :disabled="!pickedFile || disabled" @click="uploadAsset">
        {{ replaceMode ? $t('lesson.replaceAsset') : $t('lesson.uploadAsset') }}
      </el-button>
      <el-button v-if="replaceMode" size="small" @click="cancelReplace">{{ $t('lesson.cancel') }}</el-button>
      <span class="muted small">{{ $t('lesson.assetHint') }}</span>
    </div>

    <!-- Server-backed bundle asset list, grouped by layer -->
    <div class="asset-list">
      <div class="list-toolbar">
        <span class="list-title muted small">{{ $t('lesson.assetList') }}</span>
        <el-button type="text" size="mini" icon="el-icon-refresh" :loading="loadingList" @click="refreshAssets">{{ $t('lesson.refresh') }}</el-button>
      </div>
      <div v-if="!loadingList && !serverAssets.length" class="muted small">{{ $t('lesson.assetListEmpty') }}</div>
      <div v-for="layerName in layerOrder" :key="layerName">
        <template v-if="assetsByLayer[layerName] && assetsByLayer[layerName].length">
          <div class="layer-title muted small">{{ layerLabel(layerName) }}</div>
          <div v-for="a in assetsByLayer[layerName]" :key="a.profile + '/' + a.assetKey" class="asset-row">
            <img
              v-if="isPreviewable(a.url)"
              :src="a.url"
              class="thumb"
              :alt="a.assetKey"
              @click="openPreview(a)"
            />
            <div v-else class="thumb thumb-placeholder">{{ $t('lesson.noPreview') }}</div>
            <div class="asset-meta">
              <div class="kv"><span class="muted">{{ $t('lesson.assetKey') }}</span><span class="mono">{{ a.assetKey }}</span></div>
              <div class="kv"><span class="muted">{{ $t('lesson.layer') }}</span><span>{{ a.layer }} · {{ a.role }}<span v-if="a.critical"> · {{ $t('lesson.critical') }}</span></span></div>
              <div class="kv" v-if="a.dimensions"><span class="muted">{{ $t('lesson.assetPreview') }}</span><span class="mono">{{ a.dimensions.width }}×{{ a.dimensions.height }} {{ a.mediaType }} · {{ a.bytes }}B</span></div>
              <div class="kv"><span class="muted">sha256</span><span class="mono">{{ shortSha(a.sha256) }}</span></div>
            </div>
            <div class="asset-actions">
              <el-button size="mini" :disabled="disabled" @click="startReplace(a)">{{ $t('lesson.replaceAsset') }}</el-button>
              <el-popconfirm :title="$t('lesson.assetDeleteConfirm')" @confirm="onDelete(a)">
                <el-button slot="reference" type="danger" size="mini" icon="el-icon-delete" :disabled="disabled" />
              </el-popconfirm>
            </div>
          </div>
        </template>
      </div>
    </div>

    <!-- Full-size preview -->
    <el-dialog :visible.sync="previewVisible" :title="previewAsset ? previewAsset.assetKey : ''" width="640px" append-to-body>
      <img v-if="previewAsset && isPreviewable(previewAsset.url)" :src="previewAsset.url" class="preview-full" :alt="previewAsset ? previewAsset.assetKey : ''" />
    </el-dialog>
  </el-card>
</template>

<script>
import Api from '@/apis/api';
import { isUncertainNestError } from '@/apis/nestHttp';
import { reserveAssetReadEpoch } from '@/components/lesson/asset-read-epoch';

// Default role per layer (server also defaults these, but the UI prefills so the
// author can see/override). robotOverlay→pose, teachingObject→primarySubject.
const ROLE_BY_LAYER = { backgroundScene: 'poster', teachingObject: 'primarySubject', robotOverlay: 'pose' };
let assetMutationEpoch = 0;

function nextAssetMutationId() {
  assetMutationEpoch += 1;
  return `asset-mutation-${assetMutationEpoch}`;
}

export default {
  name: 'LessonAssetManager',
  props: {
    lessonId: { type: [String, Number], required: true },
    disabled: { type: Boolean, default: false },
    // Optional vocab/subject to prefill teachingObject.<subject> assetKey
    subjectHint: { type: String, default: '' },
    mutationSettler: { type: Function, required: true },
    refreshHandler: { type: Function, default: null },
    // Returns { blocked, stepKeys } for a delete candidate; the editor owns the
    // step list, so it answers whether anything still references the asset.
    deletionGuard: { type: Function, default: null },
  },
  data() {
    return {
      layer: 'backgroundScene',
      pose: 'teach',
      role: ROLE_BY_LAYER.backgroundScene,
      assetKey: 'backgroundScene.poster',
      critical: true,
      pickedFile: null,
      uploading: false,
      mutationPending: false,
      activeMutationId: null,
      // Server-backed source of truth (replaces the old session-local uploaded[]).
      serverAssets: [],
      loadingList: false,
      assetListRequestId: 0,
      replaceMode: false,
      previewVisible: false,
      previewAsset: null,
      layerOrder: ['backgroundScene', 'teachingObject', 'robotOverlay'],
    };
  },
  computed: {
    assetsByLayer() {
      const by = {};
      this.serverAssets.forEach((a) => {
        const k = a.layer || 'other';
        (by[k] || (by[k] = [])).push(a);
      });
      return by;
    },
  },
  mounted() {
    this.reload();
  },
  beforeDestroy() {
    this.invalidateAssetReads();
    this.detachActiveMutation();
  },
  methods: {
    // Backend returns the relative public_id when LESSON_ASSET_ORIGIN_BASE is unset;
    // only render a thumbnail for an absolute http(s) URL.
    isPreviewable(url) {
      return /^https?:\/\//.test(String(url || ''));
    },
    shortSha(sha) {
      const s = String(sha || '');
      return s.length > 16 ? s.slice(0, 16) + '…' : s;
    },
    layerLabel(layer) {
      const map = {
        backgroundScene: 'lesson.layerBackground',
        teachingObject: 'lesson.layerTeachingObject',
        robotOverlay: 'lesson.layerRobotOverlay',
      };
      return map[layer] ? this.$t(map[layer]) : layer;
    },
    trackAssetRead(readEpoch) {
      const epoch = Number(readEpoch);
      if (!Number.isFinite(epoch) || epoch < this.assetListRequestId) return false;
      this.assetListRequestId = epoch;
      this.loadingList = false;
      return true;
    },
    invalidateAssetReads() {
      const readEpoch = reserveAssetReadEpoch();
      this.trackAssetRead(readEpoch);
      return readEpoch;
    },
    applyServerAssets(assets, readEpoch) {
      if (!this.trackAssetRead(readEpoch)) return false;
      this.serverAssets = Array.isArray(assets) ? assets : [];
      this.$emit('assets-loaded', this.serverAssets, { readEpoch });
      return this.serverAssets;
    },
    reload(onSuccess, onError) {
      const requestId = reserveAssetReadEpoch();
      this.assetListRequestId = requestId;
      this.loadingList = true;
      this.$emit('asset-read-started', { readEpoch: requestId });
      Api.lesson.listAssets(
        this.lessonId,
        'espTft',
        (res) => {
          if (requestId !== this.assetListRequestId) return;
          const assets = this.applyServerAssets(res && res.assets, requestId);
          if (assets === false) return;
          if (typeof onSuccess === 'function') onSuccess(assets);
        },
        (msg) => {
          if (requestId !== this.assetListRequestId) return;
          this.loadingList = false;
          this.$message.error(msg);
          if (typeof onError === 'function') onError(msg);
        },
      );
    },
    refreshAssets() {
      if (this.refreshHandler && this.refreshHandler() === true) return;
      this.reload();
    },
    openPreview(a) {
      this.previewAsset = a;
      this.previewVisible = true;
    },
    // Prefill the upload form from a row, keeping layer/role stable so the key's
    // placement does not change on replace (upsert-by-assetKey).
    startReplace(a) {
      if (this.disabled || this.mutationPending) return;
      if (a && a.assetId) {
        this.$emit('impact-review-request', { intent: 'replace', asset: a });
        return;
      }
      this.confirmReplace(a);
    },
    confirmReplace(a) {
      if (!a || this.disabled || this.mutationPending) return;
      this.replaceMode = true;
      this.layer = a.layer;
      this.role = a.role;
      this.assetKey = a.assetKey;
      this.critical = !!a.critical;
      if (a.layer === 'robotOverlay') {
        const p = (a.assetKey || '').split('.')[1];
        if (p) this.pose = p;
      }
      this.pickedFile = null;
      if (this.$refs.uploader) this.$refs.uploader.clearFiles();
    },
    cancelReplace() {
      this.replaceMode = false;
      this.pickedFile = null;
      if (this.$refs.uploader) this.$refs.uploader.clearFiles();
    },
    onDelete(a) {
      // Deleting an asset a step still binds would leave a dangling src in the
      // manifest, so name the steps and make the author unbind them first.
      const impact = typeof this.deletionGuard === 'function' ? this.deletionGuard(a) : null;
      if (impact && impact.blocked) {
        this.$message.error(this.$t('lesson.assetDeleteInUse', {
          assetKey: impact.assetKey,
          steps: impact.stepKeys.join(', '),
        }));
        return;
      }
      const mutationId = this.beginMutation();
      if (!mutationId) return;
      const settleMutation = this.createMutationSettlement(mutationId);
      Api.lesson.deleteAsset(
        this.lessonId,
        a.assetKey,
        a.profile,
        () => {
          const locallyActive = this.finishMutation(mutationId);
          if (!settleMutation('success', { assetKey: a.assetKey, profile: a.profile || 'espTft' })) return;
          if (!locallyActive) return;
          this.$emit('asset-mutated', { type: 'delete', assetKey: a.assetKey, profile: a.profile || 'espTft' });
          this.$message.success(this.$t('lesson.assetDeleted'));
        },
        (msg, error) => {
          const locallyActive = this.finishMutation(mutationId);
          const outcome = isUncertainNestError(error) ? 'uncertain' : 'rejected';
          if (!settleMutation(outcome, { message: msg, error })) return;
          if (!locallyActive) return;
          this.handleMutationError(msg, error);
        },
      );
    },
    // Seed-convention default assetKey for the current layer/pose/subject.
    defaultAssetKey() {
      if (this.layer === 'backgroundScene') return 'backgroundScene.poster';
      if (this.layer === 'robotOverlay') return 'robotOverlay.' + this.pose;
      // teachingObject — prefill with the vocab word when known
      const sub = (this.subjectHint || '').trim();
      return 'teachingObject.' + (sub || 'subject');
    },
    onLayerChange() {
      this.role = ROLE_BY_LAYER[this.layer] || 'primarySubject';
      this.assetKey = this.defaultAssetKey();
    },
    onPoseChange() {
      this.assetKey = this.defaultAssetKey();
    },
    onFilePick(file) {
      this.pickedFile = file.raw || file;
    },
    beginMutation() {
      if (this.disabled || this.mutationPending) return null;
      const id = nextAssetMutationId();
      this.mutationPending = true;
      this.activeMutationId = id;
      this.$emit('mutation-state', { id, active: true });
      return id;
    },
    finishMutation(id) {
      if (!id || id !== this.activeMutationId) return false;
      this.mutationPending = false;
      this.activeMutationId = null;
      return true;
    },
    createMutationSettlement(id) {
      const settleWithParent = this.mutationSettler;
      let settled = false;
      return (outcome, details = {}) => {
        if (settled) return false;
        settled = true;
        settleWithParent({ id, outcome, ...details });
        return true;
      };
    },
    detachActiveMutation() {
      const id = this.activeMutationId;
      if (!id || !this.mutationPending) return false;
      this.mutationPending = false;
      this.activeMutationId = null;
      this.uploading = false;
      this.$emit('asset-mutation-detached', { id });
      return true;
    },
    handleMutationError(message, error) {
      if (isUncertainNestError(error)) {
        this.$emit('asset-mutation-uncertain', { message, error });
      }
      this.$message.error(message);
    },
    uploadAsset() {
      if (!this.pickedFile || this.disabled || this.mutationPending) return;
      const key = (this.assetKey || '').trim();
      if (!key) {
        this.$message.warning(this.$t('lesson.assetKeyRequired'));
        return;
      }
      const mutationId = this.beginMutation();
      if (!mutationId) return;
      const settleMutation = this.createMutationSettlement(mutationId);
      this.uploading = true;
      const layer = this.layer;
      const fields = {
        profile: 'espTft',
        layer,
        role: (this.role || '').trim() || ROLE_BY_LAYER[layer] || 'primarySubject',
        assetKey: key,
        critical: this.critical ? 'true' : 'false',
      };
      const mutationType = this.replaceMode ? 'replace' : 'upload';
      Api.lesson.uploadAsset(
        this.lessonId,
        this.pickedFile,
        fields,
        (asset) => {
          const locallyActive = this.finishMutation(mutationId);
          const a = (asset && asset.asset) ? asset.asset : (asset || {});
          if (!settleMutation('success', {
            assetKey: a.asset_key || a.assetKey || key,
            profile: a.profile || fields.profile,
          })) return;
          if (!locallyActive) return;
          this.uploading = false;
          this.pickedFile = null;
          this.replaceMode = false;
          if (this.$refs.uploader) this.$refs.uploader.clearFiles();
          this.$emit('asset-mutated', {
            type: mutationType,
            assetKey: a.asset_key || a.assetKey || key,
            profile: a.profile || fields.profile,
            layer: a.layer || layer,
          });
          this.$message.success(this.$t('lesson.uploadOk'));
          // Preserve the existing notification for any non-editor consumers.
          this.$emit('uploaded', {
            assetKey: a.asset_key || a.assetKey || key,
            layer: a.layer || layer,
          });
        },
        (msg, error) => {
          const locallyActive = this.finishMutation(mutationId);
          const outcome = isUncertainNestError(error) ? 'uncertain' : 'rejected';
          if (!settleMutation(outcome, { message: msg, error })) return;
          if (!locallyActive) return;
          this.uploading = false;
          // espTft sniff-rejects webp/gif and corrupt headers (415)
          this.handleMutationError(/415/.test(String(msg)) ? this.$t('lesson.uploadReject415') : msg, error);
        },
      );
    },
  },
};
</script>

<style lang="scss" scoped>
.content-area { margin-bottom: 16px; }
.card-header { font-weight: 600; }
.asset-form { display: flex; align-items: flex-end; gap: 14px; flex-wrap: wrap; }
.field { display: flex; flex-direction: column; gap: 4px; }
.field-label { font-size: 12px; color: #909399; }
.add-row { display: flex; align-items: center; gap: 10px; margin-top: 14px; flex-wrap: wrap; }
.asset-list { margin-top: 16px; }
.list-toolbar { display: flex; align-items: center; justify-content: space-between; }
.list-title { margin-bottom: 8px; }
.layer-title { margin: 12px 0 4px; font-weight: 600; }
.asset-row { display: flex; gap: 12px; padding: 8px 0; border-top: 1px solid #ebeef5; align-items: flex-start; }
.thumb { width: 64px; height: 64px; object-fit: cover; border-radius: 4px; border: 1px solid #ebeef5; cursor: pointer; }
.thumb-placeholder { display: flex; align-items: center; justify-content: center; text-align: center; font-size: 11px; color: #c0c4cc; cursor: default; }
.asset-meta { flex: 1; }
.asset-actions { display: flex; flex-direction: column; gap: 6px; align-items: flex-end; }
.preview-full { max-width: 100%; max-height: 60vh; display: block; margin: 0 auto; }
.kv { display: flex; gap: 10px; padding: 1px 0; }
.kv .muted { width: 90px; display: inline-block; }
.mono { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; word-break: break-all; }
.small { font-size: 12px; }
.muted { color: #909399; }
</style>
