interface Task { void exec(); }
class Vault { void transfer() { int value = 2; } }
class Flow {
    Task task;
    void sink() { task.exec(); }
    void wire(Vault vault) { task = vault::transfer; }
}
