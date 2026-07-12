<template>
  <div class="visual-page">
    <HeaderBar />
    <main>
      <div class="hero">
        <div><p class="eyebrow">LESSON STUDIO</p><h1>{{ $t('visual.libraryTitle') }}</h1><p>{{ $t('visual.libraryHint') }}</p></div>
        <el-button data-testid="visual-library-refresh" icon="el-icon-refresh" :loading="loading" @click="fetchAssets">{{ $t('course.refresh') }}</el-button>
      </div>
      <el-card shadow="never" class="library-card">
        <div class="filters">
          <el-input data-testid="visual-library-search" v-model="filters.keyword" clearable prefix-icon="el-icon-search" :placeholder="$t('visual.search')" />
          <el-select data-testid="visual-library-category" v-model="filters.category" clearable :placeholder="$t('visual.category')"><el-option v-for="item in categories" :key="item" :label="item" :value="item" /></el-select>
          <el-select data-testid="visual-library-profile" v-model="filters.profile" clearable :placeholder="$t('visual.profile')"><el-option v-for="item in profiles" :key="item" :label="item" :value="item" /></el-select>
        </div>
        <el-table data-testid="visual-library-table" v-loading="loading" :data="assets" stripe>
          <el-table-column prop="assetKey" :label="$t('visual.assetKey')" min-width="200" />
          <el-table-column prop="category" :label="$t('visual.category')" width="160"><template slot-scope="s"><el-tag effect="plain">{{ s.row.category }}</el-tag></template></el-table-column>
          <el-table-column prop="pinnedVersion" :label="$t('visual.pinnedVersion')" width="110" align="center" />
          <el-table-column :label="$t('visual.profiles')" min-width="160"><template slot-scope="s"><el-tag v-for="p in uniqueProfiles(s.row)" :key="p" size="mini" class="profile-tag">{{ p }}</el-tag></template></el-table-column>
          <el-table-column prop="usageCount" :label="$t('visual.usageCount')" width="110" align="right" />
          <el-table-column :label="$t('course.colActions')" width="120"><template slot-scope="s"><el-button :data-testid="`visual-library-inspect-${s.row.assetKey}`" type="text" @click="openDetail(s.row)">{{ $t('visual.inspect') }}</el-button></template></el-table-column>
        </el-table>
      </el-card>
    </main>
  </div>
</template>
<script>
import Api from '@/apis/api';
import HeaderBar from '@/components/HeaderBar.vue';
import { filterVisualAssets, groupVisualAssets, VISUAL_CATEGORIES, VISUAL_PROFILES } from '@/utils/lessonVisualLibraryState.mjs';
export default {
  name: 'LessonVisualLibrary', components: { HeaderBar },
  data: () => ({ loading: false, rows: [], filters: { keyword: '', category: '', profile: '' }, categories: VISUAL_CATEGORIES, profiles: VISUAL_PROFILES }),
  computed: { assets() { return groupVisualAssets(filterVisualAssets(this.rows, this.filters)); } },
  mounted() { this.fetchAssets(); },
  methods: {
    fetchAssets() { this.loading = true; Api.lesson.listVisualAssets({}, (rows) => { this.rows = rows; this.loading = false; }, (message) => { this.loading = false; this.$message.error(message || this.$t('visual.loadFail')); }); },
    uniqueProfiles(row) { return [...new Set(row.versions.map((v) => v.profile))]; },
    openDetail(row) { this.$router.push({ name: 'LessonVisualAssetDetail', params: { assetKey: row.assetKey } }); },
  },
};
</script>
<style scoped>
.visual-page{min-height:100vh;background:radial-gradient(circle at 85% 8%,#dff3ec 0,transparent 28%),#f4f1e9}.visual-page main{padding:26px 32px 50px;max-width:1500px;margin:auto}.hero{display:flex;justify-content:space-between;align-items:flex-end;padding:20px 4px 26px}.hero h1{font-family:Georgia,serif;font-size:36px;margin:4px 0;color:#17372f}.hero p{color:#60736c;margin:0}.eyebrow{font-size:11px!important;font-weight:700;letter-spacing:2px;color:#b4652d!important}.library-card{border:0;border-radius:16px}.filters{display:grid;grid-template-columns:minmax(260px,1fr) 220px 180px;gap:12px;margin-bottom:18px}.profile-tag{margin-right:5px}@media(max-width:760px){.visual-page main{padding:16px}.hero{align-items:flex-start;gap:15px}.filters{grid-template-columns:1fr}.hero h1{font-size:28px}}
</style>
