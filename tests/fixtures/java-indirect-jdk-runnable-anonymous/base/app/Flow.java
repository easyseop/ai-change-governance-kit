class Vault { void transfer() { int value = 1; } }
class Flow {
    void sink() {
        schedule(new Runnable() {
            public void run() { new Vault().transfer(); }
        });
    }
    void schedule(Runnable task) { task.run(); }
}
