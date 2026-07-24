class Vault { void transfer() { int value = 2; } }
class Flow {
    Runnable task;
    void wire(Vault vault) { task = vault::transfer; }
    void sink() { task.run(); }
}
