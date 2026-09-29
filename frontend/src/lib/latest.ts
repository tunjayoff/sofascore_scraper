/**
 * Only the most recent call of an async loader may apply its result: take a token before
 * the await and drop the response if a newer call started meanwhile (fast filter, page or
 * route changes otherwise render an older response over a newer one).
 */
export function latestOnly() {
  let current = 0
  return {
    next: () => ++current,
    isCurrent: (token: number) => token === current,
  }
}
