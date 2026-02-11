<template>
  <div class="app">
    <!-- Header -->
    <header class="header">
      <img src="https://www.logo.wine/a/logo/F5_Networks/F5_Networks-Logo.wine.svg" alt="F5" class="logo" />
      <div class="header-text">
        <h1>Lab Deployment Status</h1>
        <div v-if="metadata" class="header-meta">
          <span class="meta-line">{{ metadata.email }}</span>
          <span class="meta-line">{{ metadata.petname }}</span>
        </div>
      </div>
    </header>

    <main class="main">
      <!-- Deployment Status Card -->
      <section class="card">
        <div class="card-header">
          <h2 class="card-title">XC Deployment Status</h2>
          <span v-if="deployStatus" :class="['badge', statusBadgeClass]">
            {{ formatStatus(deployStatus.status) }}
          </span>
        </div>

        <div v-if="!deployStatus" class="loading">
          <span class="spinner"></span> Waiting for deployment state...
        </div>

        <div v-else>
          <!-- Step-by-step progress -->
          <div v-if="deployStatus.steps && deployStatus.steps.length" class="steps-list">
            <div
              v-for="(step, i) in deployStatus.steps"
              :key="i"
              class="step-row"
            >
              <span :class="['step-icon', stepIconClass(step.status)]">
                <span v-if="step.status === 'SUCCESS'" class="icon-check">&#10003;</span>
                <span v-else-if="step.status === 'IN_PROGRESS'" class="spinner spinner-sm"></span>
                <span v-else-if="step.status === 'FAILED'" class="icon-x">&#10007;</span>
                <span v-else class="icon-circle">&#9675;</span>
              </span>
              <div class="step-info">
                <span class="step-name">{{ step.name }}</span>
                <span v-if="step.detail" class="step-detail">{{ step.detail }}</span>
                <span v-if="step.error" class="step-error">{{ step.error }}</span>
              </div>
            </div>
          </div>

          <!-- Message fallback when no steps -->
          <p v-else-if="deployStatus.message" class="status-message">
            {{ deployStatus.message }}
          </p>

          <!-- Updated timestamp -->
          <div v-if="deployStatus.updated_at" class="updated-at">
            Updated: {{ deployStatus.updated_at }}
          </div>
        </div>
      </section>

      <!-- Outputs Card -->
      <section v-if="hasOutputs" class="card">
        <h2 class="card-title">Outputs</h2>
        <table class="outputs-table">
          <tbody>
            <tr v-for="(value, key) in deployStatus.outputs" :key="key">
              <td class="output-key">{{ key }}</td>
              <td class="output-value">{{ value }}</td>
            </tr>
          </tbody>
        </table>
      </section>

      <!-- CE Status Card -->
      <section v-if="showCeCard" class="card">
        <div class="card-header">
          <h2 class="card-title">CE Registration</h2>
          <span :class="['badge', ceBadgeClass]">{{ formatStatus(ceStatus.status || ceStatus.state || 'UNKNOWN') }}</span>
        </div>

        <div class="status-grid">
          <div v-if="ceStatus.ce_ip" class="status-row">
            <span class="label">CE IP</span>
            <span class="value">{{ ceStatus.ce_ip }}</span>
          </div>
          <div v-if="ceStatus.hostname" class="status-row">
            <span class="label">Hostname</span>
            <span class="value">{{ ceStatus.hostname }}</span>
          </div>
          <div v-if="ceStatus.os_version" class="status-row">
            <span class="label">OS Version</span>
            <span class="value">{{ ceStatus.os_version }}</span>
          </div>
          <div v-if="ceStatus.public_ip" class="status-row">
            <span class="label">Public IP</span>
            <span class="value">{{ ceStatus.public_ip }}</span>
          </div>
          <div v-if="ceStatus.state" class="status-row">
            <span class="label">State</span>
            <span class="value">{{ ceStatus.state }}</span>
          </div>
        </div>

        <div v-if="ceStatus.error" class="ce-error">
          {{ ceStatus.error }}
        </div>
      </section>

      <!-- Error Section -->
      <div v-if="hasErrors" class="error-log-section">
        <button class="btn-link" @click="showErrorLog = !showErrorLog">
          {{ showErrorLog ? '\u25BC' : '\u25B6' }} Errors ({{ deployStatus.errors.length }})
        </button>
        <div v-if="showErrorLog" class="error-log">
          <div
            v-for="(entry, i) in deployStatus.errors"
            :key="i"
            class="error-entry"
          >
            <span v-if="entry.timestamp" class="error-time">{{ entry.timestamp }}</span>
            <span class="error-msg">{{ entry.message || entry }}</span>
          </div>
        </div>
      </div>
    </main>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, onUnmounted } from 'vue'

// Reactive state
const metadata = ref(null)
const deployStatus = ref(null)
const ceStatus = ref(null)
const showErrorLog = ref(false)

// Polling intervals
let statusInterval = null
let ceInterval = null

// -- Computed properties --

const statusBadgeClass = computed(() => {
  if (!deployStatus.value) return 'badge-gray'
  const s = (deployStatus.value.status || '').toUpperCase()
  if (s === 'COMPLETED') return 'badge-green'
  if (s === 'IN_PROGRESS') return 'badge-amber'
  if (s === 'FAILED') return 'badge-red'
  if (s === 'PENDING' || s === 'WAITING') return 'badge-gray'
  return 'badge-gray'
})

const hasOutputs = computed(() => {
  if (!deployStatus.value || !deployStatus.value.outputs) return false
  return Object.keys(deployStatus.value.outputs).length > 0
})

const showCeCard = computed(() => {
  if (!ceStatus.value) return false
  const s = (ceStatus.value.status || '').toUpperCase()
  return s !== 'NOT_STARTED'
})

const ceBadgeClass = computed(() => {
  if (!ceStatus.value) return 'badge-gray'
  const s = (ceStatus.value.status || ceStatus.value.state || '').toUpperCase()
  if (s === 'ONLINE' || s === 'COMPLETED') return 'badge-green'
  if (s === 'REGISTERING' || s === 'POLLING' || s === 'DISCOVERING') return 'badge-amber'
  if (s === 'FAILED') return 'badge-red'
  return 'badge-gray'
})

const hasErrors = computed(() => {
  return deployStatus.value
    && deployStatus.value.errors
    && deployStatus.value.errors.length > 0
})

// -- Methods --

function formatStatus(status) {
  if (!status) return ''
  return status
    .split('_')
    .map(w => w.charAt(0).toUpperCase() + w.slice(1).toLowerCase())
    .join(' ')
}

function stepIconClass(status) {
  const s = (status || '').toUpperCase()
  if (s === 'SUCCESS') return 'step-icon-green'
  if (s === 'IN_PROGRESS') return 'step-icon-amber'
  if (s === 'FAILED') return 'step-icon-red'
  return 'step-icon-gray'
}

async function fetchMetadata() {
  try {
    const res = await fetch('/metadata')
    if (res.ok) {
      metadata.value = await res.json()
    }
  } catch (e) {
    console.warn('Failed to fetch metadata:', e)
  }
}

async function fetchDeployStatus() {
  try {
    const res = await fetch('/status/json')
    if (res.ok) {
      deployStatus.value = await res.json()
    }
  } catch (e) {
    console.warn('Failed to fetch deploy status:', e)
  }
}

async function fetchCeStatus() {
  try {
    const res = await fetch('/ce/status')
    if (res.ok) {
      ceStatus.value = await res.json()
    }
  } catch (e) {
    console.warn('Failed to fetch CE status:', e)
  }
}

// -- Lifecycle --

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

<style scoped>
.app {
  min-height: 100vh;
  background: #ffffff;
  font-family: system-ui, -apple-system, sans-serif;
  color: #1a1a1a;
}

/* -- Header -- */
.header {
  display: flex;
  align-items: center;
  gap: 1rem;
  padding: 1rem 2rem;
  border-bottom: 1px solid #e5e5e5;
}

.logo {
  height: 40px;
  width: auto;
  flex-shrink: 0;
}

.header-text {
  display: flex;
  flex-direction: column;
  gap: 0.25rem;
}

.header h1 {
  font-size: 1.25rem;
  font-weight: 600;
  margin: 0;
}

.header-meta {
  display: flex;
  flex-direction: column;
  gap: 0.125rem;
}

.meta-line {
  font-size: 0.875rem;
  font-weight: 400;
  color: #374151;
}

/* -- Main layout -- */
.main {
  max-width: 680px;
  margin: 2rem auto;
  padding: 0 1rem;
}

/* -- Cards -- */
.card {
  background: #ebebeb;
  border-radius: 8px;
  padding: 1.5rem;
  margin-bottom: 1rem;
}

.card-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 1rem;
}

.card-title {
  font-size: 1rem;
  font-weight: 600;
  margin: 0 0 1rem 0;
  color: #1a1a1a;
}

.card-header .card-title {
  margin-bottom: 0;
}

/* -- Badges -- */
.badge {
  padding: 0.25rem 0.75rem;
  border-radius: 9999px;
  font-size: 0.75rem;
  font-weight: 600;
  white-space: nowrap;
}

.badge-green {
  background: #dcfce7;
  color: #166534;
}

.badge-amber {
  background: #fef3c7;
  color: #92400e;
}

.badge-gray {
  background: #e5e7eb;
  color: #374151;
  border: 1px solid #d1d5db;
}

.badge-red {
  background: #fee2e2;
  color: #991b1b;
}

/* -- Loading -- */
.loading {
  color: #6b7280;
  font-size: 0.875rem;
}

.spinner {
  display: inline-block;
  width: 14px;
  height: 14px;
  border: 2px solid #e5e5e5;
  border-top-color: #6b7280;
  border-radius: 50%;
  animation: spin 1s linear infinite;
  margin-right: 0.5rem;
  vertical-align: middle;
}

.spinner-sm {
  width: 12px;
  height: 12px;
  margin-right: 0;
}

@keyframes spin {
  to { transform: rotate(360deg); }
}

/* -- Steps list -- */
.steps-list {
  display: flex;
  flex-direction: column;
  gap: 0.5rem;
}

.step-row {
  display: flex;
  align-items: flex-start;
  gap: 0.75rem;
  padding: 0.5rem 0;
  border-bottom: 1px solid #e5e7eb;
}

.step-row:last-child {
  border-bottom: none;
}

.step-icon {
  flex-shrink: 0;
  width: 24px;
  height: 24px;
  display: flex;
  align-items: center;
  justify-content: center;
  border-radius: 50%;
  font-size: 0.75rem;
  font-weight: 700;
}

.step-icon-green {
  background: #dcfce7;
  color: #166534;
}

.step-icon-amber {
  background: #fef3c7;
  color: #92400e;
}

.step-icon-red {
  background: #fee2e2;
  color: #991b1b;
}

.step-icon-gray {
  background: #e5e7eb;
  color: #6b7280;
}

.icon-check {
  font-size: 0.875rem;
  line-height: 1;
}

.icon-x {
  font-size: 0.875rem;
  line-height: 1;
}

.icon-circle {
  font-size: 0.625rem;
  line-height: 1;
}

.step-info {
  display: flex;
  flex-direction: column;
  gap: 0.125rem;
  min-width: 0;
}

.step-name {
  font-size: 0.875rem;
  font-weight: 500;
  color: #1a1a1a;
}

.step-detail {
  font-size: 0.8125rem;
  color: #6b7280;
}

.step-error {
  font-size: 0.8125rem;
  color: #991b1b;
}

/* -- Status message fallback -- */
.status-message {
  color: #6b7280;
  font-size: 0.875rem;
  margin: 0;
}

/* -- Updated at -- */
.updated-at {
  margin-top: 0.75rem;
  font-size: 0.75rem;
  color: #9ca3af;
}

/* -- Outputs table -- */
.outputs-table {
  width: 100%;
  border-collapse: collapse;
}

.outputs-table td {
  padding: 0.5rem 0;
  font-size: 0.875rem;
  border-bottom: 1px solid #e5e7eb;
  vertical-align: top;
}

.outputs-table tr:last-child td {
  border-bottom: none;
}

.output-key {
  color: #6b7280;
  font-weight: 500;
  width: 40%;
  padding-right: 1rem;
}

.output-value {
  font-family: ui-monospace, monospace;
  color: #1a1a1a;
  word-break: break-all;
}

/* -- Status grid (CE card) -- */
.status-grid {
  display: flex;
  flex-direction: column;
  gap: 0.75rem;
}

.status-row {
  display: flex;
  justify-content: space-between;
  align-items: center;
}

.label {
  color: #6b7280;
  font-size: 0.875rem;
}

.value {
  font-family: ui-monospace, monospace;
  font-size: 0.875rem;
}

.ce-error {
  margin-top: 0.75rem;
  padding: 0.75rem;
  background: #fee2e2;
  color: #991b1b;
  border-radius: 4px;
  font-size: 0.875rem;
}

/* -- Error log section -- */
.error-log-section {
  margin-top: 1rem;
}

.btn-link {
  background: none;
  border: none;
  color: #6366f1;
  font-size: 0.875rem;
  cursor: pointer;
  padding: 0;
}

.btn-link:hover {
  text-decoration: underline;
}

.error-log {
  margin-top: 0.5rem;
  background: #f9fafb;
  border: 1px solid #e5e7eb;
  border-radius: 4px;
  max-height: 200px;
  overflow-y: auto;
  font-family: ui-monospace, monospace;
  font-size: 0.75rem;
}

.error-entry {
  padding: 0.5rem 0.75rem;
  border-bottom: 1px solid #e5e7eb;
}

.error-entry:last-child {
  border-bottom: none;
}

.error-time {
  color: #6b7280;
  margin-right: 0.75rem;
}

.error-msg {
  color: #991b1b;
}
</style>
