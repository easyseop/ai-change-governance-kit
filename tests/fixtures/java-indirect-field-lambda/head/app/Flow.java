interface Task { void exec(); }
class Vault { void transfer() { int value = 2; } }
class Flow {
    Task task = () -> new Vault().transfer();
    void sink() { task.exec(); }
}
