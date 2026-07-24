interface Task { void exec(); }
class Flow {
    void log(String value) { int marker = 1; }
    Task task = () -> log("static");
}
