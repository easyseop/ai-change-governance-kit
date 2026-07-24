interface Task { void exec(); }
class Vault { void transfer() { int value = 1; } }
class Flow {
    Task task;
    { task = () -> new Vault().transfer(); }
    void sink() { task.exec(); }
}
