import { DEFAULT_BACKEND_URL } from '../../config/backendUrl.js';
import { getChartTheme } from '../settings/appearanceSettings.js';
import {
  MAX_RESULT_PANELS,
  getCurrentLayoutSettings,
  getPanelCharts,
  setPanelChart,
  setPanelCharts,
  setPanelMode,
} from '../settings/layoutSettings.js';
import { renderResultDiagnostics, renderSolveStatsSummary } from '../simulation/results.js';
import {
  PANEL_CHART_TYPES,
  buildDirectivityPayload,
  buildLineChartsPayload,
  directivityPlanesForChartKey,
  hasDirectivityPatterns,
  isDirectivityChartKey,
  requestDirectivityMap,
  requestLineCharts,
} from '../simulation/chartRequests.js';
import {
  disposeBalloonPanel,
  isBalloonChartKey,
  renderBalloonPanel,
} from './balloonPanel.js';
import { allJobs, formatJobListLabel } from '../simulation/jobTracker.js';

const MIN_SPLIT_FRACTION = 0.15;
const MAX_SPLIT_FRACTION = 0.7;
const AUTO_MIN_WIDTH = 900;
const AUTO_MIN_ASPECT = 2.2;
const AUTO_HYSTERESIS_PX = 40;
const IMAGE_CACHE_CAPACITY = 24;

export function clampResultsDockFraction(value) {
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) return 0.38;
  return Math.max(MIN_SPLIT_FRACTION, Math.min(MAX_SPLIT_FRACTION, numeric));
}

export function resolveResultsDockPanelCount({
  width,
  height,
  previousCount = 1,
  mode = 'auto',
} = {}) {
  if (mode !== 'auto') {
    const forced = Number(mode);
    if (Number.isInteger(forced) && forced >= 1) {
      return Math.min(forced, MAX_RESULT_PANELS);
    }
  }

  const dockWidth = Number(width);
  const dockHeight = Number(height);
  if (!Number.isFinite(dockWidth) || !Number.isFinite(dockHeight) || dockHeight <= 0) {
    return 1;
  }

  const twoPanelThreshold = Math.max(AUTO_MIN_WIDTH, dockHeight * AUTO_MIN_ASPECT);
  const threshold =
    previousCount === 2 ? twoPanelThreshold - AUTO_HYSTERESIS_PX : twoPanelThreshold;
  return dockWidth >= threshold ? 2 : 1;
}

export const resolveAutoPanelCount = resolveResultsDockPanelCount;

export function resolveResultsDockColumns({
  panelCount,
  arrangement = 'auto',
  width,
  height,
} = {}) {
  const count = Math.max(1, Math.min(MAX_RESULT_PANELS, Number(panelCount) || 1));
  if (count === 1) return 1;
  if (arrangement === 'rows') return 1;
  if (arrangement === 'columns') return count;
  if (arrangement === 'grid') return Math.ceil(count / 2);

  // 'auto': two panels reuse the side-by-side room rule; more panels use a
  // near-square grid, collapsing to one column on very narrow docks.
  const dockWidth = Number(width);
  const dockHeight = Number(height);
  const haveDims = Number.isFinite(dockWidth) && Number.isFinite(dockHeight);
  if (count === 2) {
    if (!haveDims) return 2;
    return dockWidth >= Math.max(AUTO_MIN_WIDTH, dockHeight * AUTO_MIN_ASPECT) ? 2 : 1;
  }
  if (haveDims && dockWidth < 600) return 1;
  return Math.ceil(Math.sqrt(count));
}

export function buildResultsDockCacheKey({
  jobId,
  chartKey,
  smoothing,
  refLevel,
  theme,
  compareJobId = null,
} = {}) {
  return JSON.stringify({
    jobId: jobId ?? null,
    chartKey: chartKey ?? null,
    smoothing: smoothing ?? 'none',
    refLevel: Number.isFinite(Number(refLevel)) ? Number(refLevel) : null,
    theme: theme ?? null,
    compareJobId: compareJobId || null,
  });
}

export function createPanelRequestGuard(size = 2) {
  const tokens = Array.from({ length: size }, () => 0);
  return {
    begin(index) {
      tokens[index] = (tokens[index] || 0) + 1;
      return tokens[index];
    },
    isCurrent(index, token) {
      return tokens[index] === token;
    },
    invalidate(index) {
      tokens[index] = (tokens[index] || 0) + 1;
    },
    invalidateAll() {
      for (let index = 0; index < tokens.length; index += 1) {
        tokens[index] += 1;
      }
    },
  };
}

export function createLruImageCache(capacity = IMAGE_CACHE_CAPACITY) {
  return new (class LruImageCache extends Map {
    get(key) {
      if (!super.has(key)) return undefined;
      const value = super.get(key);
      super.delete(key);
      super.set(key, value);
      return value;
    }

    set(key, value) {
      if (super.has(key)) super.delete(key);
      super.set(key, value);
      while (this.size > capacity) {
        super.delete(this.keys().next().value);
      }
      return this;
    }
  })();
}

export function buildResultsDockRequest({
  results,
  chartKey,
  smoothing = 'none',
  referenceLevel = -6,
  theme,
  reference = null,
} = {}) {
  if (chartKey === 'summary') {
    return { kind: 'summary', chartKey };
  }

  if (isBalloonChartKey(chartKey)) {
    return { kind: 'balloon', chartKey };
  }

  if (isDirectivityChartKey(chartKey)) {
    return {
      kind: 'directivity',
      chartKey,
      payload: buildDirectivityPayload(results, {
        referenceLevel,
        theme,
        reference,
        planes: directivityPlanesForChartKey(chartKey),
      }),
    };
  }

  return {
    kind: 'line',
    chartKey,
    payload: buildLineChartsPayload(results, {
      smoothing,
      theme,
      reference,
    }),
  };
}

function setHidden(element, hidden) {
  if (!element) return;
  element.classList.toggle('is-hidden', hidden);
}

function clearElement(element) {
  element.textContent = '';
}

function renderPanelStatus(state, message, kind = '') {
  if (!state.body) return;
  clearElement(state.body);
  const status = document.createElement('div');
  status.className = `results-panel-status${kind ? ` results-panel-status--${kind}` : ''}`;
  status.textContent = message;
  state.body.appendChild(status);
}

function renderPanelImage(state, chartKey, image) {
  if (!state.body) return;
  clearElement(state.body);
  if (!image) {
    renderPanelStatus(state, 'No data available');
    return;
  }

  const chart = PANEL_CHART_TYPES.find((item) => item.key === chartKey);
  const img = document.createElement('img');
  img.className = 'results-panel-img';
  img.src = image;
  img.alt = chart?.label || 'Simulation result chart';
  state.body.appendChild(img);
}

function renderPanelSummary(state, results, job) {
  if (!state.body) return;
  const markup = [renderSolveStatsSummary(results, job), renderResultDiagnostics(results)]
    .filter(Boolean)
    .join('');
  if (!markup.trim()) {
    renderPanelStatus(state, 'No summary available');
    return;
  }
  clearElement(state.body);
  const wrapper = document.createElement('div');
  wrapper.className = 'results-panel-summary';
  wrapper.innerHTML = markup;
  state.body.appendChild(wrapper);
}

function completedComparisonJobs(panel, displayedJobId) {
  if (!panel?.jobs) return [];
  return allJobs(panel).filter(
    (job) => job.status === 'complete' && String(job.id) !== String(displayedJobId || '')
  );
}

function describeRequestError(response) {
  if (response.kind === 'matplotlib-missing') {
    return 'Matplotlib is required for chart rendering';
  }
  if (response.kind === 'network') {
    return 'Chart rendering failed: backend is unreachable.';
  }
  const detail = String(response.detail || '').trim();
  return `Chart rendering failed: ${detail || `HTTP ${response.status}`}`;
}

export function setupResultsDock(app) {
  const element = document.getElementById('results-dock');
  const resizer = document.getElementById('viewport-results-resizer');
  if (!element || !resizer) {
    app.resultsDock = null;
    return null;
  }

  const requestGuard = createPanelRequestGuard(MAX_RESULT_PANELS);
  const imageCache = createLruImageCache();
  const states = Array.from({ length: MAX_RESULT_PANELS }, () => ({
    chartKey: null,
    compareJobId: null,
    compareOptionsSignature: null,
    root: null,
    body: null,
    compareSelect: null,
  }));
  const mobileQuery =
    typeof window !== 'undefined' && typeof window.matchMedia === 'function'
      ? window.matchMedia('(max-width: 768px)')
      : null;

  const dock = {
    element,
    resizer,
    imageCache,
    panelCount: 0,
    previousAutoPanelCount: 1,
    fraction: 0.38,
    visible: false,
    latestResults: null,
    latestJob: null,
    _lazyLoadPromise: null,
    _lazyLoadToken: 0,

    applyLayout() {
      const settings = getCurrentLayoutSettings();
      const shouldShow = settings.resultsLayout === 'split' && !mobileQuery?.matches;
      this.visible = shouldShow;
      setHidden(this.element, !shouldShow);
      setHidden(this.resizer, !shouldShow);
      this.element.setAttribute('aria-hidden', String(!shouldShow));

      this.fraction = clampResultsDockFraction(settings.splitFraction);
      this.element.style.setProperty('--results-dock-height', `${this.fraction * 100}%`);

      if (!shouldShow) {
        requestGuard.invalidateAll();
        app.onResize();
        return;
      }

      this._updatePanelCount(settings.panelMode);
      app.onResize();
      // The dock may not have final dimensions on the first layout pass
      // (e.g. when it was just unhidden), so recompute the grid once more
      // after layout settles; the ResizeObserver covers later changes.
      setTimeout(() => {
        if (this.visible) this._updateGridLayout();
      }, 0);
      if (this.latestResults) {
        void this.refresh();
      } else {
        void this._loadMostRecentCompletedJob();
      }
    },

    onResults(results, job = null) {
      if (!results) return;
      this._lazyLoadToken += 1;
      const panel = app.simulationPanel;
      const resolvedJob =
        job ||
        (panel?.activeJobId ? panel.jobs?.get(panel.activeJobId) || null : null) ||
        (panel?.currentJobId ? panel.jobs?.get(panel.currentJobId) || null : null);
      this.latestResults = results;
      this.latestJob = resolvedJob;
      this._updateCompareOptions();
      if (this.visible) void this.refresh({ force: true });
    },

    onJobsUpdated() {
      const comparisonChanged = this._updateCompareOptions();
      if (this.visible && !this.latestResults) {
        void this._loadMostRecentCompletedJob();
      } else if (this.visible && comparisonChanged) {
        void this.refresh({ force: true });
      }
    },

    async refresh({ force = false } = {}) {
      if (!this.visible) return [];
      this._updateCompareOptions();
      if (!this.latestResults) {
        for (let index = 0; index < this.panelCount; index += 1) {
          requestGuard.invalidate(index);
          renderPanelStatus(states[index], 'Run a simulation to see results here');
        }
        return [];
      }

      return Promise.all(
        Array.from({ length: this.panelCount }, (_, index) => this._refreshPanel(index, { force }))
      );
    },

    setFraction(value) {
      this.fraction = clampResultsDockFraction(value);
      this.element.style.setProperty('--results-dock-height', `${this.fraction * 100}%`);
      app.onResize();
      return this.fraction;
    },

    markStaleAndRefresh() {
      if (!this.visible) return Promise.resolve([]);
      return this.refresh({ force: true });
    },

    _displayedJobId() {
      return this.latestJob?.id || app.simulationPanel?.activeJobId || null;
    },

    _updatePanelCount(mode = getCurrentLayoutSettings().panelMode) {
      const rect = this.element.getBoundingClientRect();
      const nextCount = resolveResultsDockPanelCount({
        width: rect.width || this.element.clientWidth,
        height: rect.height || this.element.clientHeight,
        previousCount: this.previousAutoPanelCount,
        mode,
      });
      if (mode === 'auto') this.previousAutoPanelCount = nextCount;
      if (nextCount === this.panelCount) {
        this._syncPanelChartSelections();
        this._updateGridLayout();
        return false;
      }

      this.panelCount = nextCount;
      this._buildPanels();
      this._updateGridLayout();
      return true;
    },

    _updateGridLayout() {
      const rect = this.element.getBoundingClientRect();
      const columns = resolveResultsDockColumns({
        panelCount: this.panelCount,
        arrangement: getCurrentLayoutSettings().panelArrangement,
        width: rect.width || this.element.clientWidth,
        height: rect.height || this.element.clientHeight,
      });
      this.element.style.setProperty('--results-dock-cols', String(columns));
    },

    _setPanelCount(count) {
      const next = Math.max(1, Math.min(MAX_RESULT_PANELS, count));
      setPanelMode(String(next));
      if (this._updatePanelCount(String(next))) {
        void this.refresh();
      }
    },

    _closePanel(index) {
      const charts = getPanelCharts();
      charts.splice(index, 1);
      setPanelCharts(charts);
      this._setPanelCount(this.panelCount - 1);
    },

    _syncPanelChartSelections() {
      const panelCharts = getPanelCharts();
      for (let index = 0; index < this.panelCount; index += 1) {
        states[index].chartKey = panelCharts[index];
        if (states[index].chartSelect) {
          states[index].chartSelect.value = panelCharts[index];
        }
        this._syncPanelControlState(states[index]);
      }
    },

    _syncPanelControlState(state) {
      if (!state?.compareSelect) return;
      const isSummary = state.chartKey === 'summary';
      const isBalloon = isBalloonChartKey(state.chartKey);
      state.compareSelect.disabled = isSummary || isBalloon;
      if (isSummary) {
        state.compareSelect.title = 'Comparison is not available for the summary view';
      } else if (isBalloon) {
        state.compareSelect.title = 'Comparison is not available for the 3D balloon view';
      }
    },

    _buildPanels() {
      requestGuard.invalidateAll();
      for (const state of states) {
        disposeBalloonPanel(state);
      }
      clearElement(this.element);
      const panelCharts = getPanelCharts();

      for (let index = 0; index < this.panelCount; index += 1) {
        const state = states[index];
        state.chartKey = panelCharts[index];

        const root = document.createElement('section');
        root.className = 'results-panel';

        const header = document.createElement('div');
        header.className = 'results-panel-header';

        const chartSelect = document.createElement('select');
        chartSelect.setAttribute('aria-label', `Panel ${index + 1} chart type`);
        for (const chart of PANEL_CHART_TYPES) {
          const option = document.createElement('option');
          option.value = chart.key;
          option.textContent = chart.label;
          chartSelect.appendChild(option);
        }
        chartSelect.value = state.chartKey;
        chartSelect.addEventListener('change', (event) => {
          state.chartKey = event.target.value;
          setPanelChart(index, state.chartKey);
          this._syncPanelControlState(state);
          void this._refreshPanel(index);
        });

        const compareSelect = document.createElement('select');
        compareSelect.setAttribute('aria-label', `Panel ${index + 1} comparison`);
        compareSelect.addEventListener('change', (event) => {
          state.compareJobId = event.target.value || null;
          compareSelect.title = event.target.selectedOptions[0]?.textContent || '';
          void this._refreshPanel(index);
        });

        const openButton = document.createElement('button');
        openButton.type = 'button';
        openButton.className = 'results-panel-open';
        openButton.textContent = '⤢';
        openButton.title = 'Open full results';
        openButton.setAttribute('aria-label', 'Open full results');
        openButton.addEventListener('click', () => {
          app.simulationPanel?.openViewResults(this._displayedJobId());
        });

        const body = document.createElement('div');
        body.className = 'results-panel-body';

        header.appendChild(chartSelect);
        header.appendChild(compareSelect);
        header.appendChild(openButton);
        if (this.panelCount > 1) {
          const closeButton = document.createElement('button');
          closeButton.type = 'button';
          closeButton.className = 'results-panel-close';
          closeButton.textContent = '×';
          closeButton.title = 'Close this panel';
          closeButton.setAttribute('aria-label', `Close panel ${index + 1}`);
          closeButton.addEventListener('click', () => this._closePanel(index));
          header.appendChild(closeButton);
        }
        if (index === this.panelCount - 1 && this.panelCount < MAX_RESULT_PANELS) {
          const addButton = document.createElement('button');
          addButton.type = 'button';
          addButton.className = 'results-panel-add';
          addButton.textContent = '+';
          addButton.title = 'Add another results panel';
          addButton.setAttribute('aria-label', 'Add another results panel');
          addButton.addEventListener('click', () => this._setPanelCount(this.panelCount + 1));
          header.appendChild(addButton);
        }
        root.appendChild(header);
        root.appendChild(body);
        this.element.appendChild(root);

        Object.assign(state, {
          root,
          body,
          chartSelect,
          compareSelect,
          compareOptionsSignature: null,
        });
        this._syncPanelControlState(state);
      }

      for (let index = this.panelCount; index < states.length; index += 1) {
        Object.assign(states[index], {
          root: null,
          body: null,
          chartSelect: null,
          compareSelect: null,
          compareOptionsSignature: null,
        });
      }
      this._updateCompareOptions();
    },

    _updateCompareOptions() {
      const jobs = completedComparisonJobs(app.simulationPanel, this._displayedJobId());
      let comparisonChanged = false;
      for (let index = 0; index < this.panelCount; index += 1) {
        const state = states[index];
        const select = state.compareSelect;
        if (!select) continue;
        const selectedStillAvailable = jobs.some(
          (job) => String(job.id) === String(state.compareJobId || '')
        );
        if (state.compareJobId && !selectedStillAvailable) {
          state.compareJobId = null;
          comparisonChanged = true;
        }

        const optionsSignature = JSON.stringify({
          jobs: jobs.map((job) => [job.id, formatJobListLabel(job)]),
          selected: state.compareJobId,
        });
        if (state.compareOptionsSignature === optionsSignature) continue;

        clearElement(select);
        const offOption = document.createElement('option');
        offOption.value = '';
        offOption.textContent = 'Compare: Off';
        select.appendChild(offOption);
        for (const job of jobs) {
          const option = document.createElement('option');
          option.value = String(job.id);
          option.textContent = `Compare: ${formatJobListLabel(job)}`;
          option.title = formatJobListLabel(job);
          select.appendChild(option);
        }
        select.value = state.compareJobId || '';
        select.title = select.selectedOptions[0]?.textContent || '';
        state.compareOptionsSignature = optionsSignature;
        this._syncPanelControlState(state);
      }
      return comparisonChanged;
    },

    async _loadMostRecentCompletedJob() {
      if (!this.visible || this.latestResults || this._lazyLoadPromise) {
        return this._lazyLoadPromise;
      }

      const lazyLoadToken = ++this._lazyLoadToken;
      this._lazyLoadPromise = (async () => {
        const panel =
          app.simulationPanel ||
          (typeof app.ensureSimulationPanel === 'function'
            ? await app.ensureSimulationPanel().catch(() => null)
            : null);
        if (!this.visible || !panel || this.latestResults) return null;
        const job = allJobs(panel).find((candidate) => candidate.status === 'complete');
        if (!job) {
          await this.refresh();
          return null;
        }

        if (typeof panel.ensureResultsForJob === 'function') {
          const result = await panel.ensureResultsForJob(job.id, {
            display: false,
            activate: false,
            updateLastResults: false,
          });
          if (
            result?.ok &&
            this.visible &&
            !this.latestResults &&
            lazyLoadToken === this._lazyLoadToken
          ) {
            panel.displayResults(result.results, result.job || job);
          }
          return result;
        }

        const cached = panel.resultCache?.get(job.id);
        const results = cached || (await panel.solver?.getResults(job.id));
        if (
          results &&
          this.visible &&
          !this.latestResults &&
          lazyLoadToken === this._lazyLoadToken
        ) {
          panel.resultCache?.set(job.id, results);
          panel.displayResults?.(results, job);
        }
        return results;
      })()
        .catch(() => {
          if (this.visible && !this.latestResults) {
            for (let index = 0; index < this.panelCount; index += 1) {
              renderPanelStatus(states[index], 'Unable to load simulation results.', 'error');
            }
          }
          return null;
        })
        .finally(() => {
          this._lazyLoadPromise = null;
        });
      return this._lazyLoadPromise;
    },

    async _loadReference(compareJobId) {
      const panel = app.simulationPanel;
      const job = panel?.jobs?.get(compareJobId) || null;
      if (!panel || !job || job.status !== 'complete') {
        throw new Error('Comparison job is unavailable.');
      }

      let results = panel.resultCache?.get(compareJobId);
      if (!results) {
        results = await panel.solver.getResults(compareJobId);
        if (!results || typeof results !== 'object') {
          throw new Error('Comparison results are unavailable.');
        }
        panel.resultCache.set(compareJobId, results);
      }
      return { results, label: formatJobListLabel(job) };
    },

    async _refreshPanel(index, { force = false } = {}) {
      const state = states[index];
      if (!this.visible || !state?.body) return null;
      const token = requestGuard.begin(index);

      // The balloon panel owns a live WebGL context; drop it as soon as the
      // panel shows anything else (status text and images just replace DOM).
      if (!isBalloonChartKey(state.chartKey)) {
        disposeBalloonPanel(state);
      }

      if (!this.latestResults) {
        renderPanelStatus(state, 'Run a simulation to see results here');
        return null;
      }

      if (state.chartKey === 'summary') {
        renderPanelSummary(state, this.latestResults, this.latestJob);
        return null;
      }

      if (isBalloonChartKey(state.chartKey)) {
        if (!renderBalloonPanel(state, this.latestResults)) {
          renderPanelStatus(
            state,
            'No balloon data. Enable "3D Balloon Sampling" under Directivity Map settings and run a new simulation.'
          );
        }
        return null;
      }

      const panel = app.simulationPanel;
      const smoothing = panel?.currentSmoothing || 'none';
      const referenceLevel = Number.isFinite(Number(panel?.currentDirectivityReferenceLevel))
        ? Number(panel.currentDirectivityReferenceLevel)
        : -6;
      const theme = getChartTheme();
      const jobId = this._displayedJobId();
      const cacheKey = buildResultsDockCacheKey({
        jobId,
        chartKey: state.chartKey,
        smoothing,
        refLevel: referenceLevel,
        theme,
        compareJobId: state.compareJobId,
      });

      if (!force) {
        const cachedImage = imageCache.get(cacheKey);
        if (cachedImage !== undefined) {
          if (requestGuard.isCurrent(index, token)) {
            renderPanelImage(state, state.chartKey, cachedImage);
          }
          return cachedImage;
        }
      }

      renderPanelStatus(state, 'Rendering…', 'loading');
      let reference = null;
      if (state.compareJobId) {
        try {
          reference = await this._loadReference(state.compareJobId);
        } catch {
          if (requestGuard.isCurrent(index, token)) {
            state.compareJobId = null;
            if (state.compareSelect) state.compareSelect.value = '';
            renderPanelStatus(state, 'Unable to load comparison results.', 'error');
          }
          return null;
        }
      }

      if (!requestGuard.isCurrent(index, token)) return null;
      const request = buildResultsDockRequest({
        results: this.latestResults,
        chartKey: state.chartKey,
        smoothing,
        referenceLevel,
        theme,
        reference,
      });

      if (
        request.kind === 'directivity' &&
        (!request.payload.frequencies.length ||
          !hasDirectivityPatterns(request.payload.directivity))
      ) {
        renderPanelImage(state, state.chartKey, null);
        return null;
      }

      const backendUrl = panel?.solver?.backendUrl || DEFAULT_BACKEND_URL;
      const response =
        request.kind === 'directivity'
          ? await requestDirectivityMap(backendUrl, request.payload)
          : await requestLineCharts(backendUrl, request.payload);
      if (!requestGuard.isCurrent(index, token)) return null;

      if (!response.ok) {
        renderPanelStatus(state, describeRequestError(response), 'error');
        return null;
      }

      const image =
        request.kind === 'directivity' ? response.image : response.charts?.[state.chartKey] || null;
      imageCache.set(cacheKey, image);
      renderPanelImage(state, state.chartKey, image);
      return image;
    },
  };

  app.resultsDock = dock;

  if (typeof ResizeObserver !== 'undefined') {
    dock.resizeObserver = new ResizeObserver(() => {
      if (!dock.visible) return;
      const mode = getCurrentLayoutSettings().panelMode;
      if (mode !== 'auto') {
        dock._updateGridLayout();
        return;
      }
      if (dock._updatePanelCount('auto')) void dock.refresh();
    });
    dock.resizeObserver.observe(element);
  }

  mobileQuery?.addEventListener?.('change', () => dock.applyLayout());
  dock.applyLayout();
  return dock;
}
