<script setup>
// 回测（spec §10 页面 5）：发起回测、指标报表、净值 vs 沪深 300、逐笔明细
import { onMounted, ref } from 'vue'
import * as echarts from 'echarts'
import { get, post } from '../api'

const report = ref(null)
const running = ref(false)
const form = ref({ start: '2021-01-01', end: '2025-12-31', initial_capital: 200000 })
const el = ref(null)
let chart = null

async function run() {
  running.value = true
  try {
    const summary = await post('/backtests', form.value)
    report.value = await get(`/backtests/${summary.id}`)
    draw()
  } finally {
    running.value = false
  }
}

function draw() {
  if (!report.value) return
  chart ??= echarts.init(el.value)
  const r = report.value
  chart.setOption({
    backgroundColor: 'transparent',
    animation: false,
    tooltip: { trigger: 'axis' },
    legend: { data: ['策略净值', '沪深300'], textStyle: { color: '#7b8397' } },
    grid: { left: 60, right: 20, top: 30, bottom: 30 },
    xAxis: { type: 'category', data: r.nav.dates, axisLabel: { color: '#7b8397' } },
    yAxis: { scale: true, splitLine: { lineStyle: { color: '#222633' } }, axisLabel: { color: '#7b8397' } },
    series: [
      { name: '策略净值', type: 'line', data: r.nav.nav, showSymbol: false, lineStyle: { color: '#e14b4b' } },
      { name: '沪深300', type: 'line', data: r.nav.benchmark, showSymbol: false, lineStyle: { color: '#5b8cff' } },
    ],
  })
}

const pctF = v => (v * 100).toFixed(2) + '%'
onMounted(() => { window.addEventListener('resize', () => chart?.resize()) })
</script>

<template>
  <div class="panel">
    <h3>发起回测</h3>
    <div style="display:flex; gap:10px; align-items:center; flex-wrap:wrap">
      <label>开始 <input v-model="form.start" /></label>
      <label>结束 <input v-model="form.end" /></label>
      <label>初始资金 <input v-model.number="form.initial_capital" style="width:100px" /></label>
      <button class="btn" :disabled="running" @click="run">{{ running ? '回测中…' : '运行回测' }}</button>
    </div>
  </div>

  <template v-if="report">
    <div class="panel">
      <h3>指标报表（回测 #{{ report.id }}，{{ report.start }} ~ {{ report.end }}）</h3>
      <table class="data">
        <tbody>
          <tr><td>总收益</td><td :class="report.metrics.total_return >= 0 ? 'up' : 'down'">{{ pctF(report.metrics.total_return) }}</td></tr>
          <tr><td>年化收益</td><td>{{ pctF(report.metrics.annualized_return) }}</td></tr>
          <tr><td>最大回撤</td><td class="down">{{ pctF(report.metrics.max_drawdown) }}
            <span class="muted">（{{ report.metrics.dd_peak_date }} ~ {{ report.metrics.dd_trough_date }}）</span></td></tr>
          <tr><td>夏普</td><td>{{ report.metrics.sharpe.toFixed(2) }}</td></tr>
          <tr><td>胜率（按笔）</td><td>{{ pctF(report.metrics.win_rate) }}</td></tr>
          <tr><td>盈亏比</td><td>{{ report.metrics.profit_loss_ratio.toFixed(2) }}</td></tr>
          <tr><td>单笔最大亏损</td><td>{{ pctF(report.metrics.max_single_loss) }}</td></tr>
          <tr><td>年化换手</td><td>{{ report.metrics.turnover_annualized.toFixed(2) }}x</td></tr>
          <tr><td>沪深300 收益</td><td>{{ pctF(report.metrics.benchmark_total_return) }}</td></tr>
          <tr><td>超额收益</td><td :class="report.metrics.excess_return >= 0 ? 'up' : 'down'">{{ pctF(report.metrics.excess_return) }}</td></tr>
          <tr><td>相对回撤</td><td>{{ pctF(report.metrics.relative_drawdown) }}</td></tr>
        </tbody>
      </table>
    </div>

    <div class="panel">
      <h3>净值 vs 沪深 300</h3>
      <div ref="el" class="chart" />
    </div>

    <div class="panel">
      <h3>逐笔明细（{{ report.trades.length }} 笔）</h3>
      <table class="data">
        <thead>
          <tr><th>代码</th><th>买入日</th><th>买入价</th><th>卖出日</th><th>卖出价</th>
              <th>数量</th><th>盈亏</th><th>原因</th></tr>
        </thead>
        <tbody>
          <tr v-for="(t, i) in report.trades" :key="i">
            <td>{{ t.code }}</td>
            <td>{{ t.entry_date }}</td>
            <td>{{ t.entry_price.toFixed(2) }}</td>
            <td>{{ t.exit_date ?? '持仓中' }}</td>
            <td>{{ t.exit_price?.toFixed(2) ?? '—' }}</td>
            <td>{{ t.shares }}</td>
            <td :class="t.pnl >= 0 ? 'up' : 'down'">{{ t.pnl.toFixed(0) }}（{{ (t.pnl_pct * 100).toFixed(2) }}%）</td>
            <td class="muted">{{ t.reason }}</td>
          </tr>
          <tr v-if="!report.trades.length"><td colspan="8" class="muted">无成交</td></tr>
        </tbody>
      </table>
    </div>
  </template>
</template>
