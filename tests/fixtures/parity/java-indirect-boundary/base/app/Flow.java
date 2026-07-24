class Flow {
    void sink() { middle(); }
    void middle() { helper(); }
    void helper() { int value = 1; }
}
