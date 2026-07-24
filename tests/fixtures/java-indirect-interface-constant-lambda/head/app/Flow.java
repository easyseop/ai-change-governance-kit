interface Task { void exec(); }
class Vault { void transfer() { int value = 2; } }
interface Cfg {
    Task TASK = () -> new Vault().transfer();
    static void sink() { TASK.exec(); }
}
