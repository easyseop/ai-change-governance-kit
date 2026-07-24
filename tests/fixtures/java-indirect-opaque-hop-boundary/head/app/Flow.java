import java.util.List;

interface Task { void exec(); }
class Vault { void transfer() { int value = 2; } }
class Flow {
    Task task = () -> new Vault().transfer();
    List tasks;
    void mid() { tasks.get(0).exec(); }
    void sink() { mid(); rows(); }
    void rows() { int marker = 1; }
}
