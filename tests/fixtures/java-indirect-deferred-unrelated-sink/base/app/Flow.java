interface Task { void exec(); }
class Flow {
    void sink() { helper(); }
    void helper() { int value = 1; }
    void log(String value) { int marker = 1; }
    void wire() { consume(() -> log("base")); }
}
