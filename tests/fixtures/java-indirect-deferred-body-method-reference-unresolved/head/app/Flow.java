interface Task { void exec(); }
class Flow {
    Task task;
    void sink() { task.exec(); }
    void wire() {
        task = () -> {
            Task inner = External::run;
            inner.exec();
        };
    }
    void unrelated() { int marker = 2; }
}
