interface Port { void run(); }
class Ledger { void settle() { int value = 2; } }
class Flow {
    Port port;
    void sink() { port.run(); }
    void set(Port p) { port = p; }
    void wire() { set(() -> new Ledger().settle()); }
}
