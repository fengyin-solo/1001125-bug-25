<template>
  <section class="page" data-module="storage">
    <header class="page-head">
      <div>
        <h2>堆存计费管理</h2>
        <p class="page-desc">维护计费单，围绕计费单号、关联箱号、计费周期、堆存天数做登记、筛选与状态流转。</p>
      </div>
      <div class="page-actions">
        <button class="btn primary" type="button" @click="openCreate">登记计费单</button>
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
      <label class="filter-item">
        <span>计费单号</span>
        <input v-model="keyword" placeholder="按计费单号检索" />
      </label>
      <label class="filter-item">
        <span>计费状态</span>
        <select v-model="statusFilter">
          <option value="">全部状态</option>
          <option v-for="status in statuses" :key="status" :value="status">{{ status }}</option>
        </select>
      </label>
      <label class="filter-item checkbox-item">
        <input v-model="overdueOnly" type="checkbox" />
        <span>只看超免费堆存期</span>
      </label>
      <button class="btn" type="submit">查询</button>
      <button class="btn ghost" type="button" @click="resetFilters">重置条件</button>
    </form>

    <table class="data-table">
      <thead>
        <tr>
          <th v-for="column in columns" :key="column">{{ column }}</th>
          <th>可执行动作</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="row in rows" :key="String(row.id)">
          <td
            v-for="column in columns"
            :key="column"
            :class="{ 'overdue-cell': column === '超期原因' && row[column] }"
          >
            {{ row[column] ?? '—' }}
          </td>
          <td class="row-actions">
            <template v-if="rowActions(row).length || canEdit(row)">
              <button
                v-for="action in rowActions(row)"
                :key="action"
                class="link"
                type="button"
                :disabled="busy"
                @click="runAction(action, row)"
              >
                {{ action }}
              </button>
              <button v-if="canEdit(row)" class="link" type="button" :disabled="busy" @click="openEdit(row)">
                修改
              </button>
            </template>
            <span v-else class="muted-text">已办结</span>
          </td>
        </tr>
        <tr v-if="!rows.length">
          <td :colspan="columns.length + 1" class="empty-state">暂无堆存计费数据，可先登记计费单</td>
        </tr>
      </tbody>
    </table>

    <footer class="page-foot">
      <span>共 {{ total }} 条堆存计费记录</span>
      <span v-if="noticeMessage" class="notice-text">{{ noticeMessage }}</span>
      <span v-if="errorMessage" class="error-text">{{ errorMessage }}</span>
    </footer>

    <div v-if="dialogOpen" class="dialog-mask" @click.self="closeDialog">
      <form class="dialog" @submit.prevent="submitForm">
        <h3>{{ dialogMode === 'create' ? '登记计费单' : `修改计费单 ${form['计费单号']}` }}</h3>
        <label v-for="field in formFields" :key="field.name" class="dialog-field">
          <span>{{ field.name }}<em v-if="field.required">*</em></span>
          <input
            v-model="form[field.name]"
            :placeholder="field.placeholder"
            :disabled="busy || (dialogMode === 'edit' && field.name === '计费单号')"
          />
        </label>
        <p class="dialog-tip">保存失败时已填内容会保留，可修正后直接重试；计费周期、计费标准为空将无法生成账单。</p>
        <p v-if="dialogError" class="error-text">{{ dialogError }}</p>
        <div class="dialog-actions">
          <button class="btn primary" type="submit" :disabled="busy">{{ busy ? '提交中…' : '保存' }}</button>
          <button class="btn ghost" type="button" :disabled="busy" @click="closeDialog">取消</button>
        </div>
      </form>
    </div>
  </section>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'

import { request } from '@/api/client'

type Row = Record<string, string | number | null | undefined>

const ENDPOINT = '/api/storage'
const columns = ["计费单号", "关联箱号", "计费周期", "堆存天数", "计费标准", "应收金额", "客户名称", "计费状态", "超期原因"]
const statuses = ["待核算", "已核算", "已对账", "已开票"]
const stats = [{"label": "待核算计费单", "value": 0}, {"label": "本月应收金额", "value": 0}, {"label": "已开票金额", "value": 0}]
// 每个状态只允许对应的下一步动作，已开票不在表里，自然没有可修改入口
const ACTION_BY_STATUS: Record<string, string[]> = {
  "待核算": ["生成账单"],
  "已核算": ["确认对账"],
  "已对账": ["开具发票"],
}
const formFields = [
  { name: "计费单号", required: true, placeholder: "如 STOR-0007" },
  { name: "关联箱号", required: true, placeholder: "如 TEMU8801234" },
  { name: "计费周期", required: true, placeholder: "如 2026-09 或 2026-09-01~2026-09-30" },
  { name: "堆存天数", required: false, placeholder: "数字，超过 7 天免费堆存期才计费" },
  { name: "计费标准", required: false, placeholder: "日费率，元/箱/天" },
  { name: "客户名称", required: false, placeholder: "" },
]

const rows = ref<Row[]>([])
const total = ref(0)
const busy = ref(false)
const errorMessage = ref('')
const noticeMessage = ref('')
const keyword = ref('')
const statusFilter = ref('')
const overdueOnly = ref(false)

const dialogOpen = ref(false)
const dialogMode = ref<'create' | 'edit'>('create')
const editingId = ref<number | null>(null)
const dialogError = ref('')
const form = ref<Record<string, string>>({})

function rowActions(row: Row): string[] {
  return ACTION_BY_STATUS[String(row.status ?? '')] ?? []
}

function canEdit(row: Row): boolean {
  return row.status === '待核算'
}

function resetFilters() {
  keyword.value = ''
  statusFilter.value = ''
  overdueOnly.value = false
  void reload()
}

function exportRows() {
  window.open(`${ENDPOINT}/export`, '_blank')
}

function openCreate() {
  dialogMode.value = 'create'
  editingId.value = null
  form.value = {}
  dialogError.value = ''
  dialogOpen.value = true
}

function openEdit(row: Row) {
  dialogMode.value = 'edit'
  editingId.value = Number(row.id)
  const values: Record<string, string> = {}
  for (const field of formFields) {
    const value = row[field.name]
    values[field.name] = value === null || value === undefined ? '' : String(value)
  }
  form.value = values
  dialogError.value = ''
  dialogOpen.value = true
}

function closeDialog() {
  if (busy.value) return
  dialogOpen.value = false
}

/** 动作类接口统一返回 { ok, message }：HTTP 层和业务层的失败都要抛出来，不能静默吞掉。 */
async function parseActionResult(response: Response): Promise<{ message?: string }> {
  const payload = await response.json().catch(() => null) as { ok?: boolean; message?: string; detail?: unknown } | null
  if (!response.ok) {
    const detail = payload?.detail
    throw new Error(typeof detail === 'string' ? detail : `接口返回 ${response.status}，操作未生效`)
  }
  if (!payload || payload.ok !== true) {
    throw new Error(payload?.message || '操作未生效，请稍后重试')
  }
  return payload
}

async function runAction(action: string, row: Row) {
  if (busy.value) return
  busy.value = true
  errorMessage.value = ''
  noticeMessage.value = ''
  try {
    const response = await request(`${ENDPOINT}/${row.id}/actions`, {
      method: 'POST',
      body: JSON.stringify({ values: { action } }),
    })
    const result = await parseActionResult(response)
    noticeMessage.value = result.message ?? `计费单已${action}`
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '堆存计费操作失败'
  } finally {
    busy.value = false
  }
}

async function submitForm() {
  if (busy.value) return
  busy.value = true
  dialogError.value = ''
  try {
    const isEdit = dialogMode.value === 'edit'
    const response = await request(isEdit ? `${ENDPOINT}/${editingId.value}` : ENDPOINT, {
      method: isEdit ? 'PUT' : 'POST',
      body: JSON.stringify({ values: { ...form.value } }),
    })
    const result = await parseActionResult(response)
    noticeMessage.value = result.message ?? '计费单已保存'
    dialogOpen.value = false
    await reload()
  } catch (error) {
    // 保存失败：对话框保持打开、已填内容原样保留，修正后可直接重试
    dialogError.value = error instanceof Error ? error.message : '计费单保存失败'
  } finally {
    busy.value = false
  }
}

async function reload() {
  errorMessage.value = ''
  const params = new URLSearchParams()
  if (keyword.value.trim()) params.set('keyword', keyword.value.trim())
  if (statusFilter.value) params.set('status', statusFilter.value)
  if (overdueOnly.value) params.set('overdue', 'true')
  try {
    const response = await request(`${ENDPOINT}?${params.toString()}`)
    const payload = await response.json().catch(() => null) as { items?: Row[]; total?: number; detail?: unknown } | null
    if (!response.ok) {
      const detail = payload?.detail
      throw new Error(typeof detail === 'string' ? detail : '计费单列表读取失败')
    }
    rows.value = payload?.items ?? []
    total.value = payload?.total ?? rows.value.length
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '堆存计费列表读取失败'
  }
}

onMounted(reload)
</script>

<style scoped>
.overdue-cell { color: #b42318; }
.muted-text { color: var(--muted); font-size: 12px; }
.checkbox-item { display: flex; flex-direction: row; align-items: center; gap: 4px; }
.dialog-mask { position: fixed; inset: 0; background: rgba(15, 23, 42, 0.35); display: flex; align-items: center; justify-content: center; z-index: 10; }
.dialog { background: #fff; border-radius: 8px; padding: 16px 20px; width: 420px; display: flex; flex-direction: column; gap: 10px; }
.dialog h3 { margin: 0; font-size: 15px; }
.dialog-field span { display: block; font-size: 12px; color: var(--muted); margin-bottom: 4px; }
.dialog-field em { color: #b42318; font-style: normal; margin-left: 2px; }
.dialog-field input { width: 100%; padding: 6px 8px; border: 1px solid var(--border); border-radius: 6px; }
.dialog-tip { margin: 0; font-size: 12px; color: var(--muted); }
.dialog-actions { display: flex; gap: 8px; justify-content: flex-end; }
</style>
