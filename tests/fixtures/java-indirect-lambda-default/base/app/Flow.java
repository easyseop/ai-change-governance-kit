interface Port {
    void run();
    default void helper() { int value = 0; }
}
class Ledger { void settle() { int value = 1; } }
class Flow {
    Port port;
    void sink() { port.run(); }
    void wire() { port = () -> new Ledger().settle(); }
}
