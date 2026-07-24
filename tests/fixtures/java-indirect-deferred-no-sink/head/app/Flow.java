interface Task { void exec(); }
class Flow {
    void log(String value) { int marker = 2; }
    void wire() { consume(() -> log("head")); }
}
