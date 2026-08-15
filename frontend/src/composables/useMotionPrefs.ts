import { useReducedMotion } from 'motion-v'

/** Shared enter spring — zeroed when prefers-reduced-motion. */
export function useMotionPrefs() {
  const reduce = useReducedMotion()

  function enter(y = 14) {
    if (reduce.value) {
      return {
        initial: false as const,
        animate: { opacity: 1, y: 0 },
        transition: { duration: 0 },
      }
    }
    return {
      initial: { opacity: 0, y },
      animate: { opacity: 1, y: 0 },
      transition: { duration: 0.32, ease: [0.22, 1, 0.36, 1] as const },
    }
  }

  function stagger(i: number, base = 0.04) {
    if (reduce.value) return { duration: 0 }
    return { delay: i * base, duration: 0.35, ease: [0.22, 1, 0.36, 1] as const }
  }

  return { reduce, enter, stagger }
}
