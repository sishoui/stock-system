<script setup>
import { computed } from 'vue'

const props = defineProps({
  sparkline: { type: Object, default: () => ({ dates: [], nav: [] }) },
  height: { type: Number, default: 140 },
})
const points = computed(() => {
  const { dates, nav } = props.sparkline
  if (!nav.length) return ''
  const w = 300, h = props.height - 20
  const min = Math.min(...nav), max = Math.max(...nav)
  const span = max - min || 1
  return nav.map((v, i) =>
    `${(i / Math.max(nav.length - 1, 1)) * w},${h - ((v - min) / span) * h}`
  ).join(' ')
})
const rising = computed(() => {
  const nav = props.sparkline.nav
  return nav.length > 1 ? nav[nav.length - 1] >= nav[0] : true
})
</script>

<template>
  <svg :height="height" width="100%" preserveAspectRatio="none" viewBox="0 0 300 120">
    <polyline :points="points" fill="none" :stroke="rising ? '#e14b4b' : '#3ecf8e'" stroke-width="1.6" />
  </svg>
</template>
