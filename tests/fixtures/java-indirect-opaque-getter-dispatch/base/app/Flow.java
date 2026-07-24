interface Task { void exec(); }
class Vault { void transfer() { int value = 1; } }
class Flow {
    Task task = () -> new Vault().transfer();
    Task get() { return task; }
    void sink() { get().exec(); }
}
