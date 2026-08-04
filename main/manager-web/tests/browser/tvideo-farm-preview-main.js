(async () => {
await import('./lesson-builder-main');

const waitForFixture = async () => {
  for (let index = 0; index < 200 && !window.__LESSON_BUILDER_READY__; index += 1) {
    await new Promise((resolve) => setTimeout(resolve, 25));
  }
  if (!window.__LESSON_BUILDER_READY__) throw new Error('TVideo farm preview fixture did not mount');
};

await waitForFixture();
await window.__SET_TEST_LOCALE__('vi');
const { editor, sharedAssets } = window.__LESSON_BUILDER_TEST__;
await editor.$router.push({ path: '/', query: { lessonId: 'journey-v4' } });
for (let index = 0; index < 100 && (!editor.tvideoJourneyDraft || editor.tvideoJourneyLoading); index += 1) {
  await new Promise((resolve) => setTimeout(resolve, 25));
}
if (!editor.tvideoJourneyDraft) throw new Error('TVideo farm journey draft did not load');

const pin = (target, assetVersionId) => {
  const asset = sharedAssets.find((row) => row.versionId === assetVersionId);
  Object.assign(target, { assetVersionId, sha256: asset.sha256 });
};
pin(editor.tvideoJourneyDraft.assets.background, '10000000-0000-4000-8000-000000000001');
pin(editor.tvideoJourneyDraft.steps[0].teachingObject, '30000000-0000-4000-8000-000000000001');
pin(editor.tvideoJourneyDraft.steps[1].teachingObject, '30000000-0000-4000-8000-000000000002');
const robotVersionIds = [
  '20000000-0000-4000-8000-000000000001',
  '20000000-0000-4000-8000-000000000002',
  '20000000-0000-4000-8000-000000000003',
  '20000000-0000-4000-8000-000000000004',
];
editor.tvideoJourneyDraft.assets.robotClips.forEach((clip, index) => {
  pin(clip, robotVersionIds[index]);
});
editor.tvideoJourneyDraft = JSON.parse(JSON.stringify(editor.tvideoJourneyDraft));
await editor.$nextTick();

const banner = document.createElement('div');
banner.setAttribute('data-testid', 'tvideo-farm-preview-banner');
banner.textContent = 'PREVIEW-ONLY · Dữ liệu fixture cục bộ · Không thay đổi production · Rollout renderer v4 vẫn tắt';
Object.assign(banner.style, {
  position: 'sticky',
  top: '0',
  zIndex: '9999',
  padding: '10px 16px',
  color: '#fff8df',
  background: '#784318',
  font: '600 14px/1.4 Georgia, serif',
  letterSpacing: '0.02em',
  textAlign: 'center',
});
document.body.prepend(banner);
document.title = 'TBOT · TVideo Farm Preview Only';
})();
