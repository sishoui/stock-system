<script setup>
// 个股详情（spec §10 页面 3）：K 线 + 估值分位 + 财务指标 + AI 三判断 + 信号状态
import { onMounted, onUnmounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { get, poll } from '../api'
import KLineChart from '../components/KLineChart.vue'

const route = useRoute()
const detail = ref(null)
let stop = null

async function load() {
  detail.value = await get(`/stocks/${route.params.code}`)
}
onMounted(async () => { await load(); stop = poll(load, 30000) })
onUnmounted(() => stop?.())
watch(() => route.params.code, load)

const pct = v => v == null ? '—' : (v * 100).toFixed(1) + '%'
const verdictMap = { pass: '通过', suspect: '存疑', fail: '失败', pending: '未评估' }
const trendMap = { improving: '改善', stable: '平稳', deteriorating: '恶化' }
const decayMap = { fast: '快', medium: '中', slow: '慢' }
</script>

<template>
  <template v-if="detail">
    <div class="grid-2">
      <div class="panel">
        <h3>{{ detail.name }}（{{ detail.code }}）</h3>
        <KLineChart :kline="detail.kline" />
        <div>当前信号：<span class="badge" :class="detail.signal.level">
          {{ { strong: '强', medium: '中', weak: '弱' }[detail.signal.level] }}</span>
          <span class="muted" style="margin-left:10px">{{ detail.signal.reasons.join('；') }}</span>
        </div>
      </div>
      <div>
        <div class="panel">
          <h3>估值与财务</h3>
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
          <table class="data" v-if="detail.finance" style="margin-top:10px">
            <tbody>
              <tr><td class="muted">报告期</td><td>{{ detail.finance.report_period }}</td></tr>
              <tr><td class="muted">ROE(TTM)</td><td>{{ detail.finance.roe_ttm ?? '—' }}%</td></tr>
              <tr><td class="muted">营收增速</td><td>{{ pct(detail.finance.revenue_growth) }}</td></tr>
              <tr><td class="muted">净利增速</td><td>{{ pct(detail.finance.profit_growth) }}</td></tr>
              <tr><td class="muted">毛利率</td><td>{{ pct(detail.finance.gross_margin) }}</td></tr>
              <tr><td class="muted">资产负债率</td><td>{{ detail.finance.debt_ratio ?? '—' }}%</td></tr>
            </tbody>
          </table>
        </div>

        <div class="panel">
          <h3>AI 三判断 <span v-if="detail.ai_status === 'not_assessed'" class="muted">（未评估）</span></h3>
          <div class="judge" v-if="detail.ai">
            <div><b>真伪</b>：{{ verdictMap[detail.ai.authenticity.verdict] }}
              （置信度 {{ (detail.ai.authenticity.confidence * 100).toFixed(0) }}%）</div>
            <div><b>时效</b>：有效期至 {{ detail.ai.timeliness.valid_until }}
              （衰减{{ decayMap[detail.ai.timeliness.decay_hint] }}）</div>
            <div><b>可持续性</b>：{{ detail.ai.sustainability.score }}/10
              （{{ trendMap[detail.ai.sustainability.trend] }}）</div>
            <div class="muted" v-if="detail.ai.evidence.length">
              依据：{{ detail.ai.evidence.map(e => e.source).join('、') }}</div>
          </div>
          <div class="muted" v-else>AI 挂掉或超预算，标为未评估（不阻塞入场）</div>
        </div>
      </div>
    </div>
  </template>
  <div class="panel" v-else><h3>加载中…</h3></div>
</template>
