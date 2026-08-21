import { existsSync, readFileSync } from 'node:fs';
import { join } from 'node:path';
import { describe, expect, it } from 'vitest';
import { LessonOperationsMetrics } from './lesson-operations-metrics';
import { MetricsService } from '../metrics/metrics.service';

/**
 * T6.2 repro probe (carried by lesson-prod/repros/t62.sh).
 *
 * Deliberately written so that at the pre-fix base commit every assertion fails
 * on OBSERVABLE BEHAVIOUR rather than on a missing file or a module that will
 * not import: the rules file is read defensively and each capability is
 * asserted by calling it. A RED phase that is just an import error only proves
 * the patch is absent, which is not what the T0.4 gate is for.
 */

const REPO_ROOT = join(__dirname, '..', '..');
const RULES_PATH = join(REPO_ROOT, 'observability', 'lesson-alerts.rules.yml');
const RULES = existsSync(RULES_PATH) ? readFileSync(RULES_PATH, 'utf8') : '';

/** Just the `expr:` blocks — comments and annotation prose are not queries. */
const RULE_EXPRESSIONS = ((): string => {
  const lines = RULES.split('\n');
  const collected: string[] = [];
  for (let i = 0; i < lines.length; i++) {
    const start = /^(\s*)expr:(.*)$/.exec(lines[i]);
    if (!start) continue;
    const indent = start[1].length;
    collected.push(start[2]);
    for (let j = i + 1; j < lines.length; j++) {
      const line = lines[j];
      if (line.trim() === '') continue;
      if (line.search(/\S/) <= indent) break;
      collected.push(line);
    }
  }
  return collected.join('\n');
})();

function metricNamesIn(rulesYaml: string): string[] {
  const names = new Set<string>();
  for (const match of rulesYaml.matchAll(/(?<!")\b(lesson_[a-z0-9_]+)\b(?!")/g)) {
    names.add(match[1]);
  }
  return [...names].sort();
}

function populatedMetrics(): MetricsService {
  const metrics = new MetricsService();
  const ops = new LessonOperationsMetrics(metrics) as LessonOperationsMetrics & Record<string, any>;
  if (typeof ops.onModuleInit === 'function') ops.onModuleInit();
  ops.recordPreload('FAILED', false);
  if (typeof ops.recordLessonError === 'function') ops.recordLessonError('STEP_TIMEOUT');
  if (typeof ops.recordIngestLag === 'function') ops.recordIngestLag(new Date(Date.now() - 30_000));
  if (typeof ops.recordWatchdogScan === 'function') {
    ops.recordWatchdogScan('ok', 2);
    ops.recordWatchdogScan('failed');
  }
  ops.recordRuntimeTelemetry({ renderDegraded: true, retryCount: 1, motionDispatch: 'failed', psramFreeBytes: 1, sramFreeBytes: 1 });
  return metrics;
}

function exposition(): string {
  const metrics = populatedMetrics() as MetricsService & Record<string, any>;
  // The defect: there was no Prometheus exposition at all, so every PromQL
  // query in the doc was unrunnable. Assert the capability, don't crash on it.
  expect(
    typeof metrics.toPrometheusText,
    'MetricsService cannot render Prometheus text exposition — no PromQL query in lesson-lifecycle-metrics-promql.md can run',
  ).toBe('function');
  return metrics.toPrometheusText();
}

describe('T6.2 repro — lesson observability is alertable', () => {
  it('has alert rules for the lesson lifecycle at all', () => {
    expect(RULES.length, `no alert rules at ${RULES_PATH}`).toBeGreaterThan(0);
    expect(RULE_EXPRESSIONS).toContain('lesson_');
  });

  it('exposes the lesson counters in a scrapeable format', () => {
    const text = exposition();
    expect(text).toContain('# TYPE lesson_events_ingested_total counter');
    // Real cumulative buckets with `le` — without them histogram_quantile()
    // over the retry-count and ingest-lag histograms cannot evaluate.
    expect(text).toMatch(/lesson_[a-z_]+_bucket\{le="\+Inf"\}/);
  });

  it('measures every lifecycle stage even before it has fired', () => {
    const text = exposition();
    // An un-fired stage produced NO series, so a funnel gap and missing
    // instrumentation rendered identically.
    for (const stage of ['assignment_created', 'lesson_started', 'lesson_completed', 'lesson_failed']) {
      expect(text, `no pre-registered series for event_type=${stage}`).toContain(`event_type="${stage}"`);
    }
  });

  it('measures the lesson error code', () => {
    // `code` was persisted into progress_events.payload and never counted, so
    // nothing distinguished a preload failure from a step timeout.
    expect(exposition()).toMatch(/lesson_errors_total\{code="STEP_TIMEOUT"\} 1/);
  });

  it('measures ingest lag and the step-timeout watchdog', () => {
    const text = exposition();
    expect(text, 'no ingest-lag histogram').toMatch(/lesson_event_ingest_lag_ms_bucket/);
    expect(text, 'the no-progress watchdog emits no metric').toContain('lesson_stalled_assignments');
    expect(text, 'a dead watchdog is indistinguishable from a quiet one').toMatch(/lesson_progress_watchdog_scans_total\{result="failed"\}/);
  });

  it('names only metrics the backend can actually emit', () => {
    const emitted = new Set([...exposition().matchAll(/^([a-z0-9_]+)(?:\{|\s)/gm)].map((m) => m[1]));
    const referenced = metricNamesIn(RULE_EXPRESSIONS);
    expect(referenced.length).toBeGreaterThan(0);
    const missing = referenced.filter((name) => !emitted.has(name));
    expect(missing, `alert rules reference metrics nothing emits: ${missing.join(', ')}`).toEqual([]);
  });

  it('keeps every rule expression on the bounded-label contract', () => {
    for (const label of ['device_id', 'session_id', 'assignment_id', 'child_id', 'user_id', 'household_id']) {
      expect(RULE_EXPRESSIONS.includes(label), `alert rules must not group or filter by ${label}`).toBe(false);
    }
  });

  it('keeps latency thresholds inside the histogram bucket ceiling', () => {
    const thresholds = [...RULE_EXPRESSIONS.matchAll(/histogram_quantile\([^)]*\)[^>]*>\s*(\d+)/g)].map((m) => Number(m[1]));
    expect(thresholds.length).toBeGreaterThan(0);
    for (const threshold of thresholds) {
      expect(threshold, 'threshold above the 16384 bucket ceiling can never fire').toBeLessThan(16384);
    }
  });
});
