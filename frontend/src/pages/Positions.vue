<script setup>
// 持仓与交易（spec §10 页面 4）：持仓（仓位/浮盈/止损止盈线及距离）、待执行指令、历史成交
import { onMounted, onUnmounted, ref } from 'vue'
import { get, poll } from '../api'

const positions = ref(null)
const orders = ref(null)
const trades = ref(null)
let stop = null

onMounted(async () => {
  const load = async () => {
    ;[positions.value, orders.value, trades.value] = await Promise.all([
      get('/positions'), get('/orders'), get('/trades'),
    ])
  }
  await load()
  stop = poll(load, 30000)
})
onUnmounted(() => stop?.())

const cls = v => (v > 0 ? 'up' : v < 0 ? 'down' : 'flat')
const sign = v => (v > 0 ? '+' : '') + v.toFixed(2)
</script>

<template>
  <div class="panel">
    <h3>当前持仓（{{ positions?.total ?? '…' }}）</h3>
    <table class="data" v-if="positions">
      <thead>
        <tr><th>代码</th><th>数量</th><th>成本</th><th>市值</th><th>浮盈</th><th>仓位</th>
            <th>止损线</th><th>止盈线</th><th>距止损</th></tr>
      </thead>
      <tbody>
        <tr v-for="p in positions.items" :key="p.code">
          <td>{{ p.code }}</td>
          <td>{{ p.shares }}</td>
          <td>{{ p.entry_price.toFixed(2) }}</td>
          <td>{{ p.market_value.toFixed(0) }}</td>
          <td :class="cls(p.pnl)">{{ sign(p.pnl) }}（{{ sign(p.pnl_pct * 100) }}%）</td>
          <td>{{ (p.weight * 100).toFixed(1) }}%</td>
          <td>{{ p.stop_line?.toFixed(2) ?? '—' }}</td>
          <td>{{ p.trail_line?.toFixed(2) ?? '—' }}</td>
          <td :class="cls(p.stop_distance_pct ?? 0)">{{ p.stop_distance_pct?.toFixed(1) ?? '—' }}%</td>
        </tr>
        <tr v-if="!positions.items.length"><td colspan="9" class="muted">空仓</td></tr>
      </tbody>
    </table>
  </div>

  <div class="panel">
    <h3>待执行指令（次日开盘，{{ orders?.total ?? '…' }}）</h3>
    <table class="data" v-if="orders">
      <thead><tr><th>代码</th><th>动作</th><th>执行日</th><th>权重</th><th>原因</th><th>卖出线</th></tr></thead>
      <tbody>
        <tr v-for="(o, i) in orders.items" :key="i">
          <td>{{ o.code }}</td>
          <td :class="o.action === 'BUY' ? 'up' : 'down'">{{ o.action }}</td>
          <td>{{ o.exec_date }}</td>
          <td>{{ (o.weight * 100).toFixed(1) }}%</td>
          <td class="muted">{{ o.reason }}</td>
          <td>{{ o.stop_line?.toFixed(2) ?? '—' }}</td>
        </tr>
        <tr v-if="!orders.items.length"><td colspan="6" class="muted">无</td></tr>
      </tbody>
    </table>
  </div>

  <div class="panel">
    <h3>历史成交（{{ trades?.total ?? '…' }}）</h3>
    <table class="data" v-if="trades">
      <thead>
        <tr><th>日期</th><th>代码</th><th>动作</th><th>价格</th><th>数量</th><th>金额</th>
            <th>费用</th><th>盈亏</th><th>原因</th></tr>
      </thead>
      <tbody>
        <tr v-for="t in trades.items" :key="t.exec_date + t.code + t.action">
          <td>{{ t.exec_date }}</td>
          <td>{{ t.code }}</td>
          <td :class="t.action === 'BUY' ? 'up' : 'down'">{{ t.action }}</td>
          <td>{{ t.price.toFixed(2) }}</td>
          <td>{{ t.shares }}</td>
          <td>{{ t.amount.toFixed(0) }}</td>
          <td>{{ t.fee.toFixed(2) }}</td>
          <td :class="cls(t.pnl)">{{ t.action === 'SELL' ? sign(t.pnl) + '（' + sign(t.pnl_pct * 100) + '%）' : '—' }}</td>
          <td class="muted">{{ t.reason }}</td>
        </tr>
        <tr v-if="!trades.items.length"><td colspan="9" class="muted">无</td></tr>
      </tbody>
    </table>
  </div>
</template>
