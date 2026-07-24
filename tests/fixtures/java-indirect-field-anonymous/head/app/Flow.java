interface Task { void exec(); }
class Vault { void transfer() { int value = 2; } }
class Flow {
    Task task = new Task() { public void exec() { new Vault().transfer(); } };
    void sink() { task.exec(); }
}
