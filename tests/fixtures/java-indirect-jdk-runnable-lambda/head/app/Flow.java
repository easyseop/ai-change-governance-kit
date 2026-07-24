class Vault { void transfer() { int value = 2; } }
class Flow {
    void sink() { schedule(() -> new Vault().transfer()); }
    void schedule(Runnable task) { task.run(); }
}
