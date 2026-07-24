interface Task { void exec(); }
class Vault { void transfer() { int value = 1; } }
enum Reg {
    A;
    static Task task = () -> new Vault().transfer();
    void sink() { task.exec(); }
}
