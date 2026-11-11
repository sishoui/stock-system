import { createApp } from 'vue'
import { createRouter, createWebHashHistory } from 'vue-router'
import App from './App.vue'
import Dashboard from './pages/Dashboard.vue'
import Pool from './pages/Pool.vue'
import StockDetail from './pages/StockDetail.vue'
import Positions from './pages/Positions.vue'
import Backtest from './pages/Backtest.vue'
import Paper from './pages/Paper.vue'
import './style.css'

const router = createRouter({
  history: createWebHashHistory(),
  routes: [
    { path: '/', name: 'dashboard', component: Dashboard },
    { path: '/pool', name: 'pool', component: Pool },
    { path: '/stocks/:code', name: 'stock', component: StockDetail },
    { path: '/positions', name: 'positions', component: Positions },
    { path: '/backtests', name: 'backtests', component: Backtest },
    { path: '/paper', name: 'paper', component: Paper },
  ],
})

createApp(App).use(router).mount('#app')
