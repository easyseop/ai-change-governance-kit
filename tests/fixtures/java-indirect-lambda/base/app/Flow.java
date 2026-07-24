interface Port { void run(); }
class Ledger { void settle() { int value = 1; } }
class Flow {
    Port port;
    void sink() { port.run(); }
    void wire() { port = () -> new Ledger().settle(); }
}
