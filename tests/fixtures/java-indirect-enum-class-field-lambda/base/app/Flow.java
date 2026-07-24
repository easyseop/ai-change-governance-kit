interface Task { void exec(); }
class Vault { void transfer() { int value = 1; } }
class Reg {
    static Task task = () -> new Vault().transfer();
    void sink() { task.exec(); }
}
