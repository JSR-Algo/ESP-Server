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
        {{ $t('lesson.uploadAsset') }}
      </el-button>
      <span class="muted small">{{ $t('lesson.assetHint') }}</span>
    </div>

    <!-- Session-local list of what was uploaded -->
    <div v-if="uploaded.length" class="asset-list">
      <div class="list-title muted small">{{ $t('lesson.assetList') }}</div>
      <div v-for="(a, i) in uploaded" :key="i" class="asset-row">
        <img v-if="a.url" :src="a.url" class="thumb" :alt="a.assetKey" />
        <div class="asset-meta">
          <div class="kv"><span class="muted">{{ $t('lesson.assetKey') }}</span><span class="mono">{{ a.assetKey }}</span></div>
          <div class="kv"><span class="muted">{{ $t('lesson.layer') }}</span><span>{{ a.layer }}<span v-if="a.critical"> · {{ $t('lesson.critical') }}</span></span></div>
          <div class="kv" v-if="a.width"><span class="muted">{{ $t('lesson.assetPreview') }}</span><span class="mono">{{ a.width }}×{{ a.height }} {{ a.mediaType }}</span></div>
          <div class="kv"><span class="muted">sha256</span><span class="mono">{{ a.sha256 }}</span></div>
        </div>
      </div>
      <div class="muted small">{{ $t('lesson.assetSessionOnly') }}</div>
    </div>
  </el-card>
</template>

<script>
import Api from '@/apis/api';

// Default role per layer (server also defaults these, but the UI prefills so the
// author can see/override). robotOverlay→pose, teachingObject→primarySubject.
const ROLE_BY_LAYER = { backgroundScene: 'poster', teachingObject: 'primarySubject', robotOverlay: 'pose' };

export default {
  name: 'LessonAssetManager',
  props: {
    lessonId: { type: [String, Number], required: true },
    disabled: { type: Boolean, default: false },
    // Optional vocab/subject to prefill teachingObject.<subject> assetKey
    subjectHint: { type: String, default: '' },
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
      uploaded: [],
    };
  },
  methods: {
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
    uploadAsset() {
      if (!this.pickedFile) return;
      const key = (this.assetKey || '').trim();
      if (!key) {
        this.$message.warning(this.$t('lesson.assetKeyRequired'));
        return;
      }
      this.uploading = true;
      const layer = this.layer;
      const fields = {
        profile: 'espTft',
        layer,
        role: (this.role || '').trim() || ROLE_BY_LAYER[layer] || 'primarySubject',
        assetKey: key,
        critical: this.critical ? 'true' : 'false',
      };
      Api.lesson.uploadAsset(
        this.lessonId,
        this.pickedFile,
        fields,
        (asset) => {
          this.uploading = false;
          const a = (asset && asset.asset) ? asset.asset : (asset || {});
          this.uploaded.unshift({
            assetKey: a.asset_key || a.assetKey || key,
            layer: a.layer || layer,
            role: a.role || fields.role,
            critical: this.critical,
            url: a.url || a.src || '',
            sha256: a.sha256 || '',
            bytes: a.bytes || '',
            width: a.width || '',
            height: a.height || '',
            mediaType: a.media_type || a.mediaType || '',
          });
          this.pickedFile = null;
          if (this.$refs.uploader) this.$refs.uploader.clearFiles();
          this.$message.success(this.$t('lesson.uploadOk'));
          this.$emit('uploaded', this.uploaded[0]);
        },
        (msg) => {
          this.uploading = false;
          // espTft sniff-rejects webp/gif and corrupt headers (415)
          if (/415/.test(String(msg))) this.$message.error(this.$t('lesson.uploadReject415'));
          else this.$message.error(msg);
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
.list-title { margin-bottom: 8px; }
.asset-row { display: flex; gap: 12px; padding: 8px 0; border-top: 1px solid #ebeef5; }
.thumb { width: 64px; height: 64px; object-fit: cover; border-radius: 4px; border: 1px solid #ebeef5; }
.asset-meta { flex: 1; }
.kv { display: flex; gap: 10px; padding: 1px 0; }
.kv .muted { width: 90px; display: inline-block; }
.mono { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; word-break: break-all; }
.small { font-size: 12px; }
.muted { color: #909399; }
</style>
