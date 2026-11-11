// 轻量 API 客户端：fetch + 轮询工具（spec §10：15~60s 轮询）
const BASE = import.meta.env.VITE_API_BASE ?? '/api'

export async function get(path) {
  const resp = await fetch(`${BASE}${path}`)
  if (!resp.ok) throw new Error(`GET ${path} → ${resp.status}`)
  return resp.json()
}

export async function post(path, body) {
  const resp = await fetch(`${BASE}${path}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
  if (!resp.ok) throw new Error(`POST ${path} → ${resp.status}`)
  return resp.json()
}

/** 轮询：立即执行一次，之后每 intervalMs 一次。返回停止函数。 */
export function poll(fn, intervalMs = 30000) {
  let timer = null
  let stopped = false
  const tick = async () => {
    try { await fn() } catch (e) { console.error(e) }
    if (!stopped) timer = setTimeout(tick, intervalMs)
  }
  tick()
  return () => { stopped = true; clearTimeout(timer) }
}
