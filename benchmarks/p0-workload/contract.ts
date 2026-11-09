export type Recipe = {
  duration: number;
  rate: number;
  burstAfter: number;
  burstRate: number;
  burstDuration: number;
};

export function arrivals(recipe: Recipe): number[] {
  const { duration, rate, burstAfter, burstRate, burstDuration } = recipe;
  if (
    ![duration, rate, burstRate].every((n) => Number.isFinite(n) && n > 0) ||
    ![burstAfter, burstDuration].every((n) => Number.isFinite(n) && n >= 0) ||
    burstAfter + burstDuration > duration
  )
    throw new Error("invalid workload recipe");
  const slots: number[] = [];
  for (const [start, end, hz] of [
    [0, burstAfter, rate],
    [burstAfter, burstAfter + burstDuration, burstRate],
    [burstAfter + burstDuration, duration, rate],
  ]) {
    for (let index = 0; start + index / hz < end - 1e-9; index++) {
      slots.push(start + index / hz);
    }
  }
  return slots;
}

export function missingMarkers(content: string, expected: readonly string[]): string[] {
  return expected.filter((marker) => !content.includes(marker));
}

export function workloadFailures(planned: number, completed: number, errors: number, rejected: number): string[] {
  return [
    ...(completed !== planned ? [`completed:${completed}/${planned}`] : []),
    ...(errors ? [`operation-errors:${errors}`] : []),
    ...(rejected ? [`rejected-arrivals:${rejected}`] : []),
  ];
}
