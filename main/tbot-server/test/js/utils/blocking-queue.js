export default class BlockingQueue {
    #items   = [];
    #waiters = [];          // {resolve, reject, min, timer, onTimeout}

    /* empty queue one-shot gate */
    #emptyPromise = null;
    #emptyResolve = null;

    /* producer: putDatastuff in */
    enqueue(item, ...restItems) {
        if (restItems.length === 0) {
            this.#items.push(item);
        }
        // If hasExtra Parameters,BatchHandle allitem
        else {
            const items = [item, ...restItems].filter(i => i);
            if (items.length === 0) return;
            this.#items.push(...items);
        }
        // if empty queue gate exists, one-shotAllowallWaitperson
        if (this.#emptyResolve) {
            this.#emptyResolve();
            this.#emptyResolve = null;
            this.#emptyPromise = null;
        }

        // wake all waiting waiter
        this.#wakeWaiters();
    }

    /* consumer:min items or timeout ms whichever arrives first */
    async dequeue(min = 1, timeout = Infinity, onTimeout = null) {
        // 1. if empty, wait firstDataarrival (allCallshare same promise)
        if (this.#items.length === 0) {
            await this.#waitForFirstItem();
        }

        // satisfy immediately
        if (this.#items.length >= min) {
            return this.#flush();
        }

        // NeedWait
        return new Promise((resolve, reject) => {
            let timer = null;
            const waiter = { resolve, reject, min, onTimeout, timer };

            // timeoutLogic
            if (Number.isFinite(timeout)) {
                waiter.timer = setTimeout(() => {
                    this.#removeWaiter(waiter);
                    if (onTimeout) onTimeout(this.#items.length);
                    resolve(this.#flush());
                }, timeout);
            }

            this.#waiters.push(waiter);
        });
    }

    /* empty queue gateGeneratedevice */
    #waitForFirstItem() {
        if (!this.#emptyPromise) {
            this.#emptyPromise = new Promise(r => (this.#emptyResolve = r));
        }
        return this.#emptyPromise;
    }

    /* internal: every timeDataafter change,Checkwhich waiter satisfied */
    #wakeWaiters() {
        for (let i = this.#waiters.length - 1; i >= 0; i--) {
            const w = this.#waiters[i];
            if (this.#items.length >= w.min) {
                this.#removeWaiter(w);
                w.resolve(this.#flush());
            }
        }
    }

    #removeWaiter(waiter) {
        const idx = this.#waiters.indexOf(waiter);
        if (idx !== -1) {
            this.#waiters.splice(idx, 1);
            if (waiter.timer) clearTimeout(waiter.timer);
        }
    }

    #flush() {
        const snapshot = [...this.#items];
        this.#items.length = 0;
        return snapshot;
    }

    /* CurrentCacheLength(excludingWaitperson) */
    get length() {
        return this.#items.length;
    }

    /* Clearqueue (KeepObjectQuote, does not affectWaitperson) */
    clear() {
        this.#items.length = 0;
    }
}