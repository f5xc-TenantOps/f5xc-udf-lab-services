<template>
  <div class="app">
    <!-- Header -->
    <header class="header">
      <img src="https://www.logo.wine/a/logo/F5_Networks/F5_Networks-Logo.wine.svg" alt="F5" class="logo" />
      <div class="header-text">
        <h1>Lab Deployment Status</h1>
        <span v-if="metadata" class="header-email">{{ metadata.email }}</span>
      </div>
    </header>

    <main class="main">
      <!-- Deployment Info Card -->
      <section class="card">
        <div class="card-header">
          <h2 class="card-title">XC Deployment Status</h2>
          <span v-if="deployStatus" :class="['badge', statusBadgeClass]">
            <span class="badge-dot"></span>
            {{ formatStatus(overallStatus) }}
          </span>
        </div>

        <div v-if="!deployStatus" class="loading">
          <span class="spinner"></span> Waiting for deployment state...
        </div>

        <div v-else>
          <!-- Deployment metadata -->
          <div class="deploy-meta">
            <div class="meta-row">
              <span class="meta-label">Petname</span>
              <span class="meta-value">{{ deployStatus.petname || metadata?.petname || '...' }}</span>
            </div>
            <div class="meta-row">
              <span class="meta-label">Deployment ID</span>
              <span class="meta-value mono">{{ deployStatus.dep_id || metadata?.dep_id || '...' }}</span>
            </div>
            <div class="meta-row">
              <span class="meta-label">Lab ID</span>
              <span class="meta-value mono">{{ deployStatus.lab_id || metadata?.lab_id || '...' }}</span>
            </div>
            <div v-if="deployStatus.tenant_url" class="meta-row">
              <span class="meta-label">Tenant</span>
              <a :href="deployStatus.tenant_url" target="_blank" rel="noopener" class="meta-value tenant-link">
                {{ tenantDisplayName }}
              </a>
            </div>
          </div>

          <!-- Provisioning progress -->
          <div v-if="provisioningItems.length || showCeRow" class="steps-section">
            <h3 class="section-label">Provisioning Steps</h3>
            <div class="steps-list">
              <!-- Flat list: workflow steps (minus "resources") + individual resources -->
              <div
                v-for="item in provisioningItems"
                :key="item.key"
                :class="['step-row', { 'step-active': item.status === 'IN_PROGRESS' }]"
              >
                <div :class="['step-indicator', stepIndicatorClass(item.status)]">
                  <span v-if="item.status === 'SUCCESS'" class="icon-check">&#10003;</span>
                  <span v-else-if="item.status === 'IN_PROGRESS'" class="spinner spinner-sm"></span>
                  <span v-else-if="item.status === 'FAILED'" class="icon-x">&#10007;</span>
                  <span v-else-if="item.status === 'SKIPPED'" class="icon-skip">&#8212;</span>
                  <span v-else class="icon-pending"></span>
                </div>
                <div class="step-content">
                  <span class="step-name">{{ item.label }}</span>
                  <span v-if="item.subtitle" class="step-type">{{ item.subtitle }}</span>
                  <span v-if="item.error" class="step-error">{{ item.error }}</span>
                </div>
                <span :class="['step-badge', stepBadgeClass(item.status)]">
                  {{ formatStatus(item.status) }}
                </span>
              </div>

              <!-- CE Registration (inline at the end) -->
              <div
                v-if="showCeRow"
                :class="['step-row', { 'step-active': ceIsActive }]"
              >
                <div :class="['step-indicator', stepIndicatorClass(ceRowStatus)]">
                  <span v-if="ceRowStatus === 'SUCCESS'" class="icon-check">&#10003;</span>
                  <span v-else-if="ceRowStatus === 'IN_PROGRESS'" class="spinner spinner-sm"></span>
                  <span v-else-if="ceRowStatus === 'FAILED'" class="icon-x">&#10007;</span>
                  <span v-else class="icon-pending"></span>
                </div>
                <div class="step-content">
                  <span class="step-name">CE Registration</span>
                  <span v-if="ceStatus?.ce_ip" class="step-detail">{{ ceStatus.ce_ip }}</span>
                  <span v-if="ceSubtext" class="step-type">{{ ceSubtext }}</span>
                  <span v-if="ceStatus?.error && ceRowStatus === 'FAILED'" class="step-error">{{ ceStatus.error }}</span>
                </div>
                <span :class="['step-badge', stepBadgeClass(ceRowStatus)]">
                  {{ formatStatus(ceDisplayStatus) }}
                </span>
              </div>
            </div>
          </div>

          <!-- Message fallback when no steps -->
          <p v-else-if="deployStatus.message" class="status-message">
            {{ deployStatus.message }}
          </p>

          <!-- Updated timestamp -->
          <div v-if="displayTimestamp" class="updated-at">
            Last updated {{ formatTimestamp(displayTimestamp) }}
          </div>
        </div>
      </section>

      <!-- Outputs Card -->
      <section v-if="hasOutputs" class="card">
        <h2 class="card-title">Outputs</h2>
        <div class="outputs-list">
          <div v-for="(value, key) in deployStatus.outputs" :key="key" class="output-row">
            <span class="output-key">{{ formatStatus(key) }}</span>
            <span class="output-value">{{ value }}</span>
          </div>
        </div>
      </section>

      <!-- Error Section -->
      <section v-if="hasErrors" class="card card-error">
        <button class="error-toggle" @click="showErrorLog = !showErrorLog">
          <span class="error-toggle-icon">{{ showErrorLog ? '\u25BC' : '\u25B6' }}</span>
          Errors ({{ deployStatus.errors.length }})
        </button>
        <div v-if="showErrorLog" class="error-log">
          <div
            v-for="(entry, i) in deployStatus.errors"
            :key="i"
            class="error-entry"
          >
            <span v-if="entry.timestamp" class="error-time">{{ formatTimestamp(entry.timestamp) }}</span>
            <span class="error-msg">{{ entry.message || entry }}</span>
          </div>
        </div>
      </section>
    </main>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, onUnmounted } from 'vue'

const metadata = ref(null)
const deployStatus = ref(null)
const ceStatus = ref(null)
const showErrorLog = ref(false)
const lastFetchTime = ref(null)

let statusInterval = null
let ceInterval = null

// -- Computed --

const overallStatus = computed(() => {
  if (!deployStatus.value) return ''
  const s = (deployStatus.value.status || '').toUpperCase()
  if (s === 'COMPLETED' && ceIsActive.value) return 'IN_PROGRESS'
  return s
})

const statusBadgeClass = computed(() => {
  const s = overallStatus.value
  if (s === 'COMPLETED') return 'badge-green'
  if (s === 'IN_PROGRESS') return 'badge-amber'
  if (s === 'FAILED') return 'badge-red'
  return 'badge-gray'
})

const provisioningItems = computed(() => {
  if (!deployStatus.value) return []
  const items = []

  // Workflow steps (namespace, user) — skip "resources" since we list them individually
  const steps = deployStatus.value.steps || {}
  for (const [key, data] of Object.entries(steps)) {
    if (key === 'resources') continue
    items.push({
      key: 'step-' + key,
      label: formatStatus(key),
      subtitle: data.name || data.email || null,
      status: data.status,
      error: data.error || null,
    })
  }

  // Individual resources — type as main label, name as subtitle
  const resources = deployStatus.value.resources || {}
  for (const [name, data] of Object.entries(resources)) {
    items.push({
      key: 'res-' + name,
      label: formatResourceType(data.type) || name,
      subtitle: name,
      status: data.status,
      error: data.error || null,
    })
  }

  return items
})

const hasOutputs = computed(() => {
  if (!deployStatus.value || !deployStatus.value.outputs) return false
  return Object.keys(deployStatus.value.outputs).length > 0
})

const showCeRow = computed(() => {
  if (!ceStatus.value) return false
  return (ceStatus.value.status || '').toUpperCase() !== 'NOT_STARTED'
})

const ceIsActive = computed(() => {
  if (!ceStatus.value) return false
  const s = (ceStatus.value.status || '').toUpperCase()
  return ['DISCOVERING', 'REGISTERING', 'PROVISIONING'].includes(s)
})

const ceSubtext = computed(() => {
  if (!ceStatus.value) return ''
  const s = (ceStatus.value.status || '').toUpperCase()
  if (s === 'PROVISIONING')
    return 'Firmware upgrades and service restarts are expected during this phase'
  if (s === 'TIMEOUT')
    return ceStatus.value.error || 'CE did not come online'
  return ''
})

const ceRowStatus = computed(() => {
  if (!ceStatus.value) return 'PENDING'
  const s = (ceStatus.value.status || ceStatus.value.state || '').toUpperCase()
  if (s === 'ONLINE' || s === 'REGISTERED') return 'SUCCESS'
  if (['DISCOVERING', 'REGISTERING', 'PROVISIONING'].includes(s)) return 'IN_PROGRESS'
  if (s === 'FAILED' || s === 'TIMEOUT') return 'FAILED'
  return 'PENDING'
})

const ceDisplayStatus = computed(() => {
  if (!ceStatus.value) return 'Pending'
  const s = (ceStatus.value.status || ceStatus.value.state || '').toUpperCase()
  if (s === 'ONLINE' || s === 'REGISTERED') return 'Success'
  if (s === 'TIMEOUT') return 'Failed'
  return formatStatus(ceStatus.value.status || ceStatus.value.state || 'Pending')
})

const hasErrors = computed(() => {
  return deployStatus.value?.errors?.length > 0
})

const displayTimestamp = computed(() => {
  const backend = deployStatus.value?.updated_at
  if (ceIsActive.value && lastFetchTime.value) return lastFetchTime.value
  return backend
})

const tenantDisplayName = computed(() => {
  if (!deployStatus.value?.tenant_url) return ''
  try {
    const hostname = new URL(deployStatus.value.tenant_url).hostname
    return hostname.split('.')[0]
  } catch {
    return deployStatus.value.tenant_url
  }
})

// -- Methods --

function formatStatus(status) {
  if (!status) return ''
  return status.split('_').map(w => w.charAt(0).toUpperCase() + w.slice(1).toLowerCase()).join(' ')
}

const RESOURCE_TYPE_LABELS = {
  origin_pool: 'Origin Pool',
  http_lb: 'HTTP Load Balancer',
  tcp_lb: 'TCP Load Balancer',
  waf_policy: 'WAF Policy',
  securemesh_site_v2: 'Secure Mesh Site',
}

function formatResourceType(type) {
  if (!type) return ''
  return RESOURCE_TYPE_LABELS[type] || type.replace(/_/g, ' ')
}

function formatTimestamp(ts) {
  try { return new Date(ts).toLocaleString() }
  catch { return ts }
}

function stepIndicatorClass(status) {
  const s = (status || '').toUpperCase()
  if (s === 'SUCCESS') return 'ind-green'
  if (s === 'IN_PROGRESS') return 'ind-amber'
  if (s === 'FAILED') return 'ind-red'
  return 'ind-gray'
}

function stepBadgeClass(status) {
  const s = (status || '').toUpperCase()
  if (s === 'SUCCESS') return 'sbadge-green'
  if (s === 'IN_PROGRESS') return 'sbadge-amber'
  if (s === 'FAILED') return 'sbadge-red'
  return 'sbadge-gray'
}

async function fetchMetadata() {
  try {
    const res = await fetch('/metadata')
    if (res.ok) metadata.value = await res.json()
  } catch (e) { console.warn('metadata:', e) }
}

async function fetchDeployStatus() {
  try {
    const res = await fetch('/status/json')
    if (res.ok) deployStatus.value = await res.json()
  } catch (e) { console.warn('status:', e) }
}

async function fetchCeStatus() {
  try {
    const res = await fetch('/ce/status')
    if (res.ok) {
      ceStatus.value = await res.json()
      lastFetchTime.value = new Date().toISOString()
    }
  } catch (e) { console.warn('ce:', e) }
}

onMounted(() => {
  fetchMetadata()
  fetchDeployStatus()
  fetchCeStatus()
  statusInterval = setInterval(fetchDeployStatus, 5000)
  ceInterval = setInterval(fetchCeStatus, 5000)
})

onUnmounted(() => {
  if (statusInterval) clearInterval(statusInterval)
  if (ceInterval) clearInterval(ceInterval)
})
</script>

<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap');
html, body { margin: 0; padding: 0; background: #22242c; }
</style>

<style scoped>
.app {
  /* XC Console dark mode palette */
  --bg: #22242c;
  --surface: #303444;
  --surface-raised: #3a3e4e;
  --border: #353842;
  --border-subtle: #2e3038;
  --text: #f0f2f7;
  --text-secondary: #9ba1ad;
  --text-muted: #6f7787;
  --accent: #e4002b;
  --blue: #4f73ff;
  --green: #35d068;
  --green-dim: rgba(53, 208, 104, 0.12);
  --amber: #ffc400;
  --amber-dim: rgba(255, 196, 0, 0.12);
  --red: #f94627;
  --red-dim: rgba(249, 70, 39, 0.10);
  --font: 'Inter', system-ui, -apple-system, sans-serif;
  --mono: ui-monospace, 'SF Mono', 'Cascadia Mono', 'Segoe UI Mono', monospace;

  min-height: 100vh;
  background: var(--bg);
  font-family: var(--font);
  color: var(--text);
  line-height: 1.5;
  -webkit-font-smoothing: antialiased;
  -moz-osx-font-smoothing: grayscale;
}

/* -- Header -- */
.header {
  display: flex;
  align-items: center;
  gap: 1.25rem;
  padding: 1.25rem 2rem;
  background: var(--surface);
  border-bottom: 1px solid var(--border);
}

.logo {
  height: 56px;
  width: auto;
  flex-shrink: 0;
}

.header-text {
  display: flex;
  flex-direction: column;
  gap: 0.2rem;
  flex: 1;
}

.header h1 {
  font-size: 1.125rem;
  font-weight: 600;
  margin: 0;
  letter-spacing: -0.01em;
  color: #fff;
}

.header-email {
  font-size: 0.8125rem;
  color: var(--text-secondary);
  font-weight: 400;
}

@keyframes pulse {
  0%, 100% { opacity: 1; }
  50% { opacity: 0.35; }
}

/* -- Main layout -- */
.main {
  max-width: 660px;
  margin: 1.5rem auto;
  padding: 0 1.5rem;
}

/* -- Cards -- */
.card {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 12px;
  padding: 1.5rem 1.75rem;
  margin-bottom: 1rem;
}

.card-error {
  border-color: rgba(249, 70, 39, 0.25);
}

.card-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 1.25rem;
}

.card-title {
  font-size: 0.8125rem;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.05em;
  color: var(--text-secondary);
  margin: 0;
}

/* -- Badges -- */
.badge {
  display: inline-flex;
  align-items: center;
  gap: 0.375rem;
  padding: 0.3rem 0.875rem;
  border-radius: 9999px;
  font-size: 0.8125rem;
  font-weight: 600;
  letter-spacing: 0.01em;
}

.badge-dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
}

.badge-green { background: var(--green-dim); color: var(--green); }
.badge-green .badge-dot { background: var(--green); }
.badge-amber { background: var(--amber-dim); color: var(--amber); }
.badge-amber .badge-dot { background: var(--amber); animation: pulse 2s ease-in-out infinite; }
.badge-red { background: var(--red-dim); color: var(--red); }
.badge-red .badge-dot { background: var(--red); }
.badge-gray { background: rgba(95, 103, 118, 0.15); color: var(--text-muted); }
.badge-gray .badge-dot { background: var(--text-muted); }

/* -- Deployment metadata -- */
.deploy-meta {
  display: flex;
  flex-direction: column;
  gap: 0;
}

.meta-row {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 0.75rem 0;
  border-bottom: 1px solid var(--border-subtle);
}

.meta-row:last-child {
  border-bottom: 1px solid var(--border-subtle);
}

.meta-label {
  font-size: 0.875rem;
  color: var(--text-secondary);
  font-weight: 500;
}

.meta-value {
  font-size: 0.9375rem;
  color: var(--text);
  font-weight: 500;
}

.meta-value.mono {
  font-family: var(--mono);
  font-size: 0.8125rem;
  color: var(--text-secondary);
  letter-spacing: -0.01em;
}

.tenant-link {
  color: var(--blue);
  text-decoration: none;
  font-size: 0.875rem;
  font-weight: 500;
  transition: color 0.15s;
}

.tenant-link:hover {
  color: #6b8cff;
  text-decoration: underline;
}

/* -- Steps section -- */
.steps-section {
  margin-top: 1.5rem;
  padding-top: 0;
}

.section-label {
  font-size: 0.75rem;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.06em;
  color: var(--text-muted);
  margin: 0 0 0.625rem 0;
}

.steps-list {
  display: flex;
  flex-direction: column;
  gap: 1px;
}

.step-row {
  display: flex;
  align-items: center;
  gap: 0.75rem;
  padding: 0.625rem 0.625rem;
  border-radius: 8px;
  background: transparent;
  transition: background 0.15s;
}

.step-active {
  background: var(--surface-raised);
}

.step-indicator {
  flex-shrink: 0;
  width: 26px;
  height: 26px;
  display: flex;
  align-items: center;
  justify-content: center;
  border-radius: 50%;
  font-size: 0.75rem;
  font-weight: 700;
}

.ind-green { background: var(--green-dim); color: var(--green); }
.ind-amber { background: var(--amber-dim); color: var(--amber); }
.ind-red { background: var(--red-dim); color: var(--red); }
.ind-gray { background: rgba(95, 103, 118, 0.15); color: var(--text-muted); }


.icon-check { font-size: 0.6875rem; line-height: 1; }
.icon-x { font-size: 0.6875rem; line-height: 1; }
.icon-skip { font-size: 0.8125rem; line-height: 1; font-weight: 700; }
.icon-pending { width: 4px; height: 4px; border-radius: 50%; background: var(--text-muted); }

.step-content {
  flex: 1;
  display: flex;
  flex-direction: column;
  gap: 0.125rem;
  min-width: 0;
}

.step-name {
  font-size: 0.9375rem;
  font-weight: 500;
  color: var(--text);
}

.step-detail {
  font-size: 0.8125rem;
  color: var(--text-secondary);
}

.step-type {
  font-size: 0.8125rem;
  color: var(--text-muted);
}

.step-error {
  font-size: 0.8125rem;
  color: var(--red);
  font-family: var(--mono);
  font-size: 0.75rem;
}

.step-badge {
  font-size: 0.75rem;
  font-weight: 600;
  padding: 0.25rem 0.75rem;
  border-radius: 9999px;
  white-space: nowrap;
  flex-shrink: 0;
}

.sbadge-green { background: var(--green-dim); color: var(--green); }
.sbadge-amber { background: var(--amber-dim); color: var(--amber); }
.sbadge-red { background: var(--red-dim); color: var(--red); }
.sbadge-gray { background: rgba(95, 103, 118, 0.15); color: var(--text-muted); }

/* -- Loading -- */
.loading {
  color: var(--text-secondary);
  font-size: 0.875rem;
  display: flex;
  align-items: center;
  gap: 0.5rem;
}

.spinner {
  display: inline-block;
  width: 14px;
  height: 14px;
  border: 2px solid var(--border);
  border-top-color: var(--amber);
  border-radius: 50%;
  animation: spin 0.8s linear infinite;
}

.spinner-sm {
  width: 10px;
  height: 10px;
}

@keyframes spin {
  to { transform: rotate(360deg); }
}

/* -- Status message fallback -- */
.status-message {
  color: var(--text-secondary);
  font-size: 0.875rem;
  margin: 0;
}

/* -- Updated at -- */
.updated-at {
  margin-top: 1rem;
  padding-top: 0.75rem;
  border-top: 1px solid var(--border-subtle);
  font-size: 0.75rem;
  color: var(--text-muted);
}

/* -- Outputs -- */
.outputs-list {
  display: flex;
  flex-direction: column;
}

.output-row {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  padding: 0.625rem 0;
  border-bottom: 1px solid var(--border-subtle);
  gap: 1rem;
}

.output-row:last-child {
  border-bottom: none;
}

.output-key {
  font-size: 0.8125rem;
  color: var(--text-secondary);
  font-weight: 500;
  white-space: nowrap;
}

.output-value {
  font-family: var(--mono);
  font-size: 0.75rem;
  color: var(--text);
  word-break: break-all;
  text-align: right;
  letter-spacing: -0.01em;
}

/* -- Error section -- */
.error-toggle {
  background: none;
  border: none;
  color: var(--red);
  font-size: 0.875rem;
  font-family: var(--font);
  font-weight: 500;
  cursor: pointer;
  padding: 0;
  display: flex;
  align-items: center;
  gap: 0.5rem;
}

.error-toggle:hover {
  opacity: 0.8;
}

.error-toggle-icon {
  font-size: 0.625rem;
}

.error-log {
  margin-top: 0.75rem;
  background: var(--bg);
  border: 1px solid var(--border);
  border-radius: 8px;
  max-height: 200px;
  overflow-y: auto;
  font-family: var(--mono);
  font-size: 0.6875rem;
}

.error-entry {
  padding: 0.5rem 0.75rem;
  border-bottom: 1px solid var(--border-subtle);
}

.error-entry:last-child {
  border-bottom: none;
}

.error-time {
  color: var(--text-muted);
  margin-right: 0.75rem;
}

.error-msg {
  color: var(--red);
}

.error-banner {
  margin-top: 0.75rem;
  padding: 0.75rem;
  background: var(--red-dim);
  border: 1px solid rgba(249, 70, 39, 0.2);
  color: var(--red);
  border-radius: 8px;
  font-size: 0.8125rem;
  font-family: var(--mono);
}

/* -- Scrollbar (XC console style) -- */
::-webkit-scrollbar { width: 8px; height: 8px; }
::-webkit-scrollbar-track { background: #292b34; }
::-webkit-scrollbar-thumb { background: #454853; border-radius: 4px; }
::-webkit-scrollbar-thumb:hover { background: #5f6776; }
::selection { background-color: #272e49; color: #fff; }
</style>