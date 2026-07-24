interface Task { void exec(); }
class Vault { void transfer() { int value = 1; } }
interface Cfg {
    Task TASK = () -> new Vault().transfer();
    static void sink() { TASK.exec(); }
}
