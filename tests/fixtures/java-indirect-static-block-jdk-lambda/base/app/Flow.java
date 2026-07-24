class Vault { void transfer() { int value = 1; } }
class Flow {
    static Runnable task;
    static { task = () -> new Vault().transfer(); }
    void sink() { task.run(); }
}
