<template>
  <section class="page" data-module="cohort_extraction">
    <header class="page-head">
      <div>
        <h2>促销分群提取</h2>
        <p class="page-desc">
          先按区域与消费门槛圈出核实范围，再按排除标签校验：命中排除标签记为明确排除，
          标签未送达记为待判；两者都不进入交付名单，报表只汇总实际交付的顾客。
        </p>
      </div>
      <div class="page-actions">
        <button class="btn" data-testid="preview" type="button" @click="loadPreview">试算当前规则</button>
        <button class="btn primary" data-testid="extract" type="button" :disabled="extracting" @click="submitExtraction">
          {{ extracting ? '提交中…' : '提交提取' }}
        </button>
      </div>
    </header>

    <form class="filter-bar" data-testid="rule-form" @submit.prevent="loadPreview">
      <label class="filter-item">
        <span>区域</span>
        <input v-model="rule.region" data-testid="rule-region" placeholder="如 华东" />
      </label>
      <label class="filter-item">
        <span>消费门槛 LTV</span>
        <input v-model="rule.min_ltv" data-testid="rule-min-ltv" type="number" min="0" step="100" />
      </label>
      <label class="filter-item">
        <span>排除标签（逗号分隔）</span>
        <input v-model="rule.exclude_tags" data-testid="rule-exclude-tags" placeholder="如 促销排除,黑名单" />
      </label>
      <button class="btn" data-testid="apply-rule" type="submit">按此规则试算</button>
    </form>

    <div class="stat-row">
      <article v-for="item in statCards" :key="item.label" class="stat-card">
        <span class="stat-label">{{ item.label }}</span>
        <strong class="stat-value" :data-testid="`stat-${item.key}`">{{ item.value }}</strong>
      </article>
    </div>

    <p v-if="evaluation" class="scope-note" data-testid="scope-note">
      核实范围 {{ evaluation.verification_scope }} 人：
      {{ evaluation.usable ? '本次有可用名单' : '本次没有可用名单' }}；
      名单只含已核实且未命中的顾客，待判 {{ evaluation.pending_count }} 人需后补。
    </p>

    <h3 class="section-title">可交付名单 <small>（只含已核实且未命中排除标签的顾客）</small></h3>
    <table class="data-table" data-testid="member-table">
      <thead>
        <tr><th>顾客</th><th>区域</th><th>渠道</th><th>LTV</th><th>判定结果</th><th>入选原因</th></tr>
      </thead>
      <tbody>
        <tr v-for="row in evaluation?.members ?? []" :key="row.user_id" :data-testid="`member-${row.user_id}`">
          <td>{{ row.user_id }}</td>
          <td>{{ row.region }}</td>
          <td>{{ row.channel }}</td>
          <td>{{ row.ltv }}</td>
          <td><span class="badge badge-delivered">{{ row['判定结果'] }}</span></td>
          <td>{{ row['入选原因'] }}</td>
        </tr>
        <tr v-if="!evaluation?.members?.length">
          <td colspan="6" class="empty-state">本次没有可交付顾客</td>
        </tr>
      </tbody>
    </table>

    <h3 class="section-title">待判顾客 <small>（标签未送达，不能视为已通过）</small></h3>
    <table class="data-table" data-testid="pending-table">
      <thead>
        <tr><th>顾客</th><th>区域</th><th>LTV</th><th>判定结果</th><th>原因</th><th>补齐标签</th></tr>
      </thead>
      <tbody>
        <tr v-for="row in evaluation?.pending_members ?? []" :key="row.user_id" :data-testid="`pending-${row.user_id}`">
          <td>{{ row.user_id }}</td>
          <td>{{ row.region }}</td>
          <td>{{ row.ltv }}</td>
          <td><span class="badge badge-pending">{{ row['判定结果'] }}</span></td>
          <td>{{ row['入选原因'] }}</td>
          <td class="row-actions">
            <input v-model="backfillValues[row.user_id]" :data-testid="`backfill-input-${row.user_id}`" placeholder="标签值" />
            <button class="link" type="button" :data-testid="`backfill-${row.user_id}`" @click="submitBackfill(row.user_id)">
              补齐
            </button>
          </td>
        </tr>
        <tr v-if="!evaluation?.pending_members?.length">
          <td colspan="6" class="empty-state">没有待判顾客</td>
        </tr>
      </tbody>
    </table>

    <h3 class="section-title">明确排除 <small>（命中排除标签，不进入名单）</small></h3>
    <table class="data-table" data-testid="excluded-table">
      <thead>
        <tr><th>顾客</th><th>区域</th><th>LTV</th><th>判定结果</th><th>原因</th></tr>
      </thead>
      <tbody>
        <tr v-for="row in evaluation?.excluded_members ?? []" :key="row.user_id" :data-testid="`excluded-${row.user_id}`">
          <td>{{ row.user_id }}</td>
          <td>{{ row.region }}</td>
          <td>{{ row.ltv }}</td>
          <td><span class="badge badge-excluded">{{ row['判定结果'] }}</span></td>
          <td>{{ row['入选原因'] }}</td>
        </tr>
        <tr v-if="!evaluation?.excluded_members?.length">
          <td colspan="5" class="empty-state">没有明确排除的顾客</td>
        </tr>
      </tbody>
    </table>

    <h3 class="section-title">提取轮次与报表 <small>（报表按实际交付名单汇总）</small></h3>
    <table class="data-table" data-testid="run-table">
      <thead>
        <tr>
          <th>轮次</th><th>提交时间</th><th>核实范围</th><th>交付</th><th>待判</th><th>明确排除</th>
          <th>可用名单</th><th>LTV 总额</th><th>操作</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="run in runs" :key="run.run_id" :data-testid="`run-${run.run_id}`">
          <td>{{ run.round }} · {{ run.label }}</td>
          <td>{{ run.created_at?.replace('T', ' ').slice(0, 19) }}</td>
          <td>{{ run.verification_scope }}</td>
          <td>{{ run.delivered_count }}</td>
          <td>{{ run.pending_count }}</td>
          <td>{{ run.excluded_count }}</td>
          <td>{{ run.usable ? '有' : '无' }}</td>
          <td>{{ run.report?.total_ltv ?? 0 }}</td>
          <td class="row-actions">
            <button class="link" type="button" :data-testid="`replay-${run.run_id}`" @click="replayRun(run.run_id)">
              重放旧名单
            </button>
          </td>
        </tr>
        <tr v-if="!runs.length">
          <td colspan="9" class="empty-state">还没有提取轮次，提交一次提取即可生成名单与报表</td>
        </tr>
      </tbody>
    </table>

    <div v-if="replay" class="replay-panel" data-testid="replay-panel">
      <h4>重放结果 · {{ replay.run_id }}</h4>
      <p :class="replay.replayable ? 'ok-text' : 'error-text'">
        {{ replay.replayable ? '旧交付名单与存档报表一致，可复查' : '重放结果与存档报表不一致，请检查快照' }}
      </p>
      <table class="data-table">
        <thead><tr><th>指标</th><th>存档报表</th><th>重放报表</th></tr></thead>
        <tbody>
          <tr><td>交付人数</td><td>{{ replay.stored_report?.member_count }}</td><td>{{ replay.replayed_report?.member_count }}</td></tr>
          <tr><td>区域分布</td><td>{{ formatDistribution(replay.stored_report?.region_distribution) }}</td><td>{{ formatDistribution(replay.replayed_report?.region_distribution) }}</td></tr>
          <tr><td>LTV 总额</td><td>{{ replay.stored_report?.total_ltv }}</td><td>{{ replay.replayed_report?.total_ltv }}</td></tr>
        </tbody>
      </table>
    </div>

    <footer class="page-foot">
      <span>共 {{ runs.length }} 轮提取记录</span>
      <span v-if="errorMessage" class="error-text" data-testid="error">{{ errorMessage }}</span>
      <span v-else-if="notice" class="ok-text" data-testid="notice">{{ notice }}</span>
    </footer>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'

import { request } from '@/api/client'

type Verdict = '已交付' | '待判' | '明确排除'

type Member = {
  user_id: string
  region: string
  channel?: string
  ltv: number
  tag?: string | null
  '判定结果': Verdict
  '入选原因': string
}

type Report = {
  member_count: number
  region_distribution: Record<string, number>
  total_ltv: number
  avg_ltv: number
}

type Evaluation = {
  verification_scope: number
  candidate_count: number
  delivered_count: number
  pending_count: number
  excluded_count: number
  usable: boolean
  members: Member[]
  pending_members: Member[]
  excluded_members: Member[]
  report: Report
}

type Run = {
  run_id: string
  round: number
  label: string
  created_at: string
  verification_scope: number
  delivered_count: number
  pending_count: number
  excluded_count: number
  usable: boolean
  report: Report
}

type Replay = {
  run_id: string
  replayable: boolean
  stored_report: Report
  replayed_report: Report
}

const ENDPOINT = '/api/cohort_extraction'

const rule = reactive({ region: '华东', min_ltv: 1000, exclude_tags: '促销排除' })
const evaluation = ref<Evaluation | null>(null)
const runs = ref<Run[]>([])
const replay = ref<Replay | null>(null)
const backfillValues = reactive<Record<string, string>>({})
const extracting = ref(false)
const errorMessage = ref('')
const notice = ref('')

const statCards = computed(() => {
  const e = evaluation.value
  return [
    { key: 'scope', label: '核实范围', value: e?.verification_scope ?? 0 },
    { key: 'delivered', label: '可交付人数', value: e?.delivered_count ?? 0 },
    { key: 'pending', label: '待判人数', value: e?.pending_count ?? 0 },
    { key: 'excluded', label: '明确排除', value: e?.excluded_count ?? 0 },
    { key: 'usable', label: '可用名单', value: e ? (e.usable ? '有' : '无') : '—' },
  ]
})

function formatDistribution(dist?: Record<string, number>) {
  if (!dist || !Object.keys(dist).length) return '—'
  return Object.entries(dist).map(([key, value]) => `${key}:${value}`).join('，')
}

function queryString() {
  const params = new URLSearchParams()
  params.set('region', rule.region)
  params.set('min_ltv', String(rule.min_ltv))
  if (rule.exclude_tags.trim()) params.set('exclude_tags', rule.exclude_tags)
  return params.toString()
}

async function loadPreview() {
  errorMessage.value = ''
  notice.value = ''
  try {
    const response = await request(`${ENDPOINT}/preview?${queryString()}`)
    if (!response.ok) throw new Error('试算失败，请稍后重试')
    evaluation.value = (await response.json()) as Evaluation
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '试算失败'
  }
}

async function submitExtraction() {
  extracting.value = true
  errorMessage.value = ''
  notice.value = ''
  try {
    const response = await request(`${ENDPOINT}/extract`, {
      method: 'POST',
      body: JSON.stringify({
        region: rule.region,
        min_ltv: Number(rule.min_ltv),
        exclude_tags: rule.exclude_tags,
        label: `第${runs.value.length + 1}轮`,
      }),
    })
    const payload = await response.json()
    if (!response.ok || !payload.ok) throw new Error(payload.message || '提交提取失败')
    evaluation.value = payload.entry
    replay.value = null
    notice.value = `${payload.message}：交付 ${payload.entry.delivered_count} 人，待判 ${payload.entry.pending_count} 人`
    await loadRuns()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '提交提取失败'
  } finally {
    extracting.value = false
  }
}

async function submitBackfill(userId: string) {
  const tag = (backfillValues[userId] ?? '').trim()
  if (!tag) {
    errorMessage.value = `请先填写顾客 ${userId} 的判定标签`
    return
  }
  errorMessage.value = ''
  notice.value = ''
  try {
    const response = await request(`${ENDPOINT}/tags`, {
      method: 'POST',
      body: JSON.stringify({ user_id: userId, tag }),
    })
    const payload = await response.json()
    if (!response.ok || !payload.ok) throw new Error(payload.message || '补齐标签失败')
    notice.value = `${payload.message}，可重新试算或提交下一轮提取`
    backfillValues[userId] = ''
    await loadPreview()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '补齐标签失败'
  }
}

async function replayRun(runId: string) {
  errorMessage.value = ''
  notice.value = ''
  try {
    const response = await request(`${ENDPOINT}/runs/${runId}/replay`, { method: 'POST' })
    const payload = await response.json()
    if (!response.ok) throw new Error(payload.message || '重放失败')
    replay.value = payload.entry as Replay
    if (!payload.ok) errorMessage.value = payload.message
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '重放失败'
  }
}

async function loadRuns() {
  try {
    const response = await request(`${ENDPOINT}/runs`)
    if (!response.ok) throw new Error('提取轮次读取失败')
    const payload = await response.json()
    runs.value = payload.items ?? []
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '提取轮次读取失败'
  }
}

onMounted(async () => {
  await Promise.all([loadPreview(), loadRuns()])
})
</script>

<style scoped>
.section-title { font-size: 14px; margin: 18px 0 8px; }
.section-title small { color: var(--muted); font-weight: normal; }
.scope-note { font-size: 13px; color: var(--muted); margin: 0 0 12px; }
.badge { display: inline-block; border-radius: 10px; padding: 1px 8px; font-size: 12px; }
.badge-delivered { background: #dcfce7; color: #166534; }
.badge-pending { background: #fef3c7; color: #92400e; }
.badge-excluded { background: #fee2e2; color: #991b1b; }
.replay-panel { margin-top: 16px; padding: 12px; border: 1px solid var(--border); border-radius: 8px; background: #fff; }
.replay-panel h4 { margin: 0 0 8px; font-size: 14px; }
.ok-text { color: #15803d; }
.row-actions input { width: 90px; padding: 4px 6px; border: 1px solid var(--border); border-radius: 6px; }
</style>
