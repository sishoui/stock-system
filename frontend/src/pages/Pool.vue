<script setup>
// 股票池（spec §10 页面 2）：打分分项、总分、AI 标签、信号强度、入池记录；点击进详情
import { onMounted, onUnmounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { get, poll } from '../api'

const router = useRouter()
const pool = ref(null)
const sort = ref('rank')
const order = ref('asc')
let stop = null

async function load() {
  pool.value = await get(`/pool?sort=${sort.value}&order=${order.value}`)
}
function resort(col) {
  if (sort.value === col) order.value = order.value === 'asc' ? 'desc' : 'asc'
  else { sort.value = col; order.value = 'asc' }
  load()
}
const aiTag = item => item.ai_status === 'not_assessed'
  ? { text: '未评估', cls: '' }
  : { text: `${item.ai.authenticity.verdict} / ${item.ai.sustainability.score}分`, cls: item.ai.authenticity.verdict === 'pass' ? 'up' : 'down' }

onMounted(async () => { await load(); stop = poll(load, 30000) })
onUnmounted(() => stop?.())
</script>

<template>
  <div class="panel">
    <h3>股票池（{{ pool?.total ?? '…' }} 只）</h3>
    <table class="data" v-if="pool">
      <thead>
        <tr>
          <th @click="resort('rank')">代码</th>
          <th>名称</th>
          <th @click="resort('score')">总分 ⇅</th>
          <th>入池日</th>
          <th>AI 评估</th>
          <th>信号强度</th>
          <th>信号理由</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="it in pool.items" :key="it.code" @click="router.push(`/stocks/${it.code}`)">
          <td>{{ it.code }}</td>
          <td>{{ it.name }}</td>
          <td>{{ it.score.toFixed(1) }}</td>
          <td class="muted">{{ it.entered_at }}</td>
          <td><span :class="aiTag(it).cls">{{ aiTag(it).text }}</span></td>
          <td><span class="badge" :class="it.signal_level">{{ { strong: '强', medium: '中', weak: '弱' }[it.signal_level] }}</span></td>
          <td class="muted">{{ it.signal_reasons[0] ?? '—' }}</td>
        </tr>
      </tbody>
    </table>
  </div>
</template>
