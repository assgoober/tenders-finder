/** Wall clock behind one function, so a route test can move time. */
export const clock = { now: (): number => Date.now() };
