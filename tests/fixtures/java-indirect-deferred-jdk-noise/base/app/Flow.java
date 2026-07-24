import java.util.List;

interface Task { void exec(); }
class Flow {
    List<String> rows;
    void sink() { check(); }
    void check() { rows.size(); }
    void log(String value) { int marker = 1; }
    void wire() { consume(() -> log("base")); }
}
