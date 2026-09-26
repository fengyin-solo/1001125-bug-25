<template>
  <section class="page" data-module="storage">
    <header class="page-head">
      <div>
        <h2>堆存计费管理</h2>
        <p class="page-desc">维护计费单，围绕计费单号、关联箱号、计费周期、堆存天数做登记、筛选与状态流转；免费堆存期 {{ freeDays }} 天，超期按计费标准核算。</p>
      </div>
      <div class="page-actions">
        <button class="btn" type="button" @click="exportRows">导出堆存计费清单</button>
      </div>
    </header>

    <div class="stat-row">
      <article v-for="item in stats" :key="item.label" class="stat-card">
        <span class="stat-label">{{ item.label }}</span>
        <strong class="stat-value">{{ item.value }}</strong>
      </article>
    </div>

    <form class="filter-bar" @submit.prevent="reload">
      <label v-for="field in filterFields" :key="field" class="filter-item">
        <span>{{ field }}</span>
        <input v-model="filters[field]" :placeholder="`按${field}检索`" />
      </label>
      <label class="filter-check">
        <input v-model="overdueOnly" type="checkbox" @change="reload" />
        <span>只看超免堆期</span>
      </label>
      <button class="btn" type="submit">查询</button>
      <button class="btn ghost" type="button" @click="resetFilters">重置条件</button>
    </form>

    <p v-if="notice" class="notice-text" :class="noticeTone">{{ notice }}</p>

    <table class="data-table">
      <thead>
        <tr>
          <th v-for="column in columns" :key="column">{{ column }}</th>
          <th>可执行动作</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="row in rows" :key="String(row.id)" :class="{ 'row-overdue': row.overdue }">
          <td v-for="column in columns" :key="column">
            <template v-if="column === '计费状态'">
              <span :class="['status-tag', statusClass(row.status)]">{{ row.status ?? '—' }}</span>
              <span v-if="row.账单号" class="bill-no">（{{ row.账单号 }}）</span>
            </template>
            <template v-else-if="column === '堆存提示'">
              <span v-if="row[column]" :class="['hint-text', row.overdue ? 'hint-warn' : '']">{{ row[column] }}</span>
              <span v-else>—</span>
            </template>
            <template v-else>{{ row[column] ?? '—' }}</template>
          </td>
          <td class="row-actions">
            <template v-for="action in availableActions(row)" :key="action">
              <button
                class="link"
                type="button"
                :disabled="busyKey === actionKey(row, action)"
                @click="runAction(action, row)"
              >
                {{ busyKey === actionKey(row, action) ? '提交中…' : action }}
              </button>
            </template>
            <span v-if="!availableActions(row).length" class="action-done">已完结</span>
          </td>
        </tr>
        <tr v-if="!rows.length">
          <td :colspan="columns.length + 1" class="empty-state">暂无符合条件的堆存计费数据</td>
        </tr>
      </tbody>
    </table>

    <footer class="page-foot">
      <span>共 {{ total }} 条堆存计费记录</span>
      <span v-if="errorMessage" class="error-text">{{ errorMessage }}</span>
    </footer>
  </section>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'

import { request } from '@/api/client'

type Row = Record<string, string | number | boolean | null>

const ENDPOINT = '/api/storage'
const columns = ["计费单号", "关联箱号", "计费周期", "堆存天数", "计费标准", "应收金额", "客户名称", "计费状态", "堆存提示"]
const filterFields = ["计费单号"]
const freeDays = 7

// 动作随状态收敛：已开票是终态，刷新后不再渲染任何可修改入口。
const ACTIONS_BY_STATUS: Record<string, string[]> = {
  '待核算': ['生成账单'],
  '已核算': ['生成账单', '确认对账'],
  '已对账': ['生成账单', '开具发票'],
  '已开票': [],
}

const rows = ref<Row[]>([])
const total = ref(0)
const errorMessage = ref('')
const notice = ref('')
const noticeTone = ref('')
const filters = ref<Record<string, string>>({})
const overdueOnly = ref(false)
const busyKey = ref('')
const stats = ref([
  { label: '待核算计费单', value: 0 },
  { label: '超免堆期计费单', value: 0 },
  { label: '账单应收合计', value: 0 },
])

function availableActions(row: Row): string[] {
  return ACTIONS_BY_STATUS[String(row.status ?? '')] ?? []
}

function actionKey(row: Row, action: string): string {
  return `${row.id}:${action}`
}

function statusClass(status: unknown): string {
  return {
    '待核算': 'status-pending',
    '已核算': 'status-billed',
    '已对账': 'status-reconciled',
    '已开票': 'status-invoiced',
  }[String(status ?? '')] ?? ''
}

function flash(message: string, tone: 'ok' | 'warn' | 'err') {
  notice.value = message
  noticeTone.value = tone === 'ok' ? 'success-text' : tone === 'warn' ? 'warn-text' : 'error-text'
}

function resetFilters() {
  filters.value = {}
  overdueOnly.value = false
  void reload()
}

function exportRows() {
  window.open(`${ENDPOINT}/export`, '_blank')
}

async function runAction(action: string, row: Row) {
  errorMessage.value = ''
  notice.value = ''
  busyKey.value = actionKey(row, action)
  try {
    const response = await request(`${ENDPOINT}/${row.id}/actions`, {
      method: 'POST',
      // 后端从 values.action 取动作；之前发 { action } 导致动作永远落空。
      body: JSON.stringify({ values: { action } }),
    })
    if (!response.ok) {
      throw new Error(`服务暂不可用（HTTP ${response.status}），已填内容未受影响，请重试`)
    }
    const payload = (await response.json()) as { ok: boolean; message: string }
    // 业务校验不通过（如计费周期为空）：后端不改记录，页面保留现有内容，提示后可直接重试。
    flash(payload.message, payload.ok ? 'ok' : 'warn')
    await reload()
  } catch (error) {
    // 网络不通：保留当前列表与已填内容，不清空、不跳转，允许原样再点一次。
    flash(error instanceof Error ? error.message : '接口请求失败，内容已保留，请重试', 'err')
  } finally {
    busyKey.value = ''
  }
}

async function reload() {
  errorMessage.value = ''
  const params = new URLSearchParams()
  if (filters.value['计费单号']) params.set('keyword', filters.value['计费单号'])
  if (overdueOnly.value) params.set('overdue', 'true')
  try {
    const response = await request(`${ENDPOINT}?${params.toString()}`)
    if (!response.ok) {
      throw new Error(`计费单列表读取失败（HTTP ${response.status}）`)
    }
    const payload = await response.json()
    rows.value = payload.items ?? []
    total.value = payload.total ?? rows.value.length
    await loadStats()
  } catch (error) {
    // 列表拉取失败时保留上次数据，避免页面被清空成"无数据"。
    errorMessage.value = error instanceof Error ? error.message : '堆存计费列表读取失败'
  }
}

async function loadStats() {
  try {
    const response = await request(`${ENDPOINT}/stats`)
    if (!response.ok) return
    const data = (await response.json()) as Record<string, number>
    stats.value = [
      { label: '待核算计费单', value: Number(data['待核算计费单'] ?? 0) },
      { label: '超免堆期计费单', value: Number(data['超免堆期计费单'] ?? 0) },
      { label: '账单应收合计', value: Number(data['账单应收合计'] ?? 0) },
    ]
  } catch {
    // 统计失败不阻断列表，卡片保持上一次的值。
  }
}

onMounted(reload)
</script>
