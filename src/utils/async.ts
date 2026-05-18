export const withTimeout = async <T,>(promise: Promise<T>, timeoutMs: number, label: string): Promise<T> => {
  let timer: number | null = null;
  try {
    return await Promise.race<T>([
      promise,
      new Promise<T>((_, reject) => {
        timer = window.setTimeout(() => {
          reject(new Error(`${label} timed out after ${Math.round(timeoutMs / 1000)}s`));
        }, timeoutMs);
      }),
    ]);
  } finally {
    if (timer !== null) {
      window.clearTimeout(timer);
    }
  }
};
