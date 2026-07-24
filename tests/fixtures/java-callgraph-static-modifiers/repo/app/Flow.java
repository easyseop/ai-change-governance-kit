interface Task { void exec(); }
class Flow {
    static final Task CONST_TASK = () -> {};
    private static Task privateTask = () -> {};
    @Deprecated
    static Task annotatedTask = () -> {};
}
