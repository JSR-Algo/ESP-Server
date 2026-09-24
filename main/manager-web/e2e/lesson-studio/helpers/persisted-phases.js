const { expect } = require('@playwright/test');
const { waitForObservedRequests } = require('./real-service-evidence');

async function visitPersistedPhases(page, manifest, onPhase) {
  const stage = page.getByTestId('esp-tft-stage');
  const controls = page.getByLabel('Persisted cinematic phases');
  const steps = page.getByLabel('Lesson steps', { exact: true }).locator('.step-nav__item');
  await expect(steps).toHaveCount(manifest.steps.length);
  const visited = new Set();
  for (const [stepIndex, step] of manifest.steps.entries()) {
    const activityId = String(step.activityId || step.id);
    const phases = manifest.cinematicPhases.filter(phase => phase.templateId === 'layeredCinematic'
      && phase.activityIds.includes(activityId));
    expect(phases.length, `persisted phases for ${activityId}`).toBeGreaterThan(0);
    await waitForObservedRequests(page);
    await steps.nth(stepIndex).click();
    await expect(steps.nth(stepIndex)).toHaveClass(/\bactive\b/);
    await expect(page.getByTestId('v5-preview-context')).toContainText(step.prompt);
    await expect(controls.getByRole('button')).toHaveText(phases.map(phase => phase.phaseId));
    for (const phase of phases) {
      const button = controls.getByRole('button', { name: phase.phaseId, exact: true });
      await waitForObservedRequests(page);
      await button.click();
      await expect(button).toHaveAttribute('aria-pressed', 'true');
      for (const [slot, layerId] of [['backgroundScene', 'background'], ['teachingObject', 'teachingObject'], ['robotOverlay', 'robotOverlay']]) {
        const layer = phase.layers.find(item => item.slot === slot);
        const rendered = stage.locator(`.layer-${layerId}`);
        if (!layer) { await expect(rendered).toHaveCount(0); continue; }
        await expect(rendered).toHaveAttribute('data-source-sha256', layer.sha256);
        await expect(rendered).toHaveAttribute('data-asset-version-id', layer.assetVersionId);
        const asset = manifest.assets.find(item => item.assetKey === layer.assetKey
          && item.version === layer.version && item.sha256 === layer.sha256);
        expect(asset, `${activityId}/${phase.phaseId}/${slot} persisted source`).toBeTruthy();
        if (layer.metadata.mediaType.startsWith('video/')) {
          // The MJPEG adapter renders a canvas; source identity belongs to the layer.
          await expect(rendered).toHaveAttribute('data-source-url', asset.url);
        } else {
          await expect(rendered).toHaveAttribute('src', asset.url);
        }
      }
      visited.add(phase.phaseId);
      await onPhase({ stage, phase, activityId, stepIndex });
    }
  }
  expect([...visited].sort(), 'complete lesson-wide phase coverage').toEqual(['flyIn', 'walk', 'teach', 'listen', 'thinking', 'celebrate', 'exit'].sort());
}

module.exports = { visitPersistedPhases };
