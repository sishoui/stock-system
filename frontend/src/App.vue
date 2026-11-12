<script setup>
import { useRouter } from 'vue-router'

const router = useRouter()
</script>

<template>
  <header class="topbar">
    <span class="brand">A股日线选股系统</span>
    <nav>
      <RouterLink to="/">仪表盘</RouterLink>
      <RouterLink to="/pool">股票池</RouterLink>
      <RouterLink to="/positions">持仓与交易</RouterLink>
      <RouterLink to="/backtests">回测</RouterLink>
      <RouterLink to="/paper">模拟盘</RouterLink>
      <RouterLink to="/settings">AI 配置</RouterLink>
    </nav>
  </header>
  <RouterView />
</template>
