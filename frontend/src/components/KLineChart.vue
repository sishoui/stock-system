<script setup>
// ECharts 深色 K 线 + MA20 + 量能 + 信号/AI 标注（spec §10）
import { onMounted, onUnmounted, ref, watch } from 'vue'
import * as echarts from 'echarts'

const props = defineProps({
  kline: { type: Array, default: () => [] },   // [{date,open,high,low,close,volume,ma20}]
  markers: { type: Array, default: () => [] }, // [{date, text, value}]
})
const el = ref(null)
let chart = null

const UP = '#e14b4b', DOWN = '#3ecf8e'

function render() {
  if (!chart || !props.kline.length) return
  const k = props.kline
  const dates = k.map(b => b.date)
  const ma20 = k.map(b => b.ma20 ?? null)
  const chartMark = props.markers.map(m => ({
    coord: [m.date, m.value],
    value: m.text,
    symbol: 'pin', symbolSize: 42,
    itemStyle: { color: m.ai === 'positive' ? UP : '#e8a33d' },
    label: { show: true, formatter: m.text, fontSize: 9, color: '#fff' },
  }))
  chart.setOption({
    backgroundColor: 'transparent',
    animation: false,
    tooltip: { trigger: 'axis', axisPointer: { type: 'cross' } },
    axisPointer: { link: [{ xAxisIndex: 'all' }] },
    grid: [
      { left: 60, right: 20, top: 16, height: '58%' },
      { left: 60, right: 20, top: '76%', height: '18%' },
    ],
    xAxis: [
      { type: 'category', data: dates, gridIndex: 0, axisLine: { lineStyle: { color: '#2e3342' } }, axisLabel: { show: false } },
      { type: 'category', data: dates, gridIndex: 1, axisLine: { lineStyle: { color: '#2e3342' } }, axisLabel: { color: '#7b8397' } },
    ],
    yAxis: [
      { scale: true, gridIndex: 0, splitLine: { lineStyle: { color: '#222633' } }, axisLabel: { color: '#7b8397' } },
      { gridIndex: 1, splitLine: { show: false }, axisLabel: { show: false } },
    ],
    series: [
      {
        name: 'K线', type: 'candlestick', data: k.map(b => [b.open, b.close, b.low, b.high]),
        itemStyle: { color: UP, color0: DOWN, borderColor: UP, borderColor0: DOWN },
        markPoint: { data: chartMark },
      },
      {
        name: 'MA20', type: 'line', data: ma20, showSymbol: false,
        lineStyle: { color: '#5b8cff', width: 1.4 },
      },
      {
        name: '成交量', type: 'bar', xAxisIndex: 1, yAxisIndex: 1,
        data: k.map(b => ({
          value: b.volume,
          itemStyle: { color: b.close >= b.open ? UP : DOWN, opacity: 0.75 },
        })),
      },
    ],
  })
}

onMounted(() => {
  chart = echarts.init(el.value)
  render()
  window.addEventListener('resize', onResize)
})
function onResize() { chart?.resize() }
onUnmounted(() => { window.removeEventListener('resize', onResize); chart?.dispose() })
watch(() => [props.kline, props.markers], render, { deep: true })
</script>

<template>
  <div ref="el" class="chart" />
</template>
