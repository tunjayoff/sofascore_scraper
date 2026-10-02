/**
 * Polling only while the browser tab is visible (05-web-ui.md 5.1). `task` runs at once, then again
 * `delay()` ms after the previous run ended (so a slow answer never stacks requests), and at once when
 * the tab becomes visible again. `delay` can depend on the last outcome (back-off after a failure).
 */
export function poll(task: () => Promise<unknown>, delay: () => number): () => void {
  let timer: ReturnType<typeof setTimeout> | null = null
  let running = false
  let stopped = false

  const visible = () => typeof document === 'undefined' || document.visibilityState !== 'hidden'

  async function run() {
    if (stopped || running) return
    if (timer) clearTimeout(timer)
    timer = null
    running = true
    try {
      await task()
    } catch {
      /* the task keeps its own error state */
    } finally {
      running = false
      if (!stopped && visible()) timer = setTimeout(run, delay())
    }
  }

  function onVisibility() {
    if (visible()) void run()
    else if (timer) {
      clearTimeout(timer)
      timer = null
    }
  }

  document.addEventListener('visibilitychange', onVisibility)
  void run()
  return () => {
    stopped = true
    if (timer) clearTimeout(timer)
    document.removeEventListener('visibilitychange', onVisibility)
  }
}
