export class AutomaticGuardianScheduler {
  private readonly states = new Map<string, { running: boolean; pending: boolean; cancelled: boolean; unavailableUntil: number }>();

  constructor(
    private readonly modelAvailable: () => Promise<boolean>,
    private readonly evaluate: (deviceId: string, useModel: boolean) => Promise<void>,
    private readonly cooldownMs = 30_000,
  ) {}

  schedule(deviceId: string): void {
    let state = this.states.get(deviceId);
    if (!state) {
      state = { running: false, pending: false, cancelled: false, unavailableUntil: 0 };
      this.states.set(deviceId, state);
    }
    state.pending = true;
    if (state.running) return;
    state.running = true;
    void this.drain(deviceId, state);
  }

  cancel(deviceId: string): void {
    const state = this.states.get(deviceId);
    if (!state) return;
    state.cancelled = true;
    if (!state.running) this.states.delete(deviceId);
  }

  clear(): void {
    for (const state of this.states.values()) state.cancelled = true;
    this.states.clear();
  }

  size(): number {
    return this.states.size;
  }

  private async drain(deviceId: string, state: { running: boolean; pending: boolean; cancelled: boolean; unavailableUntil: number }): Promise<void> {
    try {
      while (state.pending && !state.cancelled) {
        state.pending = false;
        let useModel = Date.now() >= state.unavailableUntil;
        if (useModel) {
          try { useModel = await this.modelAvailable(); } catch { useModel = false; }
          if (!useModel) state.unavailableUntil = Date.now() + this.cooldownMs;
        }
        if (!state.cancelled) await this.evaluate(deviceId, useModel);
      }
    } finally {
      state.running = false;
      if (state.cancelled && this.states.get(deviceId) === state) this.states.delete(deviceId);
    }
  }
}
