<script setup>
// 仪表盘（spec §10 页面 1）：个股 K 线主区 + 行动摘要/估值分位/净值 sparkline + 系统状态 + RiskConfig 只读
import { onMounted, onUnmounted, ref } from 'vue'
import { get, poll } from '../api'
import KLineChart from '../components/KLineChart.vue'
import Sparkline from '../components/Sparkline.vue'

const dashboard = ref(null)
const pool = ref(null)
const detail = ref(null)
const selected = ref('')
const riskConfig = ref(null)
let stopFns = []

async function loadDashboard() {
  dashboard.value = await get('/dashboard')
}
async function loadDetail() {
  if (!selected.value) return
  detail.value = await get(`/stocks/${selected.value}`)
}
function select(code) {
  selected.value = code
  loadDetail()
}

onMounted(async () => {
  stopFns.push(poll(loadDashboard, 30000))
  pool.value = await get('/pool')
  riskConfig.value = await get('/risk-config')
  if (pool.value.items.length) {
    selected.value = pool.value.items[0].code
    await loadDetail()
  }
  stopFns.push(poll(loadDetail, 45000))
})
onUnmounted(() => stopFns.forEach(f => f()))

const fmtPct = v => v == null ? '—' : (v * 100).toFixed(1) + '%'
const pctColor = v => (v >= 0 ? 'up' : v < 0 ? 'down' : 'flat')
</script>

<template>
  <div class="grid-2">
    <div>
      <div class="panel" v-if="detail">
        <h3>
          {{ detail.name }}（{{ detail.code }})
          <span class="muted" style="margin-left:12px">
            信号：<span class="badge" :class="detail.signal.level">{{ { strong: '强', medium: '中', weak: '弱' }[detail.signal.level] }}</span>
          </span>
        </h3>
        <KLineChart :kline="detail.kline"
          :markers="detail.kline.length ? [{ date: detail.kline.at(-1).date, value: detail.kline.at(-1).close, text: detail.signal.level === 'strong' ? '信号' : '', ai: detail.ai?.sustainability?.trend }] : []" />
        <div class="muted" v-if="detail.signal.reasons.length">{{ detail.signal.reasons.join('；') }}</div>
      </div>
      <div class="panel" v-else><h3>加载中…</h3></div>

      <!-- 持仓与风控行 -->
      <div class="panel">
        <h3>持仓与风控</h3>
        <div class="muted" v-if="!dashboard">加载中…</div>
        <template v-else>
          <div class="muted" v-if="!dashboard.action_summary.sell_orders.length">当前无卖出指令</div>
          <div v-else>待卖出：{{ dashboard.action_summary.sell_orders.join('、') }}</div>
          <div :class="dashboard.action_summary.circuit_open ? 'down' : 'muted'">
            熔断状态：{{ dashboard.action_summary.circuit_open ? '已触发（暂停开仓）' : '正常' }}
          </div>
        </template>
      </div>
    </div>

    <div>
      <div class="panel">
        <h3>今日行动摘要</h3>
        <div v-if="dashboard">
          <div>可买：<span class="up">{{ dashboard.action_summary.buy_candidates.join('、') || '—' }}</span></div>
          <div>观察：<span class="muted">{{ dashboard.action_summary.watch.join('、') || '—' }}</span></div>
          <div>卖出：<span class="down">{{ dashboard.action_summary.sell_orders.join('、') || '—' }}</span></div>
        </div>
        <div class="muted" v-else>加载中…</div>
      </div>

      <div class="panel" v-if="detail">
        <h3>估值分位</h3>
        <div class="bar-row">
          <span class="label">PE 历史分位</span>
          <div class="bar-track"><div class="bar-fill" :style="{ width: (detail.valuation.pe_percentile ?? 0) * 100 + '%' }" /></div>
          <span>{{ detail.valuation.pe ?? '—' }}</span>
        </div>
        <div class="bar-row">
          <span class="label">PB 历史分位</span>
          <div class="bar-track"><div class="bar-fill" :style="{ width: (detail.valuation.pb_percentile ?? 0) * 100 + '%' }" /></div>
          <span>{{ detail.valuation.pb ?? '—' }}</span>
        </div>
      </div>

      <div class="panel" v-if="dashboard">
        <h3>组合净值</h3>
        <Sparkline :sparkline="dashboard.nav_sparkline" />
        <div class="muted">{{ dashboard.nav_sparkline.dates[0] }} ~ {{ dashboard.nav_sparkline.dates.at(-1) }}</div>
      </div>

      <div class="panel" v-if="dashboard">
        <h3>系统状态</h3>
        <div>数据任务：<span class="badge">{{ dashboard.system_status.data_health }}</span></div>
        <div>AI 任务：<span class="badge">{{ dashboard.system_status.ai_health }}</span></div>
      </div>

      <div class="panel" v-if="riskConfig">
        <h3>RiskConfig（只读）</h3>
        <table class="data">
          <thead><tr><th>参数</th><th>默认</th><th>区间</th></tr></thead>
          <tbody>
            <tr v-for="p in riskConfig.params" :key="p.name">
              <td>{{ p.name }}</td>
              <td>{{ p.default }}</td>
              <td class="muted">{{ p.min }} ~ {{ p.max }}</td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>
  </div>
</template>
