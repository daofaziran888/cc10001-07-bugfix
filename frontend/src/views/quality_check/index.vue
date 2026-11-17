<template>
  <section class="page" data-module="quality_check">
    <header class="page-head">
      <div><h2>质量审计</h2><p class="page-desc">维护采集结果的质量审计记录、阈值比对与处理建议，支撑数据质量闭环。</p></div>
      <div class="page-actions">
        <button class="btn primary" type="button" data-testid="open-create" @click="openCreate">登记审计记录</button>
        <button class="btn" type="button" data-testid="audit-all" @click="runAuditAll">执行批量审计</button>
        <button class="btn" type="button" data-testid="export" @click="exportRows">导出作业清单</button>
      </div>
    </header>
    <div class="filter-bar">
      <label><span>开始日期</span><input type="date" v-model="dateFrom" /></label>
      <label><span>结束日期</span><input type="date" v-model="dateTo" /></label>
      <button class="btn" type="button" @click="reload">查询</button>
    </div>
    <div class="timeline-wrap">
      <div v-for="item in timelineItems" :key="item.id" class="timeline-row">
        <span class="timeline-label">{{ item.name || item.label }}</span>
        <div class="timeline-track">
          <div class="timeline-bar" :class="`status-${item.status}`" :style="{ left: barLeft(item) + '%', width: barWidth(item) + '%' }" :data-testid="`bar-${item.id}`" :title="`${item.start} ~ ${item.end}`" @click="selectItem(item)">&nbsp;</div>
        </div>
        <span style="font-size:12px;color:var(--muted);margin-left:8px;">{{ item.start }} ~ {{ item.end }}</span>
      </div>
      <p v-if="!timelineItems.length" style="text-align:center;color:var(--muted)">暂无作业记录</p>
    </div>
    <div v-if="selectedItem" class="timeline-log" data-testid="timeline-log">
      <strong>{{ selectedItem.name || selectedItem.label }}</strong> — 状态：{{ selectedItem.status }}
      <div style="margin-top:4px;">{{ selectedItem.logs || '暂无日志' }}</div>
    </div>

    <section class="audit-section">
      <div class="page-head">
        <div><h3>审计记录数据集</h3><p class="page-desc">按批次真实行数生成的审计结论；原始行数、有效行数、存储路径与摘要长度共同构成判定依据。</p></div>
      </div>
      <table class="data-table">
        <thead>
          <tr>
            <th>记录编号</th><th>关联批次</th><th>判定结果</th><th>有效率</th>
            <th>原始行数</th><th>有效行数</th><th>存储文件行数</th><th>摘要长度</th>
            <th>修正后批次状态</th><th>审计时间</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="rec in auditRecords" :key="rec.id" :data-testid="`audit-row-${rec.id}`" class="audit-row" @click="selectAudit(rec)">
            <td>{{ rec.audit_no || '—' }}</td>
            <td>{{ rec.batch_no || rec.batch_id }}</td>
            <td><span class="verdict-tag" :class="`verdict-${rec.verdict}`">{{ rec.verdict }}</span></td>
            <td>{{ formatRate(rec.valid_rate) }}</td>
            <td>{{ rec.raw_rows ?? '—' }}</td>
            <td>{{ rec.valid_rows ?? '—' }}</td>
            <td>{{ rec.storage_rows ?? '—' }}</td>
            <td>{{ rec.checksum_length ?? '—' }}</td>
            <td>{{ rec.batch_status || '—' }}</td>
            <td>{{ rec.audited_at || '—' }}</td>
          </tr>
          <tr v-if="!auditRecords.length">
            <td colspan="10" class="empty-state">暂无审计记录，可点击「执行批量审计」对待校验批次审计</td>
          </tr>
        </tbody>
      </table>
    </section>

    <div v-if="selectedAudit" class="audit-detail" data-testid="audit-detail">
      <h4>审计详情 · {{ selectedAudit.audit_no }}</h4>
      <dl class="audit-grid">
        <dt>判定依据</dt>
        <dd>原始行数 {{ selectedAudit.raw_rows ?? '未知' }}，有效行数 {{ selectedAudit.valid_rows ?? '未知' }}，存储路径 {{ selectedAudit.storage_path || '—' }}，摘要长度 {{ selectedAudit.checksum_length ?? '—' }}</dd>
        <dt>存储文件行数</dt><dd>{{ selectedAudit.storage_rows ?? '不可读' }}</dd>
        <dt>有效率</dt><dd>{{ formatRate(selectedAudit.valid_rate) }}</dd>
        <dt>阈值</dt><dd>{{ selectedAudit.threshold || '—' }}</dd>
        <dt>实际值</dt><dd>{{ selectedAudit.actual || '—' }}</dd>
        <dt>校验摘要</dt><dd>{{ selectedAudit.checksum || '—' }}</dd>
        <dt>判定结果</dt><dd>{{ selectedAudit.verdict }}</dd>
        <dt>修正后批次状态</dt><dd>{{ selectedAudit.batch_status || '—' }}</dd>
        <dt>问题</dt><dd>{{ (selectedAudit.issues || []).join('；') || '无' }}</dd>
        <dt>处理建议</dt><dd>{{ (selectedAudit.suggestions || []).join('；') || '无需处理' }}</dd>
      </dl>
    </div>

    <div v-if="showCreate" class="modal-backdrop" data-testid="create-modal">
      <form class="modal" @submit.prevent="submitCreate"><h3>登记审计记录</h3>
        <label v-for="field in columns.slice(0,3)" :key="field" class="create-field"><span>{{ field }}</span><input v-model="createValues[field]" :placeholder="`请输入${field}`" required /></label>
        <div style="display:flex;gap:8px;justify-content:flex-end;margin-top:12px">
          <button class="btn" type="button" @click="showCreate=false">取消</button>
          <button class="btn primary" type="submit" data-testid="submit-create">提交</button>
        </div>
      </form>
    </div>
    <footer class="page-foot"><span v-if="errorMessage" class="error-text">{{ errorMessage }}</span></footer>
  </section>
</template>
<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { request } from '@/api/client'
const ENDPOINT = '/api/quality_check'
const AUDIT_ENDPOINT = '/api/pipeline/audit'
const columns = ["记录编号", "关联批次", "检查项", "阈值", "实际值", "判定结果", "审计时间", "处理建议"]
const timelineItems = ref<any[]>([])
const selectedItem = ref<any>(null)
const dateFrom = ref(''); const dateTo = ref('')
const errorMessage = ref(''); const showCreate = ref(false)
const createValues = ref<Record<string,string>>({})
const allItems = ref<any[]>([])
const auditRecords = ref<any[]>([])
const selectedAudit = ref<any>(null)
function barLeft(item: any) { const all = allItems.value.filter(i => i.start && i.end); if(!all.length) return 0; const min = Math.min(...all.map(i => new Date(i.start).getTime())); const max = Math.max(...all.map(i => new Date(i.end).getTime())); const range = max - min || 86400000; return ((new Date(item.start).getTime() - min) / range) * 100 }
function barWidth(item: any) { const all = allItems.value.filter(i => i.start && i.end); if(!all.length) return 20; const min = Math.min(...all.map(i => new Date(i.start).getTime())); const max = Math.max(...all.map(i => new Date(i.end).getTime())); const range = max - min || 86400000; return Math.max(5, ((new Date(item.end).getTime() - new Date(item.start).getTime()) / range) * 100) }
function selectItem(item: any) { selectedItem.value = selectedItem.value?.id === item.id ? null : item }
function formatRate(rate: any) { return typeof rate === 'number' ? `${(rate * 100).toFixed(1)}%` : '—' }
function selectAudit(rec: any) { selectedAudit.value = selectedAudit.value?.id === rec.id ? null : rec }
async function reload() {
  try { const res = await request(ENDPOINT); const data = await res.json(); allItems.value = data.items || []; applyFilter() } catch(e) { errorMessage.value = '加载失败' }
}
async function loadAuditRecords() {
  try {
    const res = await request(`${AUDIT_ENDPOINT}/records`)
    if (res.ok) auditRecords.value = await res.json()
  } catch(e) { /* 审计记录加载失败时保留时间线可用 */ }
}
async function runAuditAll() {
  errorMessage.value = ''
  try {
    const res = await request(`${AUDIT_ENDPOINT}/all`, { method: 'POST' })
    if (!res.ok) throw new Error('批量审计执行失败')
    await Promise.all([reload(), loadAuditRecords()])
  } catch(e: any) { errorMessage.value = e.message || '批量审计执行失败' }
}
function applyFilter() {
  let items = allItems.value
  if(dateFrom.value) items = items.filter((i: any) => i.start >= dateFrom.value)
  if(dateTo.value) items = items.filter((i: any) => i.end <= dateTo.value)
  timelineItems.value = items; selectedItem.value = null
}
function openCreate() { createValues.value = {}; showCreate.value = true }
async function submitCreate() {
  try { const res = await request(ENDPOINT, { method: 'POST', body: JSON.stringify({ values: createValues.value }) }); if(!res.ok) throw new Error('创建失败'); showCreate.value = false; await reload() } catch(e: any) { errorMessage.value = e.message }
}
function exportRows() { window.open(`${ENDPOINT}/export`, '_blank') }
onMounted(() => { reload(); loadAuditRecords() })
</script>
