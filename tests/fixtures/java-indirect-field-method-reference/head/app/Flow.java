interface Task { void exec(); }
class Vault { void transfer() { int value = 2; } }
class Flow {
    Vault vault = new Vault();
    Task task = vault::transfer;
    void sink() { task.exec(); }
}
