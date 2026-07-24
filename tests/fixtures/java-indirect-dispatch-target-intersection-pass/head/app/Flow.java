interface Task { void exec(); }
interface Other { void go(); }
class Vault { void transfer() { int value = 2; } }
class Flow {
    Task task;
    Other other = () -> new Vault().transfer();
    void sink() { task.exec(); }
}
