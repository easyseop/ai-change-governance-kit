interface Task { void exec(); }
interface Other { void go(); }
class Vault { void transfer() { int value = 1; } }
class Flow {
    Other other = () -> new Vault().transfer();
    void sink(Task task) { task.exec(); }
}
