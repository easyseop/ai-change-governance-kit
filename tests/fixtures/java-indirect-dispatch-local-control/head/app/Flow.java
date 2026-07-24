interface Task { void exec(); }
interface Other { void go(); }
class Vault { void transfer() { int value = 2; } }
class Flow {
    Other other = () -> new Vault().transfer();
    Task make() { return null; }
    void sink() {
        Task task = make();
        task.exec();
    }
}
