class Vault { void transfer() { int value = 2; } }
class Flow {
    static Runnable task = () -> new Vault().transfer();
    void sink() { task.run(); }
}
