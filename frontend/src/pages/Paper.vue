<script setup>
// 模拟盘（issue #26）：净值曲线、指标（胜率/回撤）、持仓明细，复用回测展示口径
import { onMounted, onUnmounted, ref } from 'vue'
import * as echarts from 'echarts'
import { get, poll } from '../api'

const report = ref(null)
const el = ref(null)
let chart = null

async function load() {
  report.value = await get('/paper')
  draw()
}

function draw() {
  if (!report.value) return
  chart ??= echarts.init(el.value)
  const r = report.value
  chart.setOption({
    backgroundColor: 'transparent',
    animation: false,
    tooltip: { trigger: 'axis' },
    grid: { left: 60, right: 20, top: 30, bottom: 30 },
    xAxis: { type: 'category', data: r.nav.dates, axisLabel: { color: '#7b8397' } },
    yAxis: { scale: true, splitLine: { lineStyle: { color: '#222633' } }, axisLabel: { color: '#7b8397' } },
    series: [
      { name: '模拟盘净值', type: 'line', data: r.nav.nav, showSymbol: false, lineStyle: { color: '#e14b4b' } },
    ],
  })
}

const pctF = v => (v * 100).toFixed(2) + '%'
onMounted(() => {
  load()
  poll(load, 60000)
  window.addEventListener('resize', () => chart?.resize())
})
onUnmounted(() => chart?.dispose())
</script>

<template>
  <div class="panel">
    <h3>模拟盘（起始资金 {{ report?.initial_capital?.toLocaleString() ?? '200,000' }} 元）</h3>
    <div class="muted" v-if="!report">加载中…</div>
    <table class="data" v-else>
      <tbody>
        <tr><td>总收益</td><td :class="report.metrics.total_return >= 0 ? 'up' : 'down'">{{ pctF(report.metrics.total_return) }}</td></tr>
        <tr><td>最大回撤</td><td class="down">{{ pctF(report.metrics.max_drawdown) }}</td></tr>
        <tr><td>胜率（按笔）</td><td>{{ pctF(report.metrics.win_rate) }}</td></tr>
        <tr><td>盈亏比</td><td>{{ report.metrics.profit_loss_ratio?.toFixed(2) }}</td></tr>
        <tr><td>夏普</td><td>{{ report.metrics.sharpe?.toFixed(2) }}</td></tr>
      </tbody>
    </table>
  </div>

  <div class="panel" v-if="report">
    <h3>净值曲线</h3>
    <div ref="el" class="chart" />
  </div>

  <div class="panel" v-if="report">
    <h3>持仓明细（{{ report.positions.length }} 只）</h3>
    <table class="data">
      <thead><tr><th>代码</th><th>数量</th><th>买入价</th><th>买入日</th><th>最高收盘</th><th>权重</th></tr></thead>
      <tbody>
        <tr v-for="p in report.positions" :key="p.code">
          <td>{{ p.code }}</td><td>{{ p.shares }}</td><td>{{ p.entry_price.toFixed(2) }}</td>
          <td>{{ p.entry_date }}</td><td>{{ p.highest_close.toFixed(2) }}</td>
          <td>{{ pctF(p.weight) }}</td>
        </tr>
        <tr v-if="!report.positions.length"><td colspan="6" class="muted">空仓</td></tr>
      </tbody>
    </table>
  </div>
</template>
