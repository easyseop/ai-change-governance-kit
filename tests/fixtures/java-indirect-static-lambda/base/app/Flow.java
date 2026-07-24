interface Task { void exec(); }
class Vault { void transfer() { int value = 1; } }
class Flow {
    static Task task;
    static { task = () -> new Vault().transfer(); }
    void sink() { task.exec(); }
}
