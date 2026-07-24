interface Task { void exec(); }
enum Reg {
    A {
        Task task = () -> {};
    };
}
