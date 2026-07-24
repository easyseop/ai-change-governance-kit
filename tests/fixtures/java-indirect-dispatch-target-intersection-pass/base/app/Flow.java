interface Task { void exec(); }
interface Other { void go(); }
class Vault { void transfer() { int value = 1; } }
class Flow {
    Task task;
    Other other = () -> new Vault().transfer();
    void sink() { task.exec(); }
}
