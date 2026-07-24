interface Task { void exec(); }
class Vault { void transfer() { int value = 1; } }
class Flow {
    Task task;
    void sink() { task.exec(); }
    void wire(Vault vault) {
        task = () -> {
            Task inner = vault::transfer;
            inner.exec();
        };
    }
}
